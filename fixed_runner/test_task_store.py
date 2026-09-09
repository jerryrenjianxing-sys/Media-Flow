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
    def test_model_incidents_aggregate_without_changing_task(self):
        task = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "test-worker")
        self.store.finish(task, status="failed", run_dir="run/path", result={"status": "failed"})
        ids = [self.store.record_incident(task_id=task, device_id="device-1", video_index=i, stage="topic_model",
            error_type="CloudModelError", error_message="HTTP 403", outcome="model_failed", recovery_action="none",
            context={"model_error_fingerprint": "same-rejection"}) for i in range(3)]
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.store.get_incident(ids[0]).context["occurrence_count"], 3)
        self.assertEqual(self.store.get(task).status, "failed")

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

    def test_read_only_view_can_coexist_with_tasks(self) -> None:
        session = self.store.create_device_view_session(
            device_id="device-1",
            virtual_device_id="virtual-1",
            mode="read_only",
            stream_profile="wall",
            token_hash="a" * 64,
        )
        task_id = self.store.submit("healthcheck", "device-1")

        self.assertEqual(session["mode"], "read_only")
        self.assertEqual(self.store.claim_next("device-1", "worker-1").id, task_id)

    def test_control_view_blocks_task_and_initialization_until_closed(self) -> None:
        session = self.store.create_device_view_session(
            device_id="device-1",
            virtual_device_id="virtual-1",
            mode="control",
            stream_profile="focus",
            token_hash="b" * 64,
        )

        self.assertTrue(self.store.has_active_control_session("device-1"))
        with self.assertRaisesRegex(ValueError, "人工接管"):
            self.store.submit("healthcheck", "device-1")
        with self.assertRaisesRegex(ValueError, "人工接管"):
            self.store.create_initialization("device-1")

        self.store.close_device_view_session(session["id"])
        self.assertFalse(self.store.has_active_control_session("device-1"))
        self.assertIsInstance(self.store.submit("healthcheck", "device-1"), str)

    def test_control_view_refuses_existing_task_or_initialization(self) -> None:
        self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "test-worker")
        with self.assertRaisesRegex(ValueError, "当前只能观看"):
            self.store.create_device_view_session(
                device_id="device-1",
                virtual_device_id="virtual-1",
                mode="control",
                stream_profile="focus",
                token_hash="c" * 64,
            )

        self.store.create_initialization("device-2")
        self.store.claim_initialization("device-2", "test-worker")
        with self.assertRaisesRegex(ValueError, "正在初始化"):
            self.store.create_device_view_session(
                device_id="device-2",
                virtual_device_id="virtual-2",
                mode="control",
                stream_profile="focus",
                token_hash="d" * 64,
            )

    def test_focus_view_closes_wall_and_token_is_verified(self) -> None:
        wall = self.store.create_device_view_session(
            device_id="device-1",
            virtual_device_id="virtual-1",
            mode="read_only",
            stream_profile="wall",
            token_hash="e" * 64,
        )
        focus = self.store.create_device_view_session(
            device_id="device-1",
            virtual_device_id="virtual-1",
            mode="read_only",
            stream_profile="focus",
            token_hash="f" * 64,
        )

        self.assertEqual(self.store.get_device_view_session(wall["id"])["status"], "closed")
        with self.assertRaises(KeyError):
            self.store.validate_device_view_session(focus["id"], "0" * 64)
        connected = self.store.validate_device_view_session(focus["id"], "f" * 64)
        self.assertTrue(connected["connected"])

    def test_disconnected_control_lease_expires_without_replay(self) -> None:
        session = self.store.create_device_view_session(
            device_id="device-1",
            virtual_device_id="virtual-1",
            mode="control",
            stream_profile="focus",
            token_hash="1" * 64,
        )
        self.store.heartbeat_device_view_session(session["id"], connected=False)
        past = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat(
            timespec="milliseconds"
        )
        with self.store.connection() as connection:
            connection.execute(
                "UPDATE device_view_sessions SET lease_expires_at=? WHERE id=?",
                (past, session["id"]),
            )

        self.assertFalse(self.store.has_active_control_session("device-1"))
        expired = self.store.get_device_view_session(session["id"])
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["close_reason"], "lease_expired")

    def test_disconnected_read_only_view_closes_immediately(self) -> None:
        session = self.store.create_device_view_session(
            device_id="device-1",
            virtual_device_id="virtual-1",
            mode="read_only",
            stream_profile="wall",
            token_hash="2" * 64,
        )

        closed = self.store.heartbeat_device_view_session(
            session["id"], connected=False
        )

        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["close_reason"], "stream_disconnected")

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

    def test_running_task_exposes_run_dir_before_terminal_result(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "worker-1")
        self.store.attach_run_dir(task_id, "runs/live-task")
        task = self.store.get(task_id)
        self.assertEqual(task.status, "running")
        self.assertEqual(task.run_dir, "runs/live-task")
        self.assertIsNone(task.result)

        pending_id = self.store.submit("healthcheck", "device-2")
        with self.assertRaises(RuntimeError):
            self.store.attach_run_dir(pending_id, "runs/not-started")

    def test_running_task_can_finish_as_degraded_terminal_state(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        self.store.claim_next("device-1", "worker-1")
        self.store.finish(
            task_id,
            status="degraded",
            run_dir="run/degraded",
            result={
                "status": "degraded",
                "videos_seen": 20,
                "degraded_reason": {"code": "model_channel_partial"},
            },
            error="model_channel_partial",
        )
        task = self.store.get(task_id)
        self.assertEqual(task.status, "degraded")
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(task.result["videos_seen"], 20)

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

    def test_initialization_create_is_idempotent_while_active(self) -> None:
        first = self.store.create_initialization(
            "device-1", options={"write_acceptance": False}
        )
        second = self.store.create_initialization(
            "device-1", options={"write_acceptance": True}
        )
        self.assertEqual(first.id, second.id)
        self.assertFalse(second.options["write_acceptance"])
        claimed = self.store.claim_initialization("device-1", "worker-1")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.status, "running")

    def test_waiting_initialization_can_continue_from_checkpoint(self) -> None:
        queued = self.store.create_initialization("device-1")
        self.store.claim_initialization("device-1", "worker-1")
        self.store.finish_initialization(
            queued.id,
            status="waiting_user",
            stage="waiting_user",
            message="请在手机确认安装",
        )
        continued = self.store.continue_initialization("device-1")
        self.assertEqual(continued.status, "queued")
        self.assertEqual(
            self.store.claim_initialization("device-1", "worker-2").id,
            queued.id,
        )

    def test_running_initialization_cancel_is_checkpointed(self) -> None:
        queued = self.store.create_initialization("device-1")
        self.store.claim_initialization("device-1", "worker-1")
        requested = self.store.cancel_initialization("device-1")
        self.assertTrue(requested.cancel_requested)
        self.assertEqual(requested.status, "running")
        self.assertTrue(self.store.initialization_cancel_requested(queued.id))

    def test_interrupted_initialization_fails_without_requeue(self) -> None:
        queued = self.store.create_initialization("device-1")
        self.store.claim_initialization("device-1", "worker-1")
        self.assertEqual(
            self.store.recover_interrupted_initializations("device-1"), 1
        )
        recovered = self.store.get_initialization(queued.id)
        self.assertEqual(recovered.status, "failed")
        self.assertIn("not retried", recovered.error or "")

    def test_invalid_benchmark_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.submit(
                "douyin_benchmark", "device-1", {"dwell": [1, 2, 3]}
            )

    def test_hybrid_topic_payload_validates_segment_ranges(self) -> None:
        payload = {
            "video_count": 20,
            "round_count": 1,
            "round_interval_minutes": 0,
            "max_gate_skips": 6,
            "dwell_min": 1,
            "dwell_max": 2,
            "topic_filter_enabled": True,
            "engagement_requires_topic": False,
            "comment_requires_topic": True,
            "content_mode": "hybrid",
            "topic_prompt": "智能制造",
            "search_query": "智能制造",
            "search_trust_results": True,
            "search_segment_min": 7,
            "search_segment_max": 14,
            "home_segment_min": 5,
            "home_segment_max": 10,
            "like_probability": 0.2,
            "favorite_probability": 0.1,
            "comment_probability": 0.05,
            "matched_like_probability": 0.8,
            "matched_favorite_probability": 0.7,
            "matched_comment_probability": 0.5,
            "seed": 1,
            "preview_only": True,
        }
        TaskStore.validate_payload("douyin_topic_session", payload)
        for invalid in (
            {**payload, "search_segment_min": 0},
            {**payload, "search_segment_min": 15, "search_segment_max": 14},
            {**payload, "home_segment_max": 201},
            {**payload, "home_segment_min": 11, "home_segment_max": 10},
        ):
            with self.assertRaises(ValueError):
                TaskStore.validate_payload("douyin_topic_session", invalid)

        legacy = dict(payload)
        legacy.update(content_mode="search")
        for name in (
            "search_segment_min",
            "search_segment_max",
            "home_segment_min",
            "home_segment_max",
        ):
            legacy.pop(name)
        TaskStore.validate_payload("douyin_topic_session", legacy)

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

    def test_engagement_inspection_payload_round_trip(self) -> None:
        payload = {
            "submission_id": "submission-1",
            "inspection_index": 1,
            "after_round_index": 5,
            "inspection_every_rounds": 5,
            "max_items_per_section": 20,
        }
        task_id = self.store.submit(
            "douyin_engagement_inspection", "device-1", payload
        )
        self.assertEqual(self.store.get(task_id).payload, payload)

    def test_engagement_inspection_payload_rejects_missing_invalid_and_round_index(self) -> None:
        valid = {
            "submission_id": "submission-1",
            "inspection_index": 1,
            "after_round_index": 5,
            "inspection_every_rounds": 5,
            "max_items_per_section": 20,
        }
        for invalid in (
            {**valid, "submission_id": ""},
            {**valid, "inspection_index": 0},
            {**valid, "after_round_index": 21},
            {**valid, "inspection_every_rounds": -1},
            {**valid, "max_items_per_section": 101},
            {**valid, "round_index": 5},
        ):
            with self.assertRaises(ValueError):
                self.store.submit(
                    "douyin_engagement_inspection", "device-1", invalid
                )

    def test_engagement_inspection_obeys_pause_stop_and_cancel(self) -> None:
        payload = {
            "submission_id": "submission-lifecycle",
            "inspection_index": 1,
            "after_round_index": 5,
            "inspection_every_rounds": 5,
            "max_items_per_section": 20,
        }
        paused_id = self.store.submit(
            "douyin_engagement_inspection", "device-1", payload
        )
        self.store.set_paused(True)
        self.assertIsNone(self.store.claim_next("device-1", "worker-1"))
        self.store.set_paused(False)
        self.store.request_stop(["device-1"])
        self.assertIsNone(self.store.claim_next("device-1", "worker-1"))
        self.store.clear_stop_requests(["device-1"])
        self.assertEqual(self.store.cancel_pending([paused_id]), 1)
        self.assertEqual(self.store.get(paused_id).status, "cancelled")

    def test_superseded_pending_vm_inspections_cancel_without_touching_physical(self) -> None:
        self.store.save_virtual_device(
            {
                "virtual_device_id": "virtual-1",
                "provider": "mumu",
                "provider_instance_id": "1",
                "name": "MediaFlow虚拟机1",
                "state": "stopped",
                "recipe": {},
                "provider_snapshot": {},
                "last_adb_endpoint": "127.0.0.1:16416",
                "discovery_source": "mediaflow_created",
                "managed": True,
            }
        )
        payload = {
            "submission_id": "submission-old",
            "device_id": "127.0.0.1:16416",
            "inspection_index": 1,
            "after_round_index": 5,
            "inspection_every_rounds": 5,
            "max_items_per_section": 20,
            "inspection_workflow_version": "v2",
            "expected_app_version": "35.8.0",
            "expected_display_signature": "900x1600x320x0x100",
            "inspection_calibration": {
                "profile_version": "old-v2",
                "device_id": "127.0.0.1:16416",
                "app_version": "35.8.0",
                "display_signature": "900x1600x320x0x100",
                "passes": 3,
                "later_passes_semantically_equal": True,
                "controls": {},
                "sections": {},
            },
        }
        virtual_id = self.store.submit(
            "douyin_engagement_inspection", "127.0.0.1:16416", payload
        )
        physical_id = self.store.submit(
            "douyin_engagement_inspection",
            "physical-1",
            {
                **payload,
                "inspection_workflow_version": "v1",
                "device_id": "physical-1",
            },
        )

        self.assertEqual(
            self.store.cancel_superseded_virtual_engagement_inspections(), 1
        )
        self.assertEqual(self.store.get(virtual_id).status, "cancelled")
        self.assertEqual(
            self.store.get(virtual_id).error,
            "superseded_by_engagement_inspection_v3",
        )
        self.assertEqual(self.store.get(physical_id).status, "pending")
        self.assertEqual(
            self.store.cancel_superseded_virtual_engagement_inspections(), 0
        )

    def test_v3_shared_calibration_is_not_bound_to_source_device_or_app_minor(self) -> None:
        payload = {
            "submission_id": "shared-rule",
            "device_id": "127.0.0.1:16512",
            "inspection_index": 1,
            "after_round_index": 1,
            "inspection_every_rounds": 1,
            "max_items_per_section": 100,
            "inspection_workflow_version": "v3",
            "expected_app_version": "40.4.0",
            "expected_display_signature": "900x1600x320x0x2",
            "inspection_calibration": {
                "profile_version": "mediaflow-engagement-v3-r1",
                "device_id": "127.0.0.1:16416",
                "app_version": "40.3.0",
                "display_signature": "900x1600x320x0x100",
                "passes": 3,
                "later_passes_semantically_equal": True,
                "controls": {"aggregate": ["赞评收藏"]},
                "sections": {},
            },
        }
        task_id = self.store.submit(
            "douyin_engagement_inspection", "127.0.0.1:16512", payload
        )
        self.assertEqual(self.store.get(task_id).status, "pending")

    def test_interrupted_engagement_inspection_is_not_replayed(self) -> None:
        payload = {
            "submission_id": "submission-interrupted",
            "inspection_index": 1,
            "after_round_index": 5,
            "inspection_every_rounds": 5,
            "max_items_per_section": 20,
        }
        base = datetime.now().astimezone() - timedelta(minutes=1)
        inspection_id = self.store.submit(
            "douyin_engagement_inspection",
            "device-1",
            payload,
            not_before=base.isoformat(timespec="microseconds"),
        )
        next_id = self.store.submit(
            "healthcheck",
            "device-1",
            not_before=(base + timedelta(microseconds=1)).isoformat(
                timespec="microseconds"
            ),
        )
        claimed = self.store.claim_next("device-1", "worker-1")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.id, inspection_id)
        self.assertEqual(self.store.recover_interrupted("device-1"), 1)
        self.assertEqual(self.store.get(inspection_id).status, "failed")
        self.assertIn("not retried", self.store.get(inspection_id).error or "")
        next_task = self.store.claim_next("device-1", "worker-1")
        self.assertIsNotNone(next_task)
        assert next_task is not None
        self.assertEqual(next_task.id, next_id)

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

    def test_model_channel_incident_outcomes_are_persisted_separately(self) -> None:
        task_id = self.store.submit("healthcheck", "device-1")
        for outcome in ("model_failed", "model_circuit_open"):
            self.store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=1,
                stage="topic_model",
                error_type="CloudModelError",
                error_message=outcome,
                outcome=outcome,
                recovery_action="none_model_channel",
            )
        statistics = self.store.incident_statistics()
        self.assertEqual(statistics["model_failed"], 1)
        self.assertEqual(statistics["model_circuit_open"], 1)

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
        # Pin every record to the same millisecond and give older rows larger
        # lexical IDs. Pagination must still follow insertion recency rather
        # than the random UUID tie-breaker.
        stable_task_ids = [f"task-{letter}" for letter in "zyxw"]
        stable_incident_ids = [f"incident-{letter}" for letter in "zyxw"]
        with self.store.connection() as connection:
            for old_id, new_id in zip(task_ids, stable_task_ids):
                connection.execute(
                    "UPDATE tasks SET id=?, created_at=? WHERE id=?",
                    (new_id, "2026-09-02T10:00:00.000+08:00", old_id),
                )
            for old_id, new_id in zip(incident_ids, stable_incident_ids):
                connection.execute(
                    "UPDATE incidents SET id=?, created_at=? WHERE id=?",
                    (new_id, "2026-09-02T10:00:00.000+08:00", old_id),
                )
        task_ids = stable_task_ids
        incident_ids = stable_incident_ids
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
                "degraded": 0,
                "failed": 0,
                "stopped": 0,
                "cancelled": 0,
                "waiting_model": 0,
                "waiting_device": 0,
                "waiting_user": 0,
            },
        )

    def test_interaction_alerts_are_aggregated_deduplicated_and_acknowledged(self) -> None:
        summary = {"source_count": 2, "sources": {}}
        first, created = self.store.record_interaction_alert(
            task_id="inspection-1",
            device_id="device-1",
            sources=["received_likes", "comment_danmaku"],
            summary=summary,
            fingerprint="same-change",
        )
        repeated, repeated_created = self.store.record_interaction_alert(
            task_id="inspection-2",
            device_id="device-1",
            sources=["received_likes", "comment_danmaku"],
            summary=summary,
            fingerprint="same-change",
        )
        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first["id"], repeated["id"])
        page = self.store.list_interaction_alerts(status="unread")
        self.assertEqual(page["total"], 1)
        result = self.store.acknowledge_interaction_alerts([first["id"]])
        self.assertEqual(result["acknowledged"], 1)
        self.assertEqual(result["acknowledged_ids"], [first["id"]])
        self.assertEqual(result["remaining_unread"], 0)
        self.assertEqual(self.store.list_interaction_alerts()["alerts"][0]["status"], "viewed")

    def test_visitor_baseline_stores_readable_first_row_locally(self) -> None:
        self.store.upsert_visitor_baseline(
            device_id="device-1",
            app_version="33.0.0",
            display_signature="1080x2340x480x0x100",
            row_count=4,
            first_row_hash="ui-hash",
            visual_hash="visual-hash",
            first_row={"display_name": "本地访客"},
        )
        baseline = self.store.get_visitor_baseline("device-1")
        assert baseline is not None
        self.assertEqual(baseline["row_count"], 4)
        self.assertEqual(baseline["first_row_hash"], "ui-hash")
        self.assertEqual(baseline["first_row"], {"display_name": "本地访客"})

    def test_interaction_inspection_receipt_survives_as_independent_history(self) -> None:
        receipt = self.store.record_interaction_inspection(
            inspection_id="receipt-1",
            task_id="task-1",
            device_id="device-1",
            workflow_version="v2",
            status="completed",
            result_kind="clear",
            restored=True,
            summary={"conclusion": "四个分区已检查，无新互动", "sections": {}},
            evidence=[{"id": "evidence-1", "image_name": "entry.png"}],
            run_dir="C:/local/evidence",
            started_at="2026-09-02T00:00:00+08:00",
            finished_at="2026-09-02T00:01:00+08:00",
        )
        self.assertEqual(receipt["result_kind"], "clear")
        self.assertEqual(receipt["summary"]["conclusion"], "四个分区已检查，无新互动")
        page = self.store.list_interaction_inspections(result_kind="clear")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["inspections"][0]["evidence"][0]["id"], "evidence-1")

    def test_incomplete_filter_includes_failed_run_that_also_found_an_alert(self) -> None:
        self.store.record_interaction_inspection(
            inspection_id="inspection-overlap",
            task_id="task-overlap",
            device_id="device-1",
            workflow_version="v2",
            status="failed",
            result_kind="alert",
            restored=True,
            summary={"alert_sources": ["private_messages"]},
            evidence=[],
            run_dir="C:/local/evidence",
            started_at="2026-09-02T00:00:00+08:00",
            finished_at="2026-09-02T00:01:00+08:00",
        )
        page = self.store.list_interaction_inspections(result_kind="incomplete")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["inspections"][0]["id"], "inspection-overlap")

    def test_version_recovery_is_idempotent_and_links_replacement_task(self) -> None:
        origin_id = self.store.submit(
            "douyin_engagement_inspection",
            "device-1",
            {
                "submission_id": "recovery-test",
                "inspection_index": 1,
                "after_round_index": 1,
                "inspection_every_rounds": 1,
                "max_items_per_section": 20,
            },
        )
        first, created = self.store.create_task_recovery(
            origin_task_id=origin_id,
            device_id="device-1",
            fingerprint="40.2.0->40.3.0|1080x2340",
            expected={"app_version": "40.2.0"},
            actual={"app_version": "40.3.0"},
        )
        repeated, repeated_created = self.store.create_task_recovery(
            origin_task_id=origin_id,
            device_id="device-1",
            fingerprint="40.2.0->40.3.0|1080x2340",
            expected={"app_version": "40.2.0"},
            actual={"app_version": "40.3.0"},
        )
        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first["id"], repeated["id"])

        replacement_id = self.store.submit(
            "douyin_engagement_inspection",
            "device-1",
            {
                "submission_id": "recovery-test",
                "inspection_index": 1,
                "after_round_index": 1,
                "inspection_every_rounds": 1,
                "max_items_per_section": 20,
                "recovery_parent_task_id": origin_id,
            },
        )
        ready = self.store.finish_task_recovery(
            first["id"],
            status="ready",
            progress_current=3,
            progress_total=3,
            replacement_task_id=replacement_id,
            message="三遍语义复验一致，已创建替代任务",
        )
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["replacement_task_id"], replacement_id)
        self.assertEqual(self.store.get_task_recovery(origin_id)["progress_current"], 3)

    def test_failed_version_precondition_can_be_queued_once_for_worker_recovery(self) -> None:
        origin_id = self.store.submit(
            "douyin_engagement_inspection",
            "device-1",
            {
                "submission_id": "historic-recovery",
                "inspection_index": 1,
                "after_round_index": 1,
                "inspection_every_rounds": 1,
                "max_items_per_section": 20,
            },
        )
        self.store.claim_next("device-1", "worker-1")
        result = {
            "status": "failed",
            "restored": True,
            "failure_class": "recoverable_precondition",
            "recovery_eligible": True,
            "navigation_started": False,
            "expected_app_version": "40.2.0",
            "actual_app_version": "40.3.0",
            "expected_display_signature": "1080x2340x480x0x101",
            "actual_display_signature": "1080x2340x480x0x101",
        }
        self.store.finish(
            origin_id,
            status="failed",
            run_dir=None,
            result=result,
            error="v2_app_version_changed",
        )

        first, created = self.store.request_task_recovery(origin_id)
        repeated, repeated_created = self.store.request_task_recovery(origin_id)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first["id"], repeated["id"])
        self.assertTrue(self.store.has_ready_recovery("device-1"))
        claimed = self.store.claim_task_recovery("device-1")
        self.assertEqual(claimed["origin_task_id"], origin_id)
        self.assertEqual(claimed["status"], "running")
        self.assertFalse(self.store.has_ready_recovery("device-1"))

    def test_legacy_action_free_version_failure_can_be_queued_for_probe(self) -> None:
        origin_id = self.store.submit(
            "douyin_engagement_inspection",
            "device-1",
            {
                "submission_id": "legacy-recovery",
                "inspection_index": 2,
                "after_round_index": 6,
                "inspection_every_rounds": 3,
                "max_items_per_section": 20,
                "expected_app_version": "40.2.0",
                "expected_display_signature": "1080x2340x480x0x101",
            },
        )
        self.store.claim_next("device-1", "worker-1")
        sections = {
            name: {"status": "failed", "reason": "not_checked"}
            for name in (
                "private_messages", "received_likes", "comment_danmaku", "profile_visitors"
            )
        }
        self.store.finish(
            origin_id,
            status="failed",
            run_dir=None,
            result={
                "status": "failed",
                "restored": True,
                "failure_reason": "v2_app_version_changed",
                "workflow_version": "v2",
                "sections": sections,
                "inspection_metadata": {"alert_created": False},
            },
            error="v2_app_version_changed",
        )

        recovery, created = self.store.request_task_recovery(origin_id)

        self.assertTrue(created)
        self.assertEqual(recovery["expected"]["app_version"], "40.2.0")
        self.assertEqual(recovery["actual"]["app_version"], "")
        self.store.claim_task_recovery("device-1")
        self.store.finish_task_recovery(
            recovery["id"],
            status="waiting_user",
            progress_current=0,
            progress_total=3,
            message="旧格式探测无法读取结构化版本",
            error="v2_app_version_changed",
        )

        retried, retried_created = self.store.request_task_recovery(origin_id)

        self.assertFalse(retried_created)
        self.assertEqual(retried["id"], recovery["id"])
        self.assertEqual(retried["status"], "queued")

    def test_waiting_recovery_can_be_explicitly_continued_without_creating_another_attempt(self) -> None:
        origin_id = self.store.submit(
            "douyin_engagement_inspection",
            "device-1",
            {
                "submission_id": "manual-continue",
                "inspection_index": 1,
                "after_round_index": 1,
                "inspection_every_rounds": 1,
                "max_items_per_section": 20,
            },
        )
        recovery, _ = self.store.create_task_recovery(
            origin_task_id=origin_id,
            device_id="device-1",
            fingerprint="40.2.0->40.3.0",
            expected={"app_version": "40.2.0"},
            actual={"app_version": "40.3.0"},
        )
        self.store.claim_task_recovery("device-1")
        self.store.finish_task_recovery(
            recovery["id"],
            status="waiting_user",
            progress_current=0,
            progress_total=3,
            message="页面控件需要确认",
            evidence_dir="C:/evidence/first-pass",
            error="message_filter_title_not_found",
        )

        continued = self.store.continue_task_recovery(origin_id)

        self.assertEqual(continued["id"], recovery["id"])
        self.assertEqual(continued["status"], "queued")
        self.assertEqual(continued["progress_current"], 0)
        self.assertIsNone(continued["error"])
        self.assertIsNone(continued["evidence_dir"])
        self.assertTrue(self.store.has_ready_recovery("device-1"))


if __name__ == "__main__":
    unittest.main()
