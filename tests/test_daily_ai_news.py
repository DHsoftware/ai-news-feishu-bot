import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import collect_rss as collect  # noqa: E402
from scripts import daily_ai_news as daily  # noqa: E402


def sample_news_items() -> list[dict[str, object]]:
    titles = [
        "OpenAI Codex expands enterprise coding workflow",
        "JetBrains releases model for developer automation",
        "NVIDIA details AI infrastructure manufacturing update",
        "Power electronics report covers SiC module simulation",
        "Automotive AI workflow governance guide published",
    ]
    return [
        {
            "title": title,
            "summary": "This update describes AI coding workflow improvements for engineering teams.",
            "summary_quality": "medium",
            "category": "AI编程工具",
            "source": f"Source {idx}",
            "link": f"https://example.com/news/{idx}?utm_source=test",
            "topic_tags": ["agent", "coding_agent"],
        }
        for idx, title in enumerate(titles, start=1)
    ]


def sample_learning_item() -> dict[str, object]:
    return {
        "title": "GitHub - openai/codex: Lightweight coding agent",
        "summary": "Lightweight coding agent that runs in your terminal.",
        "summary_quality": "high",
        "source": "OpenAI Codex GitHub",
        "source_type": "github_repo",
        "source_quality": "high",
        "is_official_source": True,
        "link": "https://github.com/openai/codex",
        "tags": ["agent", "agents_md", "cli", "codex"],
        "concept_hint": "Agent Instructions",
    }


def sample_power_news_items() -> list[dict[str, object]]:
    return [
        {
            "title": "OBC control design note highlights DCDC diagnostics",
            "summary": "The note discusses OBC, DCDC and power control validation topics.",
            "summary_quality": "medium",
            "category": "OBC/DCDC",
            "source": "Power Source",
            "link": "https://example.com/power/1",
            "topic_tags": ["obc", "dcdc", "power_electronics"],
        },
        {
            "title": "SiC module simulation workflow update",
            "summary": "The article covers SiC module simulation and thermal validation.",
            "summary_quality": "medium",
            "category": "功率电子",
            "source": "Power Source 2",
            "link": "https://example.com/power/2",
            "topic_tags": ["sic", "power_electronics"],
        },
    ]


def sample_official_news_items() -> list[dict[str, object]]:
    return [
        {
            "title": "Official research report on AI engineering methods",
            "summary": "An official technical report describes engineering practices for AI systems.",
            "summary_quality": "medium",
            "category": "官方研究",
            "source": "Official Lab",
            "link": "https://example.com/official/1",
            "topic_tags": ["official_report", "technical_blog"],
        }
    ]


def llm_report() -> dict[str, object]:
    items = sample_news_items()
    return {
        "title": "AI 科技日报｜2026-06-01",
        "summary": ["LiteLLM summary"],
        "top_news": [
            {
                "category": item["category"],
                "title": item["title"],
                "what_happened": item["summary"],
                "why_important": "Important for engineering workflow.",
                "auto_relevance": daily.RELATION_INDIRECT,
                "auto_impact_brief": "Useful for coding workflow evaluation.",
                "source_name": item["source"],
                "source_url": item["link"],
            }
            for item in items
        ],
        "codex_learning": {
            "resource_title": sample_learning_item()["title"],
            "resource_type": "官方文档",
            "source_name": "OpenAI Codex GitHub",
            "source_url": sample_learning_item()["link"],
            "concept": "Agent Instructions",
            "concept_explanation": "Agent Instructions 是给 Agent 的项目级背景、约束和工作规则。",
            "why_it_matters": "它帮助 Agent 在具体项目中保持一致的工程上下文。",
            "auto_relevance": "适合表达代码规范、测试要求、安全边界和工具链限制。",
            "example_scenario": "例如在项目说明中定义哪些目录可改、哪些配置不能触碰。",
            "confidence_note": "基于官方文档摘要整理。",
        },
    }


