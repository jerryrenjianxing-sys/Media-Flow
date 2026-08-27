from pathlib import Path
from datetime import datetime, timedelta
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_store import TaskStore  # noqa: E402


class TaskStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.temp.name) / "tasks.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_submit_claim_complete_lifecycle(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        pending = self.store.get(task_id)
        self.assertEqual(pending.status, "pending")

        running = self.store.claim_next("device-1", "worker-1")
        self.assertIsNotNone(running)
        assert running is not None
        self.assertEqual(running.id, task_id)
        self.assertEqual(running.status, "running")

        self.store.finish(
            task_id,
            status="completed",
            run_dir="run/path",
            result={"status": "passed"},
        )
        completed = self.store.get(task_id)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.result, {"status": "passed"})

    def test_claim_is_scoped_to_device(self) -> None:
        self.store.submit("healthcheck", "device-2")
        self.assertIsNone(self.store.claim_next("device-1", "worker-1"))

    def test_profiles_can_be_listed_by_prefix_and_deleted_exactly(self) -> None:
        self.store.save_profile("default", {"video_count": 20})
        self.store.save_profile("preset:均衡", {"video_count": 30})
        self.store.save_profile("preset:长时", {"video_count": 100})

        profiles = self.store.list_profiles("preset:")

        self.assertEqual(
            {item["name"] for item in profiles}, {"preset:均衡", "preset:长时"}
        )
        self.assertTrue(self.store.delete_profile("preset:均衡"))
        self.assertFalse(self.store.delete_profile("preset:不存在"))
        self.assertIsNotNone(self.store.get_profile("default"))
        self.assertIsNone(self.store.get_profile("preset:均衡"))

    def test_future_task_is_not_claimed_early(self) -> None:
        future = (datetime.now().astimezone() + timedelta(minutes=5)).isoformat(
            timespec="milliseconds"
        )
        task_id = self.store.submit(
            "healthcheck", "device-1", not_before=future
        )
        self.assertIsNone(self.store.claim_next("device-1", "worker-1"))
        self.assertFalse(self.store.has_ready("device-1"))
        self.assertEqual(self.store.get(task_id).status, "pending")

    def test_past_task_is_claimed(self) -> None:
        past = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat(
            timespec="milliseconds"
        )
        task_id = self.store.submit("healthcheck", "device-1", not_before=past)
        self.assertTrue(self.store.has_ready("device-1"))
        claimed = self.store.claim_next("device-1", "worker-1")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.id, task_id)

    def test_pause_is_durable_and_blocks_claims_until_resumed(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.store.set_paused(True)
        self.assertTrue(self.store.is_paused())
        self.assertFalse(self.store.has_ready("device-1"))
        self.assertIsNone(self.store.claim_next("device-1", "worker-1"))
        reopened = TaskStore(self.store.path)
        self.assertTrue(reopened.is_paused())

        reopened.set_paused(False)
        self.assertFalse(reopened.is_paused())
        claimed = reopened.claim_next("device-1", "worker-1")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.id, task_id)

    def test_stop_request_blocks_new_claims_without_changing_pause(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.assertEqual(self.store.request_stop(["device-1"]), 1)
        self.assertTrue(self.store.is_stop_requested("device-1"))
        self.assertFalse(self.store.is_paused())
        self.assertFalse(self.store.has_ready("device-1"))
        self.assertIsNone(self.store.claim_next("device-1", "worker-1"))
        self.assertEqual(self.store.get(task_id).status, "pending")
        self.assertEqual(self.store.clear_stop_requests(["device-1"]), 1)
        self.assertIsNotNone(self.store.claim_next("device-1", "worker-1"))

    def test_running_task_can_finish_as_stopped_terminal_state(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "worker-1")
        self.store.finish(
            task_id,
            status="stopped",
            run_dir="run/stopped",
            result={"status": "stopped", "videos_seen": 3},
            error="stopped_by_user",
        )
        task = self.store.get(task_id)
        self.assertEqual(task.status, "stopped")
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(task.result["videos_seen"], 3)

    def test_cancel_pending_is_atomic_and_does_not_touch_running_task(self) -> None:
        pending_id = self.store.submit("healthcheck", "device-1")
        running_id = self.store.submit("healthcheck", "device-2")
        self.store.claim_next("device-2", "worker-2")
        self.assertEqual(self.store.cancel_pending(), 1)
        cancelled = self.store.get(pending_id)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNotNone(cancelled.finished_at)
        self.assertEqual(self.store.get(running_id).status, "running")

    def test_cancel_pending_can_be_scoped_to_task_ids(self) -> None:
        first = self.store.submit("healthcheck", "device-1")
        second = self.store.submit("healthcheck", "device-1")
        self.assertEqual(self.store.cancel_pending([first]), 1)
        self.assertEqual(self.store.get(first).status, "cancelled")
        self.assertEqual(self.store.get(second).status, "pending")

    def test_running_count_can_be_scoped_to_devices(self) -> None:
        self.store.submit("healthcheck", "device-1")
        self.store.submit("healthcheck", "device-2")
        self.store.claim_next("device-1", "worker-1")
        self.assertEqual(self.store.running_count(), 1)
        self.assertEqual(self.store.running_count(["device-1"]), 1)
        self.assertEqual(self.store.running_count(["device-2"]), 0)

    def test_old_database_is_migrated_without_losing_tasks(self) -> None:
        old_path = Path(self.temp.name) / "old-tasks.db"
        connection = sqlite3.connect(old_path)
        connection.execute(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                device_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                worker_id TEXT,
                run_dir TEXT,
                result_json TEXT,
                error TEXT
            )
            """
        )
        created_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        connection.execute(
            "INSERT INTO tasks "
            "(id, task_type, device_id, payload_json, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("old-task", "healthcheck", "device-1", "{}", "pending", created_at),
        )
        connection.commit()
        connection.close()

        migrated = TaskStore(old_path)
        record = migrated.get("old-task")
        self.assertEqual(record.not_before, created_at)
        self.assertEqual(record.status, "pending")

    def test_interrupted_task_is_failed_not_requeued(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "worker-1")
        self.assertEqual(self.store.recover_interrupted("device-1"), 1)
        recovered = self.store.get(task_id)
        self.assertEqual(recovered.status, "failed")
        self.assertIn("not retried", recovered.error or "")

    def test_invalid_benchmark_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.submit(
                "douyin_benchmark", "device-1", {"dwell": [1, 2, 3]}
            )

    def test_benchmark_payload_round_trip(self) -> None:
        task_id = self.store.submit(
            "douyin_benchmark",
            "device-1",
            {"dwell": [1, 2, 3, 4], "max_gate_skips": 2},
        )
        self.assertEqual(
            self.store.get(task_id).payload,
            {"dwell": [1, 2, 3, 4], "max_gate_skips": 2},
        )

    def test_comment_preview_payload_round_trip(self) -> None:
        task_id = self.store.submit(
            "douyin_comment_preview",
            "device-1",
            {"dwell_seconds": 8.0, "max_gate_skips": 3},
        )
        self.assertEqual(
            self.store.get(task_id).payload,
            {"dwell_seconds": 8.0, "max_gate_skips": 3},
        )

    def test_invalid_comment_preview_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.submit(
                "douyin_comment_preview",
                "device-1",
                {"dwell_seconds": -1, "max_gate_skips": 3},
            )

    def test_comment_send_payload_round_trip(self) -> None:
        task_id = self.store.submit(
            "douyin_comment",
            "device-1",
            {"dwell_seconds": 10.0, "max_gate_skips": 2},
        )
        self.assertEqual(self.store.get(task_id).task_type, "douyin_comment")

    def test_two_video_demo_payload_round_trip(self) -> None:
        task_id = self.store.submit(
            "douyin_two_video_demo",
            "device-1",
            {"dwell": [4.0, 7.0], "max_gate_skips": 3},
        )
        self.assertEqual(
            self.store.get(task_id).payload,
            {"dwell": [4.0, 7.0], "max_gate_skips": 3},
        )

    def test_statistics_include_status_type_timing_and_failure(self) -> None:
        completed_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "worker-1")
        self.store.finish(
            completed_id,
            status="completed",
            run_dir="run/completed",
            result={"status": "passed", "wall_s": 12.5},
        )
        failed_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "worker-1")
        self.store.finish(
            failed_id,
            status="failed",
            run_dir="run/failed",
            error="synthetic failure",
        )

        stats = self.store.statistics()
        self.assertEqual(stats["by_status"], {"completed": 1, "failed": 1})
        self.assertEqual(stats["completed_wall_s"]["average"], 12.5)
        self.assertEqual(stats["recent_failures"][0]["id"], failed_id)

    def test_incident_is_saved_with_artifacts_and_summary(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        incident_id = self.store.record_incident(
            task_id=task_id,
            device_id="device-1",
            video_index=3,
            stage="comment",
            error_type="RuntimeError",
            error_message="Comment input box was not found",
            outcome="recovered",
            recovery_action="back_to_feed",
            screenshot_path="artifacts/error.png",
            ui_tree_path="artifacts/error.xml",
            context={"actions": ["like"]},
        )

        incident = self.store.get_incident(incident_id)
        self.assertEqual(incident.task_id, task_id)
        self.assertEqual(incident.video_index, 3)
        self.assertEqual(incident.outcome, "recovered")
        self.assertEqual(incident.analysis_status, "queued")
        self.assertEqual(incident.context, {"actions": ["like"]})
        self.assertTrue(incident.fingerprint)
        self.assertEqual(self.store.incident_statistics()["recovered"], 1)

    def test_recent_incidents_are_newest_first(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        first = self.store.record_incident(
            task_id=task_id,
            device_id="device-1",
            video_index=1,
            stage="swipe",
            error_type="RuntimeError",
            error_message="first",
            outcome="skipped",
            recovery_action="already_main_feed",
        )
        second = self.store.record_incident(
            task_id=task_id,
            device_id="device-1",
            video_index=2,
            stage="comment",
            error_type="RuntimeError",
            error_message="second",
            outcome="recovered",
            recovery_action="back_to_feed",
        )
        self.assertEqual([item.id for item in self.store.list_incidents(2)], [second, first])

    def test_incident_analysis_claim_is_atomic_and_preserves_task_status(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        incident_id = self.store.record_incident(
            task_id=task_id,
            device_id="device-1",
            video_index=1,
            stage="comment",
            error_type="RuntimeError",
            error_message="empty comment panel",
            outcome="skipped",
            recovery_action="continue",
        )

        claimed = self.store.claim_incident_for_analysis()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, incident_id)
        self.assertEqual(claimed.analysis_status, "analyzing")
        self.assertIsNone(self.store.claim_incident_for_analysis())

        finished = self.store.finish_incident_analysis(
            incident_id,
            status="completed",
            analysis={"summary": "空评论区", "auto_applicable": False},
        )
        self.assertEqual(finished.analysis_status, "completed")
        self.assertFalse(finished.analysis["auto_applicable"])
        self.assertEqual(self.store.get(task_id).status, "pending")
        self.assertEqual(self.store.incident_statistics()["analysis_completed"], 1)

    def test_interrupted_incident_analysis_can_be_requeued(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        incident_id = self.store.record_incident(
            task_id=task_id,
            device_id="device-1",
            video_index=1,
            stage="task",
            error_type="RuntimeError",
            error_message="synthetic",
            outcome="skipped",
            recovery_action="continue",
        )
        self.store.claim_incident_for_analysis()
        self.assertEqual(self.store.requeue_interrupted_incident_analyses(), 1)
        self.assertEqual(self.store.get_incident(incident_id).analysis_status, "queued")

    def test_failed_incident_analysis_retry_is_explicit(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        incident_id = self.store.record_incident(
            task_id=task_id,
            device_id="device-1",
            video_index=1,
            stage="task",
            error_type="RuntimeError",
            error_message="synthetic",
            outcome="skipped",
            recovery_action="continue",
        )
        self.store.claim_incident_for_analysis()
        self.store.finish_incident_analysis(
            incident_id,
            status="failed",
            analysis={"summary": "provider unavailable", "auto_applicable": False},
        )
        self.assertEqual(self.store.retry_failed_incident_analyses(), 1)
        retried = self.store.get_incident(incident_id)
        self.assertEqual(retried.analysis_status, "queued")
        self.assertIsNone(retried.analysis)

    def test_tasks_and_incidents_support_stable_pagination(self) -> None:
        task_ids = []
        incident_ids = []
        for index in range(4):
            task_id = self.store.submit("healthcheck", f"device-{index}")
            task_ids.append(task_id)
            incident_ids.append(
                self.store.record_incident(
                    task_id=task_id,
                    device_id=f"device-{index}",
                    video_index=index,
                    stage="test",
                    error_type="RuntimeError",
                    error_message=str(index),
                    outcome="skipped",
                    recovery_action="continue",
                )
            )
        self.assertEqual(
            [item.id for item in self.store.list(limit=2, offset=2)],
            list(reversed(task_ids))[2:4],
        )
        self.assertEqual(
            [item.id for item in self.store.list_incidents(limit=2, offset=1)],
            list(reversed(incident_ids))[1:3],
        )

    def test_clear_all_tasks_removes_tasks_and_incidents(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.store.record_incident(
            task_id=task_id,
            device_id="device-1",
            video_index=1,
            stage="swipe",
            error_type="RuntimeError",
            error_message="synthetic",
            outcome="skipped",
            recovery_action="continue",
        )
        deleted = self.store.clear_all_tasks()
        self.assertEqual(deleted, {"tasks_deleted": 1, "incidents_deleted": 1})
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.list_incidents(), [])

    def test_clear_all_tasks_refuses_while_a_task_is_running(self) -> None:
        self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "worker-1")
        with self.assertRaisesRegex(ValueError, "正在执行"):
            self.store.clear_all_tasks()

    def test_orphaned_running_task_is_closed_before_clear(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "host-98765")

        reconciled = self.store.reconcile_orphaned_running(lambda worker_id: False)

        self.assertEqual(reconciled, 1)
        task = self.store.get(task_id)
        self.assertEqual(task.status, "failed")
        self.assertIsNotNone(task.finished_at)
        self.assertIn("worker_interrupted", task.error or "")
        self.assertEqual(self.store.clear_all_tasks()["tasks_deleted"], 1)

    def test_live_running_task_is_not_reconciled_or_cleared(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "host-12345")

        reconciled = self.store.reconcile_orphaned_running(
            lambda worker_id: worker_id == "host-12345"
        )

        self.assertEqual(reconciled, 0)
        self.assertEqual(self.store.get(task_id).status, "running")
        with self.assertRaisesRegex(ValueError, "正在执行"):
            self.store.clear_all_tasks()

    def test_status_counts_cover_all_terminal_and_active_states(self) -> None:
        completed_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "worker-1")
        self.store.finish(completed_id, status="completed", run_dir=None)
        self.store.submit("healthcheck", "device-2")

        self.assertEqual(
            self.store.task_status_counts(),
            {
                "pending": 1,
                "running": 0,
                "completed": 1,
                "failed": 0,
                "stopped": 0,
                "cancelled": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
