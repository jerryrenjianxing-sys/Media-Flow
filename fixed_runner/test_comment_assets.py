from __future__ import annotations

import unittest

from comment_assets import select_comment_assets


class CommentAssetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {
            "comment_template": "专业简洁",
            "theme": {
                "comment_template": "关注实际应用",
                "comment_pool": [
                    {"id": "t1", "text": "主题一", "enabled": True},
                    {"id": "t2", "text": "主题二", "enabled": True},
                ],
            },
            "common_comment_pool": [
                {"id": "c1", "text": "通用一", "enabled": True},
                {"id": "c2", "text": "通用二", "enabled": True},
            ],
        }

    def test_theme_candidates_precede_common_and_are_deterministic(self) -> None:
        first = select_comment_assets(self.snapshot, seed=7, video_index=3)
        second = select_comment_assets(self.snapshot, seed=7, video_index=3)
        self.assertEqual(first, second)
        self.assertEqual([item["source"] for item in first.candidates[:2]], ["theme_pool", "theme_pool"])
        self.assertIn("专业简洁", first.template)
        self.assertIn("关注实际应用", first.template)

    def test_used_candidates_are_skipped_and_limit_is_five(self) -> None:
        result = select_comment_assets(
            self.snapshot, seed=1, video_index=1, used_candidate_ids={"t1", "c1"}
        )
        self.assertEqual({item["id"] for item in result.candidates}, {"t2", "c2"})
        self.assertLessEqual(len(result.candidates), 5)

    def test_missing_snapshot_falls_back_to_empty_assets(self) -> None:
        result = select_comment_assets(None, seed=1, video_index=1)
        self.assertEqual(result.template, "")
        self.assertEqual(result.candidates, ())


if __name__ == "__main__":
    unittest.main()
