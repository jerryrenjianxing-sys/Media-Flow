"""Stop regressions run against isolated real task/receipt stores only."""
import time
import unittest
from types import SimpleNamespace

import test_automation
import test_run_planning
from run_planning import build_preview, get_or_create_draft, submit_previewed_draft


class StopReceiptTests(unittest.TestCase):
    setUp = test_automation.AutomationTests.setUp
    service = test_automation.AutomationTests.service
    plan = test_automation.AutomationTests.plan
    def test_stop_batch_cancelled_is_success_and_same_receipt_never_replays(self):
        service = self.service()
        plan = self.plan(service)
        executed = service.call({'action': 'execute_plan', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'execute'})
        ids = executed['result']['task_ids']
        body = {'action': 'stop_batch', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'stop'}
        stopped = service.call(body)
        self.assertTrue(stopped['ok'], stopped)
        self.assertEqual(stopped['status'], 'cancelled')
        self.assertEqual(self.service().call(body), stopped)
        again = service.call({**body, 'request_id': 'stop-again'})
        self.assertTrue(again['ok'], again)
        self.assertEqual(again['result']['task_ids'], ids)
        self.assertEqual(len(self.launches), 1)
        self.assertFalse(service.call({'action': 'pause_batch', 'arguments': body['arguments'], 'request_id': 'pause-cancelled'})['ok'])

    def test_repeated_stop_task_reports_observed_terminal_without_new_work(self):
        task_id = self.store.submit('healthcheck', 'vm-one', {})
        body = {'action': 'stop_task', 'arguments': {'task_id': task_id}, 'request_id': 'stop-task'}
        stopped = self.service().call(body)
        again = self.service().call({**body, 'request_id': 'stop-task-again'})
        self.assertTrue(stopped['ok'], stopped)
        self.assertTrue(again['ok'], again)
        self.assertEqual(again['status'], self.store.get(task_id).status)
        self.assertFalse(again['result']['changed'])
        self.assertEqual(self.launches, [])

    def test_batch_and_session_stop_clear_only_newly_terminalized_waits(self):
        for session_stop in [False, True]:
            with self.subTest(session_stop=session_stop):
                batch = 'batch-' + str(session_stop)
                session = 'session-' + str(session_stop)
                tasks = [SimpleNamespace(task_type='healthcheck', device_id=batch + str(i), payload={}, not_before=0) for i in range(5)]
                ids = self.store.submit_agent_batch(batch, session, tasks, fingerprint=batch, deadline=time.time()+100)
                for task_id, task in zip(ids, tasks):
                    self.assertEqual(self.store.claim_next(task.device_id, 'isolated-worker').id, task_id)
                    self.store.save_task_checkpoint(task_id, {'schema_version': 1, 'fixture_cursor': 7})
                checkpoints = {task_id: self.store.get_task_checkpoint(task_id) for task_id in ids}
                with self.store.connection() as db:
                    for task_id, status in zip(ids, ['waiting_model', 'waiting_user', 'waiting_device', 'completed', 'running']):
                        db.execute('UPDATE tasks SET status=? WHERE id=?', (status, task_id))
                        db.execute("INSERT OR REPLACE INTO task_waits VALUES(?,?,'',NULL,?)", (task_id, 'fixture_reason', time.time()))
                    before = [tuple(row) for row in db.execute('SELECT * FROM task_waits WHERE task_id IN (?,?)', ids[3:])]
                if session_stop:
                    self.store.stop_agent_session(session)
                    self.assertEqual(self.store.stop_agent_session(session), 0)
                else:
                    self.store.control_agent_batch(batch, session, stop=True)
                    self.store.control_agent_batch(batch, session, stop=True)
                with self.store.connection() as db:
                    self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM task_waits WHERE task_id IN (?,?)', ids[3:])], before)
                    self.assertEqual(db.execute('SELECT count(*) FROM task_waits WHERE task_id IN (?,?,?)', ids[:3]).fetchone()[0], 0)
                self.assertEqual([self.store.get(task_id).status for task_id in ids], ['cancelled']*3 + ['completed', 'running'])
                self.assertEqual({task_id: self.store.get_task_checkpoint(task_id) for task_id in ids}, checkpoints)


class WorkbenchModeTests(unittest.TestCase):
    setUp = test_run_planning.RunPlanningTests.setUp
    tearDown = test_run_planning.RunPlanningTests.tearDown
    def test_old_draft_preview_and_submission_freeze_new_mode_without_rewriting_draft(self):
        original = self.store.save_run_draft({**test_run_planning.base_config(), 'inspection_mode': 'legacy', 'engagement_inspection_enabled': True, 'inspection_every_rounds': 1}, expected_revision=0)
        draft = get_or_create_draft(self.store, {})
        self.assertEqual(draft, original)
        preview = build_preview(self.store, draft, devices=self.devices, paused=True)
        self.assertTrue(preview['ready'], preview)
        ids = submit_previewed_draft(self.store, draft, preview, expected_plan_hash=preview['plan_hash'], confirm_writes=False)
        inspections = [self.store.get(task_id) for task_id in ids if self.store.get(task_id).task_type == 'douyin_engagement_inspection']
        self.assertTrue(inspections)
        self.assertTrue(all(task.payload['inspection_workflow_version'] == 'home_badge' for task in inspections))
        self.assertEqual(self.store.get_run_draft(), original)
