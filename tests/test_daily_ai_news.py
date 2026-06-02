import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
            "learning_point": "Read AGENTS.md first.",
            "how_to_apply": "Ask Codex to inspect project instructions before editing.",
            "example_prompt": daily.DEFAULT_LEARNING_PROMPT,
            "confidence_note": daily.DEFAULT_LEARNING_NOTE,
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

    def test_fallback_contains_codex_learning(self) -> None:
        report = daily.build_rule_based_report(sample_news_items(), sample_learning_item(), "2026-06-01")
        learning = report["codex_learning"]
        self.assertEqual(learning["source_url"], "https://github.com/openai/codex")
        self.assertTrue(learning["learning_point"])

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
