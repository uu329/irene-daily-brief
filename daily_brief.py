import os
import re
import json
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime
from html import escape
from zoneinfo import ZoneInfo

import feedparser
import requests
from dotenv import load_dotenv
from openai import OpenAI


# ============================================================
# Irene Daily Consumer & Strategy Brief
# Version 1.4
#
# What this version does:
# 1. Uses Melbourne date/time.
# 2. Reads RSS with requests + browser-like headers.
# 3. Focuses on consumer markets, China/Australia macro, and AI/TMT enablers.
# 4. Uses OpenAI to produce structured JSON.
# 5. Uses Python to render a stable, cleaner HTML email template.
# ============================================================

load_dotenv()

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
LOOKBACK_HOURS = 36
MAX_PER_SOURCE = 25
MAX_ARTICLES_FOR_AI = 45
MIN_ARTICLES_FOR_DIGEST = 8
REQUEST_TIMEOUT = 20


# ------------------------------------------------------------
# RSS sources
# You can add/remove sources here later.
# ------------------------------------------------------------
SOURCES = {
    # Consumer / retail / marketing / supply chain
    "Retail Dive": "https://www.retaildive.com/feeds/news/",
    "Marketing Dive": "https://www.marketingdive.com/feeds/news/",
    "Supply Chain Dive": "https://www.supplychaindive.com/feeds/news/",
    "Food Dive": "https://www.fooddive.com/feeds/news/",
    "Retail TouchPoints": "https://www.retailtouchpoints.com/feed/",

    # China macro / business
    "China NBS Latest Releases": "http://www.stats.gov.cn/english/PressRelease/rss.xml",
    "China Daily Bizchina": "http://www.chinadaily.com.cn/rss/bizchina_rss.xml",
    "China Daily China News": "http://www.chinadaily.com.cn/rss/china_rss.xml",

    # Australia macro / policy
    "RBA Media Releases": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
    "RBA Speeches": "https://www.rba.gov.au/rss/rss-cb-speeches.xml",
    "RBA Statement on Monetary Policy": "https://www.rba.gov.au/rss/rss-cb-smp.xml",

    # Global AI / tech enabler
    "OpenAI News": "https://openai.com/news/rss.xml",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "MIT Technology Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
    "Hugging Face Blog": "https://huggingface.co/blog/feed.xml",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "Microsoft Blog": "https://blogs.microsoft.com/feed/",
    "NVIDIA Blog": "https://blogs.nvidia.com/feed/",
    "NVIDIA Newsroom": "https://nvidianews.nvidia.com/rss",

    # Consulting / strategy
    "McKinsey Insights": "https://www.mckinsey.com/insights/rss.aspx",
    "BCG Insights": "https://www.bcg.com/publications/rss",
}


HIGH_SIGNAL_SOURCES = {
    # Consumer / retail
    "Retail Dive",
    "Marketing Dive",
    "Supply Chain Dive",
    "Food Dive",
    "Retail TouchPoints",

    # China macro / business
    "China NBS Latest Releases",
    "China Daily Bizchina",
    "China Daily China News",

    # Australia macro
    "RBA Media Releases",
    "RBA Speeches",
    "RBA Statement on Monetary Policy",

    # AI / tech enabler
    "OpenAI News",
    "Google AI Blog",
    "NVIDIA Blog",
    "NVIDIA Newsroom",

    # Consulting
    "McKinsey Insights",
    "BCG Insights",
}


