#!/usr/bin/env python3
"""Collect AI news and Codex learning candidates from public sources.

Designed for GitHub Actions. Uses Python standard library only.
Outputs:
- data/news-candidates/YYYY-MM-DD.json
- data/learning-candidates/YYYY-MM-DD.json
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOGGER = logging.getLogger("collect_rss")

TIMEZONE_NAME = "Asia/Shanghai"
RSS_TIMEOUT_SECONDS = 15
MAX_ENTRIES_PER_FEED = 40
MAX_NEWS_OUTPUT_ITEMS = 40
MAX_LEARNING_OUTPUT_ITEMS = 30
MAX_ITEMS_PER_SOURCE = 4
NEWS_FALLBACK_HOURS = 48
LEARNING_RECENT_DAYS = 30
LEARNING_FALLBACK_DAYS = 90

GOOGLE_NEWS_ZH_HL = "zh-CN"
GOOGLE_NEWS_ZH_GL = "CN"
GOOGLE_NEWS_ZH_CEID = "CN:zh-Hans"

GOOGLE_NEWS_EN_HL = "en-US"
GOOGLE_NEWS_EN_GL = "US"
GOOGLE_NEWS_EN_CEID = "US:en"


def make_google_news_rss_url(query: str, hl: str, gl: str, ceid: str) -> str:
    encoded = quote(query, safe="")
    return f"https://news.google.com/rss/search?q={encoded}&hl={hl}&gl={gl}&ceid={ceid}"


def make_source(
    name: str,
    url: str,
    language: str,
    region: str,
    source_group: str,
    source_type: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "url": url,
        "language": language,
        "region": region,
        "source_group": source_group,
        "source_type": source_type,
        "tags": tags or [],
    }


# ----------------------------
# News sources
# ----------------------------

GLOBAL_SOURCES: list[dict[str, Any]] = [
    make_source(
        "Google News - AI & LLM",
        make_google_news_rss_url(
            'AI OR "artificial intelligence" OR "large language model" OR LLM',
            GOOGLE_NEWS_EN_HL,
            GOOGLE_NEWS_EN_GL,
            GOOGLE_NEWS_EN_CEID,
        ),
        "en",
        "global",
        "global",
    ),
    make_source(
        "Google News - AI Coding Tools & Agents",
        make_google_news_rss_url(
            "AI coding assistant OR code generation OR AI agent",
            GOOGLE_NEWS_EN_HL,
            GOOGLE_NEWS_EN_GL,
            GOOGLE_NEWS_EN_CEID,
        ),
        "en",
        "global",
        "global",
    ),
    make_source(
        "Google News - Multimodal & Edge AI",
        make_google_news_rss_url(
            "multimodal AI OR edge AI OR on-device AI",
            GOOGLE_NEWS_EN_HL,
            GOOGLE_NEWS_EN_GL,
            GOOGLE_NEWS_EN_CEID,
        ),
        "en",
        "global",
        "global",
    ),
    make_source(
        "Google News - AI Chips & Compute",
        make_google_news_rss_url(
            "AI chip OR GPU OR NPU OR inference accelerator",
            GOOGLE_NEWS_EN_HL,
            GOOGLE_NEWS_EN_GL,
            GOOGLE_NEWS_EN_CEID,
        ),
        "en",
        "global",
        "global",
    ),
    make_source(
        "Google News - Robotics",
        make_google_news_rss_url(
            "robotics OR humanoid robot",
            GOOGLE_NEWS_EN_HL,
            GOOGLE_NEWS_EN_GL,
            GOOGLE_NEWS_EN_CEID,
        ),
        "en",
        "global",
        "global",
    ),
    make_source(
        "Google News - Autonomous Driving & Smart Cockpit",
        make_google_news_rss_url(
            "autonomous driving OR self-driving OR ADAS OR smart cockpit",
            GOOGLE_NEWS_EN_HL,
            GOOGLE_NEWS_EN_GL,
            GOOGLE_NEWS_EN_CEID,
        ),
        "en",
        "global",
        "global",
    ),
    make_source("Hacker News", "https://news.ycombinator.com/rss", "en", "global", "global"),
    make_source("The Verge", "https://www.theverge.com/rss/index.xml", "en", "global", "global"),
    make_source("TechCrunch", "https://techcrunch.com/feed/", "en", "global", "global"),
    make_source("VentureBeat AI", "https://venturebeat.com/ai/feed/", "en", "global", "global"),
    make_source("MIT Technology Review", "https://www.technologyreview.com/feed/", "en", "global", "global"),
    make_source("Ars Technica", "http://feeds.arstechnica.com/arstechnica/index", "en", "global", "global"),
    make_source("NVIDIA Blog", "https://blogs.nvidia.com/feed/", "en", "global", "global"),
    make_source("Microsoft Blog", "https://blogs.microsoft.com/feed/", "en", "global", "global"),
    make_source("Google AI Blog", "https://blog.google/technology/ai/rss/", "en", "global", "global"),
    make_source("OpenAI News", "https://openai.com/news/rss.xml", "en", "global", "global"),
]

CHINA_NEWS_GQUERIES = [
    "人工智能 大模型",
    "国产大模型",
    "AI 编程工具",
    "量子位 AI",
    "机器之心 AI",
    "新智元 AI",
    "InfoQ 中文 AI",
    "36氪 AI",
    "虎嗅 AI",
]

CHINA_SOURCES: list[dict[str, Any]] = [
    make_source("36氪 RSS", "https://36kr.com/feed", "zh", "china", "china"),
    make_source("虎嗅 RSS", "https://www.huxiu.com/rss/0.xml", "zh", "china", "china"),
    make_source("InfoQ 中文 RSS", "https://www.infoq.cn/rss/", "zh", "china", "china"),
]
CHINA_SOURCES.extend(
    [
        make_source(
            f"Google News 中文 - {query}",
            make_google_news_rss_url(query, GOOGLE_NEWS_ZH_HL, GOOGLE_NEWS_ZH_GL, GOOGLE_NEWS_ZH_CEID),
            "zh",
            "china",
            "china",
        )
        for query in CHINA_NEWS_GQUERIES
    ]
)

AUTO_CHINA_QUERIES = [
    "人工智能 大模型",
    "国产大模型",
    "AI 编程工具",
    "AI 芯片 算力",
    "国产 GPU NPU",
    "自动驾驶 智能驾驶",
    "端到端自动驾驶",
    "智能座舱 车载大模型",
    "车载 Agent",
    "具身智能 机器人",
    "软件定义汽车",
    "汽车软件 SOA OTA",
    "华为 ADS 智能驾驶",
    "比亚迪 智能驾驶",
    "理想 小鹏 蔚来 智能驾驶",
    "地平线 车载芯片",
    "黑芝麻智能 车载芯片",
    "芯驰科技 车载芯片",
]


def build_auto_china_news_sources() -> list[dict[str, Any]]:
    return [
        make_source(
            f"Google News 中国汽车智能化 - {query}",
            make_google_news_rss_url(query, GOOGLE_NEWS_ZH_HL, GOOGLE_NEWS_ZH_GL, GOOGLE_NEWS_ZH_CEID),
            "zh",
            "china",
            "auto_china",
        )
        for query in AUTO_CHINA_QUERIES
    ]


NEWS_SOURCES = GLOBAL_SOURCES + CHINA_SOURCES + build_auto_china_news_sources()

NEWS_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "large language model",
    "llm",
    "foundation model",
    "multimodal",
    "agent",
    "ai coding",
    "code generation",
    "copilot",
    "edge ai",
    "on-device",
    "端侧",
    "端侧ai",
    "chip",
    "gpu",
    "npu",
    "soc",
    "算力",
    "ai芯片",
    "车载芯片",
    "车载 soc",
    "autonomous driving",
    "self-driving",
    "adas",
    "smart cockpit",
    "robot",
    "robotics",
    "humanoid",
    "自动驾驶",
    "智能驾驶",
    "端到端自动驾驶",
    "智能座舱",
    "车载大模型",
    "车载agent",
    "具身智能",
    "机器人",
    "软件定义汽车",
    "汽车软件",
    "soa",
    "ota",
    "ai safety",
    "model safety",
    "regulation",
    "data security",
    "数据安全",
    "模型安全",
    "ai 监管",
    "华为",
    "比亚迪",
    "理想",
    "小鹏",
    "蔚来",
    "地平线",
    "黑芝麻智能",
    "芯驰科技",
    "寒武纪",
    "百度 apollo",
]

NEWS_AUTO_FOCUS_KEYWORDS = [
    "autonomous driving",
    "smart cockpit",
    "adas",
    "automotive",
    "车载",
    "自动驾驶",
    "智能驾驶",
    "智能座舱",
    "软件定义汽车",
    "汽车软件",
    "soa",
    "ota",
    "地平线",
    "黑芝麻智能",
    "芯驰科技",
    "比亚迪",
    "理想",
    "小鹏",
    "蔚来",
    "华为 ads",
]


# ----------------------------
# Learning sources
# ----------------------------

YOUTUBE_LEARNING_FEEDS: list[dict[str, Any]] = [
    make_source(
        "OpenAI YouTube",
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCXZCJLdBC09xxGZ6gcdrc6A",
        "en",
        "global",
        "youtube",
        source_type="youtube_video",
        tags=["codex", "agent", "cli"],
    ),
]

OFFICIAL_LEARNING_RSS_SOURCES: list[dict[str, Any]] = [
    make_source(
        "OpenAI Developers RSS",
        "https://developers.openai.com/rss.xml",
        "en",
        "global",
        "official",
        source_type="official_doc",
        tags=["codex", "agent", "cli", "mcp"],
    ),
    make_source(
        "OpenAI Codex Changelog RSS",
        "https://developers.openai.com/codex/changelog/rss.xml",
        "en",
        "global",
        "official",
        source_type="official_doc",
        tags=["codex", "cli", "workflow", "code_review"],
    ),
]

OFFICIAL_LEARNING_PAGES: list[dict[str, Any]] = [
    make_source(
        "OpenAI Developers Home",
        "https://developers.openai.com/",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "agent", "workflow"],
    ),
    make_source(
        "OpenAI Developers Resources",
        "https://developers.openai.com/resources",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "video", "tutorial"],
    ),
    make_source(
        "OpenAI Codex CLI Docs",
        "https://developers.openai.com/codex/cli",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "cli"],
    ),
    make_source(
        "OpenAI Codex Workflows Docs",
        "https://developers.openai.com/codex/workflows",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "workflow", "agent"],
    ),
    make_source(
        "OpenAI Docs MCP Guide",
        "https://platform.openai.com/docs/docs-mcp",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "mcp", "agent"],
    ),
    make_source(
        "OpenAI Codex Overview",
        "https://platform.openai.com/docs/codex/overview",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "agent"],
    ),
]

LEARNING_GNEWS_QUERIES_EN = [
    "OpenAI Codex tutorial",
    "Codex CLI tutorial",
    "Codex agent workflow",
    "OpenAI Codex AGENTS.md",
    "OpenAI Codex MCP",
    "Codex code review",
    "AI coding agent best practices",
]

LEARNING_GNEWS_QUERIES_ZH = [
    "Codex CLI 使用教程",
    "Codex Agent 教程",
    "Codex MCP 教程",
    "Codex AGENTS.md 使用",
]


def build_learning_gnews_sources() -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for query in LEARNING_GNEWS_QUERIES_EN:
        sources.append(
            make_source(
                f"Google News Learning EN - {query}",
                make_google_news_rss_url(query, GOOGLE_NEWS_EN_HL, GOOGLE_NEWS_EN_GL, GOOGLE_NEWS_EN_CEID),
                "en",
                "global",
                "learning_search_global",
                source_type="tutorial",
                tags=["codex", "agent", "tutorial"],
            )
        )
    for query in LEARNING_GNEWS_QUERIES_ZH:
        sources.append(
            make_source(
                f"Google News Learning ZH - {query}",
                make_google_news_rss_url(query, GOOGLE_NEWS_ZH_HL, GOOGLE_NEWS_ZH_GL, GOOGLE_NEWS_ZH_CEID),
                "zh",
                "china",
                "learning_search_china",
                source_type="tutorial",
                tags=["codex", "agent", "tutorial"],
            )
        )
    return sources


LEARNING_FEED_SOURCES = OFFICIAL_LEARNING_RSS_SOURCES + YOUTUBE_LEARNING_FEEDS + build_learning_gnews_sources()

LEARNING_KEYWORD_RULES: list[tuple[str, int, str]] = [
    ("openai codex", 8, "codex"),
    ("codex cli", 8, "cli"),
    ("codex", 6, "codex"),
    ("ai coding agent", 6, "agent"),
    ("coding agent", 5, "agent"),
    ("agent workflow", 5, "workflow"),
    ("workflow", 3, "workflow"),
    ("code review", 4, "code_review"),
    ("agents.md", 7, "agents"),
    ("mcp", 6, "mcp"),
    ("model context protocol", 6, "mcp"),
    ("best practices", 3, "best_practice"),
    ("prompting", 2, "prompt"),
    ("codex agent", 6, "agent"),
    ("openai developers", 2, "official"),
    ("codex 教程", 7, "tutorial"),
    ("codex 使用", 6, "tutorial"),
    ("codex 工作流", 6, "workflow"),
    ("codex cli 使用", 8, "cli"),
    ("codex agent 教程", 8, "agent"),
    ("codex mcp", 8, "mcp"),
    ("agents.md", 7, "agents"),
    ("代码审查", 3, "code_review"),
    ("工作流", 3, "workflow"),
]

LEARNING_EXCLUDE_KEYWORDS = [
    "手游",
    "游戏实况",
    "电影",
    "影视",
    "带货",
    "广告投放",
    "娱乐八卦",
    "music video",
    "gaming",
    "movie trailer",
    "celebrity",
    "coupon",
]

LEARNING_CORE_TERMS = ["codex", "agent", "cli", "mcp", "agents.md"]


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def get_timezone() -> timezone:
    try:
        return ZoneInfo(TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), name=TIMEZONE_NAME)


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&#160;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def canonical_title_key(title: str) -> str:
    value = clean_text(title)
    value = re.sub(r"\s*[-|｜]\s*[^-|｜]+$", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]", "", value).lower()
    return value


def local_name(tag: str) -> str:
    if not tag:
        return ""
    return tag.split("}", 1)[-1].lower()


def find_first_text(element: ET.Element, names: list[str]) -> str:
    names_set = {n.lower() for n in names}
    for child in element:
        if local_name(child.tag) in names_set:
            if child.text and child.text.strip():
                return clean_text(child.text)
    return ""


def extract_link(entry: ET.Element) -> str:
    link_text = find_first_text(entry, ["link"])
    if link_text.startswith("http"):
        return link_text

    for child in entry:
        if local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        rel = (child.attrib.get("rel") or "alternate").lower()
        if href and rel in ("alternate", ""):
            return href
        if href:
            return href
    return ""


def extract_entry_summary(entry: ET.Element) -> str:
    summary = find_first_text(entry, ["description", "summary", "content", "content:encoded"])
    if summary:
        return summary

    for child in entry:
        if local_name(child.tag) == "group":
            group_summary = find_first_text(child, ["description", "summary"])
            if group_summary:
                return group_summary

    # Fallback: direct text of children named description.
    for child in entry:
        if local_name(child.tag) == "description" and child.text:
            return clean_text(child.text)
    return ""


def parse_published_at(raw: str, tz: timezone) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)
    except Exception:
        pass

    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)
    except ValueError:
        return None


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "ai-news-feishu-bot-collector/1.0"})
    with urlopen(request, timeout=RSS_TIMEOUT_SECONDS) as response:
        return response.read()


def fetch_text(url: str) -> str:
    data = fetch_bytes(url)
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return data.decode("latin-1", errors="replace")


def parse_feed_entries(source_info: dict[str, Any], xml_bytes: bytes, tz: timezone) -> list[dict[str, Any]]:
    source_name = source_info["name"]
    source_language = source_info["language"]
    source_region = source_info["region"]
    source_group = source_info["source_group"]
    source_type = source_info.get("source_type", "")
    source_tags = list(source_info.get("tags", []))

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        LOGGER.warning("RSS parse failed: %s", source_name)
        return []

    feed_title = source_name
    channel_or_feed = root
    if local_name(root.tag) == "rss":
        for child in root:
            if local_name(child.tag) == "channel":
                channel_or_feed = child
                break

    maybe_title = find_first_text(channel_or_feed, ["title"])
    if maybe_title:
        feed_title = maybe_title

    entries: list[dict[str, Any]] = []
    count = 0
    for elem in channel_or_feed.iter():
        if local_name(elem.tag) not in ("item", "entry"):
            continue
        count += 1
        if count > MAX_ENTRIES_PER_FEED:
            break

        title = find_first_text(elem, ["title"])
        summary = extract_entry_summary(elem)
        link = extract_link(elem)
        raw_published = find_first_text(elem, ["pubdate", "published", "updated", "dc:date"])
        published_dt = parse_published_at(raw_published, tz) if raw_published else None

        title_clean = clean_text(title)
        summary_clean = clean_text(summary)
        link_clean = clean_text(link)
        if not title_clean or not link_clean:
            continue

        entries.append(
            {
                "title": title_clean,
                "summary": summary_clean[:420],
                "published_at": published_dt.isoformat() if published_dt else "",
                "source": clean_text(feed_title or source_name),
                "link": link_clean,
                "language": source_language,
                "region": source_region,
                "source_group": source_group,
                "source_type": source_type,
                "tags": source_tags.copy(),
                "_published_dt": published_dt,
            }
        )
    return entries


def extract_html_metadata(html_text: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    title = clean_text(title_match.group(1)) if title_match else ""

    meta_patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:description["\']',
    ]
    description = ""
    for pattern in meta_patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            description = clean_text(match.group(1))
            if description:
                break

    return title, description


def keyword_hits(text: str, keywords: list[str]) -> int:
    value = text.lower()
    return sum(1 for keyword in keywords if keyword in value)


def news_auto_focus_bonus(text: str) -> int:
    hits = keyword_hits(text, NEWS_AUTO_FOCUS_KEYWORDS)
    if hits >= 3:
        return 3
    if hits == 2:
        return 2
    if hits == 1:
        return 1
    return 0


def news_score(item: dict[str, Any]) -> tuple[int, int]:
    text = f"{item['title']} {item.get('summary', '')} {item.get('source', '')}".lower()
    hits = keyword_hits(text, NEWS_KEYWORDS)
    if hits <= 0:
        return 0, 0

    score = hits + news_auto_focus_bonus(text)
    if item.get("language") == "zh":
        score += 1
    if item.get("source_group") == "china":
        score += 2
    if item.get("source_group") == "auto_china":
        score += 3
    return score, hits


def learning_is_excluded(text: str) -> bool:
    value = text.lower()
    return any(keyword in value for keyword in LEARNING_EXCLUDE_KEYWORDS)


def infer_learning_tags(text: str, seed_tags: list[str]) -> list[str]:
    out: set[str] = {tag.strip().lower() for tag in seed_tags if tag}
    mapping = [
        ("codex cli", "cli"),
        ("codex", "codex"),
        ("agent", "agent"),
        ("mcp", "mcp"),
        ("agents.md", "agents"),
        ("code review", "code_review"),
        ("workflow", "workflow"),
        ("best practice", "best_practice"),
        ("教程", "tutorial"),
        ("工作流", "workflow"),
        ("代码审查", "code_review"),
    ]
    lower = text.lower()
    for key, tag in mapping:
        if key in lower:
            out.add(tag)
    return sorted(out)


def learning_score(item: dict[str, Any]) -> tuple[int, int, list[str]]:
    source_type = str(item.get("source_type", "")).strip().lower()
    seed_tags = [str(tag).strip().lower() for tag in item.get("tags", []) if str(tag).strip()]
    text = f"{item['title']} {item.get('summary', '')} {item.get('source', '')}".lower()

    hits = 0
    score = 0
    tags = set(seed_tags)
    for keyword, weight, tag in LEARNING_KEYWORD_RULES:
        if keyword in text:
            hits += 1
            score += weight
            tags.add(tag)

    if learning_is_excluded(text):
        score -= 8

    has_core = any(term in text for term in LEARNING_CORE_TERMS) or bool(tags.intersection({"codex", "agent", "cli", "mcp", "agents"}))
    if not has_core:
        return 0, 0, sorted(tags)

    # Reject broad ChatGPT-only guides that do not mention Codex/agent workflow context.
    if "chatgpt" in text and not any(term in text for term in ("codex", "agent", "cli", "mcp", "agents.md")):
        return 0, 0, sorted(tags)

    type_bonus = {
        "official_doc": 6,
        "youtube_video": 4,
        "tutorial": 3,
        "blog": 2,
    }.get(source_type, 0)
    score += type_bonus

    if item.get("language") == "zh":
        score += 1
    if item.get("region") == "china":
        score += 1

    return score, hits, sorted(tags)


def deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_link: dict[str, dict[str, Any]] = {}
    by_title_key: dict[str, str] = {}

    for item in items:
        link_key = clean_text(item.get("link", "")).lower()
        title_key = canonical_title_key(str(item.get("title", "")))
        if not link_key or not title_key:
            continue

        old_link = by_title_key.get(title_key, link_key)
        old_item = by_link.get(old_link)
        if not old_item:
            by_link[link_key] = item
            by_title_key[title_key] = link_key
            continue

        old_score = int(old_item.get("_score", 0))
        new_score = int(item.get("_score", 0))
        old_dt = old_item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        new_dt = item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)

        if new_score > old_score or (new_score == old_score and new_dt > old_dt):
            by_link.pop(old_link, None)
            by_link[link_key] = item
            by_title_key[title_key] = link_key

    return list(by_link.values())


def collect_news_candidates(
    tz: timezone,
    day_start: datetime,
    day_end: datetime,
    now_local: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sources = sorted(
        NEWS_SOURCES,
        key=lambda src: {"auto_china": 0, "china": 1, "global": 2}.get(str(src.get("source_group")), 9),
    )

    all_items: list[dict[str, Any]] = []
    global_source_count = sum(1 for s in sources if s["source_group"] == "global")
    china_source_count = sum(1 for s in sources if s["source_group"] in ("china", "auto_china"))
    global_success_count = 0
    china_success_count = 0
    failed_count = 0

    for source in sources:
        name = source["name"]
        group = source["source_group"]
        try:
            LOGGER.info("Fetching news RSS [%s]: %s", group, name)
            xml_bytes = fetch_bytes(source["url"])
            parsed = parse_feed_entries(source, xml_bytes, tz)
            for item in parsed:
                score, hits = news_score(item)
                if hits <= 0:
                    continue
                item["_score"] = score
                all_items.append(item)
            if group == "global":
                global_success_count += 1
            else:
                china_success_count += 1
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            failed_count += 1
            LOGGER.warning("News fetch failed [%s] %s: %s", group, name, exc)

    dedup = deduplicate_items(all_items)
    fallback_start = now_local - timedelta(hours=NEWS_FALLBACK_HOURS)

    yesterday_items: list[dict[str, Any]] = []
    recent_items: list[dict[str, Any]] = []
    no_time_items: list[dict[str, Any]] = []
    for item in dedup:
        published_dt = item.get("_published_dt")
        if not published_dt:
            no_time_items.append(item)
            continue
        if day_start <= published_dt <= day_end:
            yesterday_items.append(item)
        elif fallback_start <= published_dt <= now_local:
            recent_items.append(item)

    def sort_key(entry: dict[str, Any]) -> tuple[int, int, datetime]:
        dt = entry.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        group_weight = {"auto_china": 3, "china": 2, "global": 1}.get(entry.get("source_group", "global"), 0)
        return int(entry.get("_score", 0)), group_weight, dt

    yesterday_items.sort(key=sort_key, reverse=True)
    recent_items.sort(key=sort_key, reverse=True)
    no_time_items.sort(key=sort_key, reverse=True)

    merged = yesterday_items + recent_items + no_time_items
    output: list[dict[str, Any]] = []
    per_source_count: dict[str, int] = {}
    for item in merged:
        source_key = clean_text(item.get("source", "")) or "unknown"
        count = per_source_count.get(source_key, 0)
        if count >= MAX_ITEMS_PER_SOURCE:
            continue
        output.append(item)
        per_source_count[source_key] = count + 1
        if len(output) >= MAX_NEWS_OUTPUT_ITEMS:
            break

    for item in output:
        item.pop("_score", None)
        item.pop("_published_dt", None)

    status = {
        "source_count": len(sources),
        "success_count": global_success_count + china_success_count,
        "failed_count": failed_count,
        "global_source_count": global_source_count,
        "global_success_count": global_success_count,
        "china_source_count": china_source_count,
        "china_success_count": china_success_count,
    }
    return output, status


def collect_learning_page_items(
    tz: timezone,
    now_local: datetime,
) -> tuple[list[dict[str, Any]], int, int]:
    items: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0

    for source in OFFICIAL_LEARNING_PAGES:
        name = source["name"]
        try:
            LOGGER.info("Fetching learning page [official_page]: %s", name)
            html_text = fetch_text(source["url"])
            title, summary = extract_html_metadata(html_text)
            item_title = title or name
            item_summary = summary or "基于公开页面标题和简介抓取。"
            item = {
                "title": clean_text(item_title),
                "summary": clean_text(item_summary)[:420],
                "published_at": now_local.isoformat(),
                "source": clean_text(name),
                "source_type": source["source_type"] or "official_doc",
                "language": source["language"],
                "region": source["region"],
                "link": source["url"],
                "tags": list(source.get("tags", [])),
                "_published_dt": now_local,
            }
            score, hits, tags = learning_score(item)
            if hits > 0 and score > 0:
                item["_score"] = score
                item["tags"] = tags
                items.append(item)
            success_count += 1
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            failed_count += 1
            LOGGER.warning("Learning page fetch failed %s: %s", name, exc)
    return items, success_count, failed_count


def collect_learning_candidates(
    tz: timezone,
    now_local: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sources = LEARNING_FEED_SOURCES
    all_items: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0

    for source in sources:
        name = source["name"]
        group = source.get("source_group", "learning")
        try:
            LOGGER.info("Fetching learning RSS [%s]: %s", group, name)
            xml_bytes = fetch_bytes(source["url"])
            parsed = parse_feed_entries(source, xml_bytes, tz)
            for item in parsed:
                score, hits, tags = learning_score(item)
                if hits <= 0 or score <= 0:
                    continue
                item["_score"] = score
                item["tags"] = tags
                all_items.append(item)
            success_count += 1
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            failed_count += 1
            LOGGER.warning("Learning fetch failed [%s] %s: %s", group, name, exc)

    page_items, page_success, page_failed = collect_learning_page_items(tz=tz, now_local=now_local)
    all_items.extend(page_items)
    success_count += page_success
    failed_count += page_failed

    dedup = deduplicate_items(all_items)

    recent_start = now_local - timedelta(days=LEARNING_RECENT_DAYS)
    fallback_start = now_local - timedelta(days=LEARNING_FALLBACK_DAYS)

    recent_items: list[dict[str, Any]] = []
    fallback_items: list[dict[str, Any]] = []
    undated_items: list[dict[str, Any]] = []
    for item in dedup:
        published_dt = item.get("_published_dt")
        if not published_dt:
            undated_items.append(item)
            continue
        if published_dt >= recent_start:
            recent_items.append(item)
        elif published_dt >= fallback_start:
            fallback_items.append(item)

    def sort_key(entry: dict[str, Any]) -> tuple[int, int, int, datetime]:
        dt = entry.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        source_type = str(entry.get("source_type", "")).lower()
        source_type_weight = {
            "official_doc": 4,
            "youtube_video": 3,
            "tutorial": 2,
            "blog": 1,
        }.get(source_type, 0)
        language_weight = 1 if entry.get("language") == "zh" else 0
        return int(entry.get("_score", 0)), source_type_weight, language_weight, dt

    recent_items.sort(key=sort_key, reverse=True)
    fallback_items.sort(key=sort_key, reverse=True)
    undated_items.sort(key=sort_key, reverse=True)

    merged = recent_items if recent_items else (fallback_items + undated_items)

    output: list[dict[str, Any]] = []
    per_source_count: dict[str, int] = {}
    for item in merged:
        source_key = clean_text(item.get("source", "")) or "unknown"
        count = per_source_count.get(source_key, 0)
        if count >= MAX_ITEMS_PER_SOURCE:
            continue
        output.append(item)
        per_source_count[source_key] = count + 1
        if len(output) >= MAX_LEARNING_OUTPUT_ITEMS:
            break

    for item in output:
        item.pop("_score", None)
        item.pop("_published_dt", None)

    status = {
        "source_count": len(sources) + len(OFFICIAL_LEARNING_PAGES),
        "success_count": success_count,
        "failed_count": failed_count,
    }
    return output, status


def save_candidates(
    base_dir_name: str,
    target_date: str,
    timezone_name: str,
    items: list[dict[str, Any]],
    fetch_status: dict[str, int],
) -> Path:
    out_dir = Path("data") / base_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": timezone_name,
        "fetch_status": fetch_status,
        "items": items,
    }

    out_file = out_dir / f"{target_date}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file


def main() -> int:
    setup_logging()
    tz = get_timezone()
    now_local = datetime.now(tz)

    target_date = (now_local - timedelta(days=1)).date()
    day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
    day_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz)

    news_items, news_status = collect_news_candidates(
        tz=tz,
        day_start=day_start,
        day_end=day_end,
        now_local=now_local,
    )
    news_file = save_candidates(
        base_dir_name="news-candidates",
        target_date=target_date.isoformat(),
        timezone_name=TIMEZONE_NAME,
        items=news_items,
        fetch_status=news_status,
    )

    learning_items, learning_status = collect_learning_candidates(
        tz=tz,
        now_local=now_local,
    )
    learning_file = save_candidates(
        base_dir_name="learning-candidates",
        target_date=target_date.isoformat(),
        timezone_name=TIMEZONE_NAME,
        items=learning_items,
        fetch_status=learning_status,
    )

    LOGGER.info(
        "Saved news candidates=%s to %s | learning candidates=%s to %s",
        len(news_items),
        news_file.as_posix(),
        len(learning_items),
        learning_file.as_posix(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
