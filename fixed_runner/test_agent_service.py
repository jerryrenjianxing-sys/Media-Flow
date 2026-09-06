import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import requests

from agent_bridge import AgentBridge
from agent_mcp import handle
from agent_runtime import AgentRuntimeError, PROVIDER_ID, MODEL_ID
from agent_service import AgentService, handle_agent_http, public_messages


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.fail_send = False

    def stop(self):
        pass

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/provider':
            return {'all': [{'id': PROVIDER_ID, 'models': {MODEL_ID: {}}}], 'connected': [PROVIDER_ID]}
        if path == '/session':
            return {'id': 'ses_test'}
        if path == '/session/status':
            return {}
        if path.endswith('/prompt_async') and self.fail_send:
            raise TimeoutError('private upstream text')


class AgentServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runtime = FakeRuntime()
        self.service = AgentService(lambda: {'paused': True, 'private': 'secret', 'devices': []},
                                    root=Path(self.folder.name), runtime=self.runtime)
        self.runtime.root = Path(self.folder.name) / 'engine'
        self.service.auth.reference_resolver = lambda: 'fixture-key-not-real'
        self.service.create_session({})

    def tearDown(self):
        self.service.close()
        self.folder.cleanup()

    def test_duplicate_message_is_not_dispatched_twice(self):
        body = {'text': '读取状态', 'request_id': 'req_one'}
        self.service.send('ses_test', body)
        self.service.send('ses_test', body)
        self.assertEqual(sum(path.endswith('/prompt_async') for _, path, _ in self.runtime.calls), 1)
        with self.assertRaises(ValueError):
            self.service.send('ses_test', {**body, 'text': '不同内容'})

    def test_turn_deadline_stops_late_tools_and_does_not_replay(self):
        self.service.send('ses_test', {'text': '检查状态', 'request_id': 'deadline'})
        with self.service.database() as db:
            db.execute('UPDATE turns SET created=?', (time.time()-601,))
        with self.assertRaises(ValueError):
            self.service._tool_session('ses_test')
        original = self.runtime.request
        self.runtime.request = lambda method, path, body=None: {'ses_test': {'type': 'busy'}} if path == '/session/status' else original(method, path, body)
        self.service.expire_turns()
        with self.service.database() as db:
            self.assertEqual(db.execute('SELECT state FROM turns').fetchone()[0], 'timed_out')
        self.assertIn(('POST', '/session/ses_test/abort', None), self.runtime.calls)

    def test_completed_turn_is_not_misreported_as_timeout(self):
        self.service.send('ses_test', {'text': '检查状态', 'request_id': 'finished'})
        with self.service.database() as db:
            db.execute('UPDATE turns SET created=?', (time.time()-601,))
        self.service.expire_turns()
        with self.service.database() as db:
            self.assertEqual(db.execute('SELECT state FROM turns').fetchone()[0], 'completed')
        self.assertFalse(any(path.endswith('/abort') for _, path, _ in self.runtime.calls))

    def test_unknown_result_survives_restart_without_replay(self):
        body = {'text': '读取状态', 'request_id': 'req_unknown'}
        self.runtime.fail_send = True
        with self.assertRaises(AgentRuntimeError):
            self.service.send('ses_test', body)
        restarted = AgentService(lambda: {}, root=Path(self.folder.name), runtime=self.runtime)
        self.assertEqual(restarted.send('ses_test', body)['state'], 'unknown')
        self.assertEqual(sum(path.endswith('/prompt_async') for _, path, _ in self.runtime.calls), 1)

    def test_history_reads_restore_pending_request_and_questions_without_dispatch(self):
        self.service.send('ses_test', {'text': '检查状态', 'request_id': 'req_restore'})
        original = self.runtime.request
        def read(method, path, body=None):
            if path.endswith('/message?limit=80'):
                return [{'info': {'id': 'msg_history', 'role': 'user'}, 'parts': [{'type': 'text', 'text': '历史'}]}]
            if path == '/question':
                return [{'id': 'que_one', 'sessionID': 'ses_test', 'questions': [{'question': '多少条？'}]}]
            return original(method, path, body)
        self.runtime.request = read
        result = self.service.messages('ses_test')
        self.assertEqual(result['state'], 'waiting_user')
        self.assertEqual(result['turns'][0]['id'], 'req_restore')
        self.assertEqual(result['messages'][0]['id'], 'msg_history')
        self.assertEqual(sum(path.endswith('/prompt_async') for _, path, _ in self.runtime.calls), 1)

    def test_answer_checks_question_ownership_and_engine_fault_stays_distinct(self):
        self.runtime.request = Mock(return_value=[{'id': 'que_one', 'sessionID': 'ses_test'}])
        self.service.answer('ses_test', {'question_id': 'que_one', 'answers': [['两条']]})
        self.assertEqual(self.runtime.request.call_args.args[:2], ('POST', '/question/que_one/reply'))
        self.runtime.request.return_value = []
        with self.assertRaisesRegex(ValueError, '问题已处理'):
            self.service.answer('ses_test', {'question_id': 'que_one', 'answers': [['两条']]})
        self.runtime.request.side_effect = AgentRuntimeError('engine_not_started', '请先连接对话引擎')
        with self.assertRaises(AgentRuntimeError) as raised:
            self.service.answer('ses_test', {'question_id': 'que_one', 'answers': [['两条']]})
        self.assertEqual(raised.exception.code, 'engine_not_started')

    def test_key_in_chat_rejected_before_dispatch(self):
        with self.assertRaises(ValueError):
            self.service.send('ses_test', {'text': 'sk-sp-' + 'x' * 40, 'request_id': 'req_key'})
        self.assertFalse(any(path.endswith('/prompt_async') for _, path, _ in self.runtime.calls))

    def test_status_tool_never_exposes_full_snapshot(self):
        result = self.service.call_tool('platform_status', {})
        self.assertTrue(result['paused'])
        self.assertNotIn('private', result)
        with self.assertRaises(ValueError):
            self.service.call_tool('run_tasks', {})

    def test_tool_idempotency_is_scoped_to_conversation(self):
        self.service.permissions.set('one', 'develop', source='user_settings')
        self.service.permissions.set('two', 'develop', source='user_settings')
        with patch.object(self.service, '_tool_session'), patch.object(self.service.repairs, 'tool', return_value={}) as invoke:
            self.service.call_tool('repair_create', {}, context={'session_id': 'one', 'call_id': 'call0'})
            self.service.call_tool('repair_create', {}, context={'session_id': 'two', 'call_id': 'call0'})
            self.service.call_tool('repair_create', {}, context={'session_id': 'one', 'call_id': 'call0'})
        keys = [call.args[2]['call_id'] for call in invoke.call_args_list]
        self.assertNotEqual(keys[0], keys[1])
        self.assertEqual(keys[0], keys[2])

    def test_message_projection_omits_tool_input_and_raw_error(self):
        result = public_messages([{'info': {'role': 'assistant', 'error': 'secret'}, 'parts': [
            {'type': 'tool', 'state': {'input': 'secret', 'output': 'secret', 'status': 'completed'}}]}])
        self.assertNotIn('secret', json.dumps(result))

    def test_remote_host_is_rejected_before_service_access(self):
        handler = Mock()
        handler.client_address = ('127.0.0.1', 12345)
        handler.headers = {'Host': 'attacker.example', 'Content-Type': 'application/json'}
        handle_agent_http(handler, 'POST', '/api/agent/start', {})
        self.assertEqual(handler._json.call_args.args[1], 403)
        handler.agent_service.assert_not_called()

    def test_mcp_has_real_tool_receipt_and_rejects_unknown(self):
        invoke = Mock(return_value={'status': 'completed', 'tool_call_id': 'receipt'})
        result = handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'platform_status'}}, invoke)
        self.assertIn('receipt', result['result']['content'][0]['text'])
        result = handle({'id': 2, 'method': 'tools/call', 'params': {'name': 'shell'}}, invoke)
        self.assertTrue(result['result']['isError'])
        self.assertEqual(invoke.call_count, 1)


