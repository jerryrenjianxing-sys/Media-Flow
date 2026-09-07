"""Portable MediaFlow Skill bundle and extracted CLI integration tests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from agent_memory import AgentMemory
from agent_platform import AgentPlatform
from task_store import TaskStore


class SkillBundleTests(unittest.TestCase):
    def test_builder_creates_deterministic_allowlisted_archive(self):
        spec = importlib.util.find_spec('skill_bundle')
        self.assertIsNotNone(spec, 'skill_bundle module is missing')
        if spec is None:
            return
        from skill_bundle import build_skill_bundle

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build_skill_bundle(root/'first.zip')
            second = build_skill_bundle(root/'second.zip')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), sorted(archive.namelist()))
                self.assertEqual(set(archive.namelist()), {
                    'mediaflow-platform/NOTICE.md',
                    'mediaflow-platform/README.md',
                    'mediaflow-platform/SKILL.md',
                    'mediaflow-platform/config.example.json',
                    'mediaflow-platform/examples/execute-arguments.json',
                    'mediaflow-platform/examples/plan-arguments.json',
                    'mediaflow-platform/examples/request-status-arguments.json',
                    'mediaflow-platform/references/api.md',
                    'mediaflow-platform/references/troubleshooting.md',
                    'mediaflow-platform/references/workflows.md',
                    'mediaflow-platform/scripts/_common.py',
                    'mediaflow-platform/scripts/mediaflow.py',
                    'mediaflow-platform/scripts/mediaflow.ps1',
                })
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0)
                                    for info in archive.infolist()))

    def test_builder_ignores_private_state_and_rejects_symlinks_and_machine_paths(self):
        spec = importlib.util.find_spec('skill_bundle')
        self.assertIsNotNone(spec, 'skill_bundle module is missing')
        if spec is None:
            return
        from skill_bundle import SKILL_SOURCE, build_skill_bundle

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'
            shutil.copytree(SKILL_SOURCE, source)
            (source/'config.json').write_text('{"api_url":"http://127.0.0.1:1"}', encoding='utf-8')
            (source/'receipts.db').write_bytes(b'private receipt')
            (source/'runtime').mkdir()
            (source/'runtime/auth.json').write_text('{"token":"secret"}', encoding='utf-8')
            archive_path = build_skill_bundle(root/'safe.zip', source=source)
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
            self.assertFalse(any(name.endswith('/config.json') or 'receipts' in name or '/runtime/' in name
                                 for name in names))

            readme = source/'README.md'
            original = readme.read_text(encoding='utf-8')
            readme.write_text(original + '\nC:\\Users\\someone\\private\\python.exe\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'machine-specific absolute path'):
                build_skill_bundle(root/'unsafe-path.zip', source=source)
            readme.write_text(original, encoding='utf-8')

            secret = root/'secret.txt'
            secret.write_text('not distributable', encoding='utf-8')
            notice = source/'NOTICE.md'
            notice.unlink()
            try:
                notice.symlink_to(secret)
            except OSError:
                with patch.object(Path, 'is_symlink', return_value=True):
                    with self.assertRaisesRegex(ValueError, 'symbolic link'):
                        build_skill_bundle(root/'unsafe-link.zip', source=source)
                return
            with self.assertRaisesRegex(ValueError, 'symbolic link'):
                build_skill_bundle(root/'unsafe-link.zip', source=source)


class ExtractedSkillCliTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.find_spec('skill_bundle')
        self.assertIsNotNone(spec, 'skill_bundle module is missing')
        if spec is None:
            return
        from skill_bundle import build_skill_bundle

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = TaskStore(self.root/'tasks.db')
        self.store.set_paused(True)
        self.launches = []
        snapshot = {'paused': True, 'devices': [{'device_id': 'vm-one', 'device_type': 'virtual',
            'state': 'device', 'profile_verified': True, 'initialization_status': 'legacy'}],
            'virtualization': {'devices': [{'virtual_device_id': 'vm-uuid', 'provider_instance_id': '0',
                'adb_endpoint': 'vm-one', 'android_identity': 'android-one'}]}}
        self.host = SimpleNamespace(root=self.root/'service', status_reader=lambda: snapshot,
            platform=AgentPlatform(self.root/'platform', self.store, lambda: snapshot,
                worker_launcher=self.launch, model_status_reader=lambda: {'model_ready': True}),
            memory=AgentMemory(self.root/'memory'), operations=None, repairs=None,
            repair_updates=None, evidence_root=None)
        self.hits = []
        outer = self

        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                from automation import AutomationService
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.hits.append(body)
                response = AutomationService(outer.host).call(body)
                if body.get('request_id') == 'unknown-plan':
                    self.close_connection = True
                    return
                status = 409 if response['reason_code'] == 'request_id_conflict' else (200 if response['ok'] else 400)
                if response['status'] == 'unknown':
                    status = 202
                payload = json.dumps(response, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        archive = build_skill_bundle(self.root/'mediaflow-platform.zip')
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(self.root/'extracted')
        self.skill = self.root/'extracted/mediaflow-platform'
        self.config = {'config': {'device_ids': ['vm-uuid'], 'video_count': 2, 'round_count': 1,
            'content_mode': 'general', 'engagement_inspection_enabled': False}}

    def launch(self, devices):
        self.launches.append(devices)
        return [{'running': True}]

    def run_cli(self, action, *arguments, request_id=None, session_id='portable-session'):
        command = [sys.executable, str(self.skill/'scripts/mediaflow.py'), action, *arguments,
                   '--session-id', session_id]
        if request_id:
            command.extend(['--request-id', request_id])
        env = {**os.environ,
            'MEDIAFLOW_API_URL': 'http://127.0.0.1:%s' % self.server.server_port,
            'MEDIAFLOW_RECEIPT_DB': str(self.root/'client-receipts.db')}
        return subprocess.run(command, env=env, capture_output=True, text=True,
            encoding='utf-8', timeout=15)

    def test_extracted_cli_plan_execute_status_repeat_unknown_and_resume(self):
        plan_call = self.run_cli('plan_tasks', '--arguments', json.dumps(self.config), request_id='plan-one')
        self.assertEqual(plan_call.returncode, 0, plan_call.stderr)
        plan_id = json.loads(plan_call.stdout)['plan_id']
        repeated_plan = self.run_cli('plan_tasks', '--arguments', json.dumps(self.config), request_id='plan-one')
        self.assertEqual(repeated_plan.stdout, plan_call.stdout)

        execute_args = json.dumps({'plan_id': plan_id})
        executed = self.run_cli('execute_plan', '--arguments', execute_args, request_id='execute-one')
        self.assertEqual(executed.returncode, 0, executed.stderr)
        execution = json.loads(executed.stdout)
        repeated = self.run_cli('execute_plan', '--arguments', execute_args, request_id='execute-one')
        self.assertEqual(repeated.stdout, executed.stdout)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.list()[0].payload['video_count'], 2)
        self.assertEqual(len(self.launches), 1)

        status = self.run_cli('plan_status', '--arguments', execute_args)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)['plan_id'], plan_id)

        paused = self.run_cli('pause_batch', '--arguments', execute_args, request_id='pause-one')
        self.assertEqual(paused.returncode, 0, paused.stderr)
        resume_args = json.dumps({'plan_id': plan_id, 'resume_stopped_devices': True})
        resumed = self.run_cli('execute_plan', '--arguments', resume_args, request_id='resume-one')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)['result']['task_ids'], execution['result']['task_ids'])
        resumed_again = self.run_cli('execute_plan', '--arguments', resume_args, request_id='resume-one')
        self.assertEqual(resumed_again.stdout, resumed.stdout)
        self.assertEqual(len(self.store.list()), 1)

        unknown = self.run_cli('plan_tasks', '--arguments', json.dumps(self.config), request_id='unknown-plan')
        self.assertNotEqual(unknown.returncode, 0)
        self.assertEqual(json.loads(unknown.stdout)['status'], 'unknown')
        unknown_again = self.run_cli('plan_tasks', '--arguments', json.dumps(self.config), request_id='unknown-plan')
        self.assertEqual(unknown_again.stdout, unknown.stdout)
        self.assertEqual(sum(hit.get('request_id') == 'unknown-plan' for hit in self.hits), 1)
        receipt = self.run_cli('request_status', '--arguments', '{"request_id":"unknown-plan"}')
        self.assertEqual(receipt.returncode, 0, receipt.stderr)
        self.assertTrue(json.loads(receipt.stdout)['result']['ok'])

    def test_powershell_launcher_prefers_environment_then_configured_runtime(self):
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not powershell:
            self.skipTest('PowerShell is unavailable')
        launcher = self.skill/'scripts/mediaflow.ps1'
        base_env = {**os.environ,
            'MEDIAFLOW_API_URL': 'http://127.0.0.1:%s' % self.server.server_port,
            'MEDIAFLOW_RECEIPT_DB': str(self.root/'launcher-receipts.db')}
        config_path = self.skill/'config.json'
        config_path.write_text(json.dumps({'api_url': base_env['MEDIAFLOW_API_URL'],
            'python': 'definitely-not-a-runtime'}), encoding='utf-8')
        explicit = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(launcher), 'list_tasks'], env={**base_env, 'MEDIAFLOW_PYTHON': sys.executable},
            capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)

        config_path.write_text(json.dumps({'api_url': base_env['MEDIAFLOW_API_URL'],
            'python': sys.executable}), encoding='utf-8')
        configured_env = {key: value for key, value in base_env.items() if key != 'MEDIAFLOW_PYTHON'}
        configured_env['MEDIAFLOW_SKILL_CONFIG'] = str(config_path)
        configured = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(launcher), 'list_tasks'], env=configured_env, capture_output=True, text=True,
            encoding='utf-8', timeout=15)
        self.assertEqual(configured.returncode, 0, configured.stderr)


if __name__ == '__main__':
    unittest.main()
