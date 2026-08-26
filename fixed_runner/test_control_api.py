from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

from control_api import (
    DEFAULT_CONFIG,
    _pid_is_running,
    build_status_payload,
    normalized_config,
    submit_scheduled_rounds,
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

    def test_worker_status_rejects_reused_pid_from_non_worker_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "worker.pid"
            pid_path.write_text("12345", encoding="ascii")
            with (
                patch("control_api._worker_pid_path", return_value=pid_path),
                patch("control_api._pid_is_running", return_value=True),
                patch("control_api._process_image_name", return_value="python.exe"),
            ):
                status = worker_status("device-1")

        self.assertFalse(status["running"])

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
        self.assertEqual(config["max_comments"], 1)
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

    def test_probability_out_of_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "like_probability"):
            normalized_config({**DEFAULT_CONFIG, "like_probability": 1.2})

    def test_unlimited_topic_mode_does_not_require_topic_text(self) -> None:
        config = normalized_config(
            {**DEFAULT_CONFIG, "topic_filter_enabled": False, "topic_prompt": ""}
        )
        self.assertFalse(config["topic_filter_enabled"])

    def test_two_action_groups_can_require_topic_independently(self) -> None:
        config = normalized_config(
            {
                **DEFAULT_CONFIG,
                "engagement_requires_topic": True,
                "comment_requires_topic": False,
                "topic_prompt": "宠物日常",
            }
        )
        self.assertTrue(config["engagement_requires_topic"])
        self.assertFalse(config["comment_requires_topic"])
        self.assertTrue(config["topic_filter_enabled"])
        self.assertTrue(config["like_only_on_match"])

    def test_long_run_limits_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "round_count"):
            normalized_config({**DEFAULT_CONFIG, "round_count": 21})
        with self.assertRaisesRegex(ValueError, "max_comments"):
            normalized_config({**DEFAULT_CONFIG, "max_comments": 201})
        with self.assertRaisesRegex(ValueError, "video_count"):
            normalized_config({**DEFAULT_CONFIG, "video_count": 201})

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


if __name__ == "__main__":
    unittest.main()