KEYWORDS = [
    # Consumer / retail / brands
    "consumer", "consumption", "retail", "shopping", "e-commerce", "ecommerce",
    "brand", "brands", "fmcg", "beauty", "cosmetics", "fashion", "luxury",
    "sportswear", "food", "beverage", "coffee", "tea", "restaurant", "grocery",
    "supermarket", "membership", "costco", "walmart", "amazon", "nike", "adidas",
    "lvmh", "l'oréal", "loreal", "procter", "p&g", "unilever", "nestlé",
    "nestle", "coca-cola", "pepsico", "starbucks", "retail sales",

    # China consumer / platform economy
    "china", "chinese consumer", "消费", "零售", "社零", "社会消费品零售总额",
    "电商", "直播电商", "内容电商", "即时零售", "抖音", "小红书", "拼多多",
    "京东", "天猫", "淘宝", "美妆", "茶饮", "咖啡", "新消费", "出海",
    "品牌出海", "价格战", "性价比", "平台经济", "服务消费", "居民消费",

    # Macro / economic conditions
    "macro", "macroeconomy", "economy", "economic growth", "gdp", "inflation",
    "cpi", "ppi", "interest rate", "central bank", "rba", "unemployment",
    "employment", "labour market", "labor market", "wage", "income", "household spending",
    "consumer confidence", "housing", "mortgage", "credit", "exports", "imports",
    "australia", "澳洲", "澳大利亚", "中国经济", "物价", "通胀", "就业", "工资",
    "房地产", "出口", "进口", "货币政策", "财政政策",

    # Platform / channel / supply chain
    "platform", "channel", "omnichannel", "marketplace", "algorithm", "recommendation",
    "creator economy", "livestream", "private traffic", "supply chain", "inventory",
    "fulfillment", "logistics", "last mile", "warehouse", "pricing", "discount",
    "margin", "profitability",

    # AI / TMT as enabler
    "ai", "artificial intelligence", "generative ai", "genai", "agent", "llm",
    "automation", "data", "cloud", "digital transformation", "marketing technology",
    "personalization", "personalisation", "customer insight", "customer analytics",

    # Consulting / strategy
    "consulting", "strategy", "growth", "productivity", "operating model",
    "market share", "competitive advantage", "mckinsey", "bcg", "bain",
    "deloitte", "pwc", "ey", "kpmg",
]


PRIORITY_KEYWORDS = [
    "consumer", "retail", "brand", "china", "australia", "inflation", "cpi",
    "retail sales", "household spending", "ai", "platform", "pricing", "growth",
    "margin", "e-commerce", "supply chain", "rba", "消费", "零售", "社零",
    "电商", "通胀", "居民消费", "价格战",
]


# ------------------------------------------------------------
# Basic helpers
# ------------------------------------------------------------
def now_melbourne() -> datetime:
    return datetime.now(MELBOURNE_TZ)


def get_today_string() -> str:
    return now_melbourne().strftime("%Y-%m-%d")


