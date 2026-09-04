from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engagement_recovery import recover_version_drift  # noqa: E402
from task_store import TaskStore  # noqa: E402


def inspection_payload() -> dict:
    return {
        "submission_id": "recovery-suite",
        "inspection_index": 1,
        "after_round_index": 1,
        "inspection_every_rounds": 1,
        "max_items_per_section": 20,
        "inspection_workflow_version": "v2",
        "device_id": "device-1",
        "expected_app_version": "40.2.0",
        "expected_display_signature": "1080x2340x480x0x101",
        "inspection_calibration": {
            "profile_version": "engagement-r1",
            "device_id": "device-1",
            "app_version": "40.2.0",
            "display_signature": "1080x2340x480x0x101",
            "passes": 3,
            "later_passes_semantically_equal": True,
            "controls": {"aggregate": ["互动消息"]},
            "sections": {
                "received_likes": True,
                "received_comments": True,
                "received_danmaku": True,
                "profile_visitors": True,
            },
            "coordinate_fallbacks": {"legacy": [0.5, 0.5]},
        },
    }


def drift_result() -> dict:
    return {
        "status": "failed",
        "failure_class": "recoverable_precondition",
        "recovery_eligible": True,
        "navigation_started": False,
        "expected_app_version": "40.2.0",
        "actual_app_version": "40.3.0",
        "expected_display_signature": "1080x2340x480x0x101",
        "actual_display_signature": "1080x2340x480x0x101",
    }


class EngagementRecoveryTest(unittest.TestCase):
    def test_three_matching_semantic_passes_update_profile_and_enqueue_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "device_profiles.json"
            profile_path.write_text(
                json.dumps({"schema_version": 2, "devices": {"device-1": {"friendly_name": "测试机"}}}),
                encoding="utf-8",
            )
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("douyin_engagement_inspection", "device-1", inspection_payload())
            task = store.get(task_id)
            pass_result = {
                "status": "completed",
                "restored": True,
                "sections": {
                    "private_messages": {"status": "available", "complete": True},
                    "received_likes": {"status": "available", "complete": True},
                    "comment_danmaku": {"status": "available", "complete": True},
                    "profile_visitors": {"status": "unavailable", "complete": False, "reason": "visitor_entry_not_found"},
                },
            }
            inspectors = []

            def inspector_factory(*_args, **_kwargs):
                inspector = Mock()
                inspector.inspect.return_value = pass_result
                inspectors.append(inspector)
                return inspector

            with (
                patch("engagement_recovery.EngagementInspector", side_effect=inspector_factory),
                patch(
                    "engagement_recovery.Uia2RunRecorder",
                    side_effect=lambda *_args, **_kwargs: SimpleNamespace(run_dir=root / "evidence"),
                ),
            ):
                recovery = recover_version_drift(
                    store=store,
                    device=object(),
                    task=task,
                    result=drift_result(),
                    artifacts_root=root / "artifacts",
                    profile_path=profile_path,
                )
                repeated = recover_version_drift(
                    store=store,
                    device=object(),
                    task=task,
                    result=drift_result(),
                    artifacts_root=root / "artifacts",
                    profile_path=profile_path,
                )

            self.assertEqual(recovery["status"], "ready")
            self.assertEqual(repeated["id"], recovery["id"])
            self.assertEqual(len(inspectors), 3)
            replacement = store.get(recovery["replacement_task_id"])
            self.assertEqual(replacement.payload["recovery_parent_task_id"], task_id)
            self.assertEqual(replacement.payload["expected_app_version"], "40.3.0")
            self.assertNotIn("coordinate_fallbacks", replacement.payload["inspection_calibration"])
            profile = json.loads(profile_path.read_text(encoding="utf-8"))["devices"]["device-1"]
            self.assertEqual(profile["engagement_app_version"], "40.3.0")

    def test_inconsistent_passes_wait_for_user_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "device_profiles.json"
            store = TaskStore(root / "tasks.db")
            task = store.get(
                store.submit("douyin_engagement_inspection", "device-1", inspection_payload())
            )
            first = {"status": "completed", "restored": True, "sections": {"profile_visitors": {"status": "available", "complete": True}}}
            changed = {"status": "degraded", "restored": True, "sections": {"profile_visitors": {"status": "unavailable", "complete": False}}}
            inspector = Mock()
            inspector.inspect.side_effect = [first, first, changed]
            with (
                patch("engagement_recovery.EngagementInspector", return_value=inspector),
                patch("engagement_recovery.Uia2RunRecorder", return_value=SimpleNamespace(run_dir=root / "evidence")),
            ):
                recovery = recover_version_drift(
                    store=store,
                    device=object(),
                    task=task,
                    result=drift_result(),
                    artifacts_root=root / "artifacts",
                    profile_path=profile_path,
                )
            self.assertEqual(recovery["status"], "waiting_user")
            self.assertIsNone(recovery["replacement_task_id"])

    def test_v3_revalidation_preserves_v3_profile_and_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "device_profiles.json"
            store = TaskStore(root / "tasks.db")
            payload = inspection_payload()
            payload["inspection_workflow_version"] = "v3"
            payload["inspection_calibration"]["profile_version"] = "mediaflow-engagement-v3-r1"
            task = store.get(store.submit("douyin_engagement_inspection", "device-1", payload))
            pass_result = {
                "status": "completed",
                "restored": True,
                "workflow_version": "v3",
                "unified_activity": {
                    "status": "available",
                    "complete": True,
                    "read_boundary": "first_screen",
                    "reason_code": None,
                },
                "sections": {
                    "received_likes": {"status": "available", "complete": True},
                    "comment_danmaku": {"status": "available", "complete": True},
                    "profile_visitors": {"status": "available", "complete": True},
                },
            }
            inspector = Mock()
            inspector.inspect.return_value = pass_result
            with (
                patch("engagement_recovery.EngagementInspector", return_value=inspector),
                patch(
                    "engagement_recovery.Uia2RunRecorder",
                    return_value=SimpleNamespace(run_dir=root / "evidence"),
                ),
            ):
                recovery = recover_version_drift(
                    store=store,
                    device=object(),
                    task=task,
                    result=drift_result(),
                    artifacts_root=root / "artifacts",
                    profile_path=profile_path,
                )

            self.assertEqual(recovery["status"], "ready")
            replacement = store.get(recovery["replacement_task_id"])
            self.assertEqual(replacement.payload["inspection_workflow_version"], "v3")
            profile = json.loads(profile_path.read_text(encoding="utf-8"))["devices"]["device-1"]
            self.assertEqual(profile["engagement_inspection_version"], "v3")


if __name__ == "__main__":
    unittest.main()