class AgentBridgeTests(unittest.TestCase):
    def test_authentication_and_network_timeout_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = AgentBridge(Path(folder), lambda *_: {}, key_resolver=lambda: 'dummy-not-real')
            bridge.start()
            http = requests.Session()
            http.trust_env = False
            try:
                url = bridge.url + '/model/chat/completions'
                payload = {'model': MODEL_ID, 'messages': []}
                self.assertEqual(http.post(url, json=payload, timeout=3).status_code, 401)
                with patch('agent_bridge.requests.Session') as upstream:
                    upstream.return_value.__enter__.return_value.post.side_effect = requests.Timeout('secret')
                    response = http.post(url, json=payload, headers={'Authorization': 'Bearer ' + bridge.token}, timeout=3)
                self.assertEqual(response.status_code, 504)
                self.assertEqual(response.json()['error']['code'], 'network_timeout')
                self.assertNotIn('secret', response.text)
                deadline = time.monotonic() + 2
                while True:
                    with bridge.database() as conn:
                        recorded = conn.execute('SELECT status FROM calls').fetchone()[0]
                    if recorded != 'running' or time.monotonic() >= deadline:
                        break
                    time.sleep(.01)
                self.assertEqual(recorded, 'network_timeout')
            finally:
                http.close()
                bridge.stop()


if __name__ == '__main__':
    unittest.main()