def get_env(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def clean_text(text: str, max_length: int = 700) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "..."
    return text


def normalize_url(url: str) -> str:
    return url.strip().split("?")[0].rstrip("/")


# ------------------------------------------------------------
# RSS reading and article selection
# ------------------------------------------------------------
def read_rss_feed(feed_url: str):
    """
    Read RSS feed with a browser-like User-Agent.
    Some websites block Python's default request.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    try:
        response = requests.get(feed_url, headers=headers, timeout=REQUEST_TIMEOUT)
        print(f"  HTTP status: {response.status_code}")
        response.raise_for_status()
        return feedparser.parse(response.content)
    except Exception as error:
        print(f"  requests failed, falling back to feedparser.parse(url): {error}")
        return feedparser.parse(feed_url)


def parse_article_datetime(entry) -> datetime | None:
    for key in ["published", "updated", "created"]:
        value = entry.get(key)
        if not value:
            continue
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(MELBOURNE_TZ)
        except Exception:
            continue

    # feedparser sometimes provides parsed structs
    for key in ["published_parsed", "updated_parsed"]:
        value = entry.get(key)
        if not value:
            continue
        try:
            dt = datetime(*value[:6], tzinfo=timezone.utc)
            return dt.astimezone(MELBOURNE_TZ)
        except Exception:
            continue

    return None


def is_recent_article(article_dt: datetime | None) -> bool:
    # Keep undated RSS items because many feeds do not provide clean timestamps.
    if article_dt is None:
        return True
    cutoff = now_melbourne() - timedelta(hours=LOOKBACK_HOURS)
    return article_dt >= cutoff


def article_score(article: dict) -> int:
    source = article.get("source", "")
    text = f'{article.get("title", "")} {article.get("summary", "")}'.lower()

    score = 0

    if source in HIGH_SIGNAL_SOURCES:
        score += 5

    if is_recent_article(article.get("_dt")):
        score += 3

    for keyword in PRIORITY_KEYWORDS:
        if keyword.lower() in text:
            score += 2

    for keyword in KEYWORDS:
        if keyword.lower() in text:
            score += 1

    # Consumer and macro should be the main axis.
    if any(k in text for k in ["consumer", "retail", "brand", "消费", "零售", "社零"]):
        score += 4

    if any(k in text for k in ["inflation", "cpi", "rba", "gdp", "就业", "通胀", "居民消费"]):
        score += 3

    return score


def deduplicate_articles(articles: list[dict]) -> list[dict]:
    seen = set()
    unique_articles = []

    for article in articles:
        link_key = normalize_url(article.get("link", ""))
        title_key = article.get("title", "").strip().lower()
        key = link_key or title_key

        if not key or key in seen:
            continue

        seen.add(key)
        unique_articles.append(article)

    return unique_articles


def fetch_articles(max_per_source: int = MAX_PER_SOURCE) -> list[dict]:
    """
    Version 1.4 selection logic:
    1. Build a primary pool: recent + keyword/high-signal articles.
    2. Build a fallback pool: latest articles from all sources.
    3. If primary pool is too small, combine with fallback pool.
    """
    selected_articles = []
    fallback_articles = []

    print("Fetching articles from RSS sources...")

    for source_name, feed_url in SOURCES.items():
        print(f"- Reading: {source_name}")

        try:
            feed = read_rss_feed(feed_url)

            if getattr(feed, "bozo", False):
                print(f"  Warning: feedparser could not fully parse {source_name}.")

            entries = feed.entries[:max_per_source]
            print(f"  Entries found: {len(entries)}")

            for entry in entries:
                title = clean_text(entry.get("title", ""), max_length=260)
                link = entry.get("link", "").strip()
                summary = clean_text(entry.get("summary", entry.get("description", "")), max_length=750)
                article_dt = parse_article_datetime(entry)

                if not title or not link:
                    continue

                article = {
                    "source": source_name,
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": article_dt.strftime("%Y-%m-%d %H:%M") if article_dt else "No clear RSS date",
                    "_dt": article_dt,
                }

                combined_text = f"{title} {summary}".lower()
                keyword_hit = any(keyword.lower() in combined_text for keyword in KEYWORDS)
                high_signal_source = source_name in HIGH_SIGNAL_SOURCES
                recent = is_recent_article(article_dt)

                fallback_articles.append(article)

                if recent and (keyword_hit or high_signal_source):
                    selected_articles.append(article)

        except Exception as error:
            print(f"  Error reading {source_name}: {error}")

    selected_articles = deduplicate_articles(selected_articles)
    fallback_articles = deduplicate_articles(fallback_articles)

    print(f"Primary selected articles: {len(selected_articles)}")
    print(f"Fallback candidate articles: {len(fallback_articles)}")

    if len(selected_articles) < MIN_ARTICLES_FOR_DIGEST:
        print("Primary selection too small. Using fallback articles to avoid an empty brief.")
        combined = deduplicate_articles(selected_articles + fallback_articles)
        return sorted(combined, key=article_score, reverse=True)

    return sorted(selected_articles, key=article_score, reverse=True)


# ------------------------------------------------------------
# OpenAI summarisation
# ------------------------------------------------------------
def format_articles_for_prompt(articles: list[dict]) -> str:
    blocks = []
    for index, article in enumerate(articles, start=1):
        blocks.append(
            f"""
Article {index}
Source: {article["source"]}
Title: {article["title"]}
Published: {article["published"]}
Summary: {article["summary"]}
Link: {article["link"]}
""".strip()
        )
    return "\n\n".join(blocks)


def strip_json_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_json_from_model(text: str) -> dict:
    text = strip_json_code_fence(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: extract the first JSON-looking object.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def default_digest_data(articles: list[dict], error_message: str | None = None) -> dict:
    first_article = articles[0] if articles else None

    return {
        "executive_snapshot": "今日信息源返回内容有限，系统已生成一版保守摘要；建议重点查看深读文章并继续观察明日趋势。",
        "top_3_market_signals": [],
        "china_consumer_macro_watch": [],
        "australia_macro_consumer_context": [],
        "global_consumer_retail_watch": [],
        "platform_channel_shift": [],
        "ai_tmt_enabler": [],
        "consulting_lens": {
            "case_question": "如果一个消费品牌在宏观压力和渠道变化下增长放缓，应该如何诊断问题？",
            "suggested_framework": "可从市场需求、消费者分层、渠道结构、价格体系、成本与利润率、竞争格局六个维度拆解。",
            "why_it_matters": "这类问题高度贴近消费品与零售咨询项目，也适合 Irene 进行 case interview 训练。",
        },
        "irene_angle": [
            "将每日高信号新闻记录到 Notion，按 Consumer / Macro / Platform / AI Enabler 分类。",
            "把重点事件改写成一个 case question，用于训练 issue tree 和商业判断。",
        ],
        "deep_read": {
            "title": first_article["title"] if first_article else "今日无明确推荐",
            "source": first_article["source"] if first_article else "N/A",
            "reason": "这是今日候选文章中最适合继续阅读的一篇。" if first_article else "今日 RSS 内容不足。",
            "link": first_article["link"] if first_article else "",
        },
        "system_note": error_message or "",
    }


def generate_digest_data(articles: list[dict]) -> dict:
    if not articles:
        return default_digest_data([], "No articles were fetched from RSS sources.")

    client = OpenAI()
    today = get_today_string()
    articles_text = format_articles_for_prompt(articles)

    prompt = f"""
You are Irene's personal consumer market and strategy intelligence analyst.

Irene is a University of Melbourne Bachelor of Arts student majoring in Economics. She targets strategy consulting. Her core interest is consumer markets, brand growth, retail, platforms, and commercial strategy. She also has prior China Mobile internship experience in AI, TMT, policy tracking, and industry research. Therefore, AI/TMT should be treated as an enabling lens, not the main theme.

Your task: based ONLY on the articles below, create a structured daily intelligence brief in Chinese.

Important rules:
1. Use Chinese as the main language, but keep English proper nouns.
2. Do not invent facts not supported by the articles.
3. Prioritise consumer market, retail, brand, platform/channel, China macro, Australia macro, and AI/TMT enabler signals.
4. Make it useful for consulting preparation: case interview, coffee chat, resume/LinkedIn, and Notion knowledge base.
5. If there is not enough information for a section, return an empty list for that section rather than inventing content.
6. Output ONLY valid JSON. Do not use Markdown. Do not wrap it in a code block.

Date: {today}
Timezone: Australia/Melbourne

Return JSON using exactly this schema:
{{
  "executive_snapshot": "one sentence in Chinese",
  "top_3_market_signals": [
    {{
      "headline": "short headline",
      "what_happened": "what happened",
      "why_it_matters": "why it matters",
      "irene_angle": "how Irene can use this",
      "source_title": "article title",
      "source_url": "article link"
    }}
  ],
  "china_consumer_macro_watch": [
    {{"headline": "", "insight": "", "source_title": "", "source_url": ""}}
  ],
  "australia_macro_consumer_context": [
    {{"headline": "", "insight": "", "source_title": "", "source_url": ""}}
  ],
  "global_consumer_retail_watch": [
    {{"headline": "", "insight": "", "source_title": "", "source_url": ""}}
  ],
  "platform_channel_shift": [
    {{"headline": "", "insight": "", "source_title": "", "source_url": ""}}
  ],
  "ai_tmt_enabler": [
    {{"headline": "", "insight": "", "source_title": "", "source_url": ""}}
  ],
  "consulting_lens": {{
    "case_question": "a consulting-style case question",
    "suggested_framework": "a concise framework Irene can use",
    "why_it_matters": "why this is useful"
  }},
  "irene_angle": [
    "specific suggestion 1",
    "specific suggestion 2",
    "specific suggestion 3"
  ],
  "deep_read": {{
    "title": "article title",
    "source": "source name",
    "reason": "why this is worth reading",
    "link": "article link"
  }}
}}

Articles:
{articles_text}
"""

    print("Generating structured daily brief with OpenAI...")

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=prompt,
        )
        return parse_json_from_model(response.output_text)
    except Exception as error:
        print(f"OpenAI generation or JSON parsing failed: {error}")
        return default_digest_data(articles, str(error))


# ------------------------------------------------------------
# HTML rendering
# ------------------------------------------------------------
def esc(value) -> str:
    return escape(str(value or ""))


def link_html(title: str, url: str) -> str:
    title = esc(title) if title else "Source"
    url = esc(url)
    if not url:
        return title
    return f'<a href="{url}" style="color:#2563eb; text-decoration:none;">{title}</a>'


def section_title(title: str, subtitle: str = "") -> str:
    subtitle_html = f'<div style="font-size:13px; color:#777; margin-top:4px;">{esc(subtitle)}</div>' if subtitle else ""
    return f"""
    <div style="margin-top:26px; margin-bottom:12px;">
      <div style="font-size:18px; font-weight:800; color:#111827;">{esc(title)}</div>
      {subtitle_html}
    </div>
    """


def empty_line() -> str:
    return """
    <div style="font-size:14px; color:#777; background:#f9fafb; border:1px solid #eeeeee; border-radius:12px; padding:14px 16px; line-height:1.7;">
      今日无明显高相关动态。
    </div>
    """


def small_source(item: dict) -> str:
    source_title = item.get("source_title") or item.get("title") or "Source"
    source_url = item.get("source_url") or item.get("link") or ""
    return f"""
    <div style="font-size:12px; color:#6b7280; margin-top:10px;">
      Source: {link_html(source_title, source_url)}
    </div>
    """


def top_signal_card(item: dict, index: int) -> str:
    return f"""
    <div style="border:1px solid #e5e7eb; border-radius:16px; padding:16px; margin-bottom:12px; background:#ffffff;">
      <div style="font-size:13px; color:#2563eb; font-weight:700; margin-bottom:6px;">Signal {index}</div>
      <div style="font-size:17px; font-weight:800; color:#111827; margin-bottom:10px;">{esc(item.get('headline'))}</div>
      <div style="font-size:14px; line-height:1.7; color:#374151;"><strong>What happened:</strong> {esc(item.get('what_happened'))}</div>
      <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:6px;"><strong>Why it matters:</strong> {esc(item.get('why_it_matters'))}</div>
      <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:6px;"><strong>Irene angle:</strong> {esc(item.get('irene_angle'))}</div>
      {small_source(item)}
    </div>
    """


def insight_item(item: dict) -> str:
    return f"""
    <div style="border-left:4px solid #d1d5db; padding:10px 0 10px 14px; margin-bottom:12px;">
      <div style="font-size:15px; font-weight:750; color:#111827;">{esc(item.get('headline'))}</div>
      <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:4px;">{esc(item.get('insight'))}</div>
      {small_source(item)}
    </div>
    """


def render_list_section(title: str, items: list, subtitle: str = "") -> str:
    html = section_title(title, subtitle)
    if not items:
        return html + empty_line()
    return html + "".join(insight_item(item) for item in items[:4])


def render_bullets(items: list[str]) -> str:
    if not items:
        return empty_line()
    bullet_html = "".join(
        f'<li style="margin-bottom:8px; line-height:1.7;">{esc(item)}</li>' for item in items[:5]
    )
    return f'<ul style="padding-left:20px; margin:0; color:#374151; font-size:14px;">{bullet_html}</ul>'


def ensure_list(value) -> list:
    if isinstance(value, list):
        return value
    if value:
        return [value]
    return []


def build_html_email(data: dict, article_count: int) -> str:
    today = get_today_string()

    top_signals = ensure_list(data.get("top_3_market_signals"))[:3]
    top_signals_html = "".join(
        top_signal_card(item, idx) for idx, item in enumerate(top_signals, start=1)
    ) or empty_line()

    consulting = data.get("consulting_lens") or {}
    deep_read = data.get("deep_read") or {}
    system_note = data.get("system_note") or ""

    system_note_html = ""
    if system_note:
        system_note_html = f"""
        <div style="margin-top:22px; background:#fff7ed; border:1px solid #fed7aa; border-radius:14px; padding:14px 16px; color:#9a3412; font-size:13px; line-height:1.7;">
          <strong>System note:</strong> {esc(system_note)}
        </div>
        """

    return f"""
<!doctype html>
<html>
<body style="margin:0; padding:0; background:#f3f4f6;">
  <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,'PingFang SC','Microsoft YaHei',sans-serif; background:#f3f4f6; padding:24px;">
    <div style="max-width:760px; margin:0 auto; background:#ffffff; border-radius:20px; padding:28px; border:1px solid #e5e7eb;">
      <div style="font-size:13px; color:#6b7280; margin-bottom:8px;">{today} · Melbourne Time · {article_count} source items reviewed</div>
      <h1 style="font-size:28px; line-height:1.18; margin:0 0 14px; color:#111827;">Daily Consumer & Strategy Brief</h1>

      <div style="background:#eef2ff; border:1px solid #dbe4ff; border-radius:16px; padding:16px; margin:18px 0 22px;">
        <div style="font-size:13px; color:#4f46e5; font-weight:800; margin-bottom:6px;">Executive Snapshot</div>
        <div style="font-size:16px; line-height:1.7; color:#111827; font-weight:650;">{esc(data.get('executive_snapshot'))}</div>
      </div>

      {section_title('Top 3 Market Signals', '跨中国、澳洲、全球消费市场与 AI/TMT enabler 的今日最重要信号')}
      {top_signals_html}

      {render_list_section('China Consumer & Macro Watch', ensure_list(data.get('china_consumer_macro_watch')), '中国消费、零售、平台经济、社零、CPI/PPI、就业、政策与品牌竞争')}

      {render_list_section('Australia Macro & Consumer Context', ensure_list(data.get('australia_macro_consumer_context')), 'RBA、利率、通胀、工资、就业、住房、消费支出与本地零售环境')}

      {render_list_section('Global Consumer & Retail Watch', ensure_list(data.get('global_consumer_retail_watch')), '全球消费品公司、零售商、奢侈品、美妆、运动、食品饮料与电商平台')}

      {render_list_section('Platform & Channel Shift', ensure_list(data.get('platform_channel_shift')), '电商、内容平台、线下零售、会员制、直播电商、推荐算法与私域')}

      {render_list_section('AI / TMT Enabler', ensure_list(data.get('ai_tmt_enabler')), '只看技术如何改变消费、零售、营销、供应链、消费者洞察与渠道效率')}

      {section_title('Consulting Lens')}
      <div style="background:#f9fafb; border:1px solid #e5e7eb; border-radius:16px; padding:16px;">
        <div style="font-size:14px; line-height:1.7; color:#374151;"><strong>Case question:</strong> {esc(consulting.get('case_question'))}</div>
        <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:8px;"><strong>Suggested framework:</strong> {esc(consulting.get('suggested_framework'))}</div>
        <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:8px;"><strong>Why it matters:</strong> {esc(consulting.get('why_it_matters'))}</div>
      </div>

      {section_title('Irene Angle', '可以如何沉淀为 coffee chat、case interview、resume / LinkedIn 或 Notion 素材')}
      {render_bullets(ensure_list(data.get('irene_angle')))}

      {section_title('今日建议深读 1 篇')}
      <div style="background:#fff7ed; border:1px solid #fed7aa; border-radius:16px; padding:16px;">
        <div style="font-size:16px; font-weight:800; color:#111827;">{link_html(deep_read.get('title', '今日无明确推荐'), deep_read.get('link', ''))}</div>
        <div style="font-size:13px; color:#6b7280; margin-top:6px;">Source: {esc(deep_read.get('source'))}</div>
        <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:8px;">{esc(deep_read.get('reason'))}</div>
      </div>

      {system_note_html}

      <div style="border-top:1px solid #e5e7eb; margin-top:28px; padding-top:16px; font-size:12px; color:#9ca3af; line-height:1.6;">
        Generated automatically for Irene. Theme: consumer markets as the main axis, macro as context, AI/TMT as enabler, consulting as output.
      </div>
    </div>
  </div>
</body>
</html>
"""


def html_to_plain_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


# ------------------------------------------------------------
# Email sending
# ------------------------------------------------------------
def send_email(subject: str, body_html: str) -> None:
    smtp_host = get_env("SMTP_HOST")
    smtp_port = int(get_env("SMTP_PORT"))
    smtp_username = get_env("SMTP_USERNAME")
    smtp_password = get_env("SMTP_PASSWORD")
    email_from = get_env("EMAIL_FROM")
    email_to_raw = get_env("EMAIL_TO")

    recipients = [email.strip() for email in email_to_raw.split(",") if email.strip()]
    if not recipients:
        raise ValueError("EMAIL_TO is empty. Please check your .env file.")

    print(f"Sending email to: {', '.join(recipients)}")

    message = MIMEMultipart("alternative")
    message["From"] = email_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject

    plain_text = html_to_plain_text(body_html)
    message.attach(MIMEText(plain_text, "plain", "utf-8"))
    message.attach(MIMEText(body_html, "html", "utf-8"))

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_username, smtp_password)
        server.sendmail(email_from, recipients, message.as_string())

    print("Email sent successfully.")


# ------------------------------------------------------------
# Main workflow
# ------------------------------------------------------------
def main() -> None:
    today = get_today_string()

    print("======================================")
    print("Irene Daily Consumer & Strategy Brief")
    print("======================================")
    print(f"Date: {today} Melbourne Time")

    articles = fetch_articles(max_per_source=MAX_PER_SOURCE)
    articles = articles[:MAX_ARTICLES_FOR_AI]

    print(f"Sending {len(articles)} articles to OpenAI.")

    digest_data = generate_digest_data(articles)
    digest_html = build_html_email(digest_data, article_count=len(articles))

    subject = f"Daily Consumer & Strategy Brief | {today}"
    send_email(subject, digest_html)

    print("Done.")


if __name__ == "__main__":
    main()