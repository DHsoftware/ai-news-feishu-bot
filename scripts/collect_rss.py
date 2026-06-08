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
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOGGER = logging.getLogger("collect_rss")

TIMEZONE_NAME = "Asia/Shanghai"
RSS_TIMEOUT_SECONDS = 10
PAGE_SUMMARY_TIMEOUT_SECONDS = 5
PAGE_SUMMARY_MAX_BYTES = 200 * 1024
MAX_ENTRIES_PER_FEED = 25
PAGE_SUMMARY_MAX_PER_FEED = 2
MAX_NEWS_OUTPUT_ITEMS = 40
MAX_LEARNING_OUTPUT_ITEMS = 30
MAX_ITEMS_PER_SOURCE = 4
NEWS_FALLBACK_HOURS = 48
LEARNING_RECENT_DAYS = 30
LEARNING_FALLBACK_DAYS = 90


def parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        LOGGER.warning("%s invalid (%s), fallback to %s", name, raw, default)
        return default


def parse_float_env(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        LOGGER.warning("%s invalid (%s), fallback to %s", name, raw, default)
        return default


def parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    LOGGER.warning("%s invalid (%s), fallback to %s", name, raw, default)
    return default


RSS_FETCH_WORKERS = parse_int_env("RSS_FETCH_WORKERS", 24)
NEWS_REGION_MODE = os.getenv("NEWS_REGION_MODE", "balanced")
TARGET_CANDIDATE_COUNT = parse_int_env("TARGET_CANDIDATE_COUNT", 5)
MIN_GLOBAL_NEWS = parse_int_env("MIN_GLOBAL_NEWS", 2)
MAX_CHINA_NEWS = parse_int_env("MAX_CHINA_NEWS", 2)
MAX_AUTO_CHINA_NEWS = parse_int_env("MAX_AUTO_CHINA_NEWS", 1)
MAX_SAME_SOURCE_NEWS = parse_int_env("MAX_SAME_SOURCE_NEWS", 2)
MAX_ITEMS_FOR_LLM = parse_int_env("MAX_ITEMS_FOR_LLM", 5)
CANDIDATE_RETENTION_DAYS = parse_int_env("CANDIDATE_RETENTION_DAYS", 3)
MIN_AGENT_NEWS = parse_int_env("MIN_AGENT_NEWS", 1)
MIN_PRODUCTIVITY_NEWS = parse_int_env("MIN_PRODUCTIVITY_NEWS", 1)
MIN_COMPANY_RESEARCH_NEWS = parse_int_env("MIN_COMPANY_RESEARCH_NEWS", 1)
MAX_COMPANY_RESEARCH_NEWS = parse_int_env("MAX_COMPANY_RESEARCH_NEWS", 2)
MAX_AUTO_DRIVING_NEWS = parse_int_env("MAX_AUTO_DRIVING_NEWS", 1)
MAX_SMART_COCKPIT_NEWS = parse_int_env("MAX_SMART_COCKPIT_NEWS", 1)
POWER_ELECTRONICS_BOOST = parse_bool_env("POWER_ELECTRONICS_BOOST", True)

HISTORY_DEDUPE_DAYS = parse_int_env("HISTORY_DEDUPE_DAYS", 14)
HISTORY_RETENTION_DAYS = parse_int_env("HISTORY_RETENTION_DAYS", 30)
HISTORY_SIMILARITY_THRESHOLD = parse_float_env("HISTORY_SIMILARITY_THRESHOLD", 0.82)
HISTORY_PATH = Path("data") / "history" / "news-history.json"
LEARNING_HISTORY_DEDUPE_DAYS = parse_int_env("LEARNING_HISTORY_DEDUPE_DAYS", HISTORY_DEDUPE_DAYS)
LEARNING_HISTORY_RETENTION_DAYS = parse_int_env("LEARNING_HISTORY_RETENTION_DAYS", HISTORY_RETENTION_DAYS)
LEARNING_HISTORY_SIMILARITY_THRESHOLD = parse_float_env(
    "LEARNING_HISTORY_SIMILARITY_THRESHOLD",
    HISTORY_SIMILARITY_THRESHOLD,
)
LEARNING_HISTORY_PATH = Path("data") / "history" / "learning-history.json"
TARGET_LEARNING_CANDIDATE_COUNT = parse_int_env("TARGET_LEARNING_CANDIDATE_COUNT", 1)
MAX_SAME_SOURCE_LEARNING = parse_int_env("MAX_SAME_SOURCE_LEARNING", 1)

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
    source_quality: str = "",
    is_official_source: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "url": url,
        "language": language,
        "region": region,
        "source_group": source_group,
        "source_type": source_type,
        "tags": tags or [],
        "source_quality": source_quality,
        "is_official_source": is_official_source,
    }


# ----------------------------
# News sources
# ----------------------------

AI_AGENT_QUERIES = [
    "AI agent",
    "agentic AI",
    "autonomous agent",
    "coding agent",
    "OpenAI Codex",
    "Codex CLI",
    "Claude Code",
    "Cursor AI",
    "GitHub Copilot coding agent",
    "Devin AI software engineer",
    "MCP Model Context Protocol",
    "AI workflow automation",
    "AI software engineering",
    "AI code review",
    "AI testing automation",
    "AI developer productivity",
    "AI engineering productivity",
    "software engineering agent",
    "AI Agent",
    "智能体",
    "编程智能体",
    "代码智能体",
    "Codex",
    "Codex CLI",
    "Claude Code",
    "Cursor",
    "Devin",
    "GitHub Copilot",
    "MCP 模型上下文协议",
    "AI 工作流",
    "AI 自动化",
    "AI 软件工程",
    "AI 代码审查",
    "AI 测试自动化",
    "AI 研发提效",
    "研发智能体",
    "软件工程智能体",
]

AI_ORG_PRODUCTIVITY_QUERIES = [
    "AI productivity",
    "AI transformation",
    "AI organizational change",
    "AI enterprise automation",
    "AI workflow transformation",
    "AI knowledge management",
    "AI copilots in enterprise",
    "AI software development productivity",
    "AI engineering management",
    "AI product development",
    "AI process automation",
    "AI operating model",
    "AI 组织变革",
    "AI 提效",
    "AI 研发效率",
    "AI 企业自动化",
    "AI 流程自动化",
    "AI 知识管理",
    "企业 Copilot",
    "研发 Copilot",
    "AI 组织效率",
    "AI 软件研发提效",
    "AI 项目管理",
    "AI 产品研发",
    "AI 降本增效",
    "AI 办公自动化",
    "AI 运营模式",
    "AI 组织重构",
]

AI_POWER_ELECTRONICS_QUERIES = [
    "AI power electronics",
    "AI onboard charger",
    "AI OBC",
    "AI DC-DC converter",
    "automotive OBC AI",
    "automotive DCDC AI",
    "AI SiC power module",
    "AI GaN power electronics",
    "AI battery charger control",
    "AI power converter control",
    "AI thermal management power electronics",
    "AI fault diagnosis power electronics",
    "predictive maintenance power electronics",
    "digital twin power electronics",
    "AI simulation power electronics",
    "AI design optimization power electronics",
    "AI control algorithm power converter",
    "AI V2G",
    "AI V2H",
    "AI V2L",
    "vehicle-to-grid AI",
    "vehicle-to-home AI",
    "bidirectional charger AI",
    "AI PFC power factor correction",
    "AI inverter power electronics",
    "AI LLC resonant converter",
    "AI phase-shift converter",
    "AI soft switching",
    "AI MPPT charging",
    "AI CC CV charging",
    "AI 功率电子",
    "AI 车载充电机",
    "AI OBC",
    "AI DCDC",
    "AI DC-DC",
    "车载 OBC AI",
    "车载 DCDC AI",
    "AI 电源控制",
    "AI 功率变换器",
    "AI SiC",
    "AI 碳化硅 功率模块",
    "AI GaN",
    "AI 氮化镓 功率电子",
    "AI 热管理 功率电子",
    "AI 故障诊断 功率电子",
    "AI 预测性维护 电源",
    "AI 数字孪生 功率电子",
    "AI 仿真优化 电源",
    "AI 自动化测试 电源控制",
    "OBC 故障诊断",
    "DCDC 故障诊断",
    "OBC 数字孪生",
    "DCDC 数字孪生",
    "V2G 人工智能",
    "V2H 人工智能",
    "双向充电 人工智能",
    "PFC 人工智能",
    "LLC 谐振 人工智能",
]

# EV noise terms - generic automotive news without power electronics focus
EV_NOISE_TERMS = [
    "自动驾驶",
    "智能驾驶",
    "智能座舱",
    "销量",
    "车型",
    "发布会",
    "上市",
    "续航",
    "电池包",
    "底盘",
    "内饰",
    "外观",
    "配置",
    "试驾",
    "交付",
    "订单",
    "价格",
    "购车",
    "新能源整车",
    "智能网联",
    "辅助驾驶",
    "NOA",
    "激光雷达",
    "摄像头",
    "毫米波雷达",
    "智能悬挂",
    "空气悬挂",
    "座椅",
    "大屏",
    "车机系统",
    "语音助手",
]

COMPANY_RESEARCH_SITE_QUERIES = [
    ("OpenAI Research - AI agent", "site:openai.com/research AI agent", "official_research", "research_paper", ["company_research", "official_report", "ai_research", "ai_agent"]),
    ("OpenAI Blog - Codex", "site:openai.com/blog Codex", "company_blog", "official_blog", ["company_research", "technical_blog", "codex", "coding_agent"]),
    ("Anthropic Research - agentic AI", "site:anthropic.com/research agentic AI", "official_research", "research_paper", ["company_research", "official_report", "ai_agent"]),
    ("Anthropic Engineering - AI agent", "site:anthropic.com/engineering AI agent", "company_blog", "technical_blog", ["company_research", "technical_blog", "ai_agent"]),
    ("Google DeepMind - AI agent", "site:deepmind.google AI agent", "official_research", "research_paper", ["company_research", "ai_research", "ai_agent"]),
    ("Google Research - AI coding", "site:research.google AI coding", "official_research", "research_paper", ["company_research", "ai_research", "coding_agent"]),
    ("Microsoft Research - AI software engineering", "site:microsoft.com/en-us/research AI software engineering", "official_research", "research_paper", ["company_research", "ai_research", "engineering_productivity"]),
    ("NVIDIA - AI agent", "site:nvidia.com AI agent", "technical_report", "technical_blog", ["company_research", "technical_blog", "ai_agent"]),
    ("NVIDIA Developer - AI inference", "site:developer.nvidia.com/blog AI inference", "company_blog", "technical_blog", ["company_research", "technical_blog", "ai_research"]),
    ("Apple ML Research - AI model", "site:apple.com/machine-learning AI model", "official_research", "research_paper", ["company_research", "ai_research"]),
    ("Amazon Science - AI agent", "site:amazon.science AI agent", "official_research", "research_paper", ["company_research", "ai_research", "ai_agent"]),
    ("Meta AI Research - AI agent", "site:ai.meta.com/research AI agent", "official_research", "research_paper", ["company_research", "ai_research", "ai_agent"]),
    ("IBM Research - AI agent", "site:research.ibm.com AI agent", "official_research", "research_paper", ["company_research", "ai_research", "ai_agent"]),
    ("Salesforce AI Research - AI agent", "site:salesforceairesearch.com AI agent", "official_research", "research_paper", ["company_research", "ai_research", "ai_agent"]),
    ("GitHub Blog - Copilot coding agent", "site:github.blog Copilot coding agent", "company_blog", "technical_blog", ["company_research", "technical_blog", "coding_agent"]),
    ("Cursor Blog - AI coding", "site:cursor.com blog AI coding", "company_blog", "technical_blog", ["company_research", "technical_blog", "coding_agent"]),
    ("Sourcegraph Blog - AI coding agent", "site:sourcegraph.com/blog AI coding agent", "company_blog", "technical_blog", ["company_research", "technical_blog", "coding_agent"]),
    ("JetBrains AI Engineering - AI coding", "site:blog.jetbrains.com AI coding", "company_blog", "technical_blog", ["company_research", "technical_blog", "coding_agent"]),
    ("Tesla AI - autonomous AI", "site:tesla.com/AI AI software", "technical_report", "official_blog", ["company_research", "technical_blog", "ai_research"]),
    ("Waymo Research - AI driving", "site:waymo.com/research AI", "official_research", "research_paper", ["company_research", "ai_research"]),
    ("Toyota Research Institute - AI", "site:tri.global AI research", "official_research", "research_paper", ["company_research", "ai_research"]),
    ("Bosch Research - AI software", "site:bosch.com AI automotive software", "technical_report", "official_blog", ["company_research", "technical_blog", "engineering_report"]),
    ("Continental AI software", "site:continental.com AI automotive software", "technical_report", "official_blog", ["company_research", "technical_blog", "engineering_report"]),
    ("ZF AI software", "site:zf.com AI automotive software", "technical_report", "official_blog", ["company_research", "technical_blog", "engineering_report"]),
    ("Mercedes-Benz AI software", "site:mercedes-benz.com AI software", "technical_report", "official_blog", ["company_research", "technical_blog", "engineering_report"]),
    ("BMW AI software", "site:bmwgroup.com AI software", "technical_report", "official_blog", ["company_research", "technical_blog", "engineering_report"]),
    ("Volkswagen CARIAD AI software", "site:cariad.technology AI software", "technical_report", "official_blog", ["company_research", "technical_blog", "engineering_report"]),
    ("Infineon - AI power electronics", "site:infineon.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("TI - AI power electronics", "site:ti.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("NXP - AI automotive power", "site:nxp.com AI automotive power", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("onsemi - AI power electronics", "site:onsemi.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("Wolfspeed - AI SiC automotive", "site:wolfspeed.com AI SiC automotive", "technical_report", "technical_blog", ["company_research", "technical_blog", "sic", "power_electronics"]),
    ("ST - AI power electronics", "site:st.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("ROHM - AI SiC automotive", "site:rohm.com AI SiC automotive", "technical_report", "technical_blog", ["company_research", "technical_blog", "sic", "power_electronics"]),
    ("Renesas - AI automotive power", "site:renesas.com AI automotive power", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("Analog Devices - AI power", "site:analog.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("Vishay - AI power electronics", "site:vishay.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("Qorvo - AI GaN power", "site:qorvo.com AI GaN power", "technical_report", "technical_blog", ["company_research", "technical_blog", "gan", "power_electronics"]),
    ("Navitas - AI GaN power", "site:navitassemi.com AI GaN power", "technical_report", "technical_blog", ["company_research", "technical_blog", "gan", "power_electronics"]),
    ("Power Integrations - AI power", "site:power.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("MPS - AI power electronics", "site:monolithicpower.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("Vicor - AI power electronics", "site:vicorpower.com AI power electronics", "technical_report", "technical_blog", ["company_research", "technical_blog", "power_electronics"]),
    ("Huawei - AI intelligent automotive", "site:huawei.com AI 智能汽车", "company_blog", "official_blog", ["company_research", "technical_blog", "ai_research"]),
    ("Huawei - AI productivity", "site:huawei.com 研发提效 AI", "company_blog", "official_blog", ["company_research", "technical_blog", "org_productivity"]),
    ("Horizon Robotics - AI chip", "site:horizon.cc 地平线 AI 芯片", "technical_report", "official_blog", ["company_research", "ai_research", "benchmark"]),
    ("Black Sesame - AI chip", "site:blacksesame.com 黑芝麻智能 AI 芯片", "technical_report", "official_blog", ["company_research", "ai_research", "benchmark"]),
    ("SemiDrive - automotive AI chip", "site:semidrive.com 芯驰科技 AI 车载芯片", "technical_report", "official_blog", ["company_research", "ai_research", "benchmark"]),
    ("BYD - AI intelligentization", "site:byd.com 比亚迪 AI 智能化", "company_blog", "official_blog", ["company_research", "technical_blog"]),
    ("Bosch China - AI automotive software", "site:bosch.com.cn AI 汽车软件", "technical_report", "official_blog", ["company_research", "technical_blog", "engineering_report"]),
    ("Continental - AI automotive software", "site:continental.com AI 汽车软件", "technical_report", "official_blog", ["company_research", "technical_blog", "engineering_report"]),
]


def build_google_query_sources(
    queries: list[str],
    name_prefix: str,
    source_group: str,
    source_type: str,
    tags: list[str],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for query in queries:
        has_chinese = bool(re.search(r"[\u4e00-\u9fff]", query))
        sources.append(
            make_source(
                f"{name_prefix} - {query}",
                make_google_news_rss_url(
                    query,
                    GOOGLE_NEWS_ZH_HL if has_chinese else GOOGLE_NEWS_EN_HL,
                    GOOGLE_NEWS_ZH_GL if has_chinese else GOOGLE_NEWS_EN_GL,
                    GOOGLE_NEWS_ZH_CEID if has_chinese else GOOGLE_NEWS_EN_CEID,
                ),
                "zh" if has_chinese else "en",
                "china" if has_chinese else "global",
                source_group,
                source_type=source_type,
                tags=tags,
            )
        )
    return sources


def build_company_research_sources() -> list[dict[str, Any]]:
    rss_sources = [
        make_source("OpenAI News", "https://openai.com/news/rss.xml", "en", "global", "company_research", "official_blog", ["company_research", "official_report", "ai_research"]),
        make_source("Google AI Blog", "https://blog.google/technology/ai/rss/", "en", "global", "company_research", "technical_blog", ["company_research", "technical_blog", "ai_research"]),
        make_source("NVIDIA Technical Blog", "https://developer.nvidia.com/blog/feed/", "en", "global", "company_research", "technical_blog", ["company_research", "technical_blog", "ai_research"]),
        make_source("NVIDIA Blog", "https://blogs.nvidia.com/feed/", "en", "global", "company_research", "technical_blog", ["company_research", "technical_blog", "ai_research"]),
        make_source("Microsoft Research Blog", "https://www.microsoft.com/en-us/research/feed/", "en", "global", "company_research", "research_paper", ["company_research", "official_report", "ai_research"]),
        make_source("GitHub Blog", "https://github.blog/feed/", "en", "global", "company_research", "technical_blog", ["company_research", "technical_blog", "coding_agent"]),
        make_source("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", "en", "global", "company_research", "technical_blog", ["company_research", "technical_blog", "ai_research"]),
    ]
    site_sources = [
        make_source(
            f"Google News Company Research - {name}",
            make_google_news_rss_url(
                query,
                GOOGLE_NEWS_ZH_HL if re.search(r"[\u4e00-\u9fff]", query) else GOOGLE_NEWS_EN_HL,
                GOOGLE_NEWS_ZH_GL if re.search(r"[\u4e00-\u9fff]", query) else GOOGLE_NEWS_EN_GL,
                GOOGLE_NEWS_ZH_CEID if re.search(r"[\u4e00-\u9fff]", query) else GOOGLE_NEWS_EN_CEID,
            ),
            "zh" if re.search(r"[\u4e00-\u9fff]", query) else "en",
            "china" if re.search(r"[\u4e00-\u9fff]", query) else "global",
            source_group,
            source_type=source_type,
            tags=tags,
        )
        for name, query, source_group, source_type, tags in COMPANY_RESEARCH_SITE_QUERIES
    ]
    return rss_sources + site_sources


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

AGENT_SOURCES = build_google_query_sources(AI_AGENT_QUERIES, "Google News AI Agent", "ai_agent", "news_search", ["ai_agent", "agentic_ai", "coding_agent"])
PRODUCTIVITY_SOURCES = build_google_query_sources(AI_ORG_PRODUCTIVITY_QUERIES, "Google News AI Productivity", "ai_productivity", "news_search", ["org_productivity", "ai_transformation", "workflow_automation"])
POWER_ELECTRONICS_SOURCES = build_google_query_sources(AI_POWER_ELECTRONICS_QUERIES, "Google News AI Power Electronics", "power_electronics", "news_search", ["power_electronics", "obc", "dcdc"])
COMPANY_RESEARCH_SOURCES = build_company_research_sources()

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
    "车载 Agent",
    "汽车软件 研发提效 AI",
    "车载 OBC AI",
    "车载 DCDC AI",
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


NEWS_SOURCES = (
    GLOBAL_SOURCES
    + AGENT_SOURCES
    + PRODUCTIVITY_SOURCES
    + POWER_ELECTRONICS_SOURCES
    + COMPANY_RESEARCH_SOURCES
    + CHINA_SOURCES
    + build_auto_china_news_sources()
)

NEWS_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "large language model",
    "llm",
    "foundation model",
    "multimodal",
    "agent",
    "agentic ai",
    "autonomous agent",
    "coding agent",
    "software engineering agent",
    "mcp",
    "model context protocol",
    "codex",
    "claude code",
    "cursor",
    "devin",
    "ai coding",
    "code generation",
    "code review",
    "testing automation",
    "developer productivity",
    "engineering productivity",
    "workflow automation",
    "enterprise automation",
    "organizational change",
    "copilot",
    "power electronics",
    "onboard charger",
    "obc",
    "dcdc",
    "dc-dc",
    "sic",
    "gan",
    "power converter",
    "thermal management",
    "fault diagnosis",
    "predictive maintenance",
    "digital twin",
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
    "ai agent",
    "智能体",
    "编程智能体",
    "代码智能体",
    "模型上下文协议",
    "研发提效",
    "组织变革",
    "流程自动化",
    "知识管理",
    "代码审查",
    "测试自动化",
    "功率电子",
    "车载充电机",
    "obc",
    "dcdc",
    "dc-dc",
    "电源控制",
    "碳化硅",
    "氮化镓",
    "故障诊断",
    "预测性维护",
    "数字孪生",
    "仿真优化",
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
        source_type="official_video",
        tags=["codex", "agent", "cli"],
        source_quality="high",
        is_official_source=True,
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
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "OpenAI Codex Changelog RSS",
        "https://developers.openai.com/codex/changelog/rss.xml",
        "en",
        "global",
        "official",
        source_type="official_doc",
        tags=["codex", "cli", "workflow", "code_review"],
        source_quality="high",
        is_official_source=True,
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
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "OpenAI Developers Resources",
        "https://developers.openai.com/resources",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "video", "tutorial"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "OpenAI Codex CLI Docs",
        "https://developers.openai.com/codex/cli",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "cli"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "OpenAI Codex Workflows Docs",
        "https://developers.openai.com/codex/workflows",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "workflow", "agent"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "OpenAI Docs MCP Guide",
        "https://platform.openai.com/docs/docs-mcp",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "mcp", "agent"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "OpenAI Codex Overview",
        "https://platform.openai.com/docs/codex/overview",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["codex", "agent"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "OpenAI Codex GitHub",
        "https://github.com/openai/codex",
        "en",
        "global",
        "official_page",
        source_type="github_repo",
        tags=["codex", "cli", "agents_md", "workflow"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "GitHub Copilot Docs",
        "https://docs.github.com/en/copilot",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["coding_agent", "code_review", "workflow"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "Anthropic Claude Code Docs",
        "https://docs.anthropic.com/en/docs/claude-code",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["coding_agent", "workflow"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "Cursor Docs",
        "https://docs.cursor.com/",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["coding_agent", "workflow"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "Sourcegraph Cody Docs",
        "https://sourcegraph.com/docs/cody",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["coding_agent", "workflow"],
        source_quality="high",
        is_official_source=True,
    ),
    make_source(
        "JetBrains AI Assistant Docs",
        "https://www.jetbrains.com/help/ai-assistant/getting-started.html",
        "en",
        "global",
        "official_page",
        source_type="official_doc",
        tags=["coding_agent", "developer_productivity"],
        source_quality="high",
        is_official_source=True,
    ),
]

OFFICIAL_AGENT_CONCEPT_RESOURCES: list[dict[str, Any]] = [
    {
        "title": "Agents SDK | OpenAI API",
        "link": "https://developers.openai.com/api/docs/guides/agents",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI official guide for building agent workflows with the Agents SDK, covering agent definitions, running agents, orchestration, tool use, guardrails, observability, and evaluation.",
        "tags": ["agents_sdk", "openai_agents", "agent_workflow", "orchestration", "tool_calling", "guardrails", "evals", "observability"],
    },
    {
        "title": "Agents SDK Quickstart | OpenAI API",
        "link": "https://developers.openai.com/api/docs/guides/agents/quickstart",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI official quickstart for creating and running an agent workflow with tools, handoffs, and basic orchestration concepts.",
        "tags": ["agents_sdk", "openai_agents", "agent_workflow", "tools", "handoffs", "orchestration"],
    },
    {
        "title": "Guardrails and human review | OpenAI API",
        "link": "https://developers.openai.com/api/docs/guides/agents/guardrails-approvals",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI official guide explaining how guardrails and human review help constrain agent behavior and control sensitive actions.",
        "tags": ["guardrails", "human_in_the_loop", "approvals", "safety", "agent_governance"],
    },
    {
        "title": "Evaluate agent workflows | OpenAI API",
        "link": "https://developers.openai.com/api/docs/guides/agent-evals",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI official guide on evaluating agent workflows using traces, graders, datasets, and evaluation runs.",
        "tags": ["evals", "agent_evaluation", "traces", "graders", "datasets", "agent_quality"],
    },
    {
        "title": "Integrations and observability | OpenAI API",
        "link": "https://developers.openai.com/api/docs/guides/agents/integrations-observability",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI official guide on tracing, debugging, integrations, and observability for agent workflows.",
        "tags": ["observability", "tracing", "mcp", "integrations", "debugging"],
    },
    {
        "title": "Running agents | OpenAI API",
        "link": "https://developers.openai.com/api/docs/guides/agents/running-agents",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI official guide covering how to run agents, manage context, handle state, stream outputs, and operate agent workflows.",
        "tags": ["running_agents", "state", "context_management", "streaming", "agent_runtime"],
    },
    {
        "title": "Codex CLI",
        "link": "https://developers.openai.com/codex/cli",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI official guide for Codex CLI, a local coding agent that runs in the terminal and helps with software engineering tasks.",
        "tags": ["codex", "coding_agent", "cli", "local_agent"],
    },
    {
        "title": "Custom instructions with AGENTS.md",
        "link": "https://developers.openai.com/codex/guides/agents-md",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI Codex guide explaining how AGENTS.md provides project-level context and instructions for coding agents.",
        "tags": ["agents_md", "agent_instructions", "project_context", "coding_agent"],
    },
    {
        "title": "Model Context Protocol - Codex",
        "link": "https://developers.openai.com/codex/mcp",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI Codex guide explaining how MCP connects Codex with external tools and context providers.",
        "tags": ["mcp", "tool_integration", "external_tools", "codex"],
    },
    {
        "title": "Use Codex with the Agents SDK",
        "link": "https://developers.openai.com/codex/guides/agents-sdk",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI guide describing how Codex can work with the Agents SDK, MCP, and agent-oriented tool workflows.",
        "tags": ["codex", "agents_sdk", "mcp", "tool_calling", "agent_orchestration"],
    },
    {
        "title": "Agent Skills - Codex",
        "link": "https://developers.openai.com/codex/skills",
        "source": "OpenAI Developers",
        "source_type": "official_doc",
        "source_quality": "high",
        "summary_source": "official_static_summary",
        "summary_quality": "high",
        "is_official_source": True,
        "summary": "OpenAI Codex guide about reusable agent skills and structured capabilities for coding workflows.",
        "tags": ["agent_skills", "reusable_capabilities", "workflow", "codex"],
    },
]

LEARNING_GNEWS_QUERIES_EN = [
    "AI agent workflow concept",
    "Agents SDK tool calling guardrails evals observability",
    "Model Context Protocol agent tools",
    "coding agent concept",
]

LEARNING_GNEWS_QUERIES_ZH = [
    "AI Agent 工作流 概念",
    "智能体 工具调用 护栏 评估 可观测性",
    "MCP 智能体 工具 协议",
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
                source_type="google_news",
                tags=["agent", "agent_workflow"],
                source_quality="low",
                is_official_source=False,
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
                source_type="google_news",
                tags=["agent", "agent_workflow"],
                source_quality="low",
                is_official_source=False,
            )
        )
    return sources


LEARNING_FEED_SOURCES = OFFICIAL_LEARNING_RSS_SOURCES + YOUTUBE_LEARNING_FEEDS + build_learning_gnews_sources()

LEARNING_KEYWORD_RULES: list[tuple[str, int, str]] = [
    ("agents sdk", 10, "agents_sdk"),
    ("agent workflow", 10, "agent_workflow"),
    ("ai agent", 8, "agent"),
    ("tool calling", 9, "tool_calling"),
    ("tools", 4, "tools"),
    ("orchestration", 8, "orchestration"),
    ("guardrails", 9, "guardrails"),
    ("human review", 8, "human_in_the_loop"),
    ("human-in-the-loop", 8, "human_in_the_loop"),
    ("evaluation", 7, "agent_evaluation"),
    ("evals", 9, "evals"),
    ("observability", 9, "observability"),
    ("tracing", 7, "tracing"),
    ("model context protocol", 9, "mcp"),
    ("mcp", 9, "mcp"),
    ("agent instructions", 8, "agent_instructions"),
    ("agents.md", 8, "agents_md"),
    ("coding agent", 8, "coding_agent"),
    ("multi-agent", 7, "multi_agent"),
    ("state", 5, "state"),
    ("memory", 5, "memory"),
    ("context management", 7, "context_management"),
    ("agent skills", 8, "agent_skills"),
    ("openai codex", 6, "codex"),
    ("codex cli", 6, "cli"),
    ("codex", 5, "codex"),
    ("workflow", 5, "agent_workflow"),
    ("agents.md", 7, "agents_md"),
    ("openai developers", 2, "official"),
    ("智能体 工作流", 8, "agent_workflow"),
    ("工具调用", 8, "tool_calling"),
    ("护栏", 8, "guardrails"),
    ("人工审核", 8, "human_in_the_loop"),
    ("评估", 6, "agent_evaluation"),
    ("可观测性", 8, "observability"),
    ("编程智能体", 7, "coding_agent"),
    ("工作流", 5, "agent_workflow"),
]

LEARNING_EXCLUDE_KEYWORDS = [
    "手游",
    "游戏实况",
    "电影",
    "影视",
    "带货",
    "广告投放",
    "娱乐八卦",
    "手机号",
    "手机验证",
    "接码",
    "验证码",
    "账号购买",
    "账号注册",
    "代充",
    "充值",
    "订阅账号",
    "解锁",
    "coupon",
    "music video",
    "gaming",
    "movie trailer",
    "celebrity",
    "coupon",
    "phone verification",
    "sms verification",
    "virtual number",
    "account verification",
]

LEARNING_CORE_TERMS = [
    "agent",
    "agents sdk",
    "tool calling",
    "orchestration",
    "guardrails",
    "human-in-the-loop",
    "evaluation",
    "evals",
    "observability",
    "tracing",
    "mcp",
    "agents.md",
    "codex",
    "coding agent",
    "智能体",
]

OFFICIAL_LEARNING_HOSTS = (
    "developers.openai.com",
    "openai.com",
    "github.com/openai/codex",
    "github.blog",
    "docs.github.com",
    "anthropic.com",
    "docs.anthropic.com",
    "cursor.com",
    "docs.cursor.com",
    "sourcegraph.com",
    "jetbrains.com",
    "microsoft.com",
    "learn.microsoft.com",
)
HIGH_QUALITY_LEARNING_HOSTS = (
    "github.blog",
    "docs.github.com",
    "anthropic.com",
    "cursor.com",
    "sourcegraph.com",
    "jetbrains.com",
    "microsoft.com",
    "learn.microsoft.com",
    "infoq.com",
    "martinfowler.com",
)


def is_google_news_source_text(source: str, link: str) -> bool:
    source_l = clean_text(source).lower()
    link_l = clean_text(link).lower()
    return "google news" in source_l or "news.google.com" in link_l


def normalize_learning_source_name(source: str, link: str) -> str:
    source_clean = clean_text(source)
    link_l = clean_text(link).lower()
    mapping = [
        ("developers.openai.com", "OpenAI Developers"),
        ("openai.com", "OpenAI"),
        ("github.com/openai/codex", "OpenAI Codex GitHub"),
        ("github.blog", "GitHub Blog"),
        ("docs.github.com", "GitHub Docs"),
        ("docs.anthropic.com", "Anthropic Docs"),
        ("anthropic.com", "Anthropic"),
        ("docs.cursor.com", "Cursor Docs"),
        ("cursor.com", "Cursor"),
        ("sourcegraph.com", "Sourcegraph"),
        ("jetbrains.com", "JetBrains"),
        ("learn.microsoft.com", "Microsoft Learn"),
        ("microsoft.com", "Microsoft"),
        ("youtube.com", "OpenAI YouTube"),
        ("youtu.be", "OpenAI YouTube"),
    ]
    for needle, label in mapping:
        if needle in link_l:
            return label
    if is_google_news_source_text(source_clean, link_l):
        title_part = source_clean.split(" - Google News", 1)[0].strip()
        if title_part and not title_part.lower().startswith("google news"):
            return title_part[:60]
        return ""
    return source_clean


def infer_learning_source_metadata(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    source = clean_text(out.get("source", ""))
    link = clean_text(out.get("link", ""))
    source_type = clean_text(out.get("source_type", "")).lower()
    text = f"{out.get('title', '')} {out.get('summary', '')} {source}".lower()
    link_l = link.lower()

    is_google = is_google_news_source_text(source, link)
    is_official = bool(out.get("is_official_source")) or any(host in link_l for host in OFFICIAL_LEARNING_HOSTS)
    quality = clean_text(out.get("source_quality", "")).lower()

    if is_google:
        source_type = "google_news"
        quality = "low"
        is_official = False
    elif "youtube.com" in link_l or "youtu.be" in link_l:
        source_type = "official_video" if is_official or "openai" in source.lower() else "media_article"
        quality = "high" if source_type == "official_video" else "medium"
    elif "github.com/openai/codex" in link_l:
        source_type = "github_repo"
        quality = "high"
        is_official = True
    elif is_official:
        if source_type not in {"official_doc", "official_video", "official_blog", "github_repo", "technical_blog"}:
            source_type = "official_doc" if "docs" in link_l or "developers" in link_l else "official_blog"
        quality = "high"
    elif any(host in link_l for host in HIGH_QUALITY_LEARNING_HOSTS):
        source_type = "technical_blog"
        quality = "high"
    elif source_type in {"tutorial", "blog", ""}:
        has_steps = any(term in text for term in ("step-by-step", "tutorial", "guide", "how to", "walkthrough", "实操", "教程", "步骤", "指南"))
        source_type = "technical_blog" if has_steps else "media_article"
        quality = "medium" if has_steps else "low"

    if quality not in {"high", "medium", "low"}:
        quality = "medium"

    out["source_type"] = source_type or "media_article"
    out["source_quality"] = quality
    out["is_official_source"] = bool(is_official)
    normalized_source = normalize_learning_source_name(source, link)
    if normalized_source:
        out["source"] = normalized_source
    return out


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


def clean_html_text(html_text: str) -> str:
    if not html_text:
        return ""
    text = unescape(str(html_text))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&#160;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def truncate_summary(text: str, limit: int = 800) -> str:
    value = clean_html_text(text)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


TITLE_SOURCE_SUFFIX_RE = re.compile(r"\s*[-_|｜—]\s*[^-_|｜—]{2,30}$")
TITLE_NOISE_WORDS = [
    "视频",
    "图文",
    "快讯",
    "重磅",
    "独家",
    "刚刚",
    "最新",
    "盘中",
    "收评",
    "异动",
    "股价",
    "涨停",
    "跌停",
]
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "spm",
    "from",
    "source",
}
WEAK_BUSINESS_TERMS = [
    "stock",
    "stocks",
    "shares",
    "share price",
    "nasdaq",
    "nyse",
    "price target",
    "analyst",
    "rating",
    "market cap",
    "premarket",
    "pre-market",
    "after-hours",
    "portfolio",
    "hedge fund",
    "investment",
    "funding",
    "sales",
    "price war",
    "earnings",
    "corp.",
    "(nvda)",
    "股价",
    "股票",
    "股市",
    "行情",
    "美股",
    "港股",
    "a股",
    "涨幅",
    "跌幅",
    "目标价",
    "评级",
    "研报",
    "市值",
    "盘前",
    "盘后",
    "投资",
    "融资",
    "销量",
    "价格战",
    "车型上市",
    "上市",
    "营销",
    "财报",
]
FINANCE_SOURCE_TERMS = [
    "yahoo finance",
    "moomoo",
    "sina finance",
    "新浪财经",
    "东方财富",
    "证券时报",
    "财联社",
    "marketwatch",
    "motley fool",
    "investor",
]
STRONG_TECH_TAGS = {
    "llm",
    "agent",
    "ai_agent",
    "agentic_ai",
    "coding",
    "coding_agent",
    "mcp",
    "codex",
    "org_productivity",
    "engineering_productivity",
    "workflow_automation",
    "developer_productivity",
    "chip",
    "power_electronics",
    "obc",
    "dcdc",
    "sic",
    "gan",
    "robotics",
    "autonomous_driving",
    "smart_cockpit",
    "research",
    "company_research",
    "technical_blog",
    "whitepaper",
    "research_report",
    "opensource",
    "security",
    "regulation",
    "multimodal",
}
NEW_DEVELOPMENT_TERMS = [
    "update",
    "launches",
    "releases",
    "announces",
    "version",
    "rollout",
    "approval",
    "production",
    "open source",
    "benchmark",
    "新版本",
    "发布",
    "上线",
    "获批",
    "量产",
    "首发",
    "开源",
    "基准",
    "重大更新",
    "正式推出",
    "开始交付",
]
GOOGLE_NEWS_HOSTS = {"news.google.com"}


def normalize_title(title: str) -> str:
    value = clean_text(title).lower()
    value = TITLE_SOURCE_SUFFIX_RE.sub("", value)
    value = re.sub(r"\b\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?\b", " ", value)
    value = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\b", " ", value)
    for word in TITLE_NOISE_WORDS:
        value = value.replace(word, " ")
    value = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_link(link: str) -> str:
    value = clean_text(link)
    if not value:
        return ""
    parsed = urlparse(value)
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


def is_google_news_link(link: str) -> bool:
    return urlparse(clean_text(link)).netloc.lower() in GOOGLE_NEWS_HOSTS


def title_tokens(value: str) -> set[str]:
    normalized = normalize_title(value)
    if re.search(r"[\u4e00-\u9fff]", normalized):
        compact = re.sub(r"\s+", "", normalized)
        if len(compact) <= 2:
            return {compact} if compact else set()
        return {compact[i : i + 2] for i in range(len(compact) - 1)}
    return {token for token in normalized.split() if token}


def simple_title_similarity(a: str, b: str) -> float:
    a_tokens = title_tokens(a)
    b_tokens = title_tokens(b)
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


SOURCE_ALIAS_RE = re.compile(r"\s+[-|｜]\s+google news$", re.IGNORECASE)
TOPIC_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "into",
    "over",
    "under",
    "after",
    "before",
    "about",
    "using",
    "amid",
    "says",
    "said",
    "news",
    "report",
    "reports",
    "launch",
    "launches",
    "announces",
    "announced",
    "reveals",
    "video",
    "latest",
}
ENTITY_TERMS = (
    "nvidia",
    "openai",
    "anthropic",
    "google",
    "microsoft",
    "github",
    "meta",
    "apple",
    "deepseek",
    "huawei",
    "byd",
    "tesla",
    "阿里",
    "百度",
    "腾讯",
    "字节",
    "华为",
    "比亚迪",
)
MAX_SAME_ENTITY_NEWS = 2


def source_identity_key(item: dict[str, Any]) -> str:
    link = clean_text(item.get("link", ""))
    host = urlparse(link).netloc.lower()
    source_text = clean_text(item.get("source", "")).lower()
    is_google_host = host in GOOGLE_NEWS_HOSTS
    brand_terms = (
        "nvidia",
        "openai",
        "anthropic",
        "google",
        "microsoft",
        "github",
        "meta",
        "apple",
        "business wire",
        "oschina",
        "机器之心",
        "量子位",
        "infoq",
    )
    for term in brand_terms:
        if (not is_google_host and term in host) or term in source_text:
            return term
    if host and not is_google_host:
        return host.removeprefix("www.")
    source = SOURCE_ALIAS_RE.sub("", source_text)
    source = re.sub(r"\s+", " ", source)
    return source or clean_text(item.get("source_group", "unknown")).lower()


def topic_token_set(item: dict[str, Any]) -> set[str]:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    tokens = title_tokens(text)
    return {
        token
        for token in tokens
        if (len(token) >= 3 or re.search(r"[\u4e00-\u9fff]", token))
        and token not in TOPIC_STOPWORDS
        and not token.isdigit()
    }


def duplicate_index_tokens(item: dict[str, Any], limit: int = 8) -> list[str]:
    tokens = {
        token
        for token in title_tokens(str(item.get("title", "")))
        if (len(token) >= 3 or re.search(r"[\u4e00-\u9fff]", token))
        and token not in TOPIC_STOPWORDS
        and not token.isdigit()
    }
    return sorted(tokens, key=lambda token: (-len(token), token))[:limit]


def primary_entity_key(item: dict[str, Any]) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('source', '')} {item.get('link', '')}".lower()
    for term in ENTITY_TERMS:
        if term in text:
            return term
    return ""


def topic_signature(item: dict[str, Any], limit: int = 6) -> str:
    tags = sorted(str(tag).lower() for tag in item.get("topic_tags", item.get("tags", [])) if str(tag).strip())
    category = clean_text(item.get("category", "")).lower()
    tokens = sorted(topic_token_set(item), key=lambda token: (-len(token), token))[:limit]
    return "|".join([category, *tags[:4], *tokens])


def token_similarity(a_tokens: set[str], b_tokens: set[str]) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


CORE_ENTITY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("openai", ("openai",)),
    ("anthropic", ("anthropic",)),
    ("google", ("google", "谷歌")),
    ("deepmind", ("deepmind", "google deepmind")),
    ("microsoft", ("microsoft", "微软")),
    ("github", ("github",)),
    ("nvidia", ("nvidia", "英伟达")),
    ("meta", ("meta", "meta ai")),
    ("apple", ("apple", "苹果")),
    ("amazon", ("amazon", "亚马逊")),
    ("aws", ("aws", "amazon web services")),
    ("bosch", ("bosch", "博世")),
    ("infineon", ("infineon", "英飞凌")),
    ("ti", ("texas instruments", " ti ", "德州仪器")),
    ("nxp", ("nxp", "恩智浦")),
    ("st", (" stmicroelectronics", " st ", "意法半导体")),
    ("onsemi", ("onsemi", "安森美")),
    ("wolfspeed", ("wolfspeed",)),
    ("byd", ("byd", "比亚迪")),
    ("huawei", ("huawei", "华为")),
    ("horizon", ("horizon robotics", "地平线")),
    ("black_sesame", ("black sesame", "黑芝麻")),
    ("semidrive", ("semidrive", "芯驰", "芯驰科技")),
)
PRODUCT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("codex", ("codex",)),
    ("claude_code", ("claude code",)),
    ("cursor", ("cursor",)),
    ("copilot", ("copilot",)),
    ("devin", ("devin",)),
    ("mcp", ("mcp", "model context protocol", "模型上下文协议")),
    ("agents_sdk", ("agents sdk", "agent sdk")),
    ("agents_md", ("agents.md", "agents md")),
    ("vera", ("vera",)),
    ("rubin", ("rubin",)),
    ("obc", ("obc", "onboard charger", "on-board charger", "车载充电机")),
    ("dcdc", ("dcdc", "dc-dc", "dc/dc", "dc dc converter", "直流变换器")),
    ("sic", ("sic", "silicon carbide", "碳化硅")),
    ("gan", ("gan", "gallium nitride", "氮化镓")),
)
ACTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("release", ("launch", "launches", "launched", "unveil", "unveils", "unveiled", "release", "releases", "released", "announce", "announces", "announced", "introduce", "introduces", "introduced", "发布", "推出", "上线", "宣布", "首发")),
    ("update", ("update", "updates", "updated", "upgrade", "upgrades", "upgraded", "adds", "add", "新增", "升级", "更新", "接入")),
    ("availability", ("available", "general availability", "becomes available", "可用", "开放")),
    ("opensource", ("open source", "open-source", "开源")),
    ("partnership", ("partner", "partners", "partnership", "collaborate", "合作")),
    ("expand", ("expand", "expands", "expanded", "扩展")),
    ("production", ("production", "mass production", "量产")),
)
TOPIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("agent", ("agent", "agentic", "ai agent", "智能体")),
    ("coding_agent", ("coding agent", "software engineering agent", "编程智能体", "代码智能体")),
    ("enterprise_workflow", ("enterprise workflow", "enterprise workflows", "enterprise software teams", "企业工作流")),
    ("software_engineering", ("software engineering", "developer productivity", "engineering productivity", "研发提效", "软件研发提效")),
    ("aws", ("aws",)),
    ("bedrock", ("bedrock",)),
    ("cloud_platform", ("cloud platform", "cloud", "云平台", "上架")),
    ("ai_factory", ("ai factory", "ai 工厂")),
    ("inference", ("inference", "推理")),
    ("power_electronics", ("power electronics", "功率电子")),
    ("obc", ("obc", "onboard charger", "on-board charger", "车载充电机")),
    ("dcdc", ("dcdc", "dc-dc", "dc/dc", "直流变换器")),
    ("fault_diagnosis", ("fault diagnosis", "fault-diagnosis", "故障诊断")),
    ("digital_twin", ("digital twin", "数字孪生")),
    ("test_automation", ("test automation", "testing automation", "测试自动化")),
    ("terminal", ("terminal", "cli", "本地终端")),
    ("model_capability", ("multimodal", "frontier model", "gpt", "模型能力", "多模态", "大模型")),
    ("security", ("security", "vulnerability", "安全漏洞", "漏洞")),
    ("policy_regulation", ("policy", "regulation", "监管", "政策")),
    ("incident", ("incident", "accident", "outage", "事故", "故障")),
    ("research", ("research", "paper", "technical report", "研究", "论文", "技术报告")),
)


def _contains_alias(text: str, alias: str) -> bool:
    alias = alias.lower()
    if re.search(r"[\u4e00-\u9fff]", alias):
        return alias in text
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias.strip())}(?![a-z0-9])", text))


def _extract_alias_tokens(text: str, aliases: tuple[tuple[str, tuple[str, ...]], ...]) -> set[str]:
    normalized = f" {clean_text(text).lower()} "
    found: set[str] = set()
    for canonical, needles in aliases:
        if any(_contains_alias(normalized, needle) for needle in needles):
            found.add(canonical)
    return found


def _item_text(item: dict[str, Any]) -> str:
    return f"{item.get('title', '')} {item.get('summary', '')}"


def extract_core_entities(text: str) -> dict[str, list[str]]:
    """Extract lightweight canonical entities and products from title/summary text."""
    return {
        "entities": sorted(_extract_alias_tokens(text, CORE_ENTITY_ALIASES)),
        "products": sorted(_extract_alias_tokens(text, PRODUCT_ALIASES)),
    }


def extract_action_tokens(text: str) -> list[str]:
    return sorted(_extract_alias_tokens(text, ACTION_ALIASES))


def extract_topic_tokens(text: str) -> list[str]:
    return sorted(_extract_alias_tokens(text, TOPIC_ALIASES))


def build_event_signature(item: dict[str, Any]) -> dict[str, list[str]]:
    existing = item.get("event_signature")
    if isinstance(existing, dict):
        return {
            "entities": sorted(str(v) for v in existing.get("entities", []) if str(v).strip()),
            "products": sorted(str(v) for v in existing.get("products", []) if str(v).strip()),
            "actions": sorted(str(v) for v in existing.get("actions", []) if str(v).strip()),
            "topics": sorted(str(v) for v in existing.get("topics", []) if str(v).strip()),
        }
    text = _item_text(item)
    core = extract_core_entities(text)
    topics = set(extract_topic_tokens(text))
    topics.update(str(tag).strip().lower() for tag in item.get("topic_tags", item.get("tags", [])) if str(tag).strip())
    return {
        "entities": core["entities"],
        "products": core["products"],
        "actions": extract_action_tokens(text),
        "topics": sorted(topics),
    }


def _signature_sets(item: dict[str, Any]) -> tuple[set[str], set[str], set[str], set[str]]:
    signature = build_event_signature(item)
    return (
        set(signature["entities"]),
        set(signature["products"]),
        set(signature["actions"]),
        set(signature["topics"]),
    )


def _protected_event_types(item: dict[str, Any]) -> set[str]:
    text = _item_text(item).lower()
    topics = set(build_event_signature(item)["topics"])
    actions = set(build_event_signature(item)["actions"])
    types: set[str] = set()
    if topics & {"security"}:
        types.add("security")
    if topics & {"policy_regulation"}:
        types.add("policy_regulation")
    if topics & {"incident"}:
        types.add("incident")
    if topics & {"model_capability", "multimodal", "llm"} or any(term in text for term in ("gpt", "frontier model", "multimodal", "多模态", "大模型")):
        types.add("model_capability")
    if actions & {"partnership"}:
        types.add("partnership")
    if actions & {"availability"} or topics & {"cloud_platform", "aws", "bedrock"}:
        types.add("cloud_availability")
    if topics & {"research", "ai_research"} or any(term in text for term in ("research", "paper", "technical report", "研究", "论文", "技术报告")):
        types.add("research")
    if actions & {"release", "update", "expand", "opensource", "production"}:
        types.add("product_release")
    return types


def event_signatures_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_entities, a_products, _, a_topics = _signature_sets(a)
    b_entities, b_products, _, b_topics = _signature_sets(b)
    a_types = _protected_event_types(a)
    b_types = _protected_event_types(b)
    dangerous = {"security", "policy_regulation", "incident"}
    if (a_types & dangerous) != (b_types & dangerous):
        return True
    if ("model_capability" in a_types) != ("model_capability" in b_types) and (
        ("partnership" in a_types or "cloud_availability" in a_types)
        or ("partnership" in b_types or "cloud_availability" in b_types)
    ):
        return True
    if ("research" in a_types) != ("research" in b_types) and (a_topics & b_topics & {"research", "ai_research"}):
        return True
    if a_entities & b_entities and a_products and b_products and not (a_products & b_products):
        return True
    return False


def event_signature_match(a: dict[str, Any], b: dict[str, Any], high_confidence: bool = False) -> bool:
    if event_signatures_conflict(a, b):
        return False
    a_entities, a_products, a_actions, a_topics = _signature_sets(a)
    b_entities, b_products, b_actions, b_topics = _signature_sets(b)
    entity_overlap = a_entities & b_entities
    product_overlap = a_products & b_products
    topic_overlap = a_topics & b_topics
    action_overlap = a_actions & b_actions

    if not product_overlap and not (entity_overlap and len(topic_overlap) >= 2):
        return False
    if a_actions and b_actions and not action_overlap:
        return False

    strong_topic_count = len(topic_overlap - {"ai", "agent", "power_electronics"})
    if high_confidence:
        return bool((entity_overlap or product_overlap) and (action_overlap or strong_topic_count >= 2) and len(topic_overlap) >= 2)
    if entity_overlap and product_overlap and action_overlap and topic_overlap:
        return True
    if product_overlap and action_overlap and strong_topic_count >= 1:
        return True
    if product_overlap and len(topic_overlap) >= 2 and not a_actions and not b_actions:
        return True
    return False


def likely_same_story(a: dict[str, Any], b: dict[str, Any], base_threshold: float | None = None) -> bool:
    threshold = base_threshold if base_threshold is not None else min(HISTORY_SIMILARITY_THRESHOLD, 0.78)
    a_title = normalize_title(str(a.get("title", "")))
    b_title = normalize_title(str(b.get("title", "")))
    if a_title and b_title and a_title == b_title:
        return True

    a_link = normalize_link(str(a.get("link", "")))
    b_link = normalize_link(str(b.get("link", "")))
    if a_link and a_link == b_link:
        return True

    title_sim = simple_title_similarity(a_title, b_title)
    if title_sim > max(threshold, 0.82):
        return True

    same_source = source_identity_key(a) == source_identity_key(b)
    if same_source and title_sim > 0.72:
        return True

    if event_signature_match(a, b):
        return True

    a_topic = topic_signature(a)
    b_topic = topic_signature(b)
    same_topic = bool(a_topic and b_topic and a_topic == b_topic)
    content_sim = token_similarity(topic_token_set(a), topic_token_set(b))
    if same_source and content_sim >= 0.60:
        return True
    if same_topic and (title_sim >= 0.66 or content_sim >= 0.70):
        return True
    if content_sim >= 0.82:
        return True
    return False


def make_canonical_key(item: dict[str, Any]) -> str:
    title_key = normalize_title(str(item.get("title", "")))
    if len(title_key) >= 8:
        return title_key
    link_key = normalize_link(str(item.get("link", "")))
    source_group = clean_text(item.get("source_group", "unknown")).lower()
    return f"{source_group}:{link_key or title_key}"


def is_official_or_primary_source(item: dict[str, Any]) -> bool:
    source = clean_text(item.get("source", "")).lower()
    link = clean_text(item.get("link", "")).lower()
    source_group = clean_text(item.get("source_group", "")).lower()
    source_type = clean_text(item.get("source_type", "")).lower()
    tags = {str(tag).strip().lower() for tag in item.get("tags", []) if str(tag).strip()}
    if source_group in {"company_research", "official_research", "technical_report", "whitepaper", "company_blog"}:
        return True
    if source_type in {"company_research_report", "technical_blog", "whitepaper", "research_paper", "official_blog"}:
        return True
    if tags & {"company_research", "official_report", "technical_blog", "whitepaper", "research_report"}:
        return True
    primary_terms = [
        "openai",
        "anthropic",
        "deepmind",
        "google ai",
        "meta ai",
        "microsoft",
        "nvidia",
        "apple",
        "github",
        "arxiv",
        "research",
        "infineon",
        "st.com",
        "ti.com",
        "nxp",
        "onsemi",
        "wolfspeed",
        "renesas",
        "analog devices",
        "navitas",
    ]
    return any(term in source or term in link for term in primary_terms)


def source_preference_rank(item: dict[str, Any]) -> int:
    source_group = clean_text(item.get("source_group", "")).lower()
    source_type = clean_text(item.get("source_type", "")).lower()
    source = clean_text(item.get("source", "")).lower()
    link = clean_text(item.get("link", "")).lower()
    tags = {str(tag).strip().lower() for tag in item.get("topic_tags", item.get("tags", [])) if str(tag).strip()}

    if source_group in {"official_research", "company_research"} or source_type in {"company_research_report", "research_paper", "official_blog"} or tags & {"official_report", "company_research", "ai_research"}:
        return 60
    if source_type == "technical_blog" or source_group in {"technical_report", "company_blog"} or "technical_blog" in tags:
        return 50
    if source_type == "whitepaper" or source_group == "whitepaper" or "whitepaper" in tags:
        return 45
    if any(term in source or term in link for term in ("technologyreview", "venturebeat", "theverge", "techcrunch", "infoq", "机器之心", "量子位")):
        return 35
    if is_google_news_link(str(item.get("link", ""))) or "google news" in source:
        return 10
    return 25


def classify_item(item: dict[str, Any]) -> dict[str, Any]:
    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('source', '')} {' '.join(str(t) for t in item.get('tags', []))}".lower()
    tags: set[str] = {str(tag).strip().lower() for tag in item.get("tags", []) if str(tag).strip()}

    tag_rules = [
        ("llm", ["llm", "large language model", "大模型", "生成式ai", "generative ai"]),
        ("agent", ["agent", "智能体", "mcp", "codex", "devin"]),
        ("ai_agent", ["ai agent", "agentic ai", "autonomous agent", "智能体", "研发智能体", "软件工程智能体", "车载 agent"]),
        ("agentic_ai", ["agentic ai", "autonomous agent", "workflow agent", "agent workflow"]),
        ("coding", ["coding assistant", "ai coding", "code generation", "copilot", "cursor", "codex", "代码助手", "ai编程"]),
        ("coding_agent", ["coding agent", "code agent", "software engineering agent", "编程智能体", "代码智能体", "claude code", "cursor", "devin", "github copilot", "codex"]),
        ("mcp", ["mcp", "model context protocol", "模型上下文协议"]),
        ("codex", ["openai codex", "codex cli", "codex"]),
        ("org_productivity", ["ai productivity", "ai transformation", "organizational change", "enterprise automation", "组织变革", "组织效率", "降本增效"]),
        ("ai_transformation", ["ai transformation", "workflow transformation", "operating model", "组织重构", "运营模式"]),
        ("engineering_productivity", ["developer productivity", "engineering productivity", "software development productivity", "研发提效", "研发效率", "软件研发提效"]),
        ("workflow_automation", ["workflow automation", "process automation", "ai workflow", "ai 工作流", "流程自动化", "办公自动化"]),
        ("knowledge_management", ["knowledge management", "知识管理"]),
        ("ai_code_review", ["ai code review", "code review", "代码审查"]),
        ("ai_testing", ["ai testing", "testing automation", "test automation", "测试自动化"]),
        ("ci_cd", ["ci/cd", "cicd", "continuous integration", "continuous delivery"]),
        ("developer_productivity", ["developer productivity", "开发者效率", "研发 copilot", "企业 copilot"]),
        ("chip", ["gpu", "npu", "ai chip", "inference", "training", "算力", "芯片", "昇腾", "寒武纪", "地平线", "黑芝麻", "芯驰"]),
        ("power_electronics", ["power electronics", "power module", "power converter", "功率电子", "功率模块", "功率变换器"]),
        ("obc", ["onboard charger", "on-board charger", "obc", "车载充电机"]),
        ("dcdc", ["dc-dc", "dc/dc", "dcdc", "dc dc converter", "直流变换器"]),
        ("sic", ["sic", "silicon carbide", "碳化硅"]),
        ("gan", ["gan", "gallium nitride", "氮化镓"]),
        ("power_control", ["power control", "converter control", "电源控制", "控制算法"]),
        ("thermal_management", ["thermal management", "热管理"]),
        ("fault_diagnosis", ["fault diagnosis", "故障诊断"]),
        ("predictive_maintenance", ["predictive maintenance", "预测性维护"]),
        ("digital_twin", ["digital twin", "数字孪生"]),
        ("simulation_optimization", ["simulation", "design optimization", "仿真优化", "设计优化"]),
        ("automated_testing", ["automated testing", "自动化测试"]),
        ("robotics", ["robot", "robotics", "humanoid", "机器人", "具身智能"]),
        ("autonomous_driving", ["autonomous driving", "self-driving", "adas", "noa", "自动驾驶", "智能驾驶", "端到端", "华为ads"]),
        ("smart_cockpit", ["smart cockpit", "智能座舱", "车载大模型", "车载agent"]),
        ("automotive", ["automotive", "vehicle", "车载", "汽车", "比亚迪", "理想", "小鹏", "蔚来"]),
        ("research", ["arxiv", "paper", "icml", "neurips", "cvpr", "sota", "论文"]),
        ("company_research", ["company_research", "official research", "research blog", "官方研究", "技术博客"]),
        ("official_report", ["official report", "官方报告"]),
        ("whitepaper", ["whitepaper", "white paper", "白皮书"]),
        ("technical_blog", ["technical blog", "developer blog", "engineering blog", "技术博客", "工程博客"]),
        ("research_report", ["research report", "研究报告"]),
        ("ai_research", ["ai research", "machine learning research", "ai 研究"]),
        ("engineering_report", ["engineering report", "工程报告"]),
        ("benchmark", ["benchmark", "基准"]),
        ("reference_architecture", ["reference architecture", "参考架构"]),
        ("design_guide", ["design guide", "reference design", "设计指南", "参考设计"]),
        ("opensource", ["open source", "opensource", "开源"]),
        ("security", ["security", "safety", "安全"]),
        ("regulation", ["regulation", "policy", "监管", "法规"]),
        ("cloud", ["cloud", "云"]),
        ("multimodal", ["multimodal", "多模态"]),
    ]
    for tag, needles in tag_rules:
        if any(contains_keyword(text, needle) for needle in needles):
            tags.add(tag)

    source_group = clean_text(item.get("source_group", "")).lower()
    source_type = clean_text(item.get("source_type", "")).lower()
    category = "其他"
    if tags & {"obc", "dcdc"}:
        category = "OBC/DCDC"
    elif tags & {"power_electronics", "sic", "gan", "power_control"}:
        category = "功率电子"
    elif tags & {"fault_diagnosis", "predictive_maintenance", "digital_twin", "simulation_optimization", "thermal_management"} and tags & {"obc", "dcdc", "power_electronics", "sic", "gan"}:
        category = "车载电源电子"
    elif tags & {"coding", "coding_agent", "codex", "mcp"}:
        category = "AI编程工具"
    elif tags & {"ai_agent", "agentic_ai", "agent"}:
        category = "AI Agent"
    elif tags & {"org_productivity", "ai_transformation", "engineering_productivity", "developer_productivity", "knowledge_management"}:
        category = "AI组织提效"
    elif "workflow_automation" in tags:
        category = "AI工作流自动化"
    elif "whitepaper" in tags or source_type == "whitepaper":
        category = "白皮书"
    elif tags & {"official_report", "company_research", "ai_research"} or source_group in {"company_research", "official_research"}:
        category = "官方研究"
    elif "technical_blog" in tags or source_type in {"technical_blog", "official_blog"} or source_group in {"technical_report", "company_blog"}:
        category = "技术报告"
    elif "chip" in tags:
        category = "AI芯片"
    elif "research" in tags:
        category = "研究论文"
    elif "robotics" in tags:
        category = "全球AI"
    elif "opensource" in tags:
        category = "开源模型"
    elif "autonomous_driving" in tags:
        category = "自动驾驶"
    elif "smart_cockpit" in tags:
        category = "智能座舱"
    elif tags & {"autonomous_driving", "smart_cockpit", "automotive"} and source_group == "auto_china":
        category = "中国汽车"
    elif source_group in ("china", "auto_china") or item.get("region") == "china":
        category = "中国AI"
    elif tags:
        category = "全球AI"

    out = dict(item)
    out["topic_tags"] = sorted(tags)
    out["category"] = category
    out["dedupe_key"] = normalize_title(str(item.get("title", "")))
    out["canonical_key"] = make_canonical_key(item)
    out["normalized_link"] = normalize_link(str(item.get("link", "")))
    return out


def score_item(item: dict[str, Any]) -> int:
    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('source', '')}".lower()
    tags = set(item.get("topic_tags", []))
    score = 0

    source_group = clean_text(item.get("source_group", "")).lower()
    source_type = clean_text(item.get("source_type", "")).lower()

    if is_official_or_primary_source(item):
        score += 20
    if source_group == "company_research":
        score += 25
    if source_group == "official_research":
        score += 25
    if source_group == "whitepaper" or source_type == "whitepaper" or "whitepaper" in tags:
        score += 22
    if source_group in {"technical_report", "company_blog"} or source_type in {"technical_blog", "official_blog"} or "technical_blog" in tags:
        score += 18
    if source_type == "research_paper" or "research" in tags:
        score += 18
    if tags & {"benchmark", "reference_architecture", "design_guide"}:
        score += 16

    source = clean_text(item.get("source", "")).lower()
    if any(term in source for term in ("techcrunch", "the verge", "venturebeat", "mit technology review", "nvidia", "openai")):
        score += 15
    if any(term in text for term in ("openai", "anthropic", "deepmind", "meta ai", "microsoft", "nvidia", "apple")):
        score += 15
    if any(term in text for term in ("阿里", "通义", "百度", "文心", "腾讯", "混元", "字节", "豆包", "华为", "盘古", "deepseek", "智谱", "minimax", "月之暗面")):
        score += 10
    if tags & {"ai_agent", "agentic_ai", "agent"}:
        score += 22
    if tags & {"coding", "coding_agent", "codex"}:
        score += 22
    if tags & {"mcp", "workflow_automation"}:
        score += 18
    if tags & {"org_productivity", "ai_transformation", "engineering_productivity", "developer_productivity", "knowledge_management"}:
        score += 18
    if tags & {"ai_code_review", "ai_testing", "ci_cd"}:
        score += 18
    if tags & {"automotive"} and tags & {"engineering_productivity", "ai_testing", "ci_cd", "ai_code_review", "coding_agent", "workflow_automation"}:
        score += 10
    if tags & {"obc", "dcdc"} and ("ai" in text or "人工智能" in text or "智能" in text):
        score += 20
    if tags & {"power_electronics", "sic", "gan", "power_control"} and ("ai" in text or "人工智能" in text or "智能" in text):
        score += 16
    if tags & {"fault_diagnosis", "predictive_maintenance", "digital_twin", "simulation_optimization"} and ("ai" in text or "人工智能" in text or "智能" in text):
        score += 14
    if POWER_ELECTRONICS_BOOST and tags & {"obc", "dcdc", "power_electronics", "sic", "gan", "power_control"}:
        score += 8
    # Penalize articles that mention OBC/DCDC but only have generic EV noise terms
    if tags & {"obc", "dcdc", "power_electronics", "sic", "gan"} and any(term in text for term in EV_NOISE_TERMS):
        score -= 25
    if "chip" in tags:
        score += 12
    if tags & {"chip"} and any(term in text for term in ("inference", "edge ai", "边缘 ai", "车载算力", "ai 芯片", "推理")):
        score += 16
    if tags & {"research", "opensource"}:
        score += 12
    if "autonomous_driving" in tags:
        score += 4
    if "smart_cockpit" in tags:
        score += 4
    if item.get("source_group") == "auto_china":
        score += 5
    if item.get("language") == "zh":
        score += 2
    if is_google_news_link(str(item.get("link", ""))) or "google news" in source:
        score -= 3
    if any(term in text for term in ("转载", "编译自", "综合自")) and tags & {"company_research", "official_report", "technical_blog", "whitepaper"}:
        score -= 8
    if any(term in text for term in ("引用报告", "据报告", "根据白皮书")) and not is_official_or_primary_source(item):
        score -= 10
    if any(term in text for term in WEAK_BUSINESS_TERMS):
        score -= 20
    if any(term in text for term in ("发布会", "车型发布", "上市发布", "语音助手", "普通座舱", "销量", "价格战", "财报", "评级", "券商")):
        score -= 20
    if is_stock_market_noise(item):
        score -= 40
    if len(clean_text(item.get("summary", ""))) < 30:
        score -= 5
    return score


def is_stock_market_noise(item: dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    source = clean_text(item.get("source", "")).lower()
    tags = set(item.get("topic_tags", []))
    has_market_term = any(term in text for term in WEAK_BUSINESS_TERMS)
    finance_source = any(term in source for term in FINANCE_SOURCE_TERMS)
    strong_tech = bool(tags & STRONG_TECH_TAGS)

    ticker_like = bool(re.search(r"\([A-Z]{1,5}\)", f"{item.get('title', '')} {item.get('summary', '')}"))
    if (has_market_term or ticker_like) and not strong_tech:
        return True
    if finance_source and (has_market_term or ticker_like) and not (
        tags
        & {
            "chip",
            "autonomous_driving",
            "smart_cockpit",
            "coding",
            "coding_agent",
            "llm",
            "agent",
            "ai_agent",
            "org_productivity",
            "power_electronics",
            "obc",
            "dcdc",
            "company_research",
        }
    ):
        return True
    return False


def is_relevant_ai_auto(item: dict[str, Any]) -> bool:
    tags = set(item.get("topic_tags", []))
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if is_stock_market_noise(item):
        return False
    if tags:
        if tags & {
            "llm",
            "agent",
            "ai_agent",
            "agentic_ai",
            "coding",
            "coding_agent",
            "mcp",
            "codex",
            "org_productivity",
            "ai_transformation",
            "engineering_productivity",
            "workflow_automation",
            "developer_productivity",
            "ai_code_review",
            "ai_testing",
            "ci_cd",
            "chip",
            "power_electronics",
            "obc",
            "dcdc",
            "sic",
            "gan",
            "power_control",
            "fault_diagnosis",
            "predictive_maintenance",
            "digital_twin",
            "simulation_optimization",
            "company_research",
            "official_report",
            "whitepaper",
            "technical_blog",
            "research_report",
            "robotics",
            "autonomous_driving",
            "smart_cockpit",
            "research",
            "opensource",
            "security",
            "regulation",
            "multimodal",
        }:
            return True
    if any(term in text for term in WEAK_BUSINESS_TERMS) and not tags:
        return False
    return False


def better_duplicate_choice(old_item: dict[str, Any], new_item: dict[str, Any]) -> dict[str, Any]:
    old_score = score_item(classify_item(old_item))
    new_score = score_item(classify_item(new_item))
    old_rank = source_preference_rank(classify_item(old_item))
    new_rank = source_preference_rank(classify_item(new_item))
    quality_rank = {"": 0, "empty": 0, "low": 1, "medium": 2, "high": 3}
    if new_rank != old_rank:
        return new_item if new_rank > old_rank else old_item
    old_primary = is_official_or_primary_source(old_item) and not is_google_news_link(str(old_item.get("link", "")))
    new_primary = is_official_or_primary_source(new_item) and not is_google_news_link(str(new_item.get("link", "")))
    if new_primary != old_primary:
        return new_item if new_primary else old_item
    old_source_quality = quality_rank.get(clean_text(old_item.get("source_quality", "")).lower(), 0)
    new_source_quality = quality_rank.get(clean_text(new_item.get("source_quality", "")).lower(), 0)
    if new_source_quality != old_source_quality:
        return new_item if new_source_quality > old_source_quality else old_item
    old_summary_quality = quality_rank.get(clean_text(old_item.get("summary_quality", "")).lower(), 0)
    new_summary_quality = quality_rank.get(clean_text(new_item.get("summary_quality", "")).lower(), 0)
    if new_summary_quality != old_summary_quality:
        return new_item if new_summary_quality > old_summary_quality else old_item
    if len(clean_text(new_item.get("summary", ""))) != len(clean_text(old_item.get("summary", ""))):
        return new_item if len(clean_text(new_item.get("summary", ""))) > len(clean_text(old_item.get("summary", ""))) else old_item
    if new_score != old_score:
        return new_item if new_score > old_score else old_item
    old_google = is_google_news_link(str(old_item.get("link", "")))
    new_google = is_google_news_link(str(new_item.get("link", "")))
    if new_google != old_google:
        return old_item if new_google else new_item
    old_dt = old_item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
    new_dt = new_item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
    return new_item if new_dt > old_dt else old_item


def deduplicate_news_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    by_title: dict[str, int] = {}
    by_link: dict[str, int] = {}
    by_source: dict[str, set[int]] = {}
    token_index: dict[str, set[int]] = {}
    duplicate_count = 0

    def index_item(idx: int, item: dict[str, Any]) -> None:
        title_key = normalize_title(str(item.get("title", "")))
        link_key = normalize_link(str(item.get("link", "")))
        if title_key:
            by_title[title_key] = idx
        if link_key and not is_google_news_link(link_key):
            by_link[link_key] = idx
        by_source.setdefault(source_identity_key(item), set()).add(idx)
        for token in duplicate_index_tokens(item):
            token_index.setdefault(token, set()).add(idx)

    for item in items:
        key = normalize_title(str(item.get("title", "")))
        link_key = normalize_link(str(item.get("link", "")))
        match_index: int | None = None
        if link_key and link_key in by_link:
            match_index = by_link[link_key]
        elif key and key in by_title:
            match_index = by_title[key]
        else:
            candidate_indexes: set[int] = set()
            candidate_indexes.update(by_source.get(source_identity_key(item), set()))
            for token in duplicate_index_tokens(item):
                candidate_indexes.update(token_index.get(token, set()))
            if not candidate_indexes and selected:
                candidate_indexes = set(range(max(0, len(selected) - 30), len(selected)))
            if len(candidate_indexes) > 180:
                candidate_indexes = set(sorted(candidate_indexes, reverse=True)[:180])
            for idx in sorted(candidate_indexes):
                if likely_same_story(item, selected[idx]):
                    match_index = idx
                    break
        if match_index is None:
            selected.append(item)
            index_item(len(selected) - 1, item)
            continue
        duplicate_count += 1
        selected[match_index] = better_duplicate_choice(selected[match_index], item)
        index_item(match_index, selected[match_index])
    return selected, duplicate_count


def load_history() -> dict[str, Any]:
    if not HISTORY_PATH.exists():
        return {"updated_at": "", "timezone": TIMEZONE_NAME, "retention_days": HISTORY_RETENTION_DAYS, "items": []}
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError("invalid history structure")
        return data
    except Exception as exc:
        LOGGER.warning("Load history failed, start with empty history: %s", exc)
        return {"updated_at": "", "timezone": TIMEZONE_NAME, "retention_days": HISTORY_RETENTION_DAYS, "items": []}


def save_history(history: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def history_item_recent(history_item: dict[str, Any], target_date: str, days: int) -> bool:
    target_dt = parse_date(target_date)
    last_dt = parse_date(str(history_item.get("last_seen_date") or history_item.get("first_seen_date") or ""))
    if not target_dt or not last_dt:
        return False
    delta_days = (target_dt - last_dt).days
    return 0 < delta_days <= days


def prune_history(history: dict[str, Any], target_date: str) -> dict[str, Any]:
    target_dt = parse_date(target_date)
    if not target_dt:
        return history
    kept = []
    for item in history.get("items", []):
        first_dt = parse_date(str(item.get("first_seen_date", "")))
        last_dt = parse_date(str(item.get("last_seen_date", "")))
        anchor = last_dt or first_dt
        if anchor and (target_dt - anchor).days <= HISTORY_RETENTION_DAYS:
            kept.append(item)
    history["items"] = kept
    history["retention_days"] = HISTORY_RETENTION_DAYS
    return history


def has_new_development_signal(item: dict[str, Any], matched_history_item: dict[str, Any] | None) -> bool:
    if not matched_history_item:
        return False
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    old_text = str(matched_history_item.get("title", "")).lower()
    has_signal = any(term in text for term in NEW_DEVELOPMENT_TERMS)
    return has_signal and simple_title_similarity(text, old_text) < 0.95


def should_prefer_over_history_duplicate(item: dict[str, Any], matched_history_item: dict[str, Any] | None) -> bool:
    if not matched_history_item:
        return False
    new_rank = source_preference_rank(item)
    old_rank = source_preference_rank(matched_history_item)
    return new_rank >= 45 and new_rank > old_rank


def is_seen_in_history(
    item: dict[str, Any],
    history: dict[str, Any],
    target_date: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    canonical_key = make_canonical_key(item)
    normalized_title = normalize_title(str(item.get("title", "")))
    normalized_link = normalize_link(str(item.get("link", "")))
    item_for_match = dict(item)
    item_for_match["event_signature"] = build_event_signature(item)
    for history_item in history.get("items", []):
        if not isinstance(history_item, dict) or not history_item_recent(history_item, target_date, HISTORY_DEDUPE_DAYS):
            continue
        if canonical_key and canonical_key == history_item.get("canonical_key"):
            return True, "same_canonical_key", history_item
        old_title = str(history_item.get("normalized_title", ""))
        history_match_item = {
            "title": history_item.get("title") or old_title,
            "summary": history_item.get("summary", ""),
            "source": history_item.get("source", ""),
            "link": history_item.get("link", history_item.get("normalized_link", "")),
            "category": history_item.get("category", ""),
            "source_group": history_item.get("source_group", ""),
            "source_type": history_item.get("source_type", ""),
            "topic_tags": history_item.get("topic_tags", []),
            "event_signature": history_item.get("event_signature", {}),
        }
        if old_title and normalized_title and simple_title_similarity(normalized_title, old_title) > HISTORY_SIMILARITY_THRESHOLD:
            return True, "similar_title", history_item
        old_link = str(history_item.get("normalized_link", ""))
        if normalized_link and normalized_link == old_link:
            return True, "same_link", history_item
        if event_signature_match(item_for_match, history_match_item, high_confidence=True):
            return True, "same_event_signature", history_item
    return False, "", None


def update_history_with_items(history: dict[str, Any], selected_items: list[dict[str, Any]], target_date: str, now_local: datetime) -> dict[str, Any]:
    by_key = {
        str(item.get("canonical_key", "")): item
        for item in history.get("items", [])
        if isinstance(item, dict) and item.get("canonical_key")
    }
    for item in selected_items:
        canonical_key = make_canonical_key(item)
        existing = by_key.get(canonical_key)
        if existing:
            existing["last_seen_date"] = target_date
            existing["seen_count"] = int(existing.get("seen_count", 1)) + 1
            existing["event_signature"] = build_event_signature(item)
            continue
        record = {
            "first_seen_date": target_date,
            "last_seen_date": target_date,
            "canonical_key": canonical_key,
            "normalized_title": normalize_title(str(item.get("title", ""))),
            "normalized_link": normalize_link(str(item.get("link", ""))),
            "title": clean_text(item.get("title", "")),
            "summary": truncate_summary(str(item.get("summary", "")), 300),
            "source": clean_text(item.get("source", "")),
            "link": clean_text(item.get("link", "")),
            "category": clean_text(item.get("category", "")),
            "region": clean_text(item.get("region", "")),
            "source_group": clean_text(item.get("source_group", "")),
            "source_type": clean_text(item.get("source_type", "")),
            "topic_tags": list(item.get("topic_tags", []))[:12],
            "event_signature": build_event_signature(item),
            "seen_count": 1,
        }
        history.setdefault("items", []).append(record)
        by_key[canonical_key] = record
    history["updated_at"] = now_local.isoformat()
    history["timezone"] = TIMEZONE_NAME
    history["retention_days"] = HISTORY_RETENTION_DAYS
    return history


def load_learning_history() -> dict[str, Any]:
    if not LEARNING_HISTORY_PATH.exists():
        return {
            "updated_at": "",
            "timezone": TIMEZONE_NAME,
            "retention_days": LEARNING_HISTORY_RETENTION_DAYS,
            "items": [],
        }
    try:
        data = json.loads(LEARNING_HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError("invalid learning history structure")
        return data
    except Exception as exc:
        LOGGER.warning("Load learning history failed, start with empty history: %s", exc)
        return {
            "updated_at": "",
            "timezone": TIMEZONE_NAME,
            "retention_days": LEARNING_HISTORY_RETENTION_DAYS,
            "items": [],
        }


def save_learning_history(history: dict[str, Any]) -> None:
    LEARNING_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEARNING_HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def prune_learning_history(history: dict[str, Any], target_date: str) -> dict[str, Any]:
    target_dt = parse_date(target_date)
    if not target_dt:
        return history
    kept = []
    for item in history.get("items", []):
        last_dt = parse_date(str(item.get("last_seen_date") or item.get("first_seen_date") or ""))
        if last_dt and (target_dt - last_dt).days <= LEARNING_HISTORY_RETENTION_DAYS:
            kept.append(item)
    history["items"] = kept
    history["retention_days"] = LEARNING_HISTORY_RETENTION_DAYS
    return history


def is_seen_in_learning_history(
    item: dict[str, Any],
    history: dict[str, Any],
    target_date: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    canonical_key = make_canonical_key(item)
    normalized_title = normalize_title(str(item.get("title", "")))
    normalized_link = normalize_link(str(item.get("link", "")))
    item_for_match = dict(item)
    item_for_match["event_signature"] = build_event_signature(item)
    for history_item in history.get("items", []):
        if not isinstance(history_item, dict):
            continue
        if not history_item_recent(history_item, target_date, LEARNING_HISTORY_DEDUPE_DAYS):
            continue
        if canonical_key and canonical_key == history_item.get("canonical_key"):
            return True, "same_canonical_key", history_item
        old_link = str(history_item.get("normalized_link", ""))
        if normalized_link and normalized_link == old_link:
            return True, "same_link", history_item
        old_title = str(history_item.get("normalized_title", ""))
        history_match_item = {
            "title": history_item.get("title") or old_title,
            "summary": history_item.get("summary", ""),
            "source": history_item.get("source", ""),
            "link": history_item.get("link", history_item.get("normalized_link", "")),
            "source_type": history_item.get("source_type", ""),
            "source_quality": history_item.get("source_quality", ""),
            "summary_quality": history_item.get("summary_quality", ""),
            "tags": history_item.get("tags", []),
            "topic_tags": history_item.get("topic_tags", []),
            "concept_hint": history_item.get("concept_hint", ""),
            "event_signature": history_item.get("event_signature", {}),
        }
        if old_title and normalized_title and simple_title_similarity(normalized_title, old_title) > LEARNING_HISTORY_SIMILARITY_THRESHOLD:
            return True, "similar_title", history_item
        if event_signature_match(item_for_match, history_match_item, high_confidence=True):
            return True, "same_event_signature", history_item
    return False, "", None


def update_learning_history_with_items(
    history: dict[str, Any],
    selected_items: list[dict[str, Any]],
    target_date: str,
    now_local: datetime,
) -> dict[str, Any]:
    by_key = {
        str(item.get("canonical_key", "")): item
        for item in history.get("items", [])
        if isinstance(item, dict) and item.get("canonical_key")
    }
    for item in selected_items:
        canonical_key = make_canonical_key(item)
        existing = by_key.get(canonical_key)
        if existing:
            existing["last_seen_date"] = target_date
            existing["seen_count"] = int(existing.get("seen_count", 1)) + 1
            existing["event_signature"] = build_event_signature(item)
            continue
        record = {
            "first_seen_date": target_date,
            "last_seen_date": target_date,
            "canonical_key": canonical_key,
            "normalized_title": normalize_title(str(item.get("title", ""))),
            "normalized_link": normalize_link(str(item.get("link", ""))),
            "title": clean_text(item.get("title", "")),
            "summary": truncate_summary(str(item.get("summary", "")), 300),
            "source": clean_text(item.get("source", "")),
            "link": clean_text(item.get("link", "")),
            "source_type": clean_text(item.get("source_type", "")),
            "source_quality": clean_text(item.get("source_quality", "")),
            "summary_quality": clean_text(item.get("summary_quality", "")),
            "concept_hint": clean_text(item.get("concept_hint", "")),
            "is_official_source": bool(item.get("is_official_source", False)),
            "language": clean_text(item.get("language", "")),
            "region": clean_text(item.get("region", "")),
            "tags": list(item.get("tags", []))[:12],
            "event_signature": build_event_signature(item),
            "seen_count": 1,
        }
        history.setdefault("items", []).append(record)
        by_key[canonical_key] = record
    history["updated_at"] = now_local.isoformat()
    history["timezone"] = TIMEZONE_NAME
    history["retention_days"] = LEARNING_HISTORY_RETENTION_DAYS
    return history


def select_curated_learning_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rejected_stats = {
        "same_source_limit": 0,
        "similar_selected": 0,
        "google_or_youtube_deprioritized": 0,
        "low_quality_learning_source": 0,
    }
    selected: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    has_official_concept_resource = any(
        clean_text(item.get("source_type", "")).lower() == "official_doc"
        and clean_text(item.get("source_quality", "")).lower() == "high"
        and clean_text(item.get("summary_quality", "")).lower() == "high"
        and bool(item.get("is_official_source", False))
        for item in items
    )

    def learning_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, int, datetime]:
        source_type = clean_text(item.get("source_type", "")).lower()
        source_quality = clean_text(item.get("source_quality", "")).lower()
        source_type_weight = {
            "official_doc": 10,
            "github_repo": 8,
            "official_blog": 6,
            "technical_blog": 4,
            "official_video": 1,
            "tutorial": 2,
            "media_article": -2,
            "google_news": -4,
        }.get(source_type, 0)
        quality_weight = {"high": 3, "medium": 1, "low": -3}.get(source_quality, 0)
        official_weight = 5 if item.get("is_official_source") else 0
        language_weight = 1 if item.get("language") == "zh" else 0
        dt = item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        return official_weight, source_type_weight, quality_weight, int(item.get("_score", item.get("score", 0))), language_weight, dt

    for item in sorted(items, key=learning_sort_key, reverse=True):
        source_key = source_identity_key(item) or "unknown"
        if source_counts.get(source_key, 0) >= MAX_SAME_SOURCE_LEARNING:
            rejected_stats["same_source_limit"] += 1
            continue
        source_type = clean_text(item.get("source_type", "")).lower()
        summary_quality = clean_text(item.get("summary_quality", "")).lower()
        if has_official_concept_resource and (source_type == "google_news" or is_youtube_learning_item(item)):
            rejected_stats["google_or_youtube_deprioritized"] += 1
            continue
        if source_type == "google_news" and (
            summary_quality not in {"medium", "high"}
            or is_google_news_source_text(clean_text(item.get("source", "")), clean_text(item.get("link", "")))
        ):
            rejected_stats["low_quality_learning_source"] += 1
            continue
        if is_youtube_learning_item(item) and summary_quality not in {"medium", "high"}:
            rejected_stats["low_quality_learning_source"] += 1
            continue
        if any(likely_same_story(item, existing, base_threshold=min(LEARNING_HISTORY_SIMILARITY_THRESHOLD, 0.78)) for existing in selected):
            rejected_stats["similar_selected"] += 1
            continue
        chosen = dict(item)
        chosen["score"] = int(chosen.get("_score", chosen.get("score", 0)))
        chosen["canonical_key"] = make_canonical_key(chosen)
        chosen["dedupe_key"] = normalize_title(str(chosen.get("title", "")))
        chosen["concept_hint"] = clean_text(chosen.get("concept_hint", "")) or concept_hint_from_tags(chosen.get("tags", []))
        chosen["selection_reason"] = "learning_high_score"
        selected.append(chosen)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        if len(selected) >= TARGET_LEARNING_CANDIDATE_COUNT:
            break

    return selected, rejected_stats


def is_global_candidate(item: dict[str, Any]) -> bool:
    return item.get("region") == "global" or item.get("category") in {"全球AI", "AI芯片", "AI编程工具", "AI Agent", "官方研究", "技术报告", "白皮书", "研究论文", "开源模型"}


def is_agent_candidate(item: dict[str, Any]) -> bool:
    tags = set(item.get("topic_tags", []))
    return item.get("category") in {"AI Agent", "AI编程工具"} or bool(tags & {"ai_agent", "agentic_ai", "coding_agent", "mcp", "codex", "ai_code_review", "ai_testing"})


def is_productivity_candidate(item: dict[str, Any]) -> bool:
    tags = set(item.get("topic_tags", []))
    return item.get("category") in {"AI组织提效", "AI工作流自动化"} or bool(tags & {"org_productivity", "ai_transformation", "engineering_productivity", "workflow_automation", "knowledge_management", "developer_productivity", "ci_cd"})


def is_company_research_candidate(item: dict[str, Any]) -> bool:
    tags = set(item.get("topic_tags", []))
    return item.get("category") in {"官方研究", "技术报告", "白皮书"} or clean_text(item.get("source_group", "")).lower() in {"company_research", "official_research", "technical_report", "whitepaper", "company_blog"} or bool(tags & {"company_research", "official_report", "whitepaper", "technical_blog", "research_report"})


def is_power_candidate(item: dict[str, Any]) -> bool:
    tags = set(item.get("topic_tags", []))
    # Require actual power electronics tags
    power_tags = {"power_electronics", "obc", "dcdc", "sic", "gan", "power_control", "fault_diagnosis", "predictive_maintenance", "digital_twin", "simulation_optimization"}
    if not (tags & power_tags):
        return False
    # Reject if only has generic automotive tag without power electronics specifics
    if "automotive" in tags and not (tags & {"power_electronics", "obc", "dcdc", "sic", "gan", "power_control"}):
        return False
    return True


def is_auto_driving_candidate(item: dict[str, Any]) -> bool:
    return item.get("category") == "自动驾驶" or "autonomous_driving" in set(item.get("topic_tags", []))


def is_smart_cockpit_candidate(item: dict[str, Any]) -> bool:
    return item.get("category") == "智能座舱" or "smart_cockpit" in set(item.get("topic_tags", []))


def select_curated_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rejected_stats = {
        "same_source_limit": 0,
        "same_entity_limit": 0,
        "similar_selected": 0,
        "china_limit": 0,
        "auto_china_limit": 0,
        "company_research_limit": 0,
        "auto_driving_limit": 0,
        "smart_cockpit_limit": 0,
        "no_agent_candidate": 0,
        "no_productivity_candidate": 0,
        "no_company_research_candidate": 0,
        "no_power_electronics_candidate": 0,
    }
    selected: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    entity_counts: dict[str, int] = {}
    china_count = 0
    auto_china_count = 0
    google_news_count = 0
    company_research_count = 0
    auto_driving_count = 0
    smart_cockpit_count = 0
    sorted_items = sorted(
        items,
        key=lambda item: (
            int(item.get("score", 0)),
            source_preference_rank(item),
            item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    def can_add(
        item: dict[str, Any],
        enforce_region_limits: bool = True,
        enforce_topic_limits: bool = True,
    ) -> tuple[bool, str]:
        nonlocal china_count, auto_china_count, google_news_count, company_research_count, auto_driving_count, smart_cockpit_count
        source = source_identity_key(item) or "unknown"
        if source_counts.get(source, 0) >= MAX_SAME_SOURCE_NEWS:
            return False, "source_limit_applied"
        entity = primary_entity_key(item)
        if entity and entity_counts.get(entity, 0) >= MAX_SAME_ENTITY_NEWS:
            return False, "same_entity_limit"
        source_group = clean_text(item.get("source_group", "")).lower()
        is_china = item.get("region") == "china" or source_group in ("china", "auto_china")
        is_auto = source_group == "auto_china" or item.get("category") == "中国汽车"
        is_company = is_company_research_candidate(item)
        is_auto_driving = is_auto_driving_candidate(item)
        is_smart_cockpit = is_smart_cockpit_candidate(item)
        if enforce_region_limits and is_china and china_count >= MAX_CHINA_NEWS:
            return False, "china_limit"
        if enforce_region_limits and is_auto and auto_china_count >= MAX_AUTO_CHINA_NEWS:
            return False, "auto_china_limit"
        if enforce_topic_limits and is_company and company_research_count >= MAX_COMPANY_RESEARCH_NEWS:
            return False, "company_research_limit"
        if enforce_topic_limits and is_auto_driving and not (is_agent_candidate(item) or is_productivity_candidate(item) or is_power_candidate(item) or "chip" in set(item.get("topic_tags", []))) and auto_driving_count >= MAX_AUTO_DRIVING_NEWS:
            return False, "auto_driving_limit"
        if enforce_topic_limits and is_smart_cockpit and not (is_agent_candidate(item) or is_productivity_candidate(item) or is_power_candidate(item) or "chip" in set(item.get("topic_tags", []))) and smart_cockpit_count >= MAX_SMART_COCKPIT_NEWS:
            return False, "smart_cockpit_limit"
        google_news_limit = TARGET_CANDIDATE_COUNT
        if is_google_news_link(str(item.get("link", ""))) and google_news_count >= google_news_limit:
            return False, "source_limit_applied"
        return True, ""

    def add_item(
        item: dict[str, Any],
        reason: str,
        enforce_region_limits: bool = True,
        enforce_topic_limits: bool = True,
    ) -> bool:
        nonlocal china_count, auto_china_count, google_news_count, company_research_count, auto_driving_count, smart_cockpit_count
        if any(item.get("canonical_key") == existing.get("canonical_key") for existing in selected):
            return False
        if any(likely_same_story(item, existing) for existing in selected):
            rejected_stats["similar_selected"] += 1
            return False
        ok, rejected_reason = can_add(
            item,
            enforce_region_limits=enforce_region_limits,
            enforce_topic_limits=enforce_topic_limits,
        )
        if not ok:
            rejected_stats[rejected_reason] = rejected_stats.get(rejected_reason, 0) + 1
            return False
        chosen = dict(item)
        chosen["selection_reason"] = reason
        selected.append(chosen)
        source = source_identity_key(chosen) or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1
        entity = primary_entity_key(chosen)
        if entity:
            entity_counts[entity] = entity_counts.get(entity, 0) + 1
        source_group = clean_text(chosen.get("source_group", "")).lower()
        if chosen.get("region") == "china" or source_group in ("china", "auto_china"):
            china_count += 1
        if source_group == "auto_china" or chosen.get("category") == "中国汽车":
            auto_china_count += 1
        if is_google_news_link(str(chosen.get("link", ""))):
            google_news_count += 1
        if is_company_research_candidate(chosen):
            company_research_count += 1
        if is_auto_driving_candidate(chosen):
            auto_driving_count += 1
        if is_smart_cockpit_candidate(chosen):
            smart_cockpit_count += 1
        return True

    def add_quota(predicate: Any, target: int, reason: str, enforce_topic_limits: bool = True) -> None:
        if target <= 0:
            return
        for item in sorted_items:
            if len(selected) >= TARGET_CANDIDATE_COUNT:
                break
            if len([i for i in selected if predicate(i)]) >= target:
                break
            if predicate(item):
                add_item(item, reason, enforce_topic_limits=enforce_topic_limits)

    if not any(is_agent_candidate(item) for item in sorted_items):
        rejected_stats["no_agent_candidate"] = 1
    if not any(is_productivity_candidate(item) for item in sorted_items):
        rejected_stats["no_productivity_candidate"] = 1
    if not any(is_company_research_candidate(item) for item in sorted_items):
        rejected_stats["no_company_research_candidate"] = 1
    if not any(is_power_candidate(item) for item in sorted_items):
        rejected_stats["no_power_electronics_candidate"] = 1

    add_quota(is_agent_candidate, MIN_AGENT_NEWS, "required_agent_or_coding")
    add_quota(is_productivity_candidate, MIN_PRODUCTIVITY_NEWS, "required_productivity")
    if POWER_ELECTRONICS_BOOST:
        add_quota(is_power_candidate, 1, "power_electronics_boost", enforce_topic_limits=False)
    add_quota(is_company_research_candidate, MIN_COMPANY_RESEARCH_NEWS, "required_company_research", enforce_topic_limits=False)
    add_quota(is_global_candidate, MIN_GLOBAL_NEWS, "required_global_quota")

    for item in sorted_items:
        if len(selected) >= TARGET_CANDIDATE_COUNT:
            break
        source_group = clean_text(item.get("source_group", "")).lower()
        if item.get("region") == "china" or source_group in ("china", "auto_china"):
            reason = "auto_china_quota" if source_group == "auto_china" else "china_quota"
            add_item(item, reason)

    for item in sorted_items:
        if len(selected) >= TARGET_CANDIDATE_COUNT:
            break
        missing_reasons = [
            key
            for key in (
                "no_agent_candidate",
                "no_productivity_candidate",
                "no_company_research_candidate",
                "no_power_electronics_candidate",
            )
            if rejected_stats.get(key)
        ]
        reason = "fill_remaining_high_score"
        if missing_reasons:
            reason += ";" + ";".join(missing_reasons)
        add_item(item, reason)

    for item in sorted_items:
        if len(selected) >= TARGET_CANDIDATE_COUNT:
            break
        add_item(item, "fill_remaining_relaxed_topic_limits", enforce_topic_limits=False)

    return selected[:TARGET_CANDIDATE_COUNT], rejected_stats


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


def find_text_by_local_names(element: ET.Element, names: list[str]) -> str:
    names_set = {name.lower() for name in names}
    for child in element:
        child_name = local_name(child.tag)
        if child_name in names_set and child.text and child.text.strip():
            return clean_html_text(child.text)
    return ""


def extract_item_source_name(entry: ET.Element) -> str:
    for child in entry:
        if local_name(child.tag) == "source":
            if child.text and child.text.strip():
                return clean_text(child.text)
            title = find_first_text(child, ["title"])
            if title:
                return title
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


def summary_quality_for_text(summary: str, summary_source: str, link: str = "", is_official: bool = False) -> str:
    text = clean_html_text(summary)
    if not text:
        return "empty"
    if is_google_news_link(link):
        return "medium" if len(text) >= 80 else "low"
    if len(text) < 60:
        return "low"
    if summary_source in {"rss_content_encoded", "atom_content", "page_meta_description", "page_og_description"}:
        return "high" if len(text) >= 120 or is_official else "medium"
    if summary_source in {"rss_description", "atom_summary"}:
        return "medium" if len(text) >= 80 else "low"
    if summary_source == "page_title":
        return "low"
    return "medium" if len(text) >= 100 else "low"


def make_summary_meta(summary: str, summary_source: str, link: str = "", is_official: bool = False) -> dict[str, str]:
    text = truncate_summary(summary, 800)
    return {
        "summary": text,
        "summary_source": summary_source if text else "empty",
        "summary_quality": summary_quality_for_text(text, summary_source, link=link, is_official=is_official),
    }


def extract_item_summary(entry: ET.Element, link: str = "", is_official: bool = False) -> dict[str, str]:
    candidates: list[tuple[str, str]] = []

    rss_description = find_text_by_local_names(entry, ["description"])
    if rss_description:
        candidates.append(("rss_description", rss_description))

    rss_content = find_text_by_local_names(entry, ["encoded", "content:encoded"])
    if rss_content:
        candidates.append(("rss_content_encoded", rss_content))

    rss_summary = find_text_by_local_names(entry, ["summary"])
    if rss_summary:
        candidates.append(("atom_summary", rss_summary))

    atom_content = find_text_by_local_names(entry, ["content"])
    if atom_content:
        candidates.append(("atom_content", atom_content))

    atom_subtitle = find_text_by_local_names(entry, ["subtitle"])
    if atom_subtitle:
        candidates.append(("atom_summary", atom_subtitle))

    for child in entry:
        if local_name(child.tag) == "group":
            media_description = find_text_by_local_names(child, ["description", "summary"])
            if media_description:
                candidates.append(("rss_description", media_description))

    if not candidates:
        return make_summary_meta("", "empty", link=link, is_official=is_official)

    source_rank = {
        "rss_content_encoded": 6,
        "atom_content": 6,
        "page_meta_description": 5,
        "page_og_description": 5,
        "rss_description": 4,
        "atom_summary": 4,
        "page_title": 1,
    }
    best_source, best_text = max(
        candidates,
        key=lambda pair: (
            {"high": 3, "medium": 2, "low": 1, "empty": 0}[summary_quality_for_text(pair[1], pair[0], link=link, is_official=is_official)],
            source_rank.get(pair[0], 0),
            len(clean_html_text(pair[1])),
        ),
    )
    return make_summary_meta(best_text, best_source, link=link, is_official=is_official)


def extract_entry_summary(entry: ET.Element) -> str:
    return extract_item_summary(entry).get("summary", "")


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


def should_skip_page_summary_fetch(url: str) -> bool:
    parsed = urlparse(clean_text(url))
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if not parsed.scheme.startswith("http"):
        return True
    if host in GOOGLE_NEWS_HOSTS:
        return True
    if any(host_part in host for host_part in ("youtube.com", "youtu.be")):
        return True
    if re.search(r"\.(pdf|png|jpg|jpeg|gif|webp|svg|mp4|mov|avi|zip|rar)(\?|$)", path):
        return True
    return False


def fetch_page_summary(
    url: str,
    timeout: int = PAGE_SUMMARY_TIMEOUT_SECONDS,
    is_official: bool = False,
) -> dict[str, str]:
    if should_skip_page_summary_fetch(url):
        return make_summary_meta("", "empty", link=url, is_official=is_official)

    request = Request(
        url,
        headers={
            "User-Agent": "ai-news-feishu-bot-summary-fetcher/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and "html" not in content_type and "text/plain" not in content_type:
                return make_summary_meta("", "empty", link=url, is_official=is_official)
            raw = response.read(PAGE_SUMMARY_MAX_BYTES + 1)[:PAGE_SUMMARY_MAX_BYTES]
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        LOGGER.warning("Page summary fetch failed %s: %s", url, exc)
        return make_summary_meta("", "empty", link=url, is_official=is_official)

    html_text = raw.decode("utf-8", errors="replace")
    title, description, description_source = extract_html_metadata(html_text)
    if description:
        return make_summary_meta(description, description_source, link=url, is_official=is_official)
    if title:
        return make_summary_meta(title, "page_title", link=url, is_official=is_official)
    return make_summary_meta("", "empty", link=url, is_official=is_official)


def parse_feed_entries(source_info: dict[str, Any], xml_bytes: bytes, tz: timezone) -> list[dict[str, Any]]:
    source_name = source_info["name"]
    source_language = source_info["language"]
    source_region = source_info["region"]
    source_group = source_info["source_group"]
    source_type = source_info.get("source_type", "")
    source_tags = list(source_info.get("tags", []))
    source_quality = source_info.get("source_quality", "")
    is_official_source = bool(source_info.get("is_official_source", False))

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
    page_summary_fetch_count = 0
    for elem in channel_or_feed.iter():
        if local_name(elem.tag) not in ("item", "entry"):
            continue
        count += 1
        if count > MAX_ENTRIES_PER_FEED:
            break

        title = find_first_text(elem, ["title"])
        link = extract_link(elem)
        raw_published = find_first_text(elem, ["pubdate", "published", "updated", "dc:date"])
        published_dt = parse_published_at(raw_published, tz) if raw_published else None

        title_clean = clean_text(title)
        link_clean = clean_text(link)
        if not title_clean or not link_clean:
            continue

        summary_meta = extract_item_summary(elem, link=link_clean, is_official=is_official_source)
        needs_page_summary = (
            len(summary_meta.get("summary", "")) < 60
            or (
                len(summary_meta.get("summary", "")) < 120
                and (
                    is_official_source
                    or source_group in {"company_research", "official_research", "technical_report", "whitepaper", "company_blog", "official", "official_page"}
                    or source_type in {"official_doc", "official_blog", "technical_blog", "whitepaper", "research_paper", "github_repo"}
                )
            )
        )
        if (
            needs_page_summary
            and page_summary_fetch_count < PAGE_SUMMARY_MAX_PER_FEED
            and not is_google_news_link(link_clean)
        ):
            page_summary_fetch_count += 1
            page_summary = fetch_page_summary(link_clean, is_official=is_official_source)
            if page_summary.get("summary") and (
                summary_meta.get("summary_quality") in {"empty", "low"}
                or len(page_summary.get("summary", "")) > len(summary_meta.get("summary", ""))
            ):
                summary_meta = page_summary

        item_source_name = extract_item_source_name(elem) or feed_title or source_name
        if is_google_news_link(link_clean) and item_source_name.lower().startswith("google news"):
            item_source_name = feed_title or source_name

        entries.append(
            {
                "title": title_clean,
                "summary": summary_meta.get("summary", "")[:800],
                "summary_source": summary_meta.get("summary_source", "empty"),
                "summary_quality": summary_meta.get("summary_quality", "empty"),
                "published_at": published_dt.isoformat() if published_dt else "",
                "source": clean_text(item_source_name),
                "link": link_clean,
                "language": source_language,
                "region": source_region,
                "source_group": source_group,
                "source_type": source_type,
                "source_quality": source_quality,
                "is_official_source": is_official_source,
                "tags": source_tags.copy(),
                "_published_dt": published_dt,
            }
        )
    return entries


def extract_html_metadata(html_text: str) -> tuple[str, str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    title = clean_html_text(title_match.group(1)) if title_match else ""

    meta_patterns = [
        ("page_meta_description", r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']'),
        ("page_og_description", r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']'),
        ("page_meta_description", r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\'](.*?)["\']'),
        ("page_meta_description", r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']'),
        ("page_og_description", r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:description["\']'),
        ("page_meta_description", r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']twitter:description["\']'),
    ]
    description = ""
    description_source = "empty"
    for source, pattern in meta_patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            description = clean_html_text(match.group(1))
            if description:
                description_source = source
                break

    return title, description, description_source


def keyword_hits(text: str, keywords: list[str]) -> int:
    value = text.lower()
    return sum(1 for keyword in keywords if keyword in value)


def contains_keyword(text: str, keyword: str) -> bool:
    needle = keyword.lower().strip()
    if not needle:
        return False
    if re.search(r"[\u4e00-\u9fff]", needle):
        return needle in text
    if len(needle) <= 4 and re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", needle):
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text) is not None
    return needle in text


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
        ("agents sdk", "agents_sdk"),
        ("agent workflow", "agent_workflow"),
        ("openai agents", "openai_agents"),
        ("tool calling", "tool_calling"),
        ("tool integration", "tool_integration"),
        ("orchestration", "orchestration"),
        ("handoffs", "handoffs"),
        ("guardrails", "guardrails"),
        ("human review", "human_in_the_loop"),
        ("human-in-the-loop", "human_in_the_loop"),
        ("approvals", "approvals"),
        ("evals", "evals"),
        ("evaluation", "agent_evaluation"),
        ("graders", "graders"),
        ("datasets", "datasets"),
        ("observability", "observability"),
        ("tracing", "tracing"),
        ("debugging", "debugging"),
        ("integrations", "integrations"),
        ("external tools", "external_tools"),
        ("agent instructions", "agent_instructions"),
        ("project context", "project_context"),
        ("coding agent", "coding_agent"),
        ("local agent", "local_agent"),
        ("multi-agent", "multi_agent"),
        ("context management", "context_management"),
        ("memory", "memory"),
        ("agent skills", "agent_skills"),
        ("reusable capabilities", "reusable_capabilities"),
        ("codex cli", "cli"),
        ("codex", "codex"),
        ("agent", "agent"),
        ("mcp", "mcp"),
        ("agents.md", "agents_md"),
        ("code review", "code_review"),
        ("workflow", "agent_workflow"),
        ("best practice", "best_practice"),
        ("教程", "tutorial"),
        ("工具调用", "tool_calling"),
        ("护栏", "guardrails"),
        ("人工审核", "human_in_the_loop"),
        ("可观测性", "observability"),
        ("工作流", "agent_workflow"),
        ("代码审查", "code_review"),
    ]
    lower = text.lower()
    for key, tag in mapping:
        if key in lower:
            out.add(tag)
    return sorted(out)


CONCEPT_HINT_RULES: list[tuple[set[str], str]] = [
    ({"agents_sdk", "agent_workflow", "openai_agents"}, "Agent Workflow"),
    ({"tool_calling", "tools", "tool_integration"}, "Tool Calling"),
    ({"guardrails", "safety", "approvals"}, "Guardrails"),
    ({"human_in_the_loop", "approvals"}, "Human-in-the-loop"),
    ({"evals", "agent_evaluation", "graders", "datasets"}, "Agent Evaluation"),
    ({"observability", "tracing", "debugging"}, "Observability"),
    ({"mcp", "integrations", "external_tools"}, "MCP"),
    ({"agents_md", "agent_instructions", "project_context"}, "Agent Instructions"),
    ({"codex", "coding_agent", "cli", "local_agent"}, "Coding Agent"),
    ({"orchestration", "handoffs", "multi_agent", "agent_orchestration"}, "Orchestration"),
    ({"state", "context_management", "memory"}, "Agent State"),
    ({"agent_skills", "reusable_capabilities"}, "Agent Skills"),
]


CONCEPT_TAGS = set().union(*(tags for tags, _ in CONCEPT_HINT_RULES))


def concept_hint_from_tags(tags: list[str] | set[str]) -> str:
    tag_set = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
    for needles, concept in CONCEPT_HINT_RULES:
        if tag_set & needles:
            return concept
    return "AI Agent" if "agent" in tag_set else ""


def is_youtube_learning_item(item: dict[str, Any]) -> bool:
    link = clean_text(item.get("link", "")).lower()
    source_type = clean_text(item.get("source_type", "")).lower()
    return source_type in {"official_video", "youtube_video"} or "youtube.com" in link or "youtu.be" in link


def has_operational_tutorial_bias(text: str, tags: set[str]) -> bool:
    operation_terms = (
        "prompt",
        "prompting",
        "tutorial",
        "how to",
        "step-by-step",
        "create pr",
        "pull request",
        "edit files",
        "modify code",
        "automate coding task",
        "教程",
        "提示词",
        "如何",
        "创建 pr",
        "修改代码",
        "自动改文件",
    )
    return any(term in text for term in operation_terms) and not bool(tags & CONCEPT_TAGS)


def learning_score(item: dict[str, Any]) -> tuple[int, int, list[str]]:
    source_type = str(item.get("source_type", "")).strip().lower()
    source_quality = str(item.get("source_quality", "")).strip().lower()
    summary_source = str(item.get("summary_source", "")).strip().lower()
    summary_quality = str(item.get("summary_quality", "")).strip().lower()
    is_official_source = bool(item.get("is_official_source", False))
    seed_tags = [str(tag).strip().lower() for tag in item.get("tags", []) if str(tag).strip()]
    text = f"{item['title']} {item.get('summary', '')} {item.get('source', '')}".lower()

    hits = 0
    score = 0
    tags = set(infer_learning_tags(text, seed_tags))
    for keyword, weight, tag in LEARNING_KEYWORD_RULES:
        if keyword in text:
            hits += 1
            score += weight
            tags.add(tag)

    if learning_is_excluded(text):
        score -= 8

    has_core = any(term in text for term in LEARNING_CORE_TERMS) or bool(tags.intersection(CONCEPT_TAGS | {"agent"}))
    if not has_core:
        return 0, 0, sorted(tags)

    # Reject broad ChatGPT-only guides that do not mention Codex/agent workflow context.
    if "chatgpt" in text and not any(term in text for term in ("codex", "agent", "cli", "mcp", "agents.md", "tool calling", "guardrails")):
        return 0, 0, sorted(tags)

    if source_type == "official_doc":
        score += 30
    elif source_type in {"official_blog", "github_repo"}:
        score += 18
    elif source_type == "technical_blog":
        score += 8
    elif source_type == "media_article":
        score -= 15
    elif source_type == "google_news":
        score -= 20

    if summary_source == "official_static_summary":
        score += 20
    if source_quality == "high":
        score += 15
    elif source_quality == "medium":
        score += 1
    elif source_quality == "low":
        score -= 8
    if summary_quality == "high":
        score += 15
    elif summary_quality == "medium":
        score += 4
    elif summary_quality == "low":
        score -= 10
    elif summary_quality == "empty":
        score -= 25
    if tags & {"agents_sdk", "agent_workflow", "guardrails", "evals", "observability", "tool_calling", "mcp"}:
        score += 15
    if tags & {"codex", "coding_agent"}:
        score += 10
    if is_youtube_learning_item(item):
        score -= 10
    if has_operational_tutorial_bias(text, tags):
        score -= 10
    if is_google_news_source_text(str(item.get("source", "")), str(item.get("link", ""))):
        score -= 20

    if item.get("language") == "zh":
        score += 1
    if item.get("region") == "china":
        score += 1

    return score, hits, sorted(tags)


def deduplicate_items_with_count(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    by_title: dict[str, int] = {}
    by_link: dict[str, int] = {}
    by_source: dict[str, set[int]] = {}
    token_index: dict[str, set[int]] = {}
    duplicate_count = 0

    def index_item(idx: int, item: dict[str, Any]) -> None:
        title_key = canonical_title_key(str(item.get("title", "")))
        link_key = normalize_link(str(item.get("link", "")))
        if title_key:
            by_title[title_key] = idx
        if link_key:
            by_link[link_key] = idx
        by_source.setdefault(source_identity_key(item), set()).add(idx)
        for token in duplicate_index_tokens(item):
            token_index.setdefault(token, set()).add(idx)

    for item in items:
        link_key = normalize_link(str(item.get("link", "")))
        title_key = canonical_title_key(str(item.get("title", "")))
        match_index: int | None = None

        if link_key and link_key in by_link:
            match_index = by_link[link_key]
        elif title_key and title_key in by_title:
            match_index = by_title[title_key]
        else:
            candidate_indexes: set[int] = set()
            candidate_indexes.update(by_source.get(source_identity_key(item), set()))
            for token in duplicate_index_tokens(item):
                candidate_indexes.update(token_index.get(token, set()))
            if len(candidate_indexes) > 120:
                candidate_indexes = set(sorted(candidate_indexes, reverse=True)[:120])
            for idx in sorted(candidate_indexes):
                if likely_same_story(item, selected[idx], base_threshold=min(LEARNING_HISTORY_SIMILARITY_THRESHOLD, 0.78)):
                    match_index = idx
                    break

        if match_index is None:
            selected.append(item)
            index_item(len(selected) - 1, item)
            continue

        duplicate_count += 1
        old_item = selected[match_index]
        old_score = int(old_item.get("_score", 0))
        new_score = int(item.get("_score", 0))
        old_dt = old_item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        new_dt = item.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)

        if new_score > old_score or (new_score == old_score and new_dt > old_dt):
            selected[match_index] = item
            index_item(match_index, item)

    return selected, duplicate_count


def deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped, _ = deduplicate_items_with_count(items)
    return deduped


def fetch_and_parse_source(source: dict[str, Any], tz: timezone) -> tuple[dict[str, Any], list[dict[str, Any]], Exception | None]:
    try:
        xml_bytes = fetch_bytes(source["url"])
        return source, parse_feed_entries(source, xml_bytes, tz), None
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return source, [], exc


def collect_feed_sources(
    sources: list[dict[str, Any]],
    tz: timezone,
    log_label: str,
) -> tuple[list[dict[str, Any]], int, int]:
    all_items: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0
    workers = max(1, min(RSS_FETCH_WORKERS, len(sources) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_and_parse_source, source, tz) for source in sources]
        for future in as_completed(futures):
            source, parsed, error = future.result()
            name = source["name"]
            group = source.get("source_group", "unknown")
            if error:
                failed_count += 1
                LOGGER.warning("%s fetch failed [%s] %s: %s", log_label, group, name, error)
                continue
            success_count += 1
            all_items.extend(parsed)
    return all_items, success_count, failed_count


def collect_news_candidates(
    tz: timezone,
    day_start: datetime,
    day_end: datetime,
    now_local: datetime,
) -> dict[str, Any]:
    sources = sorted(
        NEWS_SOURCES,
        key=lambda src: {"auto_china": 0, "china": 1, "global": 2}.get(str(src.get("source_group")), 9),
    )

    raw_items: list[dict[str, Any]] = []
    global_source_count = sum(1 for s in sources if s["source_group"] == "global")
    china_source_count = sum(1 for s in sources if s["source_group"] in ("china", "auto_china"))
    global_success_count = 0
    china_success_count = 0
    failed_count = 0

    workers = max(1, min(RSS_FETCH_WORKERS, len(sources) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_and_parse_source, source, tz) for source in sources]
        for future in as_completed(futures):
            source, parsed, error = future.result()
            name = source["name"]
            group = source["source_group"]
            if error:
                failed_count += 1
                LOGGER.warning("News fetch failed [%s] %s: %s", group, name, error)
                continue
            raw_items.extend(parsed)
            if group == "global":
                global_success_count += 1
            else:
                china_success_count += 1

    dedup, duplicate_count = deduplicate_news_items(raw_items)
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

    rejected_stats: dict[str, int] = {
        "duplicate": duplicate_count,
        "history_duplicate": 0,
        "weak_relevance": 0,
        "same_source_limit": 0,
        "china_limit": 0,
        "auto_china_limit": 0,
        "old_news": 0,
    }

    candidate_pool = yesterday_items + recent_items + no_time_items
    old_seen = {id(item) for item in dedup} - {id(item) for item in candidate_pool}
    rejected_stats["old_news"] = len(old_seen)

    classified_items: list[dict[str, Any]] = []
    for item in candidate_pool:
        classified = classify_item(item)
        score = score_item(classified)
        classified["score"] = score
        if not is_relevant_ai_auto(classified) or score < 0:
            rejected_stats["weak_relevance"] += 1
            continue
        classified_items.append(classified)

    def sort_key(entry: dict[str, Any]) -> tuple[int, int, datetime]:
        dt = entry.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc)
        group_weight = {"auto_china": 3, "china": 2, "global": 1}.get(entry.get("source_group", "global"), 0)
        return int(entry.get("score", 0)), group_weight, dt

    classified_items.sort(key=sort_key, reverse=True)

    history = load_history()
    history_size_before = len(history.get("items", []))
    history_duplicate_samples: list[dict[str, Any]] = []
    history_filtered_items: list[dict[str, Any]] = []
    target_date = day_start.date().isoformat()
    for item in classified_items:
        seen, reason, matched = is_seen_in_history(item, history, target_date)
        if seen and has_new_development_signal(item, matched):
            item["selection_reason"] = "new_development_after_previous_topic"
            history_filtered_items.append(item)
            continue
        if seen and should_prefer_over_history_duplicate(item, matched):
            item["selection_reason"] = "preferred_official_source_over_history_duplicate"
            history_filtered_items.append(item)
            continue
        if seen:
            rejected_stats["history_duplicate"] += 1
            if len(history_duplicate_samples) < 10:
                history_duplicate_samples.append(
                    {
                        "title": clean_text(item.get("title", "")),
                        "source": clean_text(item.get("source", "")),
                        "matched_title": clean_text((matched or {}).get("title", "")),
                        "matched_date": clean_text((matched or {}).get("last_seen_date", "")),
                        "reason": reason,
                    }
                )
            continue
        history_filtered_items.append(item)

    curated_items, selection_rejected = select_curated_items(history_filtered_items)
    for key, value in selection_rejected.items():
        rejected_stats[key] = rejected_stats.get(key, 0) + value

    history = update_history_with_items(history, curated_items, target_date=target_date, now_local=now_local)
    history = prune_history(history, target_date)
    save_history(history)
    history_size_after = len(history.get("items", []))

    for item in classified_items:
        item.pop("_published_dt", None)

    for item in curated_items:
        item.pop("_published_dt", None)

    fetch_status = {
        "source_count": len(sources),
        "success_count": global_success_count + china_success_count,
        "failed_count": failed_count,
        "global_source_count": global_source_count,
        "global_success_count": global_success_count,
        "china_source_count": china_source_count,
        "china_success_count": china_success_count,
    }
    selection_config = {
        "news_region_mode": NEWS_REGION_MODE,
        "target_candidate_count": TARGET_CANDIDATE_COUNT,
        "min_global_news": MIN_GLOBAL_NEWS,
        "max_china_news": MAX_CHINA_NEWS,
        "max_auto_china_news": MAX_AUTO_CHINA_NEWS,
        "max_same_source_news": MAX_SAME_SOURCE_NEWS,
        "max_items_for_llm": MAX_ITEMS_FOR_LLM,
        "min_agent_news": MIN_AGENT_NEWS,
        "min_productivity_news": MIN_PRODUCTIVITY_NEWS,
        "min_company_research_news": MIN_COMPANY_RESEARCH_NEWS,
        "max_company_research_news": MAX_COMPANY_RESEARCH_NEWS,
        "max_auto_driving_news": MAX_AUTO_DRIVING_NEWS,
        "max_smart_cockpit_news": MAX_SMART_COCKPIT_NEWS,
        "power_electronics_boost": POWER_ELECTRONICS_BOOST,
    }
    history_dedupe = {
        "history_path": HISTORY_PATH.as_posix(),
        "dedupe_days": HISTORY_DEDUPE_DAYS,
        "retention_days": HISTORY_RETENTION_DAYS,
        "similarity_threshold": HISTORY_SIMILARITY_THRESHOLD,
        "history_size_before": history_size_before,
        "history_size_after": history_size_after,
        "history_duplicate_count": rejected_stats["history_duplicate"],
    }
    return {
        "date": target_date,
        "generated_at": now_local.isoformat(),
        "timezone": TIMEZONE_NAME,
        "fetch_status": fetch_status,
        "selection_config": selection_config,
        "history_dedupe": history_dedupe,
        "raw_item_count": len(raw_items),
        "deduped_item_count": len(dedup),
        "filtered_item_count": len(classified_items),
        "curated_item_count": len(curated_items),
        "curated_items": curated_items,
        "rejected_stats": rejected_stats,
    }


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
            title, summary, summary_source = extract_html_metadata(html_text)
            item_title = title or name
            summary_meta = make_summary_meta(
                summary or title or "",
                summary_source if summary else ("page_title" if title else "empty"),
                link=source["url"],
                is_official=bool(source.get("is_official_source", True)),
            )
            item = {
                "title": clean_text(item_title),
                "summary": summary_meta.get("summary", "")[:800],
                "summary_source": summary_meta.get("summary_source", "empty"),
                "summary_quality": summary_meta.get("summary_quality", "empty"),
                "published_at": now_local.isoformat(),
                "source": clean_text(name),
                "source_type": source["source_type"] or "official_doc",
                "source_quality": source.get("source_quality", "high"),
                "is_official_source": bool(source.get("is_official_source", True)),
                "language": source["language"],
                "region": source["region"],
                "link": source["url"],
                "tags": list(source.get("tags", [])),
                "_published_dt": now_local,
            }
            item = infer_learning_source_metadata(item)
            score, hits, tags = learning_score(item)
            if hits > 0 and score > 0:
                item["_score"] = score
                item["tags"] = tags
                item["concept_hint"] = concept_hint_from_tags(tags)
                items.append(item)
            success_count += 1
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            failed_count += 1
            LOGGER.warning("Learning page fetch failed %s: %s", name, exc)
    return items, success_count, failed_count


def collect_learning_candidates(
    tz: timezone,
    now_local: datetime,
) -> dict[str, Any]:
    sources = LEARNING_FEED_SOURCES
    all_items: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0

    raw_feed_items, success_count, failed_count = collect_feed_sources(sources, tz, "Learning")
    for item in raw_feed_items:
        item = infer_learning_source_metadata(item)
        score, hits, tags = learning_score(item)
        if hits <= 0 or score <= 0:
            continue
        item["_score"] = score
        item["tags"] = tags
        item["concept_hint"] = concept_hint_from_tags(tags)
        all_items.append(item)

    page_items, page_success, page_failed = collect_learning_page_items(tz=tz, now_local=now_local)
    all_items.extend(page_items)
    success_count += page_success
    failed_count += page_failed

    for resource in OFFICIAL_AGENT_CONCEPT_RESOURCES:
        item = dict(resource)
        item.update(
            {
                "published_at": now_local.isoformat(),
                "language": "en",
                "region": "global",
                "source_group": "official_agent_concept",
                "_published_dt": now_local,
            }
        )
        item = infer_learning_source_metadata(item)
        score, hits, tags = learning_score(item)
        item["_score"] = score
        item["score"] = score
        item["tags"] = tags
        item["concept_hint"] = concept_hint_from_tags(tags)
        if hits > 0 and score > 0:
            all_items.append(item)

    dedup, duplicate_count = deduplicate_items_with_count(all_items)

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

    target_date = (now_local - timedelta(days=1)).date().isoformat()
    history = load_learning_history()
    history_size_before = len(history.get("items", []))
    history_duplicate_samples: list[dict[str, Any]] = []
    history_filtered_items: list[dict[str, Any]] = []
    rejected_stats: dict[str, int] = {
        "duplicate": duplicate_count,
        "history_duplicate": 0,
        "same_source_limit": 0,
        "old_resource": 0,
    }

    for item in merged:
        item["score"] = int(item.get("_score", 0))
        item["canonical_key"] = make_canonical_key(item)
        item["dedupe_key"] = normalize_title(str(item.get("title", "")))
        seen, reason, matched = is_seen_in_learning_history(item, history, target_date)
        if seen:
            rejected_stats["history_duplicate"] += 1
            if len(history_duplicate_samples) < 10:
                history_duplicate_samples.append(
                    {
                        "title": clean_text(item.get("title", "")),
                        "source": clean_text(item.get("source", "")),
                        "matched_title": clean_text((matched or {}).get("title", "")),
                        "matched_date": clean_text((matched or {}).get("last_seen_date", "")),
                        "reason": reason,
                    }
                )
            continue
        history_filtered_items.append(item)

    rejected_stats["old_resource"] = max(0, len(dedup) - len(merged))

    curated_items, selection_rejected = select_curated_learning_items(history_filtered_items)
    for key, value in selection_rejected.items():
        rejected_stats[key] = rejected_stats.get(key, 0) + value

    history = update_learning_history_with_items(history, curated_items, target_date=target_date, now_local=now_local)
    history = prune_learning_history(history, target_date)
    save_learning_history(history)
    history_size_after = len(history.get("items", []))

    for item in curated_items:
        item.pop("_score", None)
        item.pop("_published_dt", None)

    status = {
        "source_count": len(sources) + len(OFFICIAL_LEARNING_PAGES),
        "success_count": success_count,
        "failed_count": failed_count,
    }
    return {
        "date": target_date,
        "generated_at": now_local.isoformat(),
        "timezone": TIMEZONE_NAME,
        "fetch_status": status,
        "selection_config": {
            "target_learning_candidate_count": TARGET_LEARNING_CANDIDATE_COUNT,
            "max_same_source_learning": MAX_SAME_SOURCE_LEARNING,
            "max_items_for_llm": TARGET_LEARNING_CANDIDATE_COUNT,
        },
        "history_dedupe": {
            "history_path": LEARNING_HISTORY_PATH.as_posix(),
            "dedupe_days": LEARNING_HISTORY_DEDUPE_DAYS,
            "retention_days": LEARNING_HISTORY_RETENTION_DAYS,
            "similarity_threshold": LEARNING_HISTORY_SIMILARITY_THRESHOLD,
            "history_size_before": history_size_before,
            "history_size_after": history_size_after,
            "history_duplicate_count": rejected_stats["history_duplicate"],
        },
        "raw_item_count": len(all_items),
        "deduped_item_count": len(dedup),
        "filtered_item_count": len(history_filtered_items),
        "curated_item_count": len(curated_items),
        "curated_items": curated_items,
        "rejected_stats": rejected_stats,
    }


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


def save_news_payload(payload: dict[str, Any]) -> Path:
    out_dir = Path("data") / "news-candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_date = clean_text(payload.get("date", "")) or datetime.now().date().isoformat()
    out_file = out_dir / f"{target_date}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file


def save_learning_payload(payload: dict[str, Any]) -> Path:
    out_dir = Path("data") / "learning-candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_date = clean_text(payload.get("date", "")) or datetime.now().date().isoformat()
    out_file = out_dir / f"{target_date}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file


def prune_candidate_files(base_dir_name: str, target_date: str) -> int:
    data_dir = Path("data") / base_dir_name
    if not data_dir.exists():
        return 0

    try:
        target_dt = datetime.fromisoformat(target_date).date()
    except ValueError:
        return 0

    cutoff_date = target_dt - timedelta(days=max(CANDIDATE_RETENTION_DAYS, 1) - 1)
    deleted_count = 0
    for path in data_dir.glob("*.json"):
        try:
            file_date = datetime.fromisoformat(path.stem).date()
        except ValueError:
            continue
        if file_date >= cutoff_date:
            continue
        try:
            path.unlink()
            deleted_count += 1
        except OSError as exc:
            LOGGER.warning("Delete old candidate file failed %s: %s", path.as_posix(), exc)
    return deleted_count


def main() -> int:
    setup_logging()
    tz = get_timezone()
    now_local = datetime.now(tz)

    target_date = (now_local - timedelta(days=1)).date()
    day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
    day_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz)

    news_payload = collect_news_candidates(
        tz=tz,
        day_start=day_start,
        day_end=day_end,
        now_local=now_local,
    )
    news_file = save_news_payload(news_payload)

    learning_payload = collect_learning_candidates(
        tz=tz,
        now_local=now_local,
    )
    learning_file = save_learning_payload(learning_payload)
    deleted_news_files = prune_candidate_files("news-candidates", target_date.isoformat())
    deleted_learning_files = prune_candidate_files("learning-candidates", target_date.isoformat())

    LOGGER.info(
        "Saved news candidates=%s to %s | learning candidates=%s to %s | pruned old candidates news=%s learning=%s",
        len(news_payload.get("curated_items", [])),
        news_file.as_posix(),
        len(learning_payload.get("curated_items", [])),
        learning_file.as_posix(),
        deleted_news_files,
        deleted_learning_files,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
