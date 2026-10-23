import os
import re
import json
import smtplib
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime
from html import escape
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

import feedparser
import requests
from dotenv import load_dotenv
from openai import OpenAI


# ============================================================
# Irene Daily World Strategy Brief
# Version 2.4
#
# Positioning:
# - Email = world radar
# - Notion = deep analysis workspace
#
# Key improvements:
# - More balanced industry-group sourcing.
# - Each item includes:
#   1. What happened
#   2. Why it matters
#   3. Daily Insight = mechanism explanation + conclusion
# - Daily Insight is objective and does NOT use "Irene", "you", or study-advice wording.
# - More sources for Energy / Industrials and Consulting / Strategy.
# - Avoid empty placeholder cards like "Source: Source".
#
# Date logic:
# - Report date = previous day
# - Generated time = actual Melbourne time
# ============================================================

load_dotenv()

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")

REPORT_LAG_DAYS = 1
LOOKBACK_HOURS = 42
MAX_PER_SOURCE = 25
MAX_ARTICLES_FOR_AI = 95
MIN_ARTICLES_FOR_DIGEST = 10
REQUEST_TIMEOUT = 20


# ------------------------------------------------------------
# Date helpers
# ------------------------------------------------------------
def now_melbourne() -> datetime:
    return datetime.now(MELBOURNE_TZ)


def get_generated_time_string() -> str:
    return now_melbourne().strftime("%Y-%m-%d %H:%M")


def get_report_date_string() -> str:
    report_date = now_melbourne() - timedelta(days=REPORT_LAG_DAYS)
    return report_date.strftime("%Y-%m-%d")


# ------------------------------------------------------------
# RSS helpers
# ------------------------------------------------------------
def google_news_rss(query: str) -> str:
    encoded = quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"


# ------------------------------------------------------------
# RSS sources
# ------------------------------------------------------------
SOURCES = {
    # Macro / policy / economy
    "China NBS Latest Releases": "http://www.stats.gov.cn/english/PressRelease/rss.xml",
    "China Daily Bizchina": "http://www.chinadaily.com.cn/rss/bizchina_rss.xml",
    "China Daily China News": "http://www.chinadaily.com.cn/rss/china_rss.xml",
    "RBA Media Releases": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
    "RBA Speeches": "https://www.rba.gov.au/rss/rss-cb-speeches.xml",
    "RBA Statement on Monetary Policy": "https://www.rba.gov.au/rss/rss-cb-smp.xml",
    "Google News - China Economy": google_news_rss(
        "China economy consumption retail sales property employment inflation policy"
    ),
    "Google News - Australia Economy": google_news_rss(
        "Australia economy inflation RBA employment wages household spending retail sales housing"
    ),
    "Google News - Global Economy": google_news_rss(
        "global economy inflation interest rates trade growth central banks"
    ),

    # Financial services / markets
    "CNBC Finance": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "Dow Jones Markets": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "Google News - Financial Services": google_news_rss(
        "banking financial services capital markets asset management insurance fintech credit lending"
    ),
    "Google News - Markets": google_news_rss(
        "markets bonds equities interest rates investors banks earnings"
    ),
    "Google News - Banks and Credit": google_news_rss(
        "banks credit lending mortgage risk financial regulation"
    ),

    # Consumer / retail
    "Retail Dive": "https://www.retaildive.com/feeds/news/",
    "Marketing Dive": "https://www.marketingdive.com/feeds/news/",
    "Supply Chain Dive": "https://www.supplychaindive.com/feeds/news/",
    "Food Dive": "https://www.fooddive.com/feeds/news/",
    "Retail TouchPoints": "https://www.retailtouchpoints.com/feed/",
    "Google News - Consumer Retail": google_news_rss(
        "consumer retail FMCG brands ecommerce luxury grocery pricing discount"
    ),
    "Google News - China Consumer": google_news_rss(
        "China consumer retail ecommerce brands coffee tea beauty luxury"
    ),

    # TMT / AI
    "OpenAI News": "https://openai.com/news/rss.xml",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "Microsoft Blog": "https://blogs.microsoft.com/feed/",
    "NVIDIA Blog": "https://blogs.nvidia.com/feed/",
    "NVIDIA Newsroom": "https://nvidianews.nvidia.com/rss",
    "Google News - TMT AI": google_news_rss(
        "AI cloud semiconductor telecom platform data center digital transformation AI agent coding agent model release"
    ),
    "Google News - AI Frontier": google_news_rss(
        "AI agents autonomous agents coding agents open source AI frontier model AI startup"
    ),

    # Healthcare / life sciences
    "Fierce Pharma": "https://www.fiercepharma.com/rss/xml",
    "Google News - Healthcare": google_news_rss(
        "healthcare pharma biotech medical services life sciences hospitals drug pricing"
    ),
    "Google News - Pharma Strategy": google_news_rss(
        "pharma strategy biotech partnership drug pricing FDA clinical trial"
    ),

    # Energy / industrials / infrastructure
    "Google News - Energy Industrials": google_news_rss(
        "energy industrials infrastructure manufacturing mining supply chain logistics renewables"
    ),
    "Google News - Supply Chain": google_news_rss(
        "supply chain logistics manufacturing industrial production shipping"
    ),
    "Google News - AI Energy Demand": google_news_rss(
        "AI data centers electricity power grid energy demand utilities"
    ),
    "Google News - Data Centers Energy": google_news_rss(
        "data centers power demand electricity grid energy infrastructure"
    ),
    "Google News - Infrastructure Investment": google_news_rss(
        "infrastructure investment transport utilities grid construction industrial strategy"
    ),
    "Google News - Mining Critical Minerals": google_news_rss(
        "mining critical minerals lithium copper rare earths battery supply chain"
    ),
    "Google News - Manufacturing Strategy": google_news_rss(
        "manufacturing strategy industrial production automation factory reshoring"
    ),

    # Consulting / strategy
    "McKinsey Insights": "https://www.mckinsey.com/insights/rss.aspx",
    "BCG Insights": "https://www.bcg.com/publications/rss",
    "Google News - Strategy Consulting": google_news_rss(
        "corporate strategy transformation productivity profitability operating model consulting"
    ),
    "Google News - McKinsey Strategy": google_news_rss(
        "McKinsey strategy transformation productivity operating model growth"
    ),
    "Google News - BCG Strategy": google_news_rss(
        "BCG strategy transformation growth productivity operating model"
    ),
    "Google News - Bain Strategy": google_news_rss(
        "Bain strategy customer growth private equity transformation"
    ),
    "Google News - Business Transformation": google_news_rss(
        "business transformation operating model cost reduction productivity restructuring"
    ),
    "Google News - Corporate Strategy": google_news_rss(
        "corporate strategy market share competitive advantage growth profitability"
    ),
}


