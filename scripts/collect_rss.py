#!/usr/bin/env python3
"""Collect public RSS news candidates and save as JSON.

This script is designed for GitHub Actions and uses Python standard library only.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOGGER = logging.getLogger("collect_rss")

TIMEZONE_NAME = "Asia/Shanghai"
RSS_TIMEOUT_SECONDS = 15
MAX_ENTRIES_PER_FEED = 40
MAX_OUTPUT_ITEMS = 40

RSS_SOURCES = [
    {
        "name": "Google News - AI & LLM",
        "url": "https://news.google.com/rss/search?q=AI+OR+%22artificial+intelligence%22+OR+%22large+language+model%22+OR+LLM&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - AI Coding Tools & Agents",
        "url": "https://news.google.com/rss/search?q=AI+coding+assistant+OR+code+generation+OR+AI+agent&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - Multimodal & Edge AI",
        "url": "https://news.google.com/rss/search?q=multimodal+AI+OR+edge+AI+OR+on-device+AI&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - AI Chips & Compute",
        "url": "https://news.google.com/rss/search?q=AI+chip+OR+GPU+OR+NPU+OR+inference+accelerator&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - Robotics",
        "url": "https://news.google.com/rss/search?q=robotics+OR+humanoid+robot&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - Autonomous Driving & Smart Cockpit",
        "url": "https://news.google.com/rss/search?q=autonomous+driving+OR+self-driving+OR+ADAS+OR+smart+cockpit&hl=en-US&gl=US&ceid=US:en",
    },
    {"name": "Hacker News", "url": "https://news.ycombinator.com/rss"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/ai/feed/"},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "Ars Technica", "url": "http://feeds.arstechnica.com/arstechnica/index"},
    {"name": "NVIDIA Blog", "url": "https://blogs.nvidia.com/feed/"},
    {"name": "Microsoft Blog", "url": "https://blogs.microsoft.com/feed/"},
    {"name": "Google AI Blog", "url": "https://blog.google/technology/ai/rss/"},
    {"name": "OpenAI News", "url": "https://openai.com/news/rss.xml"},
]

KEYWORDS = [
    "ai",
    "artificial intelligence",
    "large language model",
    "llm",
    "multimodal",
    "agent",
    "ai coding",
    "code generation",
    "copilot",
    "edge ai",
    "on-device",
    "inference",
    "chip",
    "gpu",
    "npu",
    "soc",
    "robot",
    "robotics",
    "autonomous driving",
    "self-driving",
    "adas",
    "smart cockpit",
    "智能座舱",
    "自动驾驶",
    "ai safety",
    "model safety",
    "regulation",
    "data security",
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
        # Standard-library fallback for Windows environments without tzdata.
        return timezone(timedelta(hours=8), name=TIMEZONE_NAME)


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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
    # RSS <link>text</link>
    link_text = find_first_text(entry, ["link"])
    if link_text.startswith("http"):
        return link_text

    # Atom <link href="...">
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


def keyword_score(title: str, summary: str, source: str) -> int:
    text = f"{title} {summary} {source}".lower()
    return sum(1 for keyword in KEYWORDS if keyword in text)


def fetch_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "ai-news-feishu-bot-collector/1.0"},
    )
    with urlopen(request, timeout=RSS_TIMEOUT_SECONDS) as response:
        return response.read()


def parse_feed(source_name: str, xml_bytes: bytes, tz: timezone) -> list[dict[str, Any]]:
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
        source = feed_title or source_name

        if not title or not link:
            continue

        item = {
            "title": clean_text(title),
            "summary": clean_text(summary)[:320],
            "published_at": published_at.isoformat() if published_at else "",
            "source": clean_text(source),
            "link": clean_text(link),
            "_score": keyword_score(title, summary, source),
            "_published_dt": published_at,
        }
        if item["_score"] > 0:
            entries.append(item)
    return entries


def collect_items(tz: timezone, day_start: datetime, day_end: datetime, now_local: datetime) -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    for source in RSS_SOURCES:
        name = source["name"]
        url = source["url"]
        try:
            LOGGER.info("Fetching RSS: %s", name)
            xml_bytes = fetch_bytes(url)
            all_items.extend(parse_feed(name, xml_bytes, tz))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            LOGGER.warning("Fetch failed for %s: %s", name, exc)

    # Deduplicate by link.
    dedup: dict[str, dict[str, Any]] = {}
    for item in all_items:
        key = item["link"].strip().lower()
        if not key:
            continue
        old = dedup.get(key)
        if not old:
            dedup[key] = item
            continue
        old_dt = old.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        new_dt = item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        if item["_score"] > old["_score"] or new_dt > old_dt:
            dedup[key] = item

    relevant = list(dedup.values())
    fallback_start = now_local - timedelta(hours=48)

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

    def sort_key(item: dict[str, Any]) -> tuple[int, datetime]:
        dt = item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        return int(item.get("_score", 0)), dt

    yesterday_items.sort(key=sort_key, reverse=True)
    recent_items.sort(key=sort_key, reverse=True)
    no_time_items.sort(key=sort_key, reverse=True)

    output = yesterday_items + recent_items + no_time_items
    output = output[:MAX_OUTPUT_ITEMS]

    # Drop internal fields.
    for item in output:
        item.pop("_score", None)
        item.pop("_published_dt", None)
    return output


def save_candidates(target_date: str, timezone_name: str, items: list[dict[str, Any]]) -> Path:
    out_dir = Path("data") / "news-candidates"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": timezone_name,
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

    items = collect_items(
        tz=tz,
        day_start=day_start,
        day_end=day_end,
        now_local=now_local,
    )

    out_file = save_candidates(target_date.isoformat(), TIMEZONE_NAME, items)
    LOGGER.info("Saved %s candidate items to %s", len(items), out_file.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
