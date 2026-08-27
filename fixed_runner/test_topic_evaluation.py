from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from topic_evaluation import (  # noqa: E402
    attach_riskflow_decisions,
    evaluate_topic_samples,
)


class TopicEvaluationTest(unittest.TestCase):
    def test_only_confirmed_references_count_toward_agreement(self) -> None:
        report = evaluate_topic_samples(
            [
                {
                    "id": "confirmed-match",
                    "reference": {"relevance": "exact", "confirmed": True},
                    "riskflow": {"relevance": "exact"},
                },
                {
                    "id": "confirmed-difference",
                    "reference": {"relevance": "exact", "confirmed": True},
                    "riskflow": {"relevance": "unrelated"},
                },
                {
                    "id": "teacher-only",
                    "reference": {"relevance": "adjacent", "confirmed": False},
                    "riskflow": {"relevance": "exact"},
                },
            ],
            prompt_version="topic-test",
        )

        self.assertEqual(report["total_samples"], 3)
        self.assertEqual(report["confirmed_samples"], 2)
        self.assertEqual(report["pending_review"], ["teacher-only"])
        self.assertEqual(report["candidate_agreements"], 0)
        self.assertEqual(report["candidate_differences"][0]["id"], "teacher-only")
        self.assertEqual(report["agreements"], 1)
        self.assertEqual(report["agreement_rate"], 0.5)
        self.assertEqual(report["differences"][0]["id"], "confirmed-difference")
        self.assertEqual(report["confusion"]["exact"]["unrelated"], 1)
        self.assertEqual(report["prompt_version"], "topic-test")
        self.assertEqual(report["policy_version"], "unknown")
        self.assertEqual(report["result_status"], "provisional")
        self.assertIn("adjacent", report["coverage"]["missing_relevance"])
        self.assertIn("keyword-only", report["coverage"]["missing_hard_negatives"])

    def test_invalid_relevance_fails_closed(self) -> None:
        report = evaluate_topic_samples(
            [
                {
                    "id": "bad-output",
                    "reference": {"relevance": "exact", "confirmed": True},
                    "riskflow": {"relevance": "yes"},
                }
            ],
            prompt_version="topic-test",
        )

        self.assertEqual(report["differences"][0]["riskflow"], "uncertain")

    def test_model_evaluation_attaches_decision_without_changing_reference(self) -> None:
        class Decision:
            relevance = "exact"
            topic = "人工智能"
            evidence = ("画面出现AI模型",)
            reason = "主体讨论AI"
            safe = True

        samples = [
            {
                "id": "one",
                "image_path": "one.png",
                "reference": {"relevance": "exact", "confirmed": False},
            }
        ]
        calls = []

        def analyze(image_path, topic, **kwargs):
            calls.append((str(image_path), topic, kwargs["model"]))
            return Decision()

        updated = attach_riskflow_decisions(
            samples,
            target_topic="人工智能",
            model="test-model",
            base_url="https://example.invalid/v1",
            analyze=analyze,
        )

        self.assertNotIn("riskflow", samples[0])
        self.assertEqual(updated[0]["riskflow"]["relevance"], "exact")
        self.assertEqual(updated[0]["riskflow"]["model"], "test-model")
        self.assertEqual(updated[0]["riskflow"]["policy_version"], "legacy-free-text@1")
        self.assertEqual(calls, [("one.png", "人工智能", "test-model")])

    def test_complete_coverage_is_reported_only_for_confirmed_samples(self) -> None:
        samples = []
        for relevance in ("exact", "adjacent", "unrelated", "uncertain"):
            samples.append(
                {
                    "id": relevance,
                    "tags": [],
                    "reference": {"relevance": relevance, "confirmed": True},
                    "riskflow": {"relevance": relevance},
                }
            )
        samples[2]["tags"] = ["keyword-only", "visual-commerce"]

        report = evaluate_topic_samples(
            samples,
            prompt_version="topic-test",
            policy_version="policy-test@1",
        )

        self.assertEqual(report["result_status"], "complete")
        self.assertTrue(report["coverage"]["complete"])
        self.assertEqual(report["coverage"]["missing_relevance"], [])
        self.assertEqual(report["coverage"]["missing_hard_negatives"], [])
        self.assertEqual(report["policy_version"], "policy-test@1")


if __name__ == "__main__":
    unittest.main()
