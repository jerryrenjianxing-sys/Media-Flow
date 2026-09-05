import tempfile
import unittest
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from task_store import TaskStore
from virtual_device_qualification import qualify_virtual_device


class OnDemandTests(unittest.TestCase):
    def test_upgrade_only_cancels_explicit_unstarted_legacy_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/"tasks.db")
            old = store.create_initialization("old")
            new = store.create_initialization("new", options={"preparation_version": "on-demand-v1"})
            running = store.create_initialization("running")
            store.claim_initialization("running", "worker")
            ids = [old.id, new.id, running.id]
            self.assertEqual(store.cancel_unstarted_legacy_initializations(ids), 1)
            self.assertEqual(store.cancel_unstarted_legacy_initializations(ids), 0)
            self.assertEqual(store.get_initialization(new.id).status, "queued")
            self.assertEqual(store.get_initialization(running.id).status, "running")

    def test_only_absent_executor_releases_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/"tasks.db")
            store.create_initialization("vm")
            store.claim_initialization("vm", "worker-123")
            self.assertEqual(store.reconcile_orphaned_initializations(lambda _: True), 0)
            self.assertEqual(store.latest_initialization("vm").status, "running")
            self.assertEqual(store.reconcile_orphaned_initializations(lambda _: False), 1)
            self.assertEqual(store.latest_initialization("vm").status, "failed")

    def test_override_density_wins(self):
        from task_preparation import effective_density
        self.assertEqual(effective_density("Physical density: 240\nOverride density: 320"), 320)
        self.assertEqual(effective_density("Physical density: 320\nOverride density: 480"), 480)
        self.assertIsNone(effective_density("error 320"))

    def test_queued_preparation_does_not_block_manual_control(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/"tasks.db")
            task = store.submit("healthcheck", "vm")
            store.create_initialization("vm")
            session = store.create_device_view_session(device_id="vm", virtual_device_id="vm-uuid",
                mode="control", stream_profile="focus", token_hash="a" * 64)
            self.assertIsNone(store.claim_initialization("vm", "worker"))
            self.assertIsNone(store.claim_next("vm", "worker"))
            store.close_device_view_session(session["id"])
            self.assertEqual(store.get(task).status, "pending")
            self.assertIsNotNone(store.claim_initialization("vm", "worker"))

    def test_paused_worker_claims_maintenance_but_never_business(self):
        from worker import run_worker
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root/"tasks.db")
            task = store.submit("healthcheck", "vm")
            record = store.create_initialization("vm")
            store.set_paused(True)
            def execute(_device, claimed, _store, **_kwargs):
                self.assertEqual(claimed.id, record.id)
                self.assertEqual(claimed.status, "running")
                store.finish_initialization(claimed.id, status="ready", stage="ready", message="基础准备已完成", result={})
                store.request_stop(["vm"])
                return {"status": "ready"}
            with patch("worker.DeviceLock", return_value=nullcontext()), patch("worker.connect_with_retry", return_value=Mock()), patch("worker.execute_initialization", side_effect=execute), patch("worker.execute_task") as business:
                run_worker(store=store, device_id="vm", artifacts_root=root/"artifacts", poll_seconds=0,
                           max_tasks=1, connect_attempts=1, reconnect_delay_seconds=0, offline_wait_seconds=0)
            business.assert_not_called()
            self.assertEqual(store.get(task).status, "pending")
            self.assertTrue(store.is_paused())

    def test_inspection_failure_scope_depends_on_safe_restore(self):
        from task_preparation import record_inspection_outcome, inspection_suspension_key
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/"tasks.db")
            task = SimpleNamespace(task_type="douyin_engagement_inspection", device_id="vm", id="receipt")
            record_inspection_outcome(store, task, {"status": "degraded", "restored": True})
            self.assertTrue(store.get_profile(inspection_suspension_key("vm"))["suspended"])
            self.assertFalse(store.is_stop_requested("vm"))
            record_inspection_outcome(store, task, {"status": "failed", "restored": False})
            self.assertTrue(store.is_stop_requested("vm"))

    def test_no_calibration_required_through_business_executor(self):
        from execution_tasks import execute_task
        from task_preparation import PREPARATION_VERSION, engagement_rule
        from test_engagement_inspection import V3Device, V2Recorder, v3_page, node
        from test_engagement_preflight import EngagementPreflightTest
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root/"tasks.db")
            EngagementPreflightTest.save_virtual(store, "android-new")
            original = store.get(store.submit("healthcheck", "127.0.0.1:16416"))
            task = replace(original, task_type="douyin_engagement_inspection", payload={
                "preparation_version": PREPARATION_VERSION, "inspection_workflow_version": "v3",
                "inspection_calibration": engagement_rule(), "expected_display_signature": "900x1600x320x0x100",
                "max_items_per_section": 100})
            device = V3Device([v3_page(node("互动消息", bounds="[330,40][570,130]"), node("已读", bounds="[410,470][490,530]"))])
            with patch("task_preparation.prepare_capabilities") as prepare, patch("engagement_inspection.time.sleep"):
                result = execute_task(device, task, V2Recorder(root), task_store=store)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["restored"])
            self.assertEqual(device.swipes, 0)
            self.assertNotIn("search_input", prepare.call_args.args[2])
            self.assertNotIn("passes", task.payload["inspection_calibration"])

    def test_suspended_inspection_skips_without_actions(self):
        from execution_tasks import execute_task
        from task_preparation import PREPARATION_VERSION, inspection_suspension_key
        from test_engagement_preflight import EngagementPreflightTest
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/"tasks.db")
            EngagementPreflightTest.save_virtual(store, "android-a")
            original = store.get(store.submit("healthcheck", "127.0.0.1:16416"))
            task = replace(original, task_type="douyin_engagement_inspection", payload={"preparation_version": PREPARATION_VERSION})
            store.save_profile(inspection_suspension_key(task.device_id), {"suspended": True})
            device = Mock()
            result = execute_task(device, task, Mock(), task_store=store)
            self.assertTrue(result["skipped"])
            self.assertFalse(result["complete"])
            self.assertFalse(device.mock_calls)

    def test_standard_device_can_be_selected_without_calibration(self):
        value = qualify_virtual_device({"provider_snapshot": {"settings": {"width": 900, "height": 1600, "dpi": 320}}}, connected=True)
        self.assertTrue(value["task_eligibility"]["browse"])
        self.assertEqual(value["capabilities"]["engagement_v3"]["status"], "preparable")

    def test_business_pause_still_allows_explicit_preparation(self):
        from task_preparation import maintenance_may_run
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/"tasks.db")
            store.set_paused(True)
            store.create_initialization("vm", options={})
            self.assertTrue(maintenance_may_run(store, "vm"))
            store.request_stop(["vm"])
            self.assertFalse(maintenance_may_run(store, "vm"))

    def test_waiting_record_is_not_running_in_ui(self):
        from task_preparation import preparation_presentation
        result = preparation_presentation("queued", "等待Worker接管")
        self.assertFalse(result["busy"])
        self.assertIn("cancel_initialization", result["actions"])
        self.assertIn("manual_control", result["actions"])

    def test_general_rule_does_not_fake_three_pass_proof(self):
        from task_preparation import engagement_rule
        rule = engagement_rule()
        self.assertNotIn("passes", rule)
        self.assertNotIn("device_id", rule)
        self.assertEqual(rule["rule_version"], "mediaflow-engagement-v3-r2")


if __name__ == "__main__":
    unittest.main()
