import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import feedparser
from dotenv import load_dotenv
from openai import OpenAI


# ============================================================
# Irene Daily Strategy & Industry Brief
# Version 1: RSS -> OpenAI Summary -> 163 Email
# ============================================================


# 1. Load private information from .env
# 读取 .env 文件里的密钥和邮箱配置
load_dotenv()


# 2. RSS sources
# 第一版信息源：先少而精，后面我们可以继续加
SOURCES = {
    "McKinsey Insights": "https://www.mckinsey.com/insights/rss.aspx",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "Microsoft Blog": "https://blogs.microsoft.com/feed/",
}


# 3. Keywords
# 只保留与你的目标相关的信息
KEYWORDS = [
    "ai",
    "artificial intelligence",
    "generative ai",
    "genai",
    "agent",
    "openai",
    "anthropic",
    "google",
    "microsoft",
    "nvidia",
    "cloud",
    "data center",
    "semiconductor",
    "telecom",
    "5g",
    "digital transformation",
    "policy",
    "regulation",
    "macroeconomy",
    "inflation",
    "interest rate",
    "consulting",
    "strategy",
    "mckinsey",
    "bcg",
    "bain",
    "deloitte",
    "pwc",
    "ey",
    "kpmg",
]


def get_env(name: str, required: bool = True, default: str | None = None) -> str | None:
    """
    Read an environment variable.

    读取环境变量。
    如果 required=True 但是没有找到，就直接报错，提醒我们去检查 .env。
    """
    value = os.getenv(name, default)

    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")

    return value


def fetch_articles(max_per_source: int = 10) -> list[dict]:
    """
    Fetch recent articles from RSS feeds.

    从 RSS 信息源抓取文章。
    每个 source 最多抓 max_per_source 条。
    """
    articles = []

    print("Fetching articles from RSS sources...")

    for source_name, feed_url in SOURCES.items():
        print(f"- Reading: {source_name}")

        try:
            feed = feedparser.parse(feed_url)

            if feed.bozo:
                print(f"  Warning: Could not fully parse {source_name}. Skipping if empty.")

            entries = feed.entries[:max_per_source]

            for entry in entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = entry.get("summary", "").strip()
                published = entry.get("published", "").strip()

                if not title or not link:
                    continue

                combined_text = f"{title} {summary}".lower()

                # Keep article if it contains at least one keyword
                if any(keyword in combined_text for keyword in KEYWORDS):
                    articles.append(
                        {
                            "source": source_name,
                            "title": title,
                            "link": link,
                            "summary": summary,
                            "published": published,
                        }
                    )

        except Exception as error:
            print(f"  Error reading {source_name}: {error}")

    return articles


def deduplicate_articles(articles: list[dict]) -> list[dict]:
    """
    Remove duplicate articles by link or title.

    根据链接和标题去重。
    """
    seen = set()
    unique_articles = []

    for article in articles:
        key = article["link"] or article["title"]

        if key in seen:
            continue

        seen.add(key)
        unique_articles.append(article)

    return unique_articles


def format_articles_for_prompt(articles: list[dict]) -> str:
    """
    Convert article list into text for the AI prompt.

    把文章列表转换成可以交给 AI 总结的文本。
    """
    article_blocks = []

    for index, article in enumerate(articles, start=1):
        block = f"""
Article {index}
Source: {article["source"]}
Title: {article["title"]}
Published: {article["published"]}
Summary: {article["summary"]}
Link: {article["link"]}
"""
        article_blocks.append(block.strip())

    return "\n\n".join(article_blocks)


def generate_digest(articles: list[dict]) -> str:
    """
    Use OpenAI to generate a Chinese daily briefing.

    调用 OpenAI，把文章总结成中文日报。
    """
    if not articles:
        return """
今日没有抓取到足够相关的信息。

可能原因：
1. RSS 信息源当天没有更新；
2. 关键词筛选过严；
3. 某些信息源暂时无法读取。

建议：
明天继续观察，或在程序里增加更多信息源和关键词。
"""

    client = OpenAI()

    articles_text = format_articles_for_prompt(articles)

    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
你是 Irene 的个人战略与行业情报分析师。

