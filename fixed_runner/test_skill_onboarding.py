"""First contact must not turn status reads into maintenance or service startup."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from task_store import TaskStore


class OnboardingTests(unittest.TestCase):
    def test_get_reads_existing_snapshot_without_constructing_business_context(self):
        from automation import handle_automation_http
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root/'tasks.db')
            store.set_paused(True)
            task = store.submit('healthcheck', 'test-only', {})
            with store.connection() as db:
                db.execute("UPDATE tasks SET status='running' WHERE id=?", (task,))
                db.execute("INSERT INTO virtual_devices(id,provider,provider_instance_id,name,state,recipe_json,"
                    "provider_snapshot_json,created_at,updated_at,last_seen_at) VALUES"
                    "('vm','mumu','0','sample','running','{}','{}','old','new','yesterday')")
            before = hashlib.sha256(store.path.read_bytes()).hexdigest()
            calls = []
            def forbidden():
                self.fail('GET constructed business services or reconciled state')
            handler = SimpleNamespace(headers={'Host':'127.0.0.1:48138'},
                client_address=('127.0.0.1', 1), store=store, automation_context=forbidden,
                _json=lambda body, status=200: calls.append((status, body)))
            for _ in range(2):
                handle_automation_http(handler, 'GET', '/api/automation')
            payload = calls[-1][1]
            self.assertEqual(payload['product'], 'MediaFlow')
            snapshot = payload['onboarding']
            self.assertTrue(snapshot['paused'])
            self.assertEqual(snapshot['task_summary']['running'], 1)
            self.assertEqual(snapshot['devices'][0]['last_seen_at'], 'yesterday')
            self.assertEqual(snapshot['devices'][0]['last_known_state'], 'running')
            self.assertIsNone(snapshot['devices'][0]['online'])
            self.assertEqual(snapshot['source'], 'stored_snapshot')
            self.assertEqual(store.get(task).status, 'running')
            self.assertEqual(hashlib.sha256(store.path.read_bytes()).hexdigest(), before)
            self.assertFalse((root/'agent').exists())

    def test_missing_database_is_unknown_not_empty_or_created(self):
        from automation import handle_automation_http
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'missing.db'
            calls = []
            handler = SimpleNamespace(headers={'Host':'localhost:48138'}, client_address=('127.0.0.1',1),
                store=SimpleNamespace(path=path), automation_context=lambda: self.fail('not read only'),
                _json=lambda body, status=200: calls.append(body))
            handle_automation_http(handler, 'GET', '/api/automation')
            self.assertFalse(calls[0]['onboarding']['available'])
            self.assertIsNone(calls[0]['onboarding']['paused'])
            self.assertIsNone(calls[0]['onboarding']['task_summary'])
            self.assertFalse(path.exists())


@unittest.skipUnless(shutil.which('powershell') or shutil.which('pwsh'), 'PowerShell required')
class PowerShellCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = Path(__file__).parent/'assets/agent/skills/mediaflow-platform/scripts/mediaflow.ps1'
        self.hits = []
        self.mode = 'ok'
        outer = self
        class Endpoint(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(('GET', self.path))
                if outer.mode == 'timeout':
                    time.sleep(6)
                    return
                if outer.mode == 'not_found':
                    self.send_error(404)
                    return
                self.send_response(200)
                self.end_headers()
                data = {'product':'MediaFlow', 'api_version':'1', 'status':'available',
                    'client_runtime':{'available':False, 'python':None},
                    'onboarding':{'available':True,'source':'stored_snapshot','paused':True,
                        'task_summary':{'pending':4},'devices':[]}}
                if outer.mode == 'trickle':
                    try:
                        for _ in range(14):
                            self.wfile.write(b' ')
                            self.wfile.flush()
                            time.sleep(.5)
                    except OSError:
                        pass
                    return
                if outer.mode == 'large':
                    try:
                        self.wfile.write(b' '*2097152)
                    except OSError:
                        pass
                    return
                self.wfile.write((json.dumps(data) if outer.mode == 'ok' else '<html>not MediaFlow</html>').encode())
            def do_POST(self):
                outer.hits.append(('POST',self.path))
                self.send_error(405)
            def log_message(self,*args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Endpoint)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def check(self, url=None):
        env = {k:v for k,v in os.environ.items() if not k.startswith('MEDIAFLOW_')}
        env.update(PATH='', MEDIAFLOW_PYTHON='nonexistent-python',
            MEDIAFLOW_SKILL_CONFIG=str(self.root/'config.json'),
            MEDIAFLOW_API_URL=url or 'http://127.0.0.1:%s' % self.server.server_port)
        return subprocess.run([shutil.which('powershell') or shutil.which('pwsh'), '-NoProfile',
            '-ExecutionPolicy','Bypass','-File',str(self.script),'check'],env=env,cwd=self.root,
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=10)

    def test_check_works_without_python_and_never_writes_config_or_posts(self):
        config = self.root/'config.json'
        config.write_text('{"python":"invalid","custom":"keep"}',encoding='utf-8')
        before = config.read_bytes()
        for _ in range(2):
            result = self.check()
            self.assertEqual(result.returncode,0,result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['reason_code'],'connected')
            self.assertFalse(payload['client_runtime']['available'])
        self.assertEqual(self.hits,[('GET','/api/automation')]*2)
        self.assertEqual(config.read_bytes(),before)
        self.assertEqual([p.name for p in self.root.iterdir()],['config.json'])

    def test_unreachable_is_not_claimed_stopped(self):
        result = self.check('http://127.0.0.1:0')
        self.assertNotEqual(result.returncode,0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['reason_code'],'connection_failed')
        self.assertEqual(payload['service_state'],'unknown')
        self.assertEqual(payload['next_action'],'ask_user')
        self.assertEqual(self.hits,[])

    def test_wrong_service_and_timeout_are_bounded(self):
        for mode, reason in [('wrong','unexpected_service'),('not_found','unexpected_service'),
                             ('large','unexpected_service'),('timeout','connection_timeout'),
                             ('trickle','connection_timeout')]:
            with self.subTest(mode=mode):
                self.mode=mode
                started=time.monotonic()
                result=self.check()
                self.assertLess(time.monotonic()-started,9)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(json.loads(result.stdout)['reason_code'],reason)


if __name__ == '__main__':
    unittest.main()