# Regional discovery feeds: editorial emphasis, not a restriction on global coverage.
REGIONAL_QUERIES = {
    "China - Policy and Growth": ("中国 国务院 发改委 财政 经济 政策 消费 投资", "Macro & Policy"),
    "China - Retail and Brands": ("中国 新消费 零售 美妆 奢侈品 购物中心 品牌 首店", "Consumer & Retail"),
    "China - Commercial Property": ("中国 商业地产 购物中心 城市更新 商业综合体 招商", "Consumer & Retail"),
    "Hong Kong - Finance Retail": ("香港 港股 IPO 零售 旅游 商业地产 金融", "Financial Services & Markets"),
    "US - Economy and Business": ("United States Fed economy business retail earnings antitrust", "Macro & Policy"),
    "EU - Economy and Business": ("European Union ECB industrial policy regulation consumer business", "Macro & Policy"),
    "Australia - Retail and Property": ("Australia retail shopping centre commercial property RBA consumer", "Consumer & Retail"),
    "Sichuan - Policy Industry": ("四川省 经济 政策 财政 投资 招商 产业 统计 就业", "Sichuan & Chengdu Regional Intelligence"),
    "Sichuan - Consumption and Brands": ("四川 消费 零售 新消费 品牌 食品饮料 美妆 电商 文旅", "Sichuan & Chengdu Regional Intelligence"),
    "Chengdu - Consumer Trends": ("成都 消费市场 商圈 餐饮 咖啡 茶饮 美妆 年轻消费者 消费数据", "Sichuan & Chengdu Regional Intelligence"),
    "Chengdu - Brands and Retail": ("成都 品牌 首店 新店 入驻 连锁 零售 购物中心 本地品牌", "Sichuan & Chengdu Regional Intelligence"),
    "Chengdu - Retail and Commercial Property": ("成都 商业地产 购物中心 首店 商圈 城市更新 非标商业", "Sichuan & Chengdu Regional Intelligence"),
    "Chengdu - Planned Projects": ("成都 拟建 规划公示 土地出让 商业项目 招商 在建 开业 延期", "Sichuan & Chengdu Regional Intelligence"),
    "Sichuan - Major Projects": ("四川 重大项目 立项 环评 开工 竣工 招商 产业园", "Sichuan & Chengdu Regional Intelligence"),
    "Chengdu - Transit and Housing": ("成都 地铁 建设 新站点 住宅 人口 TOD 城市规划", "Sichuan & Chengdu Regional Intelligence"),
    "Sichuan - Industry and Companies": ("四川 成都 企业 投融资 制造业 电子信息 汽车 生物医药 新能源 供应链", "Sichuan & Chengdu Regional Intelligence"),
    "Chengdu - Tourism and Services": ("成都 四川 文旅 酒店 演出 会展 旅游 生活服务 夜间经济", "Sichuan & Chengdu Regional Intelligence"),
}
for _label, (_query, _category) in REGIONAL_QUERIES.items():
    SOURCES[_label] = google_news_rss(_query)

SOURCE_CATEGORY_HINTS = {
    "China NBS Latest Releases": "Macro & Policy",
    "China Daily Bizchina": "Macro & Policy",
    "China Daily China News": "Macro & Policy",
    "RBA Media Releases": "Macro & Policy",
    "RBA Speeches": "Macro & Policy",
    "RBA Statement on Monetary Policy": "Macro & Policy",
    "Google News - China Economy": "Macro & Policy",
    "Google News - Australia Economy": "Macro & Policy",
    "Google News - Global Economy": "Macro & Policy",

    "CNBC Finance": "Financial Services & Markets",
    "Dow Jones Markets": "Financial Services & Markets",
    "Google News - Financial Services": "Financial Services & Markets",
    "Google News - Markets": "Financial Services & Markets",
    "Google News - Banks and Credit": "Financial Services & Markets",

    "Retail Dive": "Consumer & Retail",
    "Marketing Dive": "Consumer & Retail",
    "Supply Chain Dive": "Consumer & Retail",
    "Food Dive": "Consumer & Retail",
    "Retail TouchPoints": "Consumer & Retail",
    "Google News - Consumer Retail": "Consumer & Retail",
    "Google News - China Consumer": "Consumer & Retail",

    "OpenAI News": "TMT & AI",
    "TechCrunch AI": "TMT & AI",
    "MIT Technology Review": "TMT & AI",
    "Google AI Blog": "TMT & AI",
    "Microsoft Blog": "TMT & AI",
    "NVIDIA Blog": "TMT & AI",
    "NVIDIA Newsroom": "TMT & AI",
    "Google News - TMT AI": "TMT & AI",
    "Google News - AI Frontier": "TMT & AI",

    "Fierce Pharma": "Healthcare & Life Sciences",
    "Google News - Healthcare": "Healthcare & Life Sciences",
    "Google News - Pharma Strategy": "Healthcare & Life Sciences",

    "Google News - Energy Industrials": "Energy, Industrials & Infrastructure",
    "Google News - Supply Chain": "Energy, Industrials & Infrastructure",
    "Google News - AI Energy Demand": "Energy, Industrials & Infrastructure",
    "Google News - Data Centers Energy": "Energy, Industrials & Infrastructure",
    "Google News - Infrastructure Investment": "Energy, Industrials & Infrastructure",
    "Google News - Mining Critical Minerals": "Energy, Industrials & Infrastructure",
    "Google News - Manufacturing Strategy": "Energy, Industrials & Infrastructure",

    "McKinsey Insights": "Consulting & Strategy Insights",
    "BCG Insights": "Consulting & Strategy Insights",
    "Google News - Strategy Consulting": "Consulting & Strategy Insights",
    "Google News - McKinsey Strategy": "Consulting & Strategy Insights",
    "Google News - BCG Strategy": "Consulting & Strategy Insights",
    "Google News - Bain Strategy": "Consulting & Strategy Insights",
    "Google News - Business Transformation": "Consulting & Strategy Insights",
    "Google News - Corporate Strategy": "Consulting & Strategy Insights",
}