Irene 是墨尔本大学 Bachelor of Arts 经济学学生，目标是战略咨询。
她有中国移动战略部门实习经历，做过 AI 行业概览、TMT/通信行业分析、政策日报自动化、咨询式行业研究。
她希望每天通过一封中文日报恢复对外部变化的敏锐度，并把信息转化为可用于：
- consulting case interview
- coffee chat
- resume / LinkedIn
- Notion 知识库
- 行业判断力训练
的素材。

请基于下面的文章，生成一份中文日报。

要求：
1. 中文为主，英文专有名词保留。
2. 结论先行，不要写成普通新闻摘要。
3. 每条都要解释“为什么重要”。
4. 不要夸张预测，不要编造文章中没有的信息。
5. 如果信息不足，请明确说明。
6. 输出要适合 5 分钟内读完。
7. 语气：清晰、专业、咨询顾问风格，但不要过度 AI 腔。

日报日期：{today}

请按以下结构输出：

# Daily Strategy & Industry Brief | {today}

## 1. 今日最重要的 3 件事
每件事包含：
- 发生了什么
- 为什么重要
- 对 Irene 的启发

## 2. AI / Tech Watch
总结 2-3 条最值得关注的 AI / Tech 动态。

## 3. TMT / Telecom Watch
总结与通信、云、算力、数字化转型相关的信息。
如果当天没有相关信息，请写“今日无明显高相关动态”。

## 4. Policy & Macro Signal
总结政策、监管、宏观经济或商业环境信号。
如果当天没有相关信息，请写“今日无明显高相关动态”。

## 5. Consulting Lens
把今天最重要的一条信息转化成咨询视角：
- 如果这是一个 case interview，可能会怎么问？
- 可以用什么框架拆解？

## 6. Career Relevance for Irene
说明今天的信息如何用于：
- coffee chat
- case interview
- resume / LinkedIn
- Notion 积累

## 7. 今日建议深读 1 篇
选择最值得 Irene 深读的一篇。
包含：
- 标题
- 来源
- 推荐理由
- 链接

下面是今天抓取到的文章：

{articles_text}
"""

    print("Generating daily brief with OpenAI...")

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=prompt,
        )

        return response.output_text

    except Exception as error:
        print(f"OpenAI generation failed: {error}")
        return f"""
日报生成失败。

错误信息：
{error}

可能原因：
1. OpenAI API Key 不正确；
2. API 账户余额或权限有问题；
3. 网络连接不稳定；
4. 模型名称暂时不可用。

请检查 .env 文件里的 OPENAI_API_KEY。
"""


def send_email(subject: str, body: str) -> None:
    """
    Send the daily briefing by email using SMTP.

    用 163 邮箱 SMTP 发邮件。
    """
    smtp_host = get_env("SMTP_HOST")
    smtp_port = int(get_env("SMTP_PORT"))
    smtp_username = get_env("SMTP_USERNAME")
    smtp_password = get_env("SMTP_PASSWORD")
    email_from = get_env("EMAIL_FROM")
    email_to_raw = get_env("EMAIL_TO")

    # Support multiple recipients separated by comma
    # 支持多个收件人，用英文逗号分隔
    recipients = [email.strip() for email in email_to_raw.split(",") if email.strip()]

    if not recipients:
        raise ValueError("EMAIL_TO is empty. Please check your .env file.")

    print(f"Sending email to: {', '.join(recipients)}")

    message = MIMEMultipart()
    message["From"] = email_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject

    message.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_username, smtp_password)
            server.sendmail(email_from, recipients, message.as_string())

        print("Email sent successfully.")

    except Exception as error:
        print(f"Email sending failed: {error}")
        raise


def main() -> None:
    """
    Main workflow.

    主流程：
    1. 抓文章
    2. 去重
    3. 生成日报
    4. 发邮件
    """
    today = datetime.now().strftime("%Y-%m-%d")

    print("======================================")
    print("Irene Daily Strategy & Industry Brief")
    print("======================================")
    print(f"Date: {today}")

    articles = fetch_articles(max_per_source=10)
    articles = deduplicate_articles(articles)

    print(f"Found {len(articles)} relevant articles.")

    # To keep the first version simple and cheap, only send up to 20 articles to OpenAI.
    # 为了控制成本，第一版最多给 OpenAI 20 篇文章。
    articles = articles[:20]

    digest = generate_digest(articles)

    subject = f"Daily Strategy & Industry Brief | {today}"

    send_email(subject, digest)

    print("Done.")


if __name__ == "__main__":
    main()