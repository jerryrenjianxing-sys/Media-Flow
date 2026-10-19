"""The installed client connects without rebuilding files or trusting stale identity."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


SCRIPTS = Path(__file__).parent / 'assets/agent/skills/mediaflow-platform/scripts'


@unittest.skipUnless(shutil.which('powershell') or shutil.which('pwsh'), 'PowerShell required')
class ConnectionClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='MediaFlow 中文 空格 ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hits = []
        self.response_override = {}
        self.mode = 'normal'
        outer = self

        class Endpoint(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(('GET', self.path))
                if outer.mode == 'slow_headers':
                    try:
                        self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                        self.wfile.flush()
                        for _ in range(18):
                            self.wfile.write(b'x')
                            self.wfile.flush()
                            time.sleep(.4)
                        self.wfile.write(b'\r\nContent-Length: 2\r\n\r\n{}')
                        self.wfile.flush()
                    except OSError:
                        pass
                    return
                if outer.mode == 'trickle':
                    self.send_response(200)
                    self.end_headers()
                    try:
                        for _ in range(16):
                            self.wfile.write(b' ')
                            self.wfile.flush()
                            time.sleep(.4)
                    except OSError:
                        pass
                    return
                data = {'product': 'MediaFlow', 'api_version': '1', 'status': 'available',
                    'product_version': {'version': '0.4.1-dev.51'},
                    'connection': dict(outer.connection, **outer.response_override),
                    'client_runtime': {'python': None, 'available': False},
                    'capabilities': [{'id': 'content', 'title': '定制主题'}],
                    'onboarding_summary': {'registered_devices': 2, 'online_verified': False,
                        'task_readiness': 'not_checked', 'paused': True},
                    'onboarding': {'task_summary': {'completed': 999}, 'devices': [{'name': 'retired'}]}}
                self.send_response(200)
                payload = json.dumps(data).encode()
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                outer.hits.append(('POST', self.path))
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true,"status":"read"}')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.file = self.root / '连接资料.json'
        self.skill = self.root / 'SKILL.md'
        self.skill.write_text('# 测试技能', encoding='utf-8')
        self.connection = {'schema': 1, 'product': 'MediaFlow', 'instance_id': 'acceptance-client',
            'data_root': str(self.root), 'app_root': str(self.root),
            'connection_file': str(self.file),
            'api_url': 'http://127.0.0.1:%d' % self.server.server_port,
            'management_url': 'http://127.0.0.1:3001', 'stream_url': 'http://127.0.0.1:48139',
            'skill_path': str(self.skill), 'client_path': str(SCRIPTS / 'mediaflow.ps1'),
            'python': str(self.root / 'missing-python.exe'), 'runtime_available': False,
            'version': {'version': '0.4.1-dev.51'}, 'mode': 'installed'}
        self.save_connection()

    def save_connection(self):
        self.file.write_text(json.dumps(self.connection, ensure_ascii=False), encoding='utf-8')

    def call(self, *args, python=False, environment=None, launcher=None):
        env = {key: value for key, value in os.environ.items() if not key.startswith('MEDIAFLOW_')}
        env.update(PATH='', MEDIAFLOW_API_URL='http://127.0.0.1:0',
            MEDIAFLOW_PYTHON='not-a-runtime', MEDIAFLOW_SKILL_CONFIG=str(self.root/'unused.json'),
            MEDIAFLOW_RECEIPT_DB=str(self.root/'receipts.db'), LOCALAPPDATA=str(self.root/'isolated-appdata'))
        env.update(environment or {})
        executable = [sys.executable, str(SCRIPTS/'mediaflow.py')] if python else [
            shutil.which('powershell') or shutil.which('pwsh'), '-NoProfile', '-ExecutionPolicy',
            'Bypass', '-File', str(launcher or SCRIPTS/'mediaflow.ps1')]
        return subprocess.run(executable + list(args), env=env, cwd=self.root, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=12)

    def test_existing_connection_file_wins_legacy_settings_without_python_or_new_files(self):
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        result = self.call('check', '--connection-file', str(self.file))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['reason_code'], 'connected')
        self.assertFalse(payload['client_runtime']['available'])
        self.assertEqual(payload['onboarding_summary']['registered_devices'], 2)
        self.assertNotIn('onboarding', payload)
        self.assertEqual(self.hits, [('GET', '/api/automation')])
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, before)

    def test_rendered_connection_card_runs_under_restricted_execution_policy(self):
        from platform_connection import connection_card
        self.file = self.root/"用户's 连接资料.json"
        self.connection['connection_file'] = str(self.file)
        self.save_connection()
        card = connection_card(self.connection)
        command = re.search(r'```powershell\n(.*?)\n```', card, re.DOTALL).group(1)
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        env = {key: value for key, value in os.environ.items() if not key.startswith('MEDIAFLOW_')}
        env.update(PATH='', MEDIAFLOW_PYTHON='missing-python', MEDIAFLOW_API_URL='http://127.0.0.1:0',
            LOCALAPPDATA=str(self.root/'isolated-appdata'))
        result = subprocess.run([shutil.which('powershell') or shutil.which('pwsh'), '-NoProfile',
            '-ExecutionPolicy', 'Restricted', '-Command', command], env=env, cwd=self.root,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['reason_code'], 'connected')
        self.assertEqual(self.hits, [('GET', '/api/automation')])
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, before)

    def test_direct_python_rejects_wrong_instance_before_any_business_request(self):
        self.response_override = {'instance_id': 'some-other-installation'}
        result = self.call('content_plan_save', '--arguments', '{}', '--request-id',
            'must-not-submit', '--connection-file', str(self.file), python=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['reason_code'], 'wrong_instance')
        self.assertEqual(self.hits, [('GET', '/api/automation')])
        self.assertFalse((self.root/'receipts.db').exists())

    def test_direct_python_uses_current_connection_for_business_action(self):
        result = self.call('list_tasks', '--connection-file', str(self.file), python=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertEqual(self.hits.count(('POST', '/api/automation')), 1)

    def test_explicit_connection_preserves_legacy_receipts_and_at_most_once_writes(self):
        receipt_path = self.root/'已存在回执目录/requests.db'
        (self.root/'unused.json').write_text(json.dumps({'api_url': 'http://127.0.0.1:0',
            'python': 'wrong-python', 'receipt_db': str(receipt_path)}), encoding='utf-8')
        for _ in range(2):
            result = self.call('content_plan_save', '--request-id', 'saved-once',
                '--connection-file', str(self.file), python=True, environment={'MEDIAFLOW_RECEIPT_DB': ''})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(receipt_path.exists())
        self.assertEqual(self.hits.count(('POST', '/api/automation')), 1)

    def test_invalid_receipt_config_does_not_block_check_but_never_guesses_a_write_store(self):
        (self.root/'unused.json').write_text('{', encoding='utf-8')
        check = self.call('check', '--connection-file', str(self.file))
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        result = self.call('content_plan_save', '--request-id', 'never-guess-receipts',
            '--connection-file', str(self.file), python=True)
        self.assertEqual(json.loads(result.stdout)['reason_code'], 'invalid_configuration')
        self.assertFalse(any(method == 'POST' for method, _ in self.hits))
        self.assertFalse((self.root/'receipts.db').exists())

    def test_missing_invalid_and_stale_files_are_explained_without_network_requests(self):
        for content, expected in [(None, 'missing_connection_file'), ('{', 'invalid_connection_file')]:
            with self.subTest(expected=expected):
                if content is None:
                    self.file.unlink()
                else:
                    self.file.write_text(content, encoding='utf-8')
                for direct in (False, True):
                    result = self.call('check' if not direct else 'list_tasks', '--connection-file', str(self.file), python=direct)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload['reason_code'], expected)
                    self.assertIn('重新复制', payload['user_message'])
                    self.assertEqual(self.hits, [])
        self.connection['skill_path'] = str(self.root/'moved/SKILL.md')
        self.save_connection()
        for direct in (False, True):
            result = self.call('check' if not direct else 'list_tasks', '--connection-file', str(self.file), python=direct)
            self.assertEqual(json.loads(result.stdout)['reason_code'], 'stale_connection')
        self.assertEqual(self.hits, [])

    def test_wrong_instance_or_version_never_sends_a_write(self):
        for override, expected in [({'instance_id': 'other'}, 'wrong_instance'),
                ({'data_root': str(self.root/'other')}, 'wrong_instance'),
                ({'version': {'version': '0.4.1-dev.50'}}, 'stale_connection')]:
            for direct in (False, True):
                with self.subTest(override=override, python=direct):
                    self.response_override = override
                    result = self.call('content_plan_save', '--request-id', 'never-submit',
                        '--connection-file', str(self.file), python=direct)
                    self.assertEqual(json.loads(result.stdout)['reason_code'], expected)
                    self.assertFalse(any(method == 'POST' for method, _ in self.hits))
                    self.assertFalse((self.root/'receipts.db').exists())

    def test_windows_path_identity_is_case_insensitive(self):
        self.response_override = {key: self.connection[key].upper() for key in (
            'data_root', 'app_root', 'skill_path', 'client_path', 'python')}
        for direct in (False, True):
            result = self.call('list_tasks' if direct else 'check', '--connection-file', str(self.file), python=direct)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_connection_is_reread_after_upgrade_and_missing_runtime_does_not_hide_connection(self):
        first = self.call('check', '--connection-file', str(self.file))
        self.assertEqual(json.loads(first.stdout)['client_runtime']['reason_code'], 'runtime_missing')
        moved = self.root/'新版 程序'
        moved.mkdir()
        moved_skill = moved/'SKILL.md'
        moved_skill.write_text('# 已升级', encoding='utf-8')
        self.connection.update(app_root=str(moved), skill_path=str(moved_skill), python=sys.executable,
            runtime_available=True, version={'version': '0.4.1-dev.52'})
        self.save_connection()
        second = self.call('check', '--connection-file=' + str(self.file))
        payload = json.loads(second.stdout)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(payload['connection']['version']['version'], '0.4.1-dev.52')
        self.assertEqual(payload['client_runtime']['python'], sys.executable)
        self.assertTrue(payload['client_runtime']['available'])
        self.assertEqual(self.hits, [('GET', '/api/automation')] * 2)

    def test_missing_runtime_blocks_launcher_business_request_without_claiming_disconnection(self):
        result = self.call('list_tasks', '--connection-file', str(self.file))
        payload = json.loads(result.stdout)
        self.assertEqual(payload['reason_code'], 'runtime_missing')
        self.assertEqual(payload['service_state'], 'running')
        self.assertEqual(self.hits, [('GET', '/api/automation')])

    def test_launcher_uses_installed_python_instead_of_legacy_environment(self):
        self.connection.update(python=sys.executable, runtime_available=True)
        self.save_connection()
        result = self.call('list_tasks', '--connection-file', str(self.file))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertEqual(self.hits.count(('POST', '/api/automation')), 1)

    def test_old_launcher_uses_current_client_after_installation_moves(self):
        current = self.root/'升级后的安装'
        shutil.copytree(SCRIPTS.parent, current)
        old_launcher = self.root/'仅保留的旧入口.ps1'
        shutil.copyfile(SCRIPTS/'mediaflow.ps1', old_launcher)
        self.connection.update(app_root=str(current), skill_path=str(current/'SKILL.md'),
            client_path=str(current/'scripts/mediaflow.ps1'), python=sys.executable, runtime_available=True)
        self.save_connection()
        result = self.call('list_tasks', '--connection-file', str(self.file), launcher=old_launcher)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertEqual(self.hits.count(('POST', '/api/automation')), 1)

    def test_python_identity_deadline_includes_slow_response_headers(self):
        self.mode = 'slow_headers'
        started = time.monotonic()
        result = self.call('content_plan_save', '--request-id', 'no-post-after-timeout',
            '--connection-file', str(self.file), python=True)
        self.assertLess(time.monotonic() - started, 6.2)
        self.assertEqual(json.loads(result.stdout)['reason_code'], 'connection_timeout')
        self.assertFalse(any(method == 'POST' for method, _ in self.hits))
        self.assertFalse((self.root/'receipts.db').exists())

    def test_check_deadline_is_shared_across_streamed_response_body(self):
        self.mode = 'trickle'
        started = time.monotonic()
        result = self.call('check', '--connection-file', str(self.file))
        self.assertLess(time.monotonic()-started, 8)
        self.assertEqual(json.loads(result.stdout)['reason_code'], 'connection_timeout')
        self.assertEqual(self.hits, [('GET', '/api/automation')])


if __name__ == '__main__':
    unittest.main()