SOURCE_CATEGORY_HINTS.update({label: cat for label, (_, cat) in REGIONAL_QUERIES.items()})

CATEGORY_ORDER = [
    "Macro & Policy",
    "Financial Services & Markets",
    "Consumer & Retail",
    "TMT & AI",
    "Healthcare & Life Sciences",
    "Energy, Industrials & Infrastructure",
    "Consulting & Strategy Insights",
    "Sichuan & Chengdu Regional Intelligence",
]


CATEGORY_TARGETS = {
    "Macro & Policy": 11,
    "Financial Services & Markets": 10,
    "Consumer & Retail": 9,
    "TMT & AI": 11,
    "Healthcare & Life Sciences": 8,
    "Energy, Industrials & Infrastructure": 12,
    "Consulting & Strategy Insights": 9,
    "Sichuan & Chengdu Regional Intelligence": 13,
}


HIGH_SIGNAL_SOURCES = set(SOURCES.keys())


PRIORITY_KEYWORDS = [
    # Macro / policy
    "economy", "economic growth", "gdp", "inflation", "cpi", "ppi",
    "interest rate", "central bank", "rba", "employment", "unemployment",
    "wage", "household spending", "retail sales", "property", "housing",
    "trade", "exports", "imports", "china economy", "australia economy",
    "policy", "regulation", "budget", "stimulus",
    "中国经济", "社零", "居民消费", "通胀", "就业", "房地产", "政策",
    "四川", "成都", "香港", "商圈", "购物中心", "城市更新", "拟建", "招商", "立项", "土地出让", "首店", "产业园",
    "消费市场", "新消费", "本地品牌", "品牌入驻", "新店", "连锁", "茶饮", "餐饮", "文旅", "会展", "服务业", "融资",

    # Financial services
    "bank", "banking", "financial services", "capital markets",
    "asset management", "wealth management", "insurance", "fintech",
    "credit", "lending", "mortgage", "markets", "bond", "equity",
    "investment", "investors", "rate cut", "rate hike",

    # Consumer / retail
    "consumer", "retail", "brand", "brands", "fmcg", "e-commerce", "ecommerce",
    "grocery", "luxury", "beauty", "fashion", "food", "beverage",
    "pricing", "discount", "margin", "profitability", "shopping",
    "消费", "零售", "电商", "品牌", "价格战", "性价比",

    # TMT / AI
    "ai", "artificial intelligence", "generative ai", "cloud", "semiconductor",
    "chip", "telecom", "platform", "digital transformation", "data center",
    "automation", "agent", "llm", "coding agent", "model release",
    "frontier model", "ai safety", "prompt injection", "open source ai",

    # Healthcare
    "healthcare", "pharma", "biotech", "medical", "life sciences", "drug",
    "hospital", "health insurance", "clinical", "vaccine", "FDA",

    # Energy / industrials
    "energy", "renewable", "oil", "gas", "mining", "manufacturing",
    "infrastructure", "supply chain", "logistics", "industrial",
    "electricity", "battery", "ev", "grid", "power demand", "data center",

    # Consulting / strategy
    "strategy", "growth", "profitability", "productivity", "operating model",
    "transformation", "market share", "competitive advantage", "cost reduction",
    "restructuring", "pricing power",
]


GENERAL_KEYWORDS = PRIORITY_KEYWORDS + [
    "mckinsey", "bcg", "bain", "deloitte", "pwc", "ey", "kpmg",
    "china", "australia", "global", "asia", "us", "europe",
    "consumer confidence", "business confidence", "earnings", "revenue",
    "cost", "pricing power", "demand", "supply", "channels",
]


