import tempfile
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from control_config import DEFAULT_CONFIG, build_scheduled_plan, normalized_config
from run_planning import build_preview, get_or_create_draft, save_draft
from task_store import RunDraftConflict, TaskStore


def base_config() -> dict:
    return {
        "device_id": "device-a",
        "device_ids": ["device-a"],
        "content_mode": "general",
        "topic_prompt": "不限主题",
        "video_count": 2,
        "round_count": 1,
        "round_interval_minutes": 0,
        "dwell_min": 1,
        "dwell_max": 2,
        "like_probability": 0,
        "favorite_probability": 0,
        "comment_probability": 0.5,
        "matched_like_probability": 0,
        "matched_favorite_probability": 0,
        "matched_comment_probability": 0,
        "preview_only": True,
    }


class RunPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.temp.name) / "tasks.db")
        self.devices = [
            {
                "device_id": "device-a",
                "friendly_name": "测试机",
                "state": "device",
                "profile_verified": True,
                "initialization_status": "legacy",
            }
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_draft_versions_and_rejects_stale_write(self) -> None:
        first = get_or_create_draft(self.store, base_config())
        second = save_draft(
            self.store,
            {**first["config"], "video_count": 3},
            expected_revision=first["revision"],
        )
        self.assertEqual(second["revision"], first["revision"] + 1)
        with self.assertRaises(RunDraftConflict):
            save_draft(
                self.store,
                {**first["config"], "video_count": 4},
                expected_revision=first["revision"],
            )

    def test_empty_device_draft_is_valid_but_submission_plan_is_not(self) -> None:
        config = normalized_config(DEFAULT_CONFIG)
        self.assertEqual(config["device_ids"], [])
        self.assertEqual(config["device_id"], "")
        with self.assertRaisesRegex(ValueError, "至少一台"):
            build_scheduled_plan(config)

    def test_comment_preview_is_not_a_write_action(self) -> None:
        draft = get_or_create_draft(self.store, base_config())
        preview = build_preview(
            self.store, draft, devices=self.devices, paused=False
        )
        self.assertTrue(preview["ready"])
        self.assertFalse(preview["requires_confirmation"])
        self.assertEqual(preview["comment_mode"], "仅生成预览")

    def test_like_probability_requires_confirmation(self) -> None:
        draft = get_or_create_draft(
            self.store, {**base_config(), "like_probability": 0.2}
        )
        preview = build_preview(
            self.store, draft, devices=self.devices, paused=False
        )
        self.assertTrue(preview["requires_confirmation"])
        self.assertEqual(preview["write_actions"], ["点赞"])

    def test_unavailable_device_blocks_preview(self) -> None:
        draft = get_or_create_draft(self.store, base_config())
        preview = build_preview(
            self.store,
            draft,
            devices=[{"device_id": "device-a", "state": "offline"}],
            paused=False,
        )
        self.assertFalse(preview["ready"])
        self.assertEqual(preview["total_task_count"], 0)

    def test_hybrid_preview_exposes_search_and_home_segment_ranges(self) -> None:
        draft = get_or_create_draft(
            self.store,
            {
                **base_config(),
                "content_mode": "hybrid",
                "topic_prompt": "智能制造",
                "search_query": "智能制造",
                "search_segment_min": 7,
                "search_segment_max": 14,
                "home_segment_min": 5,
                "home_segment_max": 10,
            },
        )
        preview = build_preview(
            self.store, draft, devices=self.devices, paused=False
        )
        self.assertEqual(
            preview["segments"],
            {"search": {"min": 7, "max": 14}, "home": {"min": 5, "max": 10}},
        )

    def test_model_required_mode_is_blocked_until_current_model_test_passes(self) -> None:
        draft = get_or_create_draft(
            self.store,
            {**base_config(), "content_mode": "search", "search_query": "智能制造"},
        )
        blocked = build_preview(
            self.store,
            draft,
            devices=self.devices,
            paused=False,
            model_status={"model_ready": False},
        )
        self.assertFalse(blocked["ready"])
        self.assertTrue(blocked["model_required"])
        ready = build_preview(
            self.store,
            draft,
            devices=self.devices,
            paused=False,
            model_status={"model_ready": True},
        )
        self.assertTrue(ready["ready"])

    def test_non_hybrid_preview_ignores_stale_segment_values(self) -> None:
        draft = get_or_create_draft(
            self.store,
            {
                **base_config(),
                "search_segment_min": 0,
                "search_segment_max": 999,
                "home_segment_min": 0,
                "home_segment_max": 999,
            },
        )
        preview = build_preview(
            self.store, draft, devices=self.devices, paused=False
        )
        self.assertIsNone(preview["segments"])

    def test_v3_preview_rejects_physical_or_nonstandard_device(self) -> None:
        draft = get_or_create_draft(
            self.store,
            {**base_config(), "engagement_inspection_enabled": True},
        )
        with patch(
            "run_planning.inspection_profiles_for_store",
            return_value={"device-a": {"device_kind": "physical"}},
        ):
            preview = build_preview(
                self.store, draft, devices=self.devices, paused=True
            )

        self.assertFalse(preview["ready"])
        self.assertIn(
            "互动巡检 v3 仅支持已复验的900×1600标准虚拟机",
            preview["blockers"],
        )


if __name__ == "__main__":
    unittest.main()
