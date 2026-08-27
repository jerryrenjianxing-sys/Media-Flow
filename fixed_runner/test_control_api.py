from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

from control_api import (
    BUILTIN_PRESETS,
    DEFAULT_CONFIG,
    PRESET_FIELDS,
    _pid_is_running,
    build_status_payload,
    comment_screenshot_path,
    delete_preset,
    list_presets,
    normalized_config,
    save_preset,
    submit_scheduled_rounds,
    validate_preset_name,
    validate_openrouter_key,
    worker_id_is_running,
    worker_status,
)
from task_store import TaskStore


class ControlApiTest(unittest.TestCase):
    def test_current_python_process_is_recognized_as_a_live_worker(self) -> None:
        self.assertTrue(_pid_is_running(os.getpid()))
        self.assertTrue(worker_id_is_running(f"test-host-{os.getpid()}"))
        self.assertFalse(worker_id_is_running("malformed-worker"))

    def test_worker_status_uses_verified_runtime_identity(self) -> None:
        with patch("control_api.RuntimeControl.status") as status:
            status.return_value = {
                "role": "worker-device-1",
                "running": False,
                "pid": 12345,
                "identity": "command_mismatch",
            }
            result = worker_status("device-1")
        self.assertFalse(result["running"])
        self.assertEqual(result["identity"], "command_mismatch")

    def test_default_config_is_valid(self) -> None:
        config = normalized_config(DEFAULT_CONFIG)
        self.assertTrue(config["preview_only"])
        self.assertFalse(config["topic_filter_enabled"])
        self.assertFalse(config["engagement_requires_topic"])
        self.assertFalse(config["comment_requires_topic"])
        self.assertEqual(config["video_count"], 20)
        self.assertEqual(config["like_probability"], 0.2)
        self.assertEqual(config["favorite_probability"], 0.1)
        self.assertEqual(config["comment_probability"], 0.05)
        self.assertEqual(config["max_comments"], config["video_count"])
        self.assertGreater(config["dwell_max"], config["dwell_min"])
        self.assertEqual(config["round_count"], 1)

    def test_empty_run_payload_can_reuse_saved_multi_device_config(self) -> None:
        saved = {**DEFAULT_CONFIG, "device_ids": ["device-1", "device-2"]}
        self.assertEqual(normalized_config(saved)["device_ids"], ["device-1", "device-2"])

    def test_profile_round_trip_uses_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_profile("default", DEFAULT_CONFIG)
            self.assertEqual(store.get_profile("default"), DEFAULT_CONFIG)

    def test_builtin_presets_are_read_only_and_complete(self) -> None:
        self.assertEqual(
            list(BUILTIN_PRESETS), ["保守预演", "均衡测试", "长时稳定性"]
        )
        with self.assertRaisesRegex(ValueError, "内置预设不能覆盖"):
            validate_preset_name("均衡测试")
        with self.assertRaisesRegex(ValueError, "请输入预设名称"):
            validate_preset_name("   ")

    def test_custom_preset_round_trip_isolated_from_runtime_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_profile("default", DEFAULT_CONFIG)
            source = {
                **DEFAULT_CONFIG,
                "video_count": 37,
                "device_id": "private-device",
                "device_ids": ["private-device"],
                "seed": 99,
                "preview_only": False,
            }

            created = save_preset(store, "我的预设", source)
            updated = save_preset(store, "我的预设", {**source, "video_count": 41})
            listed = list_presets(store)

            self.assertEqual(created["config"]["video_count"], 37)
            self.assertEqual(updated["config"]["video_count"], 41)
            self.assertEqual(set(updated["config"]), set(PRESET_FIELDS))
            self.assertNotIn("device_ids", updated["config"])
            self.assertNotIn("seed", updated["config"])
            self.assertNotIn("preview_only", updated["config"])
            self.assertEqual(len([item for item in listed if item["name"] == "我的预设"]), 1)
            self.assertEqual(store.get_profile("default"), DEFAULT_CONFIG)
            self.assertTrue(delete_preset(store, "我的预设"))
            self.assertFalse(delete_preset(store, "我的预设"))

    def test_preset_rejects_non_object_config_and_long_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            with self.assertRaisesRegex(ValueError, "参数格式无效"):
                save_preset(store, "测试", "not-an-object")
            with self.assertRaisesRegex(ValueError, "最多 40"):
                save_preset(store, "太" * 41, DEFAULT_CONFIG)

    def test_probability_out_of_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "like_probability"):
            normalized_config({**DEFAULT_CONFIG, "like_probability": 1.2})

    def test_unlimited_topic_mode_does_not_require_topic_text(self) -> None:
        config = normalized_config(
            {**DEFAULT_CONFIG, "topic_filter_enabled": False, "topic_prompt": ""}
        )
        self.assertFalse(config["topic_filter_enabled"])

    def test_legacy_topic_switches_migrate_to_mixed_mode(self) -> None:
        config = normalized_config(
            {
                **DEFAULT_CONFIG,
                "content_mode": "mixed",
                "engagement_requires_topic": True,
                "comment_requires_topic": False,
                "topic_prompt": "宠物日常",
            }
        )
        self.assertEqual(config["content_mode"], "mixed")
        self.assertTrue(config["topic_filter_enabled"])
        self.assertFalse(config["engagement_requires_topic"])
        self.assertFalse(config["comment_requires_topic"])
        self.assertFalse(config["like_only_on_match"])

    def test_long_run_limits_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "round_count"):
            normalized_config({**DEFAULT_CONFIG, "round_count": 21})
        with self.assertRaisesRegex(ValueError, "video_count"):
            normalized_config({**DEFAULT_CONFIG, "video_count": 201})

    def test_legacy_action_caps_are_ignored_and_normalized_to_video_count(self) -> None:
        config = normalized_config(
            {
                **DEFAULT_CONFIG,
                "video_count": 37,
                "max_likes": 0,
                "max_favorites": 1,
                "max_comments": 9999,
            }
        )
        self.assertEqual(config["max_likes"], 37)
        self.assertEqual(config["max_favorites"], 37)
        self.assertEqual(config["max_comments"], 37)
        self.assertNotIn("max_likes", PRESET_FIELDS)
        self.assertNotIn("max_favorites", PRESET_FIELDS)
        self.assertNotIn("max_comments", PRESET_FIELDS)

    def test_legacy_anomaly_threshold_is_migrated_into_safe_range(self) -> None:
        self.assertEqual(normalized_config({**DEFAULT_CONFIG, "max_gate_skips": 0})["max_gate_skips"], 1)
        self.assertEqual(normalized_config({**DEFAULT_CONFIG, "max_gate_skips": 99})["max_gate_skips"], 50)

    def test_scheduled_rounds_have_distinct_seeds_and_times(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_id": "device-1",
                    "device_ids": ["device-1"],
                    "round_count": 3,
                    "round_interval_minutes": 15,
                }
            )
            task_ids = submit_scheduled_rounds(store, config)
            tasks = [store.get(task_id) for task_id in task_ids]
            self.assertEqual(len(tasks), 3)
            self.assertEqual([task.payload["seed"] for task in tasks], [20260821, 20260822, 20260823])
            scheduled = [datetime.fromisoformat(task.not_before) for task in tasks]
            self.assertEqual(
                [round((value - scheduled[0]).total_seconds() / 60) for value in scheduled],
                [0, 15, 30],
            )

    def test_scheduled_rounds_are_distributed_across_devices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_ids": ["device-1", "device-2"],
                    "round_count": 2,
                    "round_interval_minutes": 5,
                }
            )
            task_ids = submit_scheduled_rounds(store, config)
            tasks = [store.get(task_id) for task_id in task_ids]
            self.assertEqual(len(tasks), 4)
            self.assertEqual(
                [task.device_id for task in tasks],
                ["device-1", "device-1", "device-2", "device-2"],
            )
            self.assertEqual(
                [task.payload["seed"] for task in tasks],
                [20260821, 20260822, 20260823, 20260824],
            )

    def test_openrouter_key_format_is_validated_without_echoing_value(self) -> None:
        valid = "sk-or-v1-" + "a" * 64
        self.assertEqual(validate_openrouter_key(valid), valid)
        with self.assertRaisesRegex(ValueError, "OpenRouter"):
            validate_openrouter_key("not-a-key")

    def test_status_payload_includes_recent_correction_incidents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=2,
                stage="comment",
                error_type="RuntimeError",
                error_message="comment input missing",
                outcome="recovered",
                recovery_action="back_to_feed",
            )
            with (
                patch(
                    "control_api.device_statuses",
                    return_value=[{"device_id": "device-1", "state": "device"}],
                ),
                patch(
                    "control_api.worker_status",
                    return_value={"device_id": "device-1", "running": True, "pid": 1},
                ),
            ):
                payload = build_status_payload(
                    store,
                    normalized_config({**DEFAULT_CONFIG, "device_ids": ["device-1"]}),
                )
        self.assertEqual(payload["incident_summary"]["recovered"], 1)
        self.assertEqual(payload["incidents"][0]["video_index"], 2)
        self.assertEqual(payload["incidents"][0]["analysis_status"], "queued")

    def test_status_payload_is_bounded_to_five_tasks_and_incidents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            for index in range(7):
                task_id = store.submit("healthcheck", f"device-{index}")
                store.record_incident(
                    task_id=task_id,
                    device_id=f"device-{index}",
                    video_index=index,
                    stage="test",
                    error_type="RuntimeError",
                    error_message=f"incident-{index}",
                    outcome="skipped",
                    recovery_action="continue",
                )
            with (
                patch("control_api.device_statuses", return_value=[{"device_id": "device-0", "state": "device"}]),
                patch("control_api.worker_status", return_value={"device_id": "device-0", "running": False, "pid": None}),
            ):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        self.assertEqual(len(payload["tasks"]), 5)
        self.assertEqual(len(payload["incidents"]), 5)
        self.assertEqual(payload["task_summary"]["pending"], 7)
        self.assertEqual(payload["incident_summary"]["total"], 7)

    def test_status_payload_closes_task_whose_worker_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "host-98765")
            with (
                patch(
                    "control_api.device_statuses",
                    return_value=[{"device_id": "device-1", "state": "device"}],
                ),
                patch(
                    "control_api.worker_status",
                    return_value={"device_id": "device-1", "running": False, "pid": None},
                ),
                patch("control_api.worker_id_is_running", return_value=False),
            ):
                payload = build_status_payload(
                    store,
                    normalized_config({**DEFAULT_CONFIG, "device_ids": ["device-1"]}),
                )

        task = next(item for item in payload["tasks"] if item["id"] == task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIsNotNone(task["finished_at"])
        self.assertEqual(payload["task_summary"]["running"], 0)
        self.assertEqual(payload["task_summary"]["failed"], 1)

    def test_comment_screenshot_path_accepts_registered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            image = artifacts / "runs" / "run-1" / "video-2-comment-sent.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="completed",
                run_dir=str(image.parent),
                result={
                    "comment_screenshots": [
                        {"video_index": 2, "screenshot_path": str(image)}
                    ]
                },
            )
            with patch("control_api.DEFAULT_ARTIFACTS", artifacts):
                resolved = comment_screenshot_path(store, task_id, 2)
        self.assertEqual(resolved, image.resolve())

    def test_comment_screenshot_path_rejects_unregistered_or_outside_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(b"png")
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="completed",
                run_dir=str(root),
                result={
                    "comment_screenshots": [
                        {"video_index": 1, "screenshot_path": str(outside)}
                    ]
                },
            )
            with patch("control_api.DEFAULT_ARTIFACTS", artifacts):
                with self.assertRaises(KeyError):
                    comment_screenshot_path(store, task_id, 1)
                with self.assertRaises(KeyError):
                    comment_screenshot_path(store, task_id, 9)


if __name__ == "__main__":
    unittest.main()
