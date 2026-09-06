"""Automation admission uses real isolated plans/queue, never a live service."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import urllib.error
import urllib.request

from agent_platform import AgentPlatform
from agent_memory import AgentMemory
from task_store import TaskStore


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'tasks.db')
        self.store.set_paused(True)
        self.snapshot = {'paused': True, 'devices': [{'device_id': 'vm-one', 'device_type': 'virtual',
            'state': 'device', 'profile_verified': True, 'initialization_status': 'legacy'}],
            'virtualization': {'devices': [{'virtual_device_id': 'vm-uuid', 'provider_instance_id': '0',
                'adb_endpoint': 'vm-one', 'android_identity': 'android-one'}]}}
        self.launches = []
        def launch(devices):
            self.launches.append(devices)
            return [{'running': True}]
        self.host = SimpleNamespace(root=self.root, status_reader=lambda: self.snapshot,
            platform=AgentPlatform(self.root/'platform', self.store, lambda: self.snapshot,
                worker_launcher=launch, model_status_reader=lambda: {'model_ready': True}),
            memory=AgentMemory(self.root/'memory'), operations=None, repairs=None,
            repair_updates=None, evidence_root=None)
        self.config = {'device_ids': ['vm-uuid'], 'video_count': 2, 'round_count': 1,
            'content_mode': 'general', 'engagement_inspection_enabled': False}

    def service(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('automation'), 'Automation API is missing')
        from automation import AutomationService
        return AutomationService(self.host)

    def plan(self, service):
        return service.call({'action': 'plan_tasks', 'arguments': {'config': self.config}, 'request_id': 'plan-1'})['result']

    def test_no_host_session_default_zero_draft_and_scoped_resume(self):
        service = self.service()
        self.store.save_run_draft({'custom': 'untouched'}, expected_revision=0)
        other = self.store.submit('healthcheck', 'other', {})
        plan = self.plan(service)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(plan['config']['like_probability'], 0)
        self.assertEqual(plan['config']['matched_comment_probability'], 0)
        body = {'action': 'execute_plan', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'execute-1'}
        first = service.call(body)
        self.assertEqual(first['result']['batch_id'], plan['plan_id'])
        self.assertNotEqual(first['status'], 'completed')
        self.assertEqual(self.service().call(body), first)
        self.assertEqual(len(self.launches), 1)
        service.call({'action': 'pause_batch', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'pause-1'})
        resumed = service.call({**body, 'request_id': 'resume-1'})
        self.assertEqual(resumed['result']['task_ids'], first['result']['task_ids'])
        self.assertTrue(self.store.is_paused())
        self.assertIsNone(self.store.claim_next('other', 'test'))
        self.assertEqual(self.store.get(other).status, 'pending')
        self.assertEqual(self.store.get_run_draft()['config'], {'custom': 'untouched'})

    def test_concurrent_same_id_and_conflicting_payload(self):
        service = self.service()
        plan = self.plan(service)
        body = {'action': 'execute_plan', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'execute-1'}
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.service().call(body), range(4)))
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.launches), 1)
        saved = service.call(body)
        self.assertTrue(any(r == saved for r in replies))
        bad = service.call({**body, 'arguments': {**body['arguments'], 'resume_stopped_devices': True}})
        self.assertEqual(bad['reason_code'], 'request_id_conflict')

    def test_unknown_after_dispatch_is_durable_and_never_replayed(self):
        service = self.service()
        calls = []
        def uncertain(*args, **kwargs):
            calls.append(1)
            raise TimeoutError('sk-abcdefghijklmnopqrstuv')
        self.host.memory.save = uncertain
        body = {'action': 'memory_save', 'arguments': {'title': 'x', 'body': 'y'}, 'request_id': 'uncertain'}
        first = service.call(body)
        self.assertEqual(first['status'], 'unknown')
        self.assertFalse(first['retryable'])
        self.assertNotIn('abcdefghijklmnopqrstuv', json.dumps(first))
        self.assertEqual(self.service().call(body), first)
        self.assertEqual(len(calls), 1)
        lookup = self.service().call({'action': 'request_status', 'arguments': {'request_id': 'uncertain'}})
        self.assertEqual(lookup['result'], first)

    def test_reads_missing_id_and_device_fault_do_not_submit(self):
        service = self.service()
        for action in ['platform_status', 'list_devices', 'list_tasks', 'plan_status', 'memory_list']:
            self.assertTrue(service.call({'action': action})['ok'])
        self.assertFalse(service.call({'action': 'plan_tasks', 'arguments': {'config': self.config}})['ok'])
        self.snapshot['virtualization']['devices'][0]['adb_endpoint'] = None
        failed = service.call({'action': 'plan_tasks', 'arguments': {'config': self.config}, 'request_id': 'offline'})
        self.assertEqual(failed['status'], 'blocked')
        self.assertIn('ADB', failed['user_message'])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.launches, [])

    def test_http_route_json_origin_and_catalog(self):
        from control_api import Handler
        host = self.host
        class TestHandler(Handler):
            def agent_service(self):
                return host
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = 'http://127.0.0.1:%s/api/automation' % server.server_port
        try:
            with urllib.request.urlopen(url) as response:
                catalog = json.load(response)
        except urllib.error.HTTPError as exc:
            self.fail('Automation route must be registered: HTTP %s' % exc.code)
        self.assertEqual(catalog['api_version'], '1')
        self.assertTrue(any(a['action'] == 'execute_plan' and a['mutates'] for a in catalog['actions']))
        for headers, expected in [({'Origin': 'https://evil.example', 'Content-Type': 'application/json'}, 403),
                                  ({'Content-Type': 'text/plain'}, 415)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, data=b'{}', headers=headers))
            self.assertEqual(error.exception.code, expected)
        request = urllib.request.Request(url, data=json.dumps({'action': 'list_tasks'}).encode(),
            headers={'Content-Type': 'application/json'})
        self.assertEqual(json.load(urllib.request.urlopen(request))['result']['tasks'], [])

    def test_repair_native_workspace_and_hash_bound_updates_without_old_session(self):
        from agent_repairs import AgentRepairs
        from agent_repair_updates import AgentRepairUpdates
        bundle = self.root/'source.zip'
        content = b'VALUE = 1\n'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('fixed_runner/page.py', content)
            archive.writestr('manifest.json', json.dumps({'schema': 1, 'source_revision': 'a'*40,
                'contains_runtime_data': False, 'files': {'fixed_runner/page.py': hashlib.sha256(content).hexdigest()}}))
        repairs = AgentRepairs(self.root/'repairs', bundle=bundle, source_root=self.root)
        self.host.repairs = repairs
        # No legacy permission object exists. A test launcher records queued jobs only.
        launched = []
        self.host.repair_updates = AgentRepairUpdates(self.root/'updates', repairs, None, launcher=launched.append)
        service = self.service()
        created = service.call({'action': 'repair_create', 'arguments': {'purpose': 'fixture'}, 'request_id': 'repair-new'})
        self.assertTrue(created['ok'], created)
        workspace = Path(created['result']['workspace_path'])
        self.assertTrue(workspace.is_dir())
        repair_id = created['result']['id']
        (workspace/'fixed_runner/page.py').write_text('VALUE = 2\n', encoding='utf-8')
        def tested(*args):
            return [{'mode': 'all', 'state': 'passed', 'source_hash': repairs.diff(repair_id, 'skill-local')['source_hash']}]
        with patch('agent_repair_updates.development_revision', return_value='a'*40), patch.object(repairs, 'tests', side_effect=tested):
            prepared = service.call({'action': 'repair_prepare_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'prepare-1'})
            self.assertTrue(prepared['ok'], prepared)
            (workspace/'fixed_runner/page.py').write_text('VALUE = 3\n', encoding='utf-8')
            changed = service.call({'action': 'repair_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'apply-changed'})
            self.assertFalse(changed['ok'])
            self.assertIn('变化', changed['user_message'])
            self.assertEqual(launched, [])
            (workspace/'fixed_runner/page.py').write_text('VALUE = 2\n', encoding='utf-8')
            applied = service.call({'action': 'repair_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'apply-1'})
            self.assertEqual(applied['status'], 'queued', applied)
            self.assertEqual(len(launched), 1)
            self.assertEqual(self.service().call({'action': 'repair_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'apply-1'}), applied)
            self.host.repair_updates.update(applied['operation_id'], status='failed', stage='result_unknown', message='unknown')
            unknown = service.call({'action': 'repair_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'apply-next'})
            self.assertEqual(unknown['status'], 'unknown')
            self.assertEqual(len(launched), 1)

    def test_blocked_plan_explains_actual_device_condition(self):
        self.snapshot['devices'][0]['state'] = 'offline'
        response = self.service().call({'action': 'plan_tasks', 'arguments': {'config': self.config}, 'request_id': 'blocked'})
        self.assertEqual(response['status'], 'blocked')
        self.assertIn('不可用', response['user_message'])

    def test_non_finite_json_is_rejected_before_receipt_or_dispatch(self):
        response = self.service().call({'action': 'memory_save', 'arguments': {'body': float('nan')}, 'request_id': 'nan'})
        self.assertFalse(response['ok'])
        self.assertEqual(response['reason_code'], 'invalid_arguments')

    def test_virtual_failure_is_not_reported_as_successful_submission(self):
        from agent_operations import AgentOperations
        self.store.save_virtual_device({'virtual_device_id': 'vm1', 'provider': 'mumu',
            'provider_install_id': 'install', 'provider_instance_id': '0', 'name': 'Test VM',
            'state': 'stopped', 'recipe': {}, 'provider_snapshot': {}})
        def dispatch(device, body):
            operation, _ = self.store.create_virtual_operation(body['action'], body,
                idempotency_key=body['idempotency_key'])
            self.store.update_virtual_operation(operation['id'], status='failed', stage='failed',
                progress=100, message='fixture device unavailable', error='device unavailable')
        self.host.operations = AgentOperations(self.root/'commands', self.store, dispatch)
        service = self.service()
        command = service.call({'action': 'plan_virtual_operation', 'arguments': {'virtual_device_id': 'vm1', 'action': 'start'}, 'request_id': 'virtual-plan'})
        result = service.call({'action': 'execute_virtual_operation', 'arguments': {'command_id': command['result']['command_id']}, 'request_id': 'virtual-execute'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('device unavailable', result['user_message'])
        self.assertIsNotNone(result['operation_id'])


class AutomationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hits = []
        self.failures = 0
        self.drop_response = False
        outer = self
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.hits.append(body)
                if outer.drop_response:
                    self.close_connection = True
                    return
                failed = len(outer.hits) <= outer.failures
                self.send_response(503 if failed else 200)
                self.end_headers()
                self.wfile.write(json.dumps({'ok': not failed, 'status': 'blocked' if failed else 'observed',
                    'reason_code': 'unavailable' if failed else 'ok', 'result': {'received': body}}).encode())
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_cli(self, action, *args, url=None):
        script = Path(__file__).parent/'assets/agent/skills/mediaflow-platform/scripts/mediaflow.py'
        self.assertTrue(script.is_file(), 'Skill HTTP client is missing')
        env = {**os.environ, 'MEDIAFLOW_API_URL': url or 'http://127.0.0.1:%s' % self.server.server_port,
               'MEDIAFLOW_RECEIPT_DB': str(Path(self.temp.name)/'receipts.db')}
        return subprocess.run([sys.executable, str(script), action, *args], env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=15)

    def test_read_retries_are_bounded_and_return_json(self):
        self.failures = 2
        response = self.run_cli('list_tasks', '--arguments', '{"limit":2}')
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertTrue(json.loads(response.stdout)['ok'])
        self.assertEqual(len(self.hits), 3)
        self.hits.clear()
        self.failures = 10
        self.assertNotEqual(self.run_cli('list_tasks').returncode, 0)
        self.assertEqual(len(self.hits), 3)

    def test_write_failure_never_retries_even_across_processes(self):
        self.failures = 100
        first = self.run_cli('execute_plan', '--request-id', 'write-1', '--arguments', '{"plan_id":"one"}')
        self.assertNotEqual(first.returncode, 0)
        second = self.run_cli('execute_plan', '--request-id', 'write-1', '--arguments', '{"plan_id":"one"}')
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(len(self.hits), 1)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        conflict = self.run_cli('execute_plan', '--request-id', 'write-1', '--arguments', '{"plan_id":"other"}')
        self.assertEqual(json.loads(conflict.stdout)['reason_code'], 'request_id_conflict')

    def test_non_loopback_and_missing_write_id_fail_before_network(self):
        self.assertNotEqual(self.run_cli('list_tasks', url='http://example.com').returncode, 0)
        self.assertNotEqual(self.run_cli('execute_plan').returncode, 0)
        self.assertEqual(self.hits, [])

    def test_large_arguments_file_and_cached_success(self):
        path = Path(self.temp.name)/'args.json'
        path.write_text('{"title":"test","body":"preference"}', encoding='utf-8')
        first = self.run_cli('memory_save', '--arguments-file', str(path), '--request-id', 'memory-1')
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_cli('memory_save', '--arguments-file', str(path), '--request-id', 'memory-1')
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(self.hits), 1)

    def test_response_lost_after_server_acceptance_does_not_resend(self):
        self.drop_response = True
        first = self.run_cli('execute_plan', '--request-id', 'lost', '--arguments', '{"plan_id":"one"}')
        self.assertEqual(json.loads(first.stdout)['status'], 'unknown')
        second = self.run_cli('execute_plan', '--request-id', 'lost', '--arguments', '{"plan_id":"one"}')
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(self.hits), 1)

    def test_corrupt_client_receipt_fails_closed_with_json_and_redacted_stderr(self):
        (Path(self.temp.name)/'receipts.db').write_bytes(b'corrupt sqlite fixture')
        result = self.run_cli('execute_plan', '--request-id', 'corrupt')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reason_code', json.loads(result.stdout))
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.hits, [])
