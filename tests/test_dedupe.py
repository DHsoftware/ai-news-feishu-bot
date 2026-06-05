import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import collect_rss as collect  # noqa: E402


def news(
    title: str,
    summary: str = "",
    link: str = "https://example.com/news",
    source: str = "Example Media",
    **extra: object,
) -> dict[str, object]:
    item: dict[str, object] = {
        "title": title,
        "summary": summary,
        "link": link,
        "source": source,
        "summary_quality": "medium",
        "source_quality": "medium",
    }
    item.update(extra)
    return item


class CollectRssDedupeTest(unittest.TestCase):
    def test_same_link_with_different_utm_is_deduped(self) -> None:
        items = [
            news("OpenAI launches Codex update", link="https://openai.com/news/codex?utm_source=google&id=1"),
            news("OpenAI launches Codex update", link="https://openai.com/news/codex?id=1&utm_campaign=rss"),
        ]

        deduped, duplicate_count = collect.deduplicate_news_items(items)

        self.assertEqual(duplicate_count, 1)
        self.assertEqual(len(deduped), 1)

    def test_rewritten_titles_for_same_codex_event_match(self) -> None:
        a = news(
            "OpenAI launches Codex update for enterprise workflows",
            "The release improves coding agent workflows for software engineering teams.",
        )
        b = news(
            "OpenAI unveils new Codex capabilities for enterprise software teams",
            "The launch adds Codex coding agent support for enterprise workflow use.",
        )

        self.assertTrue(collect.likely_same_story(a, b))

    def test_same_company_different_events_do_not_match(self) -> None:
        a = news("OpenAI launches Codex update", "Codex adds coding agent workflow capabilities.", link="https://example.com/codex")
        b = news("OpenAI releases new multimodal model", "The new GPT model improves image and video reasoning.", link="https://example.com/gpt")

        self.assertFalse(collect.likely_same_story(a, b))

    def test_same_product_different_events_do_not_match(self) -> None:
        a = news("Codex becomes available on AWS", "OpenAI Codex is available through AWS Bedrock cloud platform.", link="https://example.com/codex-aws")
        b = news("Codex CLI adds local terminal features", "The Codex CLI update improves local terminal workflows.", link="https://example.com/codex-cli")

        self.assertFalse(collect.likely_same_story(a, b))

    def test_google_news_duplicate_loses_to_official_source(self) -> None:
        google_item = news(
            "OpenAI unveils new Codex capabilities for enterprise software teams",
            "Short rewritten summary.",
            link="https://news.google.com/rss/articles/abc?oc=5",
            source="OpenAI Codex - Google News",
            source_group="global",
            source_type="google_news",
            source_quality="low",
            summary_quality="low",
        )
        official_item = news(
            "OpenAI launches Codex update for enterprise workflows",
            "OpenAI details a Codex release for enterprise software engineering workflows and coding agents.",
            link="https://openai.com/news/codex-enterprise-workflows",
            source="OpenAI News",
            source_group="company_research",
            source_type="official_blog",
            source_quality="high",
            summary_quality="high",
            is_official_source=True,
        )

        deduped, duplicate_count = collect.deduplicate_news_items([google_item, official_item])

        self.assertEqual(duplicate_count, 1)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "OpenAI News")
        self.assertEqual(deduped[0]["link"], "https://openai.com/news/codex-enterprise-workflows")

    def test_chinese_and_english_same_codex_event_match(self) -> None:
        english = news(
            "OpenAI launches Codex update for enterprise workflows",
            "The Codex release targets coding agent workflows for enterprise software teams.",
        )
        chinese = news(
            "OpenAI 发布 Codex 企业工作流更新",
            "此次推出面向企业软件团队的编程智能体能力，用于研发提效。",
        )

        self.assertTrue(collect.likely_same_story(english, chinese))

    def test_obc_fault_diagnosis_same_topic_matches(self) -> None:
        a = news("AI fault diagnosis for OBC power electronics", "A method detects faults in onboard charger power electronics.")
        b = news("AI-based onboard charger fault diagnosis method", "The study covers OBC fault diagnosis for power electronics.")

        self.assertTrue(collect.likely_same_story(a, b))

    def test_obc_and_dcdc_different_topics_do_not_match(self) -> None:
        a = news("OBC thermal optimization", "AI improves thermal management for an onboard charger.", link="https://example.com/obc-thermal")
        b = news("DCDC control loop update", "A DC-DC converter control loop receives a firmware update.", link="https://example.com/dcdc-control")

        self.assertFalse(collect.likely_same_story(a, b))

    def test_history_dedupes_rewritten_event_signature(self) -> None:
        history = {
            "items": [
                {
                    "first_seen_date": "2026-06-01",
                    "last_seen_date": "2026-06-01",
                    "canonical_key": "openai unveils codex enterprise software teams",
                    "normalized_title": collect.normalize_title("OpenAI unveils new Codex capabilities for enterprise software teams"),
                    "normalized_link": "https://example.com/media/codex",
                    "title": "OpenAI unveils new Codex capabilities for enterprise software teams",
                    "summary": "OpenAI announces Codex coding agent capabilities for enterprise workflow use.",
                    "source": "Example Media",
                    "link": "https://example.com/media/codex",
                    "event_signature": collect.build_event_signature(
                        news(
                            "OpenAI unveils new Codex capabilities for enterprise software teams",
                            "OpenAI announces Codex coding agent capabilities for enterprise workflow use.",
                        )
                    ),
                }
            ]
        }
        item = news(
            "OpenAI launches Codex update for enterprise workflows",
            "The release improves Codex coding agent workflows for software engineering teams.",
            link="https://another.example/codex",
        )

        seen, reason, matched = collect.is_seen_in_history(item, history, "2026-06-02")

        self.assertTrue(seen)
        self.assertEqual(reason, "same_event_signature")
        self.assertIsNotNone(matched)

    def test_security_vulnerability_does_not_merge_with_release(self) -> None:
        a = news("Codex security vulnerability disclosed", "A security vulnerability affects Codex CLI users.", link="https://example.com/codex-security")
        b = news("Codex enterprise workflow release", "OpenAI releases Codex capabilities for enterprise workflows.", link="https://example.com/codex-release")

        self.assertFalse(collect.likely_same_story(a, b))


if __name__ == "__main__":
    unittest.main()
