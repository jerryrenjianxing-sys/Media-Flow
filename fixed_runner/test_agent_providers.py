import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from agent_providers import AgentProviders, safe_authorization_url
from agent_runtime import PROVIDER_ID


class NativeFixture:
    def __init__(self, root):
        self.root, self.calls, self.states = root, [], {}
        self.callback_gate = None
        self.bad_url = False

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path))  # Never record request bodies in receipts.
        if path == '/provider':
            return {'all': [{'id': x, 'models': {'test': {'name': 'test'}}} for x in ('openai', PROVIDER_ID, 'azure')], 'connected': ['openai', PROVIDER_ID]}
        if path == '/provider/auth':
            return {'openai': [{'type': 'oauth', 'label': 'Browser'}, {'type': 'api', 'label': 'Key'}],
                    'azure': [{'type': 'api', 'label': 'Key', 'prompts': [{'key': 'resourceName', 'type': 'text'}]}]}
        if path == '/session/status':
            return self.states
        if path.startswith('/auth/'):
            file = self.root / 'data/opencode/auth.json'
            file.parent.mkdir(parents=True, exist_ok=True)
            values = json.loads(file.read_text()) if file.is_file() else {}
            values[path.split('/')[-1]] = body
            file.write_text(json.dumps(values))
            return True
        if path.endswith('/authorize'):
            return {'url': 'javascript:bad' if self.bad_url else 'https://login.example/authorize', 'method': 'code', 'instructions': 'Enter code'}
        if path.endswith('/callback'):
            if self.callback_gate:
                self.callback_gate.wait(3)
            return True
        return True


class AgentProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = NativeFixture(self.root / 'engine')
        self.lock = threading.RLock()
        self.auth = AgentProviders(self.root / 'ledger', self.runtime, self.lock, reference_resolver=lambda: 'reference-fixture')

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_preserves_native_auth_and_adds_key_fallback(self):
        values = {p['id']: p for p in self.auth.catalog()['providers']}
        self.assertEqual(len(values['openai']['auth_methods']), 2)
        self.assertEqual(values['azure']['auth_methods'][0]['prompts'][0]['key'], 'resourceName')
        self.assertEqual(values[PROVIDER_ID]['auth_methods'][0]['type'], 'api')
        self.assertNotIn('reference-fixture', json.dumps(values))

    def test_native_key_survives_restart_and_other_provider_is_unchanged(self):
        self.auth.save('openai', {'method': 1, 'key': 'openai-fixture'})
        self.auth.save(PROVIDER_ID, {'key': 'qwen-fixture'})
        restarted = AgentProviders(self.root / 'ledger', self.runtime, self.lock, reference_resolver=lambda: 'old-fixture')
        self.assertEqual(restarted.qwen_key(), 'qwen-fixture')
        self.assertEqual(restarted.native_auth('openai')['key'], 'openai-fixture')
        self.assertEqual(restarted.state(PROVIDER_ID)['test_status'], 'not_tested')
        self.assertNotIn(b'qwen-fixture', (self.root / 'ledger/providers.db').read_bytes())
        restarted.use_reference()
        self.assertEqual(restarted.qwen_key(), 'old-fixture')
        self.assertEqual(restarted.native_auth(PROVIDER_ID)['key'], 'qwen-fixture')

    def test_busy_rejects_before_mutation(self):
        self.runtime.states = {'session': {'type': 'busy'}}
        with self.assertRaises(ValueError):
            self.auth.save(PROVIDER_ID, {'key': 'candidate'})
        self.assertFalse(any(method == 'PUT' for method, _ in self.runtime.calls))

    def test_metadata_and_validation_preserve_native_semantics(self):
        self.auth.save('azure', {'key': 'fixture', 'inputs': {'resourceName': 'unit-test'}})
        self.assertEqual(self.auth.native_auth('azure')['metadata'], {'resourceName': 'unit-test'})
        with self.assertRaises(ValueError):
            self.auth.save('azure', {'key': 'candidate', 'inputs': {'unknown': 'value'}})
        self.assertEqual(self.auth.native_auth('azure')['key'], 'fixture')

    def test_qwen_gateway_password_is_not_real_credential(self):
        self.auth.reference_resolver = lambda: ''
        qwen = next(p for p in self.auth.catalog()['providers'] if p['id'] == PROVIDER_ID)
        self.assertFalse(qwen['connected'])

    def test_oauth_callback_once_and_configuration_locked_during_callback(self):
        flow = self.auth.authorize('openai', {'method': 0})
        gate = self.runtime.callback_gate = threading.Event()
        try:
            self.auth.callback(flow['id'], {'code': 'fixture-code'})
            self.auth.callback(flow['id'], {'code': 'fixture-code'})
            with self.assertRaises(ValueError):
                self.auth.save(PROVIDER_ID, {'key': 'candidate'})
            self.assertNotIn(b'fixture-code', (self.root / 'ledger/providers.db').read_bytes())
        finally:
            gate.set()
        deadline = time.monotonic() + 3
        while self.auth.flow(flow['id'])['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.auth.flow(flow['id'])['state'], 'completed')
        self.assertEqual(sum(path.endswith('/callback') for _, path in self.runtime.calls), 1)

    def test_restart_expiry_cancel_and_untrusted_url_have_exits(self):
        flow = self.auth.authorize('openai', {})
        restarted = AgentProviders(self.root / 'ledger', self.runtime, self.lock, reference_resolver=lambda: '')
        self.assertEqual(restarted.flow(flow['id'])['state'], 'interrupted')
        self.assertEqual(restarted.callback(flow['id'], {})['state'], 'interrupted')
        flow = restarted.authorize('openai', {})
        self.assertEqual(restarted.cancel(flow['id'])['state'], 'cancelled')
        flow = restarted.authorize('openai', {})
        with restarted.database() as db:
            db.execute('UPDATE auth_flows SET deadline=0 WHERE id=?', (flow['id'],))
        self.assertEqual(restarted.flow(flow['id'])['state'], 'expired')
        self.runtime.bad_url = True
        self.assertEqual(restarted.authorize('openai', {})['state'], 'failed')
        self.assertFalse(safe_authorization_url('https://user:password@example.org'))
        self.assertFalse(safe_authorization_url('file:///secret'))


if __name__ == '__main__':
    unittest.main()