class DailyAiNewsTest(unittest.TestCase):
    def test_litellm_normal_path(self) -> None:
        content = json.dumps(llm_report(), ensure_ascii=False)
        with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "60", "LLM_MAX_RETRIES": "1"}, clear=False):
            with patch.object(
                daily,
                "http_post_json",
                return_value={"choices": [{"message": {"content": content}}]},
            ) as mocked_post:
                report = daily.create_report_json_with_litellm(
                    base_url="https://litellm.example/v1",
                    api_key="secret",
                    model="gpt-5.4",
                    report_date="2026-06-01",
                    timezone_name="Asia/Shanghai",
                    news_top_n=5,
                    news_max_chars=3500,
                    items=sample_news_items(),
                    source_json_name="2026-06-01.json",
                    used_fallback_cache=False,
                    selected_learning_item=sample_learning_item(),
                    learning_items=[sample_learning_item()],
                    learning_source_json_name="2026-06-01.json",
                    learning_used_fallback_cache=False,
                )
        self.assertEqual(report["summary"], ["LiteLLM summary"])
        self.assertEqual(mocked_post.call_args.kwargs["timeout_seconds"], 60)

    def test_litellm_timeout_uses_fallback_in_main(self) -> None:
        sent: list[dict[str, object]] = []

        def fake_find(data_dir: Path, target_date: str):
            if "learning-candidates" in data_dir.as_posix():
                return {"curated_items": [sample_learning_item()]}, data_dir / f"{target_date}.json", False
            return {"curated_items": sample_news_items()}, data_dir / f"{target_date}.json", False

        with patch.dict(
            os.environ,
            {
                "FEISHU_WEBHOOK_URL": "https://feishu.example/webhook",
                "LITELLM_API_KEY": "secret",
                "LITELLM_BASE_URL": "https://litellm.example/v1",
                "ENABLE_RULE_BASED_FALLBACK": "true",
                "NEWS_TOP_N": "5",
            },
            clear=False,
        ):
            with patch.object(daily, "find_candidates_json", side_effect=fake_find):
                with patch.object(daily, "create_report_json_with_litellm", side_effect=RuntimeError("timed out")):
                    with patch.object(daily, "send_report_with_fallback", side_effect=lambda **kwargs: sent.append(kwargs["report"])):
                        self.assertEqual(daily.main(), 0)

        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0]["top_news"]), 5)
        self.assertEqual(sent[0]["codex_learning"]["source_url"], "https://github.com/openai/codex")

    def test_litellm_invalid_json_uses_fallback_in_main(self) -> None:
        sent: list[dict[str, object]] = []

        def fake_find(data_dir: Path, target_date: str):
            if "learning-candidates" in data_dir.as_posix():
                return {"curated_items": [sample_learning_item()]}, data_dir / f"{target_date}.json", False
            return {"curated_items": sample_news_items()}, data_dir / f"{target_date}.json", False

        with patch.dict(
            os.environ,
            {
                "FEISHU_WEBHOOK_URL": "https://feishu.example/webhook",
                "LITELLM_API_KEY": "secret",
                "LITELLM_BASE_URL": "https://litellm.example/v1",
                "ENABLE_RULE_BASED_FALLBACK": "true",
            },
            clear=False,
        ):
            with patch.object(daily, "find_candidates_json", side_effect=fake_find):
                with patch.object(daily, "create_report_json_with_litellm", side_effect=RuntimeError("invalid json")):
                    with patch.object(daily, "send_report_with_fallback", side_effect=lambda **kwargs: sent.append(kwargs["report"])):
                        self.assertEqual(daily.main(), 0)

        self.assertEqual(len(sent), 1)
        summary_text = "\n".join(sent[0]["summary"])
        self.assertNotIn("模板日报", summary_text)
        self.assertNotIn("fallback", summary_text)
        self.assertNotIn("LLM", summary_text)

    def test_fallback_generates_report_json(self) -> None:
        report = daily.build_rule_based_report(sample_news_items(), sample_learning_item(), "2026-06-01")
        self.assertEqual(report["title"], "AI 科技日报｜2026-06-01")
        self.assertEqual(len(report["top_news"]), 5)
        self.assertIn("codex_learning", report)
        self.assertLessEqual(len(report["summary"]), 3)

    def test_feishu_title_is_top_5_for_litellm_and_fallback(self) -> None:
        report = llm_report()
        report["news_top_n"] = 5
        fallback = daily.build_rule_based_report(sample_news_items(), sample_learning_item(), "2026-06-01")
        fallback["news_top_n"] = 5
        self.assertIn("重要新闻 Top 5", daily.build_single_text_payload(report, "x.json", False)["content"]["text"])
        self.assertIn("重要新闻 Top 5", daily.build_single_text_payload(fallback, "x.json", False)["content"]["text"])

    def test_feishu_does_not_show_category_when_llm_returns_category(self) -> None:
        report = llm_report()
        report["news_top_n"] = 5
        text = daily.build_single_text_payload(report, "x.json", False)["content"]["text"]
        self.assertIn("重要新闻 Top 5", text)
        self.assertIn("1. OpenAI Codex expands enterprise coding workflow", text)
        self.assertNotIn("[AI编程工具]", text)
        self.assertNotIn("1. [", text)

    def test_rule_based_feishu_hides_generated_analysis_fields(self) -> None:
        report = daily.build_rule_based_report(sample_news_items(), sample_learning_item(), "2026-06-01")
        report["news_top_n"] = 5
        text = daily.build_single_text_payload(report, "x.json", False)["content"]["text"]
        for hidden in ("汽车行业关联：", "简要影响：", "可复制 Prompt："):
            self.assertNotIn(hidden, text)
        self.assertIn("发生了什么：", text)
        self.assertIn("来源：", text)
        self.assertIn("Agent 概念每日一学", text)
        self.assertIn("今日概念：Agent Instructions", text)

    def test_litellm_feishu_keeps_generated_analysis_fields(self) -> None:
        report = llm_report()
        report["news_top_n"] = 5
        text = daily.build_single_text_payload(report, "x.json", False)["content"]["text"]
        for visible in ("为什么重要：", "汽车行业关联：", "简要影响：", "今日概念：", "一句话解释："):
            self.assertIn(visible, text)
        self.assertNotIn("可复制 Prompt：", text)

    def test_feishu_renders_when_llm_omits_category(self) -> None:
        report = llm_report()
        for item in report["top_news"]:
            item.pop("category", None)
        report["news_top_n"] = 5
        text = daily.build_single_text_payload(report, "x.json", False)["content"]["text"]
        self.assertIn("重要新闻 Top 5", text)
        self.assertIn("1. OpenAI Codex expands enterprise coding workflow", text)
        self.assertNotIn("[AI编程工具]", text)
        self.assertNotIn("1. [", text)

    def test_collect_rss_internal_category_fields_remain(self) -> None:
        collect_rss = (ROOT / "scripts" / "collect_rss.py").read_text(encoding="utf-8")
        self.assertIn("category", collect_rss)
        self.assertIn("topic_tags", collect_rss)
        self.assertIn("topic_signature", collect_rss)

    def test_official_agent_concept_resources_are_static_high_quality(self) -> None:
        self.assertGreaterEqual(len(collect.OFFICIAL_AGENT_CONCEPT_RESOURCES), 11)
        first = collect.OFFICIAL_AGENT_CONCEPT_RESOURCES[0]
        self.assertEqual(first["source_quality"], "high")
        self.assertEqual(first["summary_quality"], "high")
        self.assertEqual(first["summary_source"], "official_static_summary")
        self.assertTrue(first["is_official_source"])

    def test_collect_learning_candidates_adds_official_static_resources(self) -> None:
        now = datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc)
        with patch.object(collect, "collect_feed_sources", return_value=([], 0, 0)):
            with patch.object(collect, "collect_learning_page_items", return_value=([], 0, 0)):
                with patch.object(collect, "load_learning_history", return_value={"items": []}):
                    with patch.object(collect, "save_learning_history"):
                        payload = collect.collect_learning_candidates(timezone.utc, now)
        self.assertGreaterEqual(payload["raw_item_count"], len(collect.OFFICIAL_AGENT_CONCEPT_RESOURCES))
        self.assertEqual(payload["curated_item_count"], 1)
        self.assertEqual(payload["curated_items"][0]["source_type"], "official_doc")
        self.assertTrue(payload["curated_items"][0]["is_official_source"])

    def test_collect_learning_prefers_official_concept_over_google_youtube(self) -> None:
        official = {
            "title": "Agents SDK | OpenAI API",
            "summary": "OpenAI official guide for building agent workflows with tool calling and guardrails.",
            "summary_source": "official_static_summary",
            "summary_quality": "high",
            "source": "OpenAI Developers",
            "source_type": "official_doc",
            "source_quality": "high",
            "is_official_source": True,
            "link": "https://developers.openai.com/api/docs/guides/agents",
            "tags": ["agents_sdk", "agent_workflow", "tool_calling"],
            "_score": 120,
        }
        google = {
            "title": "Codex prompt tutorial - Google News",
            "summary": "Short prompt tips.",
            "summary_source": "rss_description",
            "summary_quality": "medium",
            "source": "Google News",
            "source_type": "google_news",
            "source_quality": "low",
            "is_official_source": False,
            "link": "https://news.google.com/rss/articles/x",
            "tags": ["tutorial"],
            "_score": 999,
        }
        youtube = {
            "title": "Build Hour: Agents SDK",
            "summary": "Video about agents.",
            "summary_source": "rss_description",
            "summary_quality": "medium",
            "source": "OpenAI YouTube",
            "source_type": "official_video",
            "source_quality": "high",
            "is_official_source": True,
            "link": "https://www.youtube.com/watch?v=x",
            "tags": ["agents_sdk"],
            "_score": 998,
        }
        selected, rejected = collect.select_curated_learning_items([google, youtube, official])
        self.assertEqual(selected[0]["link"], official["link"])
        self.assertEqual(selected[0]["concept_hint"], "Agent Workflow")
        self.assertNotIn(selected[0]["source_type"], {"google_news", "official_video"})

    def test_concept_hint_mapping(self) -> None:
        self.assertEqual(collect.concept_hint_from_tags(["agents_sdk"]), "Agent Workflow")
        self.assertEqual(collect.concept_hint_from_tags(["tool_calling"]), "Tool Calling")
        self.assertEqual(collect.concept_hint_from_tags(["guardrails"]), "Guardrails")
        self.assertEqual(collect.concept_hint_from_tags(["codex", "coding_agent"]), "Coding Agent")
        self.assertEqual(collect.concept_hint_from_tags(["agents_md"]), "Agent Instructions")

    def test_daily_uses_concept_hint_and_tag_mapping(self) -> None:
        self.assertEqual(daily.concept_from_learning_item({"concept_hint": "Tool Calling", "tags": ["agents_sdk"]}), "Tool Calling")
        self.assertEqual(daily.concept_from_learning_item({"tags": ["agents_sdk"]}), "Agent Workflow")
        self.assertEqual(daily.concept_from_learning_item({"tags": ["tool_calling"]}), "Tool Calling")
        self.assertEqual(daily.concept_from_learning_item({"tags": ["guardrails"]}), "Guardrails")
        self.assertEqual(daily.concept_from_learning_item({"tags": ["codex", "coding_agent"]}), "Coding Agent")
        self.assertEqual(daily.concept_from_learning_item({"tags": ["agents_md"]}), "Agent Instructions")

    def test_codex_learning_no_longer_requires_example_prompt(self) -> None:
        report = daily.normalize_report_payload(
            raw={
                "title": "AI 科技日报｜2026-06-01",
                "summary": ["x"],
                "top_news": [],
                "codex_learning": {"concept": "Guardrails"},
            },
            report_date="2026-06-01",
            news_top_n=5,
            candidate_items=sample_news_items(),
            selected_learning_item={**sample_learning_item(), "concept_hint": "Guardrails", "tags": ["guardrails"]},
            learning_candidate_items=[sample_learning_item()],
        )
        self.assertEqual(report["codex_learning"]["concept"], "Guardrails")
        self.assertNotIn("example_prompt", report["codex_learning"])

    def test_feishu_learning_section_has_no_copyable_prompt(self) -> None:
        report = llm_report()
        text = daily.build_single_text_payload(report, "x.json", False)["content"]["text"]
        self.assertIn("三、Agent 概念每日一学", text)
        self.assertNotIn("可复制 Prompt", text)
        self.assertNotIn("请先读取 AGENTS.md", text)

    def test_fallback_outputs_concept_learning(self) -> None:
        item = {**sample_learning_item(), "concept_hint": "Tool Calling", "tags": ["tool_calling"]}
        learning = daily.build_rule_based_codex_learning(item)
        self.assertEqual(learning["concept"], "Tool Calling")
        self.assertIn("Tool Calling", learning["concept_explanation"])
        self.assertNotIn("example_prompt", learning)

    def test_fallback_contains_codex_learning(self) -> None:
        report = daily.build_rule_based_report(sample_news_items(), sample_learning_item(), "2026-06-01")
        learning = report["codex_learning"]
        self.assertEqual(learning["source_url"], "https://github.com/openai/codex")
        self.assertEqual(learning["concept"], "Agent Instructions")
        self.assertTrue(learning["concept_explanation"])

    def test_fallback_summary_hides_internal_words(self) -> None:
        report = daily.build_rule_based_report(sample_news_items(), sample_learning_item(), "2026-06-01")
        summary_text = "\n".join(report["summary"])
        for forbidden in ("fallback", "LLM", "RSS", "模板", "curated_items", "不调用", "自动生成", "候选", "规则生成"):
            self.assertNotIn(forbidden, summary_text)

    def test_fallback_summary_dedupes_repeated_ai_agent_topic(self) -> None:
        agent_items = [
            {
                "title": f"AI Agent workflow update {idx}",
                "summary": "The item describes coding agent workflow and developer productivity.",
                "summary_quality": "medium",
                "category": "AI编程工具",
                "source": f"Agent Source {idx}",
                "link": f"https://example.com/agent/{idx}",
                "topic_tags": ["ai_agent", "coding_agent", "agent"],
            }
            for idx in range(1, 4)
        ]
        report = daily.build_rule_based_report(agent_items, sample_learning_item(), "2026-06-01")
        summary_text = "\n".join(report["summary"])
        self.assertEqual(summary_text.count("AI Agent"), 1)

    def test_fallback_summary_mentions_official_content_naturally(self) -> None:
        report = daily.build_rule_based_report(sample_official_news_items(), sample_learning_item(), "2026-06-01")
        summary_text = "\n".join(report["summary"])
        self.assertIn("官方", summary_text)
        self.assertTrue("技术" in summary_text or "研究" in summary_text)
        self.assertNotIn("模板", summary_text)

    def test_fallback_summary_mentions_power_topics(self) -> None:
        report = daily.build_rule_based_report(sample_power_news_items(), sample_learning_item(), "2026-06-01")
        summary_text = "\n".join(report["summary"])
        self.assertTrue("车载电源电子" in summary_text or "功率电子" in summary_text)
        self.assertNotIn("fallback", summary_text)

    def test_fallback_summary_empty_top_news(self) -> None:
        self.assertEqual(
            daily.build_rule_based_summary([]),
            ["今日未发现足够高质量的 AI 相关新增内容，暂不生成完整日报。"],
        )

    def test_litellm_empty_content_retries(self) -> None:
        content = json.dumps(llm_report(), ensure_ascii=False)
        with patch.dict(os.environ, {"LLM_MAX_RETRIES": "1"}, clear=False):
            with patch.object(
                daily,
                "http_post_json",
                side_effect=[
                    {"choices": [{"message": {"content": ""}}]},
                    {"choices": [{"message": {"content": content}}]},
                ],
            ) as mocked_post:
                report = daily.create_report_json_with_litellm(
                    base_url="https://litellm.example/v1",
                    api_key="secret",
                    model="gpt-5.4",
                    report_date="2026-06-01",
                    timezone_name="Asia/Shanghai",
                    news_top_n=5,
                    news_max_chars=3500,
                    items=sample_news_items(),
                    source_json_name="2026-06-01.json",
                    used_fallback_cache=False,
                    selected_learning_item=sample_learning_item(),
                    learning_items=[sample_learning_item()],
                    learning_source_json_name="2026-06-01.json",
                    learning_used_fallback_cache=False,
                )
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(report["title"], "AI 科技日报｜2026-06-01")


if __name__ == "__main__":
    unittest.main()