# ------------------------------------------------------------
# Basic helpers
# ------------------------------------------------------------
def get_env(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def clean_text(text: str, max_length: int = 750) -> str:
    if not text:
        return ""

    text = str(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
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
    if article_dt is None:
        return True

    cutoff = now_melbourne() - timedelta(hours=LOOKBACK_HOURS)
    return article_dt >= cutoff


def infer_region(article: dict) -> str:
    text = f'{article.get("source", "")} {article.get("title", "")} {article.get("summary", "")}'.lower()
    if any(k in text for k in ("成都", "chengdu", "四川", "sichuan")):
        return "Sichuan/Chengdu"
    if any(k in text for k in ("香港", "hong kong", "hongkong", "hkex")):
        return "Hong Kong"
    if any(k in text for k in ("china", "chinese", "beijing", "shanghai", "中国", "北京", "上海")):
        return "China"
    if any(k in text for k in ("australia", "australian", "rba", "sydney", "melbourne", "澳大利亚", "澳洲")):
        return "Australia"
    if any(k in text for k in ("u.s.", "united states", "america", "wall street", "美国")):
        return "US"
    if any(k in text for k in ("european union", "europe", "ecb", "eu commission", "欧盟", "欧洲")):
        return "EU/Europe"
    return "Global"


def article_score(article: dict) -> int:
    source = article.get("source", "")
    source_category = article.get("category_hint", "")
    text = f'{source} {article.get("title", "")} {article.get("summary", "")}'.lower()

    score = 0

    if source in HIGH_SIGNAL_SOURCES:
        score += 4

    if is_recent_article(article.get("_dt")):
        score += 3

    for keyword in PRIORITY_KEYWORDS:
        if keyword.lower() in text:
            score += 3

    for keyword in GENERAL_KEYWORDS:
        if keyword.lower() in text:
            score += 1

    # Make selected industry groups structurally important.
    if source_category == "Macro & Policy":
        score += 5

    if source_category == "Financial Services & Markets":
        score += 4

    if source_category == "TMT & AI":
        score += 3

    if source_category == "Healthcare & Life Sciences":
        score += 3

    if source_category == "Energy, Industrials & Infrastructure":
        score += 4

    if source_category == "Consulting & Strategy Insights":
        score += 4

    if source_category == "Consumer & Retail":
        score += 6
    if source_category == "Sichuan & Chengdu Regional Intelligence":
        score += 14
    region = article.get("region_hint", "")
    score += {"China": 13, "Sichuan/Chengdu": 17, "Hong Kong": 9,
              "US": 6, "Australia": 6, "EU/Europe": 6}.get(region, 0)
    if any(term in text for term in ("购物中心", "commercial property", "shopping mall", "城市更新", "首店", "规划公示", "拟建")):
        score += 10

    return score


def deduplicate_articles(articles: list[dict]) -> list[dict]:
    seen = set()
    seen_titles = []
    unique_articles = []

    for article in articles:
        link_key = normalize_url(article.get("link", ""))
        title_key = article.get("title", "").strip().lower()
        key = link_key or title_key

        title_compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", title_key)
        if not key or key in seen:
            continue
        if title_compact and any(SequenceMatcher(None, title_compact, old).ratio() >= 0.91 for old in seen_titles[-400:]):
            continue
        if title_compact:
            seen_titles.append(title_compact)
        seen.add(key)
        unique_articles.append(article)

    return unique_articles


def fetch_articles(max_per_source: int = MAX_PER_SOURCE) -> list[dict]:
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

            category_hint = SOURCE_CATEGORY_HINTS.get(source_name, "General")

            for entry in entries:
                title = clean_text(entry.get("title", ""), max_length=260)
                link = entry.get("link", "").strip()
                summary = clean_text(entry.get("summary", entry.get("description", "")), max_length=800)
                article_dt = parse_article_datetime(entry)

                if not title or not link:
                    continue

                article = {
                    "source": source_name,
                    "category_hint": category_hint,
                    "region_hint": "",
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": article_dt.strftime("%Y-%m-%d %H:%M") if article_dt else "No clear RSS date",
                    "_dt": article_dt,
                }

                article["region_hint"] = infer_region(article)

                combined_text = f"{title} {summary}".lower()
                keyword_hit = any(keyword.lower() in combined_text for keyword in GENERAL_KEYWORDS)
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


def balance_articles_by_category(articles: list[dict], max_total: int = MAX_ARTICLES_FOR_AI) -> list[dict]:
    """
    Balance article candidates by consulting-style industry group.

    This prevents one high-volume category from crowding out other sections.
    """
    articles = deduplicate_articles(articles)

    grouped = {category: [] for category in CATEGORY_ORDER}

    for article in articles:
        category = article.get("category_hint", "General")
        if category in grouped:
            grouped[category].append(article)

    balanced = []

    print("Article candidates by category:")

    for category in CATEGORY_ORDER:
        category_articles = sorted(grouped.get(category, []), key=article_score, reverse=True)
        target = CATEGORY_TARGETS.get(category, 6)
        chosen = category_articles[:target]

        print(f"- {category}: {len(category_articles)} available, {len(chosen)} selected")

        balanced.extend(chosen)

    balanced = deduplicate_articles(balanced)

    if len(balanced) < max_total:
        selected_keys = {normalize_url(article.get("link", "")) for article in balanced}

        remaining = [
            article for article in articles
            if normalize_url(article.get("link", "")) not in selected_keys
        ]

        remaining = sorted(remaining, key=article_score, reverse=True)

        space_left = max_total - len(balanced)
        balanced.extend(remaining[:space_left])

    # Regional coverage is an editorial floor where possible; global stories remain eligible.
    regional_floors = {"China": 12, "Hong Kong": 4, "US": 5, "Australia": 4, "EU/Europe": 4}
    chosen = deduplicate_articles(balanced)[:max_total]
    regional_all = [a for a in articles if a.get("region_hint") != "Sichuan/Chengdu"]
    def count_region(region):
        return sum(a.get("region_hint") == region for a in chosen)
    for region, floor in regional_floors.items():
        extra = [a for a in regional_all if a.get("region_hint") == region and a not in chosen]
        for item in extra[:max(0, floor - count_region(region))]:
            replaceable = [i for i, a in enumerate(chosen)
                           if a.get("region_hint") == "Global" and a.get("category_hint") != "Consulting & Strategy Insights"]
            if replaceable:
                chosen[replaceable[-1]] = item
            elif len(chosen) < max_total:
                chosen.append(item)
    print("Regional candidate mix:", {r: count_region(r) for r in regional_floors})
    return deduplicate_articles(chosen)[:max_total]


# ------------------------------------------------------------
# OpenAI summarisation
# ------------------------------------------------------------
def format_articles_for_prompt(articles: list[dict]) -> str:
    blocks = []

    for index, article in enumerate(articles, start=1):
        blocks.append(
            f"""
Article {index}
Category Hint: {article["category_hint"]}
Region Hint: {article["region_hint"]}
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
        start = text.find("{")
        end = text.rfind("}")

        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])

        raise


def default_digest_data(articles: list[dict], error_message: str | None = None) -> dict:
    first_article = articles[0] if articles else None

    return {
        "executive_snapshot": "今日信息源返回内容有限，系统已生成保守版摘要；建议重点查看 Top Signals 与深读文章。",
        "top_5_strategic_signals": [],
        "macro_policy_watch": [],
        "financial_services_markets": [],
        "consumer_retail": [],
        "tmt_ai": [],
        "healthcare_life_sciences": [],
        "energy_industrials_infrastructure": [],
        "consulting_strategy_insights": [],
        "sichuan_chengdu_intelligence": [],
        "why_this_matters": "今日可用信息不足，暂不提炼强趋势判断。",
        "notion_analysis_seed": {
            "signal": "今日无明确高信号事件",
            "analysis_question": "今天看到的外部变化中，哪一条最可能影响企业增长或行业竞争格局？",
            "suggested_framework": "可用 3C、4P、Profit Tree、Price × Volume × Mix、Value Chain 或 Porter Five Forces 进行初步拆解。",
            "notion_skill_tags": [
                "行业洞察 / Industry Insight",
                "结构化能力 / Structured Thinking",
            ],
        },
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

    report_date = get_report_date_string()
    generated_time = get_generated_time_string()
    articles_text = format_articles_for_prompt(articles)

    prompt = f"""
You are Irene's personal world strategy radar.

Irene is a University of Melbourne Bachelor of Arts student majoring in Economics.
She targets strategy consulting.
She wants to know what is happening in the world every day, especially business, economy, markets, and industry changes.

The email should be a world radar, not a heavy case-training task.
Deep analysis will happen later in Irene's Notion consulting training workspace.

Irene's current needs:
- Restore sensitivity to what is happening in the outside world.
- Build consulting-style commercial judgement.
- Connect news signals to structured thinking, case interview logic, and Notion analysis.
- Understand how macro, finance, consumer, TMT/AI, healthcare, energy/industrial, and consulting signals affect business decisions.
- Avoid generic study suggestions; each item should include a short analytical reasoning process and a clear conclusion.

Editorial positioning:
- Main goal: help Irene know key developments and understand why they matter.
- Use consulting-style industry groups rather than region-only sections.
- Cover macro/policy, financial services, consumer/retail, TMT/AI, healthcare, energy/industrials, and consulting/strategy insights.
- Global coverage is ESSENTIAL. China is the primary editorial lens, NOT a geographic wall: never exclude a consequential worldwide event merely for its location.
- Among general news, aim for roughly China mainland 40-50%, US 15-20%, Hong Kong 10-15%, Australia 10%, EU 10% if the candidate evidence supports it; never fill quotas with weak news.
- Sichuan/Chengdu is a dedicated regional intelligence section IN ADDITION to the global coverage, not a replacement or quota deduction.
- Prioritise Chinese consumption/retail/beauty/FMCG, commercial property, city renewal, macro and policy, while preserving global banking, TMT, healthcare and energy coverage.
- Sichuan/Chengdu intelligence is a BROAD regional economic and business radar, NOT primarily a real-estate or construction digest.
- Treat the following regional beats as equally legitimate: (1) consumer demand, spending, retail sales and shopper behaviour; (2) local and incoming brands, first stores, product launches, beauty/FMCG, dining, coffee/tea, ecommerce and channel shifts; (3) shopping districts, commercial property, urban renewal and project pipeline; (4) tourism, hospitality, events and service economy; (5) Sichuan economic policy, investment, employment and business environment; (6) leading companies, startups, financing and industrial sectors; (7) Chengdu-Chongqing economic links and intra-Sichuan regional development.
- Select regional stories based on economic and commercial significance, material change and credible evidence, NOT just construction milestones. Aim to cover consumption/brands/retail regularly when credible items exist; do not force a category if no verifiable news exists.
- Monitor Chengdu and Sichuan planned / approved / under-construction / leasing / opening / delayed projects, including metro, housing, commercial and industrial developments, as ONE regional subtopic rather than the overriding focus.
- For consumer/brand stories, explain demand drivers, customer segment, pricing, competition, channels and potential impact on local businesses where evidence allows. For projects, preserve development stage, uncertainty and changes versus earlier documented milestones.
- For overseas events, discuss Chinese business implications ONLY if a verifiable mechanism is evident; do not invent connections.
- Financial services and markets should be a regular section.
- Consumer & Retail should be included, but not dominate the entire brief.
- AI/TMT should be treated as a business and strategy signal, not just tech gossip.
- Keep Notion connection lightweight: only one analysis seed at the end.

Rules:
1. Use Chinese as the main language. Keep English proper nouns.
2. Use only the articles provided. Do not invent facts.
3. Prioritise significance over quantity.
4. If a section has insufficient evidence, return an empty list for that section.
4a. Never return placeholder objects with empty strings. If a section has no real content, return [].
5. Every item must include:
   - what_happened: what happened in plain language
   - why_it_matters: why this matters for business, markets, economy, strategy, or industry structure
   - daily_insight: a short analytical reasoning process plus a clear conclusion
6. The daily_insight must NOT be a study suggestion. It must be an analytical result.
7. The daily_insight must be written in an objective analytical voice. Do NOT use "Irene", "你", "我们", "should learn", "should understand", "should pay attention to", "需关注", "应理解", "练习", "增强" as the subject or main expression.
8. The daily_insight should briefly explain the mechanism, not merely name the topic. It should show the chain:
   trigger → transmission mechanism → strategic implication.
9. A good daily_insight should usually contain 2 sentences:
   Sentence 1: explain how the signal transmits through the economy / market / industry / company.
   Sentence 2: state the strategic implication for companies, investors, consumers, or industry structure.
10. Do NOT write generic advice such as "关注...", "练习...", "增强...", "可以用...框架分析", "Irene应理解...".
11. You may mention a consulting framework only after giving the conclusion, not as the main content.
12. Avoid repeating the same narrative between Top 5 and industry sections: top headlines may reappear only when the industry entry contributes a DISTINCT, additional fact or perspective; otherwise exclude it from the category list.
12a. A headline, number, or market-share claim must maintain the source's exact denominator, period, units and regulatory status. If source summary lacks them, remove the unsupported statistic instead of guessing.
12b. Never infer that planned equals confirmed, construction started, or operational. State project stage and uncertainty explicitly.
12c. Clearly distinguish reported facts, analytical inferences, and speculative implications. No forced causality; avoid generic AI claims unsupported by article text.
12d. RSS feeds expose excerpts, not full article bodies: do not claim independent verification or fill in missing numerical details.
12e. For region section, prioritize 2-4 truly consequential, distinct local events, and return [] when none. Planned projects matter even before construction, if the source contains a meaningful new status or milestone.
13. For each industry section, aim for 3-5 items when enough relevant source articles exist.
14. Output ONLY valid JSON. No Markdown. No code fences.

Daily Insight style guide:

Bad daily_insight:
"Irene应理解货币政策分歧如何传导至企业金融战略和市场定价。"

Good daily_insight:
"货币政策分歧会提高市场对未来利率路径的不确定性，进而影响债券收益率、企业融资成本和投资者风险偏好。对企业而言，这会压缩估值空间、推迟资本开支决策，并迫使 CFO 更谨慎地管理债务期限、现金流和再融资风险。"

Bad daily_insight:
"关注AI公司如何通过产品整合构筑竞争壁垒。"

Good daily_insight:
"OpenAI 聚焦产品与开发者工具生态整合，说明 AI 领先者正在从模型能力竞争转向平台入口、开发者生态和用户工作流竞争。未来竞争壁垒将更多来自高频场景嵌入、生态锁定和切换成本，而不只是单一模型性能。"

Bad daily_insight:
"关注跨国医药合作与联盟的盈利模式和风险分摊机制。"

Good daily_insight:
"大型药企通过跨国合作补充研发管线，说明制药行业正在把早期研发风险部分转移给外部创新公司，同时保留后期商业化和全球销售优势。对创新药企而言，价值不只来自单个产品，而来自能否进入全球药企的研发、审批和商业化网络。"

Bad daily_insight:
"理解新兴技术带来的间接能源成本压力。"

Good daily_insight:
"AI 需求先推高数据中心用电，再传导到电网投资和地方电价压力，说明 AI 竞争已经从模型能力延伸到能源获取与基础设施承载力。未来 AI 公司、数据中心运营商和地方政府之间的竞争会更像工业资源配置竞争。"

Report date: {report_date}
Generated time: {generated_time} Melbourne Time

Return JSON using exactly this schema:
{{
  "executive_snapshot": "one concise sentence in Chinese summarising the most important overall signal",
  "top_5_strategic_signals": [
    {{
      "headline": "short headline",
      "category": "Macro & Policy / Financial Services & Markets / Consumer & Retail / TMT & AI / Healthcare & Life Sciences / Energy, Industrials & Infrastructure / Consulting & Strategy Insights",
      "region": "China / Hong Kong / US / Australia / EU/Europe / Sichuan/Chengdu / Global / Other",
      "what_happened": "what happened",
      "why_it_matters": "why it matters for business, markets, economy, or strategy",
      "daily_insight": "a short analytical reasoning process plus a clear conclusion for this signal",
      "source_title": "article title",
      "source_url": "article link"
    }}
  ],
  "macro_policy_watch": [
    {{
      "headline": "",
      "region": "",
      "what_happened": "",
      "why_it_matters": "",
      "daily_insight": "",
      "source_title": "",
      "source_url": ""
    }}
  ],
  "financial_services_markets": [
    {{
      "headline": "",
      "region": "",
      "what_happened": "",
      "why_it_matters": "",
      "daily_insight": "",
      "source_title": "",
      "source_url": ""
    }}
  ],
  "consumer_retail": [
    {{
      "headline": "",
      "region": "",
      "what_happened": "",
      "why_it_matters": "",
      "daily_insight": "",
      "source_title": "",
      "source_url": ""
    }}
  ],
  "tmt_ai": [
    {{
      "headline": "",
      "region": "",
      "what_happened": "",
      "why_it_matters": "",
      "daily_insight": "",
      "source_title": "",
      "source_url": ""
    }}
  ],
  "healthcare_life_sciences": [
    {{
      "headline": "",
      "region": "",
      "what_happened": "",
      "why_it_matters": "",
      "daily_insight": "",
      "source_title": "",
      "source_url": ""
    }}
  ],
  "energy_industrials_infrastructure": [
    {{
      "headline": "",
      "region": "",
      "what_happened": "",
      "why_it_matters": "",
      "daily_insight": "",
      "source_title": "",
      "source_url": ""
    }}
  ],
  "consulting_strategy_insights": [
    {{
      "headline": "",
      "region": "",
      "what_happened": "",
      "why_it_matters": "",
      "daily_insight": "",
      "source_title": "",
      "source_url": ""
    }}
  ],
  "sichuan_chengdu_intelligence": [
    {{
      "headline": "",
      "region": "Sichuan/Chengdu",
      "project_name": "only for an identifiable development project, otherwise empty",
      "project_stage": "planning / proposed / approved / under construction / leasing / opening / operational / delayed / cancelled / not applicable; use not applicable for consumer, retail, brands, policy, and non-project news",
      "what_changed": "documented NEW information or milestone, otherwise state as snapshot",
      "what_happened": "",
      "why_it_matters": "",
      "daily_insight": "",
      "source_title": "",
      "source_url": ""
    }}
  ],
  "why_this_matters": "a short paragraph explaining what today's signals collectively suggest",
  "notion_analysis_seed": {{
    "signal": "the one signal most worth analysing later in Notion",
    "analysis_question": "one consulting-style question Irene can analyse in Notion",
    "suggested_framework": "one concise suggested framework, e.g. 3C, 4P, Profit Tree, Price x Volume x Mix, Value Chain, Market Sizing, Porter's Five Forces",
    "notion_skill_tags": [
      "suggested Notion skill tag 1",
      "suggested Notion skill tag 2"
    ]
  }},
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
        if os.getenv("STRICT_AI", "1") == "1":
            raise RuntimeError("OpenAI digest generation failed; not emailing a misleading fallback digest") from error
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


def badge(text: str) -> str:
    if not text:
        return ""

    return f"""
    <span style="display:inline-block; font-size:11px; color:#374151; background:#f3f4f6; border:1px solid #e5e7eb; border-radius:999px; padding:3px 8px; margin-right:6px;">
      {esc(text)}
    </span>
    """


def section_title(title: str, subtitle: str = "") -> str:
    subtitle_html = (
        f'<div style="font-size:13px; color:#777; margin-top:4px; line-height:1.5;">{esc(subtitle)}</div>'
        if subtitle
        else ""
    )

    return f"""
    <div style="margin-top:28px; margin-bottom:12px;">
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
    source_title = str(item.get("source_title") or item.get("title") or "").strip()
    source_url = str(item.get("source_url") or item.get("link") or "").strip()

    if not source_title and not source_url:
        return ""

    if source_title.lower() in ["source", "n/a", "none", "null"] and not source_url:
        return ""

    return f"""
    <div style="font-size:12px; color:#6b7280; margin-top:10px;">
      Source: {link_html(source_title or "Source", source_url)}
    </div>
    """


def daily_insight_block(daily_insight: str, font_size: int = 13) -> str:
    daily_insight = str(daily_insight or "").strip()

    if not daily_insight:
        return ""

    return f"""
    <div style="background:#f8fafc; border:1px solid #e5e7eb; border-radius:10px; padding:9px 11px; margin-top:8px;">
      <div style="font-size:12px; color:#64748b; font-weight:800; margin-bottom:4px;">Daily Insight</div>
      <div style="font-size:{font_size}px; line-height:1.7; color:#334155;">{esc(daily_insight)}</div>
    </div>
    """


def strategic_signal_card(item: dict, index: int) -> str:
    return f"""
    <div style="border:1px solid #e5e7eb; border-radius:16px; padding:16px; margin-bottom:12px; background:#ffffff;">
      <div style="margin-bottom:8px;">
        <span style="font-size:13px; color:#2563eb; font-weight:800;">Signal {index}</span>
      </div>
      <div style="margin-bottom:8px;">
        {badge(item.get("category"))}
        {badge(item.get("region"))}
      </div>
      <div style="font-size:17px; font-weight:800; color:#111827; margin-bottom:10px;">{esc(item.get("headline"))}</div>
      <div style="font-size:14px; line-height:1.7; color:#374151;"><strong>What happened:</strong> {esc(item.get("what_happened"))}</div>
      <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:6px;"><strong>Why it matters:</strong> {esc(item.get("why_it_matters"))}</div>
      {daily_insight_block(item.get("daily_insight"), font_size=14)}
      {small_source(item)}
    </div>
    """


def insight_item(item: dict) -> str:
    what_happened = str(item.get("what_happened", "")).strip()
    why_it_matters = str(item.get("why_it_matters", "")).strip()
    fallback_insight = str(item.get("insight", "")).strip()

    what_happened_html = ""
    if what_happened:
        what_happened_html = f"""
        <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:6px;">
          <strong>What happened:</strong> {esc(what_happened)}
        </div>
        """

    why_it_matters_html = ""
    if why_it_matters:
        why_it_matters_html = f"""
        <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:6px;">
          <strong>Why it matters:</strong> {esc(why_it_matters)}
        </div>
        """

    fallback_html = ""
    if fallback_insight and not what_happened and not why_it_matters:
        fallback_html = f"""
        <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:4px;">
          {esc(fallback_insight)}
        </div>
        """

    return f"""
    <div style="border-left:4px solid #d1d5db; padding:10px 0 10px 14px; margin-bottom:14px;">
      <div style="margin-bottom:6px;">{badge(item.get("region"))}{badge(item.get("project_stage"))}</div>
      <div style="font-size:15px; font-weight:750; color:#111827;">{esc(item.get("headline"))}</div>
      {f'<div style="font-size:12px;color:#64748b;">Latest documented change: {esc(item.get("what_changed"))}</div>' if item.get("what_changed") else ''}
      {what_happened_html}
      {why_it_matters_html}
      {fallback_html}
      {daily_insight_block(item.get("daily_insight"), font_size=13)}
      {small_source(item)}
    </div>
    """


def render_bullets(items: list[str]) -> str:
    if not items:
        return empty_line()

    bullet_html = "".join(
        f'<li style="margin-bottom:8px; line-height:1.7;">{esc(item)}</li>'
        for item in items[:5]
    )

    return f'<ul style="padding-left:20px; margin:0; color:#374151; font-size:14px;">{bullet_html}</ul>'


def ensure_list(value) -> list:
    if isinstance(value, list):
        return value

    if value:
        return [value]

    return []


def is_meaningful_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False

    meaningful_fields = [
        "headline",
        "what_happened",
        "why_it_matters",
        "insight",
        "daily_insight",
        "source_title",
        "source_url",
        "title",
        "link",
    ]

    for field in meaningful_fields:
        value = str(item.get(field, "")).strip()
        if value and value.lower() not in ["source", "n/a", "none", "null"]:
            return True

    return False


def clean_items(value, limit: int = 5) -> list[dict]:
    items = ensure_list(value)
    items = [item for item in items if is_meaningful_item(item)]
    return items[:limit]


def render_list_section(title: str, items: list, subtitle: str = "") -> str:
    html = section_title(title, subtitle)

    cleaned_items = clean_items(items, limit=5)

    if not cleaned_items:
        return html + empty_line()

    return html + "".join(insight_item(item) for item in cleaned_items)


PROJECT_PIPELINE_PATH = Path("data/chengdu_project_pipeline.json")


def record_project_changes(data: dict, articles: list[dict]) -> None:
    """Store sourced project milestones between GitHub runs. No inferred status changes."""
    PROJECT_PIPELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        history = json.loads(PROJECT_PIPELINE_PATH.read_text(encoding="utf-8")) if PROJECT_PIPELINE_PATH.exists() else {}
        if not isinstance(history, dict):
            history = {}
    except (ValueError, OSError):
        history = {}

    valid_links = {normalize_url(a.get("link", "")) for a in articles}
    changes = []
    for item in clean_items(data.get("sichuan_chengdu_intelligence", []), limit=8):
        name = str(item.get("project_name") or "").strip()
        stage = str(item.get("project_stage") or "").strip().lower()
        url = str(item.get("source_url") or "").strip()
        if not name or stage in ("", "not applicable", "n/a") or normalize_url(url) not in valid_links:
            continue
        key = re.sub(r"\s+", "", name).casefold()
        past = history.get(key)
        if not isinstance(past, dict):
            past = None
        if past and past.get("stage") == stage:
            # An unchanged stage is not a new milestone; retain the last observation.
            history[key]["last_seen"] = get_report_date_string()
            continue
        change_type = "首次记录" if past is None else f"{past.get('stage', '未知')} → {stage}"
        record = {
            "name": name, "stage": stage, "previous_stage": past.get("stage") if past else "",
            "last_seen": get_report_date_string(), "source_title": item.get("source_title", ""),
            "source_url": url, "what_changed": item.get("what_changed", ""),
        }
        history[key] = record
        changes.append({**record, "change_type": change_type})
    if changes or not PROJECT_PIPELINE_PATH.exists():
        PROJECT_PIPELINE_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    data["project_pipeline_changes"] = changes[:8]
    print(f"Project pipeline: {len(history)} tracked; {len(changes)} new or changed stages")


def render_pipeline_section(changes: list) -> str:
    if not changes:
        return ""  # No false claims that project status changed today.
    html = section_title("Chengdu Project Pipeline Changes", "与历史记录相比，项目阶段发生的变化；首次入库不等同于项目新启动")
    for entry in changes:
        name = esc(entry.get("name"))
        change = esc(entry.get("change_type"))
        summary = esc(entry.get("what_changed"))
        source = link_html(entry.get("source_title"), entry.get("source_url"))
        html += f'<div style="border:1px solid #e5e7eb; border-radius:12px; padding:12px; margin:8px 0"><strong>{name}</strong> · {change}<div style="font-size:13px; margin-top:6px">{summary}</div><div style="font-size:12px; margin-top:6px">Source: {source}</div></div>'
    return html


def build_html_email(data: dict, article_count: int) -> str:
    report_date = get_report_date_string()
    generated_time = get_generated_time_string()

    top_signals = clean_items(data.get("top_5_strategic_signals"), limit=5)
    top_signals_html = "".join(
        strategic_signal_card(item, idx)
        for idx, item in enumerate(top_signals, start=1)
    ) or empty_line()

    notion_seed = data.get("notion_analysis_seed") or {}
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
    <div style="max-width:780px; margin:0 auto; background:#ffffff; border-radius:20px; padding:28px; border:1px solid #e5e7eb;">
      <div style="font-size:13px; color:#6b7280; margin-bottom:8px;">
        Report date: {report_date} · Generated: {generated_time} Melbourne Time · {article_count} source items reviewed
      </div>

      <h1 style="font-size:28px; line-height:1.18; margin:0 0 14px; color:#111827;">
        Daily World Strategy Brief
      </h1>

      <div style="font-size:13px; color:#6b7280; line-height:1.6; margin-bottom:18px;">
        Structured by consulting-style industry groups: Macro & Policy · Financial Services · Consumer & Retail · TMT & AI · Healthcare · Energy & Industrials · Consulting Insights
      </div>

      <div style="background:#eef2ff; border:1px solid #dbe4ff; border-radius:16px; padding:16px; margin:18px 0 22px;">
        <div style="font-size:13px; color:#4f46e5; font-weight:800; margin-bottom:6px;">Executive Snapshot</div>
        <div style="font-size:16px; line-height:1.7; color:#111827; font-weight:650;">{esc(data.get("executive_snapshot"))}</div>
      </div>

      {section_title("Top 5 Strategic Signals", "跨宏观、金融、消费、科技、医疗、工业和咨询洞察的今日关键动向")}
      {top_signals_html}

      {render_list_section("Macro & Policy Watch", data.get("macro_policy_watch"), "中国、澳洲与全球宏观经济、通胀、就业、利率、政策、贸易、地产与消费支出")}

      {render_list_section("Financial Services & Markets", data.get("financial_services_markets"), "银行、资本市场、资产管理、保险、fintech、信贷、债券、股市与金融监管")}

      {render_list_section("Consumer & Retail", data.get("consumer_retail"), "消费品、零售、电商、品牌、渠道、价格战、奢侈品、美妆、食品饮料与会员制零售")}

      {render_list_section("TMT & AI", data.get("tmt_ai"), "AI、云、芯片、平台、数据中心、通信、数字化转型与企业技术")}

      {render_list_section("Healthcare & Life Sciences", data.get("healthcare_life_sciences"), "医药、医疗服务、生物科技、医保、健康消费与生命科学")}

      {render_list_section("Energy, Industrials & Infrastructure", data.get("energy_industrials_infrastructure"), "能源、制造业、矿业、供应链、物流、基础设施、新能源与工业企业")}

      {render_list_section("Consulting & Strategy Insights", data.get("consulting_strategy_insights"), "咨询公司观点、企业战略、组织转型、生产率、盈利能力与运营模式")}

      {render_list_section("Sichuan & Chengdu Regional Intelligence", data.get("sichuan_chengdu_intelligence"), "四川与成都综合区域情报：消费、品牌、零售、餐饮、文旅、产业、政策、投资、商圈与商业项目动态")}
      {render_pipeline_section(data.get("project_pipeline_changes", []))}

      {section_title("Why This Matters", "今天这些信号放在一起，说明了什么？")}
      <div style="background:#f9fafb; border:1px solid #e5e7eb; border-radius:16px; padding:16px;">
        <div style="font-size:14px; line-height:1.8; color:#374151;">{esc(data.get("why_this_matters"))}</div>
      </div>

      {section_title("Notion Analysis Seed", "只选一条信号，作为进入 Notion 深度分析的入口")}
      <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:16px; padding:16px;">
        <div style="font-size:13px; color:#15803d; font-weight:800; margin-bottom:8px;">Recommended analysis seed</div>
        <div style="font-size:15px; line-height:1.7; color:#111827;"><strong>Signal:</strong> {esc(notion_seed.get("signal"))}</div>
        <div style="font-size:15px; line-height:1.7; color:#111827; margin-top:8px;"><strong>Question:</strong> {esc(notion_seed.get("analysis_question"))}</div>
        <div style="font-size:15px; line-height:1.7; color:#111827; margin-top:8px;"><strong>Framework:</strong> {esc(notion_seed.get("suggested_framework"))}</div>
        <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:12px;"><strong>Suggested Notion tags:</strong></div>
        {render_bullets(ensure_list(notion_seed.get("notion_skill_tags")))}
      </div>

      {section_title("今日建议深读 1 篇")}
      <div style="background:#fff7ed; border:1px solid #fed7aa; border-radius:16px; padding:16px;">
        <div style="font-size:16px; font-weight:800; color:#111827;">{link_html(deep_read.get("title", "今日无明确推荐"), deep_read.get("link", ""))}</div>
        <div style="font-size:13px; color:#6b7280; margin-top:6px;">Source: {esc(deep_read.get("source"))}</div>
        <div style="font-size:14px; line-height:1.7; color:#374151; margin-top:8px;">{esc(deep_read.get("reason"))}</div>
      </div>

      {system_note_html}

      <div style="border-top:1px solid #e5e7eb; margin-top:28px; padding-top:16px; font-size:12px; color:#9ca3af; line-height:1.6;">
        Generated automatically for Irene. Email = world radar; Notion = analysis workspace.
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
    report_date = get_report_date_string()
    generated_time = get_generated_time_string()

    print("=================================")
    print("Irene Daily World Strategy Brief")
    print("=================================")
    print(f"Report date: {report_date}")
    print(f"Generated: {generated_time} Melbourne Time")

    articles = fetch_articles(max_per_source=MAX_PER_SOURCE)
    articles = balance_articles_by_category(articles, max_total=MAX_ARTICLES_FOR_AI)

    print(f"Sending {len(articles)} articles to OpenAI.")

    digest_data = generate_digest_data(articles)
    record_project_changes(digest_data, articles)
    digest_html = build_html_email(digest_data, article_count=len(articles))

    subject = f"Daily World Strategy Brief | {report_date}"

    send_email(subject, digest_html)

    print("Done.")


if __name__ == "__main__":
    main()