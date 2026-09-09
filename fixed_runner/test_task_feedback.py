"""Offline API receipts for waiting tasks; no device or model calls."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from agent_platform import AgentPlatform
from automation import AutomationService
from task_store import TaskStore


class TaskFeedbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = TaskStore(self.root / 'tasks.db')
        self.launches = []
        self.platform = AgentPlatform(self.root / 'platform', self.store, lambda: {},
            worker_launcher=lambda devices: self.launches.append(devices) or [{'running': True}])
        self.service = AutomationService(SimpleNamespace(root=self.root, platform=self.platform))
        items = [SimpleNamespace(task_type='healthcheck', device_id=device, payload={},
            not_before='2000-01-01T00:00:00') for device in ('one', 'two')]
        self.ids = self.store.submit_agent_batch('batch', 'session', items,
            fingerprint='fixed', deadline=0, stop_policy='round_count')
        for device in ('one', 'two'):
            task = self.store.claim_next(device, device)
            self.store.save_task_checkpoint(task.id, {'next_slot': 4, 'summary': {
                'processed_slots': 3, 'successful_slots': 1, 'failed_slots': 2}, 'evidence_dirs': []})
            self.store.wait_task(task.id, 'waiting_device', 'device_offline')

    def call(self, action, request_id='request'):
        return self.service.call({'action': action, 'arguments': {'task_id': self.ids[0]},
            'request_id': request_id})

    def test_resume_original_task_once_and_only_its_device(self):
        reply = self.call('resume_task')
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(reply['result']['task']['task_id'], self.ids[0])
        self.assertEqual(self.store.get(self.ids[0]).status, 'pending')
        self.assertEqual(self.store.get(self.ids[1]).status, 'waiting_device')
        self.assertEqual(self.launches, [['one']])
        self.assertEqual(self.call('resume_task'), reply)
        self.assertEqual(self.launches, [['one']])
        self.assertEqual(self.store.get_task_checkpoint(self.ids[0])['next_slot'], 4)

    def test_model_not_ready_retains_wait_and_never_launches(self):
        with self.store.connection() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id=?", (self.ids[0],))
        self.store.wait_task(self.ids[0], 'waiting_model', 'network_timeout', model_key='model')
        reply = self.call('resume_task')
        self.assertFalse(reply['ok'], reply)
        self.assertEqual(reply['status'], 'waiting_model')
        self.assertEqual(reply['result']['task']['progress']['waiting_reason'], 'network_timeout')
        self.assertEqual(self.launches, [])

    def test_stop_fences_device_without_cancelling_other_device_or_batch(self):
        reply = self.call('stop_task')
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(self.store.get(self.ids[0]).status, 'stopped')
        self.assertTrue(self.store.is_stop_requested('one'))
        self.assertFalse(self.store.is_stop_requested('two'))
        self.assertEqual(self.store.get(self.ids[1]).status, 'waiting_device')
        self.assertEqual(self.store.agent_batch_receipt('batch', 'session')['state'], 'active')
        resumed = self.call('resume_task', 'second')
        self.assertFalse(resumed['ok'], resumed)
        self.assertEqual(self.launches, [])

    def test_durable_progress_in_evidence_batch_and_detail(self):
        evidence = self.call('task_evidence')['result']['task']
        self.assertEqual(evidence['progress']['processed_slots'], 3)
        receipt = self.store.agent_batch_receipt('batch', 'session')
        self.assertEqual(receipt['tasks'][0]['progress']['failed_slots'], 2)
        self.assertEqual(receipt['progress']['processed_slots'], 6)
        self.assertEqual(receipt['device_progress']['one']['processed_slots'], 3)
        from control_api import task_detail_payload, paged_task_groups_payload, active_tasks_for_display
        detail = task_detail_payload(self.store, [self.ids[0]])['tasks'][0]
        self.assertEqual(detail['progress']['next_slot'], 4)
        groups = paged_task_groups_payload(self.store, 10, 0)['items']
        self.assertEqual(groups[0]['progress']['processed_slots'], 3)
        active = active_tasks_for_display(self.store.list_active_tasks(), ['one', 'two'])
        self.assertEqual(len(active), 2)

    def test_http_controls_and_extracted_skill_client_keep_original_task(self):
        from http.server import ThreadingHTTPServer
        import json
        import os
        import subprocess
        import sys
        import threading
        import urllib.request
        import zipfile
        from control_api import Handler
        from skill_bundle import build_skill_bundle
        store, host = self.store, self.service.host
        class OfflineHandler(Handler):
            def automation_context(self):
                return host
        OfflineHandler.store = store
        server = ThreadingHTTPServer(('127.0.0.1', 0), OfflineHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            request = urllib.request.Request(base + '/api/tasks/' + self.ids[0] + '/resume',
                data=json.dumps({'request_id': 'http-resume'}).encode(),
                headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(json.load(response)['result']['task']['task_id'], self.ids[0])
            bundle = build_skill_bundle(self.root / 'skill.zip')
            with zipfile.ZipFile(bundle) as archive:
                archive.extractall(self.root / 'extracted')
            script = self.root / 'extracted/mediaflow-platform/scripts/mediaflow.py'
            env = {**os.environ, 'MEDIAFLOW_API_URL': base, 'MEDIAFLOW_RECEIPT_DB': str(self.root/'client.db')}
            for action in ('resume_task', 'stop_task'):
                result = subprocess.run([sys.executable, str(script), action, '--arguments',
                    json.dumps({'task_id': self.ids[1]}), '--request-id', 'cli-' + action],
                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=10)
                # Resume succeeds; stop is tested on a waiting task again, without a real worker.
                if action == 'resume_task':
                    self.assertEqual(result.returncode, 0, result.stdout)
                    with self.store.connection() as db:
                        db.execute("UPDATE tasks SET status='running' WHERE id=?", (self.ids[1],))
                    self.store.wait_task(self.ids[1], 'waiting_device', 'device_offline')
                else:
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(self.store.get(self.ids[1]).status, 'stopped')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == '__main__':
    unittest.main()
