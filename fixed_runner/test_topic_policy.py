from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from topic_policy import (  # noqa: E402
    DEFAULT_POLICY_PATH,
    compile_topic_policy,
    load_topic_policy,
    resolve_topic_policy,
)


CURRENT_TOPIC_TEXT = """1. 人工智能技术、AI工具、大模型、机器人及科技产业动态；
2. AI人才培养、AI岗位、职业发展及企业AI能力建设；
3. 传统制造业数字化转型、智能制造、工厂自动化和AI降本增效；
4. 塑料袋、塑料包装、吹膜、制袋、印刷等工厂的生产工艺、设备、经营管理和转型案例。"""


class TopicPolicyTest(unittest.TestCase):
    def test_default_policy_is_versioned_and_complete(self) -> None:
        policy = load_topic_policy(DEFAULT_POLICY_PATH)

        self.assertEqual(policy.policy_id, "ai-manufacturing-plastics")
        self.assertEqual(policy.version, "1.2.0")
        self.assertEqual(len(policy.eligible_categories), 4)
        self.assertTrue(policy.adjacent_categories)
        self.assertTrue(policy.global_exclusions)
        self.assertTrue(policy.evidence_requirement)

    def test_compiled_policy_makes_keywords_non_decisive(self) -> None:
        compiled = compile_topic_policy(load_topic_policy(DEFAULT_POLICY_PATH))

        self.assertIn("POLICY: ai-manufacturing-plastics@1.2.0", compiled)
        self.assertIn("关键词只是纳入线索", compiled)
        self.assertIn("主要画面语义", compiled)
        self.assertIn("明确排除", compiled)
        self.assertIn("人工智能技术、产业与社会影响", compiled)
        self.assertIn("传统制造业数字化与智能制造", compiled)

    def test_existing_topic_text_resolves_to_default_policy(self) -> None:
        policy = resolve_topic_policy(CURRENT_TOPIC_TEXT)

        self.assertEqual(policy.policy_id, "ai-manufacturing-plastics")
        self.assertEqual(policy.version, "1.2.0")

    def test_unknown_free_text_uses_legacy_compatibility_policy(self) -> None:
        policy = resolve_topic_policy("宠物健康与科学喂养")
        compiled = compile_topic_policy(policy)

        self.assertEqual(policy.policy_id, "legacy-free-text")
        self.assertEqual(policy.version, "1")
        self.assertIn("宠物健康与科学喂养", compiled)
        self.assertIn("用户名、评论、搜索建议", compiled)

    def test_empty_topic_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "主题"):
            resolve_topic_policy("  ")


if __name__ == "__main__":
    unittest.main()
