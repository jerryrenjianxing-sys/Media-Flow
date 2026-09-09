import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from task_store import TaskStore

def claim_probe_in_process(arguments):
    import os
    import subprocess
    import sys
    path, now = arguments
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parent)}
    code = 'from pathlib import Path; import sys; from task_store import TaskStore; print(bool(TaskStore(Path(sys.argv[1])).claim_model_probe("shared", now=float(sys.argv[2]))))'
    result = subprocess.run([sys.executable, '-c', code, path, str(now)], env=env, capture_output=True, text=True, timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
    return result.stdout.strip() == 'True'


class ResilienceStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name) / 'tasks.db')

    def batch(self, devices=('phone1', 'phone2'), rounds=2):
        items = [SimpleNamespace(task_type='healthcheck', device_id=d,
                 payload={'resilience_version': 'v1'}, not_before='2000-01-01T00:00:00')
                 for _ in range(rounds) for d in devices]
        return self.store.submit_agent_batch('batch', 'session', items, fingerprint='fixed',
                     deadline=0, stop_policy='round_count')

    def test_one_failure_does_not_cancel_other_devices_or_tail(self):
        ids = self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.finish(task.id, status='failed', run_dir=None, error='device lost')
        self.assertEqual(self.store.agent_batch_receipt('batch', 'session')['state'], 'active')
        self.assertEqual(self.store.get(ids[-1]).status, 'pending')
        self.assertIsNotNone(self.store.claim_next('phone2', 'other'))
        self.assertIsNone(self.store.claim_next('phone1', 'worker'))

    def test_wait_releases_action_lease_but_blocks_tail_and_retains_checkpoint(self):
        self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.save_task_checkpoint(task.id, {'next_slot': 4, 'summary': {'failed_slots': 3}})
        self.store.wait_task(task.id, 'waiting_model', 'transient_network', model_key='qwen:3')
        self.assertEqual(self.store.running_count(), 0)
        self.assertFalse(self.store.has_ready('phone1'))
        self.assertIsNone(self.store.claim_next('phone1', 'other'))
        self.assertIsNotNone(self.store.claim_next('phone2', 'other'))
        restored = TaskStore(self.store.path)
        self.assertEqual(restored.get_task_checkpoint(task.id)['next_slot'], 4)
        self.assertEqual(restored.waiting_task('phone1')['reason_code'], 'transient_network')

    def test_probe_is_single_cross_connection_backoff_and_fenced(self):
        self.batch()
        for phone in ('phone1', 'phone2'):
            task = self.store.claim_next(phone, phone)
            self.store.wait_task(task.id, 'waiting_model', 'transient_network', model_key='qwen:3', now=100)
        second = TaskStore(self.store.path)
        self.assertIsNone(self.store.claim_model_probe('qwen:3', now=129))
        lease = self.store.claim_model_probe('qwen:3', now=130)
        self.assertIsNotNone(lease)
        self.assertIsNone(second.claim_model_probe('qwen:3', now=131))
        self.store.finish_model_probe('qwen:3', lease, success=False, reason='network_timeout', now=135)
        self.assertIsNone(second.claim_model_probe('qwen:3', now=194))
        lease2 = second.claim_model_probe('qwen:3', now=195)
        self.assertIsNotNone(lease2)
        self.assertFalse(self.store.finish_model_probe('qwen:3', lease, success=True, now=196))
        self.assertTrue(second.finish_model_probe('qwen:3', lease2, success=True, now=197))
        self.assertEqual(second.get_model_probe('qwen:3')['next_check'], 197)

    def test_user_stop_and_cancel_are_not_resurrected_by_successful_probe(self):
        ids = self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.wait_task(task.id, 'waiting_model', 'timeout', model_key='qwen:3', now=100)
        lease = self.store.claim_model_probe('qwen:3', now=130)
        self.store.control_agent_batch('batch', 'session', stop=True)
        self.store.finish_model_probe('qwen:3', lease, success=True, now=131)
        self.assertEqual(self.store.get(task.id).status, 'cancelled')
        self.assertFalse(self.store.resume_waiting_task(task.id))
        self.assertTrue(all(self.store.get(i).status == 'cancelled' for i in ids))

    def test_restart_pending_action_never_replayed_and_disables_batch_capability(self):
        self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.save_task_checkpoint(task.id, {'next_slot': 5, 'summary': {}})
        self.store.begin_task_action(task.id, slot=4, action='comment')
        self.store.recover_interrupted('phone1')
        self.assertEqual(self.store.get(task.id).status, 'waiting_device')
        checkpoint = self.store.get_task_checkpoint(task.id)
        self.assertEqual(checkpoint['next_slot'], 5)
        self.assertIsNone(checkpoint.get('pending_action'))
        self.assertEqual(checkpoint['summary']['unknown_actions'], 1)
        self.assertEqual(self.store.disabled_task_actions(task.id), ['comment'])
        self.assertTrue(self.store.resume_waiting_task(task.id))
        self.assertEqual(self.store.claim_next('phone1', 'worker2').id, task.id)
        self.assertFalse(self.store.resume_waiting_task(task.id))

    def test_terminal_history_remains_terminal(self):
        ids = self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.finish(task.id, status='completed', run_dir=None)
        self.assertFalse(self.store.resume_waiting_task(task.id))
        self.store.recover_interrupted('phone1')
        self.assertEqual(self.store.get(task.id).status, 'completed')

    def test_stop_during_failure_cannot_become_resumable_wait(self):
        for stop_kind in ('device', 'global', 'batch'):
            with self.subTest(stop_kind=stop_kind):
                store = TaskStore(Path(self.temp.name) / (stop_kind + '.db'))
                items = [SimpleNamespace(task_type='healthcheck', device_id='phone',
                         payload={'resilience_version': 'v1'}, not_before='2000-01-01T00:00:00')]
                store.submit_agent_batch('batch', 'session', items, fingerprint='fixed', deadline=0, stop_policy='round_count')
                task = store.claim_next('phone', 'worker')
                if stop_kind == 'device':
                    store.request_stop(['phone'], task_id=task.id)
                elif stop_kind == 'global':
                    store.save_profile('automation-stop', {'stopped': True})
                else:
                    store.control_agent_batch('batch', 'session', stop=True)
                self.assertFalse(store.wait_task(task.id, 'waiting_device', 'device_lost'))
                self.assertIn(store.get(task.id).status, ('stopped', 'cancelled'))
                store.clear_stop_requests(['phone'])
                store.save_profile('automation-stop', {'stopped': False})
                self.assertFalse(store.resume_waiting_task(task.id))

    def test_terminal_wait_preserves_checkpoint_counts_without_overwriting_final_results(self):
        for terminal in ('stop', 'cancel'):
            store = TaskStore(Path(self.temp.name) / (terminal + '.db'))
            task_id = store.submit('healthcheck', 'phone', {'resilience_version': 'v1'})
            store.claim_next('phone', 'worker')
            summary = {'processed_slots': 8, 'successful_slots': 6, 'failed_slots': 2, 'unknown_actions': 1}
            store.save_task_checkpoint(task_id, {'summary': summary})
            store.wait_task(task_id, 'waiting_device', 'worker_interrupted')
            if terminal == 'stop':
                store.request_stop(['phone'])
            else:
                store.cancel_pending([task_id])
            for name, value in summary.items():
                self.assertEqual(store.task_progress(task_id)[name], value)
            with store.connection() as db:
                db.execute('UPDATE tasks SET result_json=? WHERE id=?', (json.dumps({'processed_slots': 10, 'successful_slots': 8, 'failed_slots': 2}), task_id))
            self.assertEqual(store.task_progress(task_id)['processed_slots'], 10)

    def test_terminal_resumed_wait_does_not_report_stale_previous_wait_result(self):
        self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.save_task_checkpoint(task.id, {'summary': {'processed_slots': 3, 'failed_slots': 3}})
        self.store.wait_task(task.id, 'waiting_device', 'lost', result={'processed_slots': 3, 'failed_slots': 3})
        self.store.resume_waiting_task(task.id)
        self.store.claim_next('phone1', 'worker2')
        self.store.save_task_checkpoint(task.id, {'summary': {'processed_slots': 8, 'successful_slots': 5, 'failed_slots': 3}})
        self.store.wait_task(task.id, 'waiting_device', 'lost_again')
        self.store.request_stop(['phone1'])
        self.assertEqual(self.store.task_progress(task.id)['processed_slots'], 8)
        self.assertEqual(self.store.task_progress(task.id)['successful_slots'], 5)

    def test_resume_keeps_counts_and_rejects_a_probe_for_a_different_current_config(self):
        self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.save_task_checkpoint(task.id, {'next_slot': 4, 'summary': {'processed_slots': 3, 'failed_slots': 3}})
        self.store.wait_task(task.id, 'waiting_model', 'timeout', model_key='old', now=100)
        token = self.store.claim_model_probe('old', now=130)
        self.store.finish_model_probe('old', token, success=True, now=131)
        with patch('model_recovery.model_configuration_key', return_value='new'):
            self.assertFalse(self.store.resume_waiting_task(task.id, now=140))
        with patch('model_recovery.model_configuration_key', return_value='old'):
            self.assertTrue(self.store.resume_waiting_task(task.id, now=140))
        self.assertEqual(self.store.task_progress(task.id)['processed_slots'], 3)

    def test_host_retains_scoped_wait_owner_even_without_saved_selection_or_adb(self):
        from background_host import RuntimeSupervisor
        self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.wait_task(task.id, 'waiting_model', 'timeout', model_key='qwen:3')
        supervisor = RuntimeSupervisor(store=self.store, device_discovery=lambda: set())
        self.assertIn('phone1', supervisor.eligible_device_ids())
        self.store.control_agent_batch('batch', 'session', stop=True)
        self.assertNotIn('phone1', supervisor.eligible_device_ids())

    def test_stale_reconciler_cannot_release_reclaimed_live_worker(self):
        self.batch()
        task = self.store.claim_next('phone1', 'old-worker')
        def alive(_worker):
            self.store.recover_interrupted('phone1')
            self.store.resume_waiting_task(task.id)
            self.assertEqual(self.store.claim_next('phone1', 'new-worker').id, task.id)
            return False
        self.assertEqual(self.store.reconcile_orphaned_running(alive), 0)
        current = self.store.get(task.id)
        self.assertEqual(current.status, 'running')
        self.assertEqual(current.worker_id, 'new-worker')

    def test_resume_before_first_checkpoint_still_precedes_due_tail(self):
        self.batch(devices=('phone1',), rounds=3)
        task = self.store.claim_next('phone1', 'worker')
        self.store.wait_task(task.id, 'waiting_device', 'device_identity_unavailable')
        self.assertTrue(self.store.resume_waiting_task(task.id))
        self.assertEqual(self.store.claim_next('phone1', 'worker2').id, task.id)

    def test_five_processes_share_exactly_one_probe_lease(self):
        from concurrent.futures import ThreadPoolExecutor
        self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.wait_task(task.id, 'waiting_model', 'timeout', model_key='shared', now=100)
        with ThreadPoolExecutor(max_workers=5) as pool:
            tokens = list(pool.map(claim_probe_in_process, [(str(self.store.path), 130)] * 5))
        self.assertEqual(sum(bool(token) for token in tokens), 1)

    def test_probe_recovery_preserves_original_id_and_resets_streak(self):
        from model_recovery import service_model_wait
        self.batch()
        task = self.store.claim_next('phone1', 'worker')
        self.store.save_task_checkpoint(task.id, {'next_slot': 4, 'summary': {'processed_slots': 3}, 'model_failure_streak': 3})
        now = time.time()
        self.store.wait_task(task.id, 'waiting_model', 'transient_network', model_key='shared', now=now-31)
        with patch('model_recovery.model_configuration_key', return_value='shared'):
            wait = self.store.waiting_task('phone1')
            calls = []
            self.assertFalse(service_model_wait(self.store, wait, probe=lambda: calls.append(1), now=now))
            self.assertEqual(calls, [1])
            self.assertTrue(service_model_wait(self.store, wait, probe=lambda: self.fail('no second probe'), now=time.time()+8))
        self.assertEqual(self.store.claim_next('phone1', 'worker2').id, task.id)
        self.assertEqual(self.store.get_task_checkpoint(task.id)['model_failure_streak'], 0)


if __name__ == '__main__':
    unittest.main()
