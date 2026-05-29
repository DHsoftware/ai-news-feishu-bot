#!/usr/bin/env python3
"""Collect public RSS news candidates and save as JSON.

Designed for GitHub Actions. Uses Python standard library only.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
MAX_OUTPUT_ITEMS = 40
MAX_ITEMS_PER_SOURCE = 4
FALLBACK_HOURS = 48

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
) -> dict[str, str]:
    return {
        "name": name,
        "url": url,
        "language": language,
        "region": region,
        "source_group": source_group,
    }


GLOBAL_SOURCES: list[dict[str, str]] = [
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
    make_source(
        "MIT Technology Review",
        "https://www.technologyreview.com/feed/",
        "en",
        "global",
        "global",
    ),
    make_source(
        "Ars Technica",
        "http://feeds.arstechnica.com/arstechnica/index",
        "en",
        "global",
        "global",
    ),
    make_source("NVIDIA Blog", "https://blogs.nvidia.com/feed/", "en", "global", "global"),
    make_source("Microsoft Blog", "https://blogs.microsoft.com/feed/", "en", "global", "global"),
    make_source("Google AI Blog", "https://blog.google/technology/ai/rss/", "en", "global", "global"),
    make_source("OpenAI News", "https://openai.com/news/rss.xml", "en", "global", "global"),
]

CHINA_GNEWS_QUERIES = [
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

CHINA_SOURCES: list[dict[str, str]] = [
    make_source("36氪 RSS", "https://36kr.com/feed", "zh", "china", "china"),
    make_source("虎嗅 RSS", "https://www.huxiu.com/rss/0.xml", "zh", "china", "china"),
    make_source("InfoQ 中文 RSS", "https://www.infoq.cn/rss/", "zh", "china", "china"),
]
CHINA_SOURCES.extend(
    [
        make_source(
            f"Google News 中文 - {query}",
            make_google_news_rss_url(
                query,
                GOOGLE_NEWS_ZH_HL,
                GOOGLE_NEWS_ZH_GL,
                GOOGLE_NEWS_ZH_CEID,
            ),
            "zh",
            "china",
            "china",
        )
        for query in CHINA_GNEWS_QUERIES
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


def build_auto_china_sources() -> list[dict[str, str]]:
    return [
        make_source(
            f"Google News 中国汽车智能化 - {query}",
            make_google_news_rss_url(
                query,
                GOOGLE_NEWS_ZH_HL,
                GOOGLE_NEWS_ZH_GL,
                GOOGLE_NEWS_ZH_CEID,
            ),
            "zh",
            "china",
            "auto_china",
        )
        for query in AUTO_CHINA_QUERIES
    ]


ALL_SOURCES: list[dict[str, str]] = GLOBAL_SOURCES + CHINA_SOURCES + build_auto_china_sources()

KEYWORDS = [
    # General AI.
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
    # Chip / infra.
    "chip",
    "gpu",
    "npu",
    "soc",
    "算力",
    "ai芯片",
    "车载芯片",
    "车载 soc",
    # Auto + robotics.
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
    # Safety / regulation.
    "ai safety",
    "model safety",
    "regulation",
    "data security",
    "数据安全",
    "模型安全",
    "ai 监管",
    # CN ecosystem.
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

AUTO_PRIOR_KEYWORDS = [
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
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&#160;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def canonical_title_key(title: str) -> str:
    value = clean_text(title)
    # Remove trailing publisher segment like " - xx媒体".
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


def keyword_hits(title: str, summary: str, source: str) -> int:
    text = f"{title} {summary} {source}".lower()
    return sum(1 for keyword in KEYWORDS if keyword in text)


def auto_focus_bonus(title: str, summary: str, source: str) -> int:
    text = f"{title} {summary} {source}".lower()
    hits = sum(1 for keyword in AUTO_PRIOR_KEYWORDS if keyword in text)
    if hits >= 3:
        return 3
    if hits == 2:
        return 2
    if hits == 1:
        return 1
    return 0


def item_score(
    title: str,
    summary: str,
    source: str,
    language: str,
    source_group: str,
) -> tuple[int, int]:
    hits = keyword_hits(title, summary, source)
    if hits <= 0:
        return 0, 0

    score = hits
    score += auto_focus_bonus(title, summary, source)

    if language == "zh":
        score += 1
    if source_group == "china":
        score += 2
    elif source_group == "auto_china":
        score += 3

    return score, hits


def fetch_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "ai-news-feishu-bot-collector/1.0"},
    )
    with urlopen(request, timeout=RSS_TIMEOUT_SECONDS) as response:
        return response.read()


def parse_feed(source_info: dict[str, str], xml_bytes: bytes, tz: timezone) -> list[dict[str, Any]]:
    source_name = source_info["name"]
    source_language = source_info["language"]
    source_region = source_info["region"]
    source_group = source_info["source_group"]

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

    possible_feed_title = find_first_text(channel_or_feed, ["title"])
    if possible_feed_title:
        feed_title = possible_feed_title

    entries: list[dict[str, Any]] = []
    count = 0
    for elem in channel_or_feed.iter():
        if local_name(elem.tag) not in ("item", "entry"):
            continue
        count += 1
        if count > MAX_ENTRIES_PER_FEED:
            break

        title = find_first_text(elem, ["title"])
        summary = find_first_text(elem, ["description", "summary", "content"])
        link = extract_link(elem)
        raw_published = find_first_text(elem, ["pubdate", "published", "updated", "dc:date"])
        published_at = parse_published_at(raw_published, tz) if raw_published else None
        source = clean_text(feed_title or source_name)

        title_clean = clean_text(title)
        summary_clean = clean_text(summary)
        link_clean = clean_text(link)
        if not title_clean or not link_clean:
            continue

        score, hits = item_score(
            title=title_clean,
            summary=summary_clean,
            source=source,
            language=source_language,
            source_group=source_group,
        )
        if hits <= 0:
            continue

        entries.append(
            {
                "title": title_clean,
                "summary": summary_clean[:320],
                "published_at": published_at.isoformat() if published_at else "",
                "source": source,
                "link": link_clean,
                "language": source_language,
                "region": source_region,
                "source_group": source_group,
                "_score": score,
                "_published_dt": published_at,
            }
        )
    return entries


def source_sort_key(source: dict[str, str]) -> tuple[int, str]:
    group = source["source_group"]
    priority = {"auto_china": 0, "china": 1, "global": 2}.get(group, 3)
    return priority, source["name"]


def collect_items(
    tz: timezone,
    day_start: datetime,
    day_end: datetime,
    now_local: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sources = sorted(ALL_SOURCES, key=source_sort_key)
    all_items: list[dict[str, Any]] = []

    global_source_count = sum(1 for s in sources if s["source_group"] == "global")
    china_source_count = sum(1 for s in sources if s["source_group"] in ("china", "auto_china"))
    auto_source_count = sum(1 for s in sources if s["source_group"] == "auto_china")

    global_success_count = 0
    china_success_count = 0
    auto_success_count = 0
    failed_count = 0

    for source in sources:
        name = source["name"]
        url = source["url"]
        group = source["source_group"]
        try:
            LOGGER.info("Fetching RSS [%s]: %s", group, name)
            xml_bytes = fetch_bytes(url)
            parsed_items = parse_feed(source, xml_bytes, tz)
            all_items.extend(parsed_items)

            if group == "global":
                global_success_count += 1
            elif group == "china":
                china_success_count += 1
            elif group == "auto_china":
                china_success_count += 1
                auto_success_count += 1
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            failed_count += 1
            LOGGER.warning("Fetch failed for [%s] %s: %s", group, name, exc)

    dedup: dict[str, dict[str, Any]] = {}
    dedup_title: dict[str, str] = {}

    for item in all_items:
        link_key = item["link"].strip().lower()
        title_key = canonical_title_key(item["title"])
        if not link_key or not title_key:
            continue

        existing_link = dedup_title.get(title_key)
        if existing_link:
            old = dedup.get(existing_link)
        else:
            old = dedup.get(link_key)

        if not old:
            dedup[link_key] = item
            dedup_title[title_key] = link_key
            continue

        old_dt = old.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        new_dt = item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        old_score = int(old.get("_score", 0))
        new_score = int(item.get("_score", 0))

        if new_score > old_score or (new_score == old_score and new_dt > old_dt):
            old_key = dedup_title.get(title_key, link_key)
            dedup.pop(old_key, None)
            dedup[link_key] = item
            dedup_title[title_key] = link_key

    relevant = list(dedup.values())
    fallback_start = now_local - timedelta(hours=FALLBACK_HOURS)

    yesterday_items: list[dict[str, Any]] = []
    recent_items: list[dict[str, Any]] = []
    no_time_items: list[dict[str, Any]] = []

    for item in relevant:
        published_dt = item.get("_published_dt")
        if not published_dt:
            no_time_items.append(item)
            continue
        if day_start <= published_dt <= day_end:
            yesterday_items.append(item)
        elif fallback_start <= published_dt <= now_local:
            recent_items.append(item)

    def sort_key(item: dict[str, Any]) -> tuple[int, int, datetime]:
        dt = item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        source_group = item.get("source_group", "global")
        group_weight = {"auto_china": 3, "china": 2, "global": 1}.get(source_group, 0)
        return int(item.get("_score", 0)), group_weight, dt

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
        if len(output) >= MAX_OUTPUT_ITEMS:
            break

    for item in output:
        item.pop("_score", None)
        item.pop("_published_dt", None)

    fetch_status = {
        "source_count": len(sources),
        "success_count": global_success_count + china_success_count,
        "failed_count": failed_count,
        "global_source_count": global_source_count,
        "global_success_count": global_success_count,
        "china_source_count": china_source_count,
        "china_success_count": china_success_count,
        "auto_china_source_count": auto_source_count,
        "auto_china_success_count": auto_success_count,
    }
    return output, fetch_status


def save_candidates(
    target_date: str,
    timezone_name: str,
    items: list[dict[str, Any]],
    fetch_status: dict[str, int],
) -> Path:
    out_dir = Path("data") / "news-candidates"
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

    items, fetch_status = collect_items(
        tz=tz,
        day_start=day_start,
        day_end=day_end,
        now_local=now_local,
    )

    out_file = save_candidates(target_date.isoformat(), TIMEZONE_NAME, items, fetch_status)
    LOGGER.info(
        "Saved %s candidate items to %s (failed=%s, china_success=%s/%s, global_success=%s/%s)",
        len(items),
        out_file.as_posix(),
        fetch_status["failed_count"],
        fetch_status["china_success_count"],
        fetch_status["china_source_count"],
        fetch_status["global_success_count"],
        fetch_status["global_source_count"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
