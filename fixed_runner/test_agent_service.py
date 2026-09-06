import json
from pathlib import Path
import tempfile
import time
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import requests

from agent_bridge import AgentBridge
from agent_mcp import handle
from agent_runtime import AgentRuntimeError, PROVIDER_ID, MODEL_ID
from agent_service import AgentService, handle_agent_http, public_messages
from agent_response_state import READ_TOOLS
from agent_mcp import TOOLS


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.fail_send = False

    def stop(self):
        pass

    def request(self, method, path, body=None, **kwargs):
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
    def test_cancelled_answer_does_not_leave_a_stale_question_blocking_input(self):
        fixture = self.recovery_fixture()
        fixture['questions'] = [{'id':'pending','sessionID':'ses_test','questions':[{'question':'多少条？'}]}]
        self.service.stop_response('ses_test')
        result = self.service.messages('ses_test')
        self.assertEqual(result['questions'], [])
        self.assertEqual(result['state'], 'idle')

    def test_old_generic_title_uses_visible_user_request_without_extra_model_call(self):
        self.runtime.request = Mock(side_effect=[
            [{'info': {'role':'user','id':'u'},'parts':[{'type':'text','text':'检查设备并整理异常证据'}]}], {}, []])
        self.service.messages('ses_test')
        self.assertEqual(self.service.sessions()['sessions'][0]['title'], '检查设备并整理异常证据')
        self.assertFalse(any(call.args[0] == 'POST' for call in self.runtime.request.call_args_list))

    def test_skill_guide_and_plan_navigation_have_real_tools(self):
        guide = self.service.call_tool('workflow_guide', {})
        self.assertIn('name: mediaflow-platform', guide['guide'])
        self.assertEqual(guide['guide_version'], '23')
        self.service.platform = Mock()
        self.service.platform.plans.return_value = {'plans': [{'plan_id': 'old'}]}
        context = {'session_id': 'ses_test', 'call_id': 'call'}
        with patch.object(self.service, '_tool_session'):
            result = self.service.call_tool('plan_status', {}, context=context)
            self.assertEqual(result['plans'][0]['plan_id'], 'old')
            self.service.call_tool('repreview_plan', {'plan_id': 'old'}, context=context)
        self.service.platform.repreview.assert_called_once()
        self.assertEqual(self.service.platform.repreview.call_args.args[:2], ('old', 'ses_test'))

    def test_actual_plan_writes_survive_followup_without_prose_gate(self):
        config = dict(zip(('like_probability','favorite_probability','comment_probability',
                           'matched_like_probability','matched_favorite_probability','matched_comment_probability'),
                          (.3,.2,.1,.6,.5,.4)))
        self.service.platform = Mock()
        self.service.platform.get.return_value = {'plan_id': 'plan', 'preview': {'plan_hash': 'hash'},
            'config': config, 'stopped_device_ids': ['device']}
        self.service.platform.confirm.return_value = {'batch_id': 'plan'}
        self.service.permissions.record('ses_test', 'original', '按之前要求运行')
        self.service.permissions.record('ses_test', 'followup', '恢复这批任务')
        with patch.object(self.service, '_tool_session'):
            result = self.service.call_tool('execute_plan', {'plan_id': 'plan'},
                context={'session_id': 'ses_test', 'call_id': 'native_call'})
        self.assertEqual(result['authorization']['writes'], config)
        self.assertTrue(result['authorization']['tool_call_id'])
        self.assertTrue(self.service.platform.confirm.call_args.args[2]['resume_stopped_devices'])

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

    def recovery_fixture(self):
        self.service.send('ses_test', {'text': '帮我规划视频任务', 'request_id': 'recover_one'})
        row = self.service.recovery.current('ses_test')
        messages = [{'info': {'id': row['original_message'], 'role': 'user'}, 'parts': []},
                    {'info': {'id': 'original_reply', 'role': 'assistant', 'finish': 'stop'},
                     'parts': [{'type': 'text', 'text': '还差几个参数，请确认：'}]}]
        fixture = {'messages': messages, 'questions': [], 'engine': 'idle', 'fail_dispatch': False}
        original = self.runtime.request
        def request(method, path, body=None, **kwargs):
            if path.endswith('/message?limit=80'):
                return fixture['messages']
            if path == '/session/status':
                return {'ses_test': {'type': fixture['engine']}}
            if path == '/question':
                return fixture['questions']
            result = original(method, path, body, **kwargs)
            if path.endswith('/prompt_async') and fixture['fail_dispatch']:
                raise TimeoutError('upstream unavailable')
            return result
        self.runtime.request = request
        return fixture

    def test_recovery_claim_is_once_across_concurrent_services_and_restart(self):
        self.recovery_fixture()
        second = AgentService(lambda: {}, root=self.service.root, runtime=self.runtime)
        self.addCleanup(second.close)
        barrier = threading.Barrier(2)
        original = self.runtime.request
        def synchronized(method, path, body=None, **kwargs):
            if path == '/question':
                barrier.wait(timeout=5)
            return original(method, path, body, **kwargs)
        self.runtime.request = synchronized
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(service.recovery.tick, 'ses_test', {}) for service in (self.service, second)]
            for future in futures:
                future.result(timeout=10)
        self.runtime.request = original
        second.recovery.tick('ses_test', {})
        self.assertEqual(self.service.recovery.current('ses_test')['attempts'], 1)
        self.assertEqual(sum(path.endswith('/prompt_async') for _, path, _ in self.runtime.calls), 2)

    def test_unknown_recovery_dispatch_is_not_replayed_on_restart(self):
        fixture = self.recovery_fixture()
        fixture['fail_dispatch'] = True
        self.service.recovery.tick('ses_test', {})
        second = AgentService(lambda: {}, root=self.service.root, runtime=self.runtime)
        self.addCleanup(second.close)
        second.recovery.tick('ses_test', {})
        self.assertEqual(second.recovery.current('ses_test')['state'], 'unknown')
        self.assertEqual(sum(path.endswith('/prompt_async') for _, path, _ in self.runtime.calls), 2)

    def test_stalled_recovery_read_uses_remaining_deadline_and_expires_on_error(self):
        self.recovery_fixture()
        self.service.recovery.tick('ses_test', {})
        clock = [time.time()]
        with self.service.database() as db:
            db.execute('UPDATE reply_recovery SET deadline=?', (clock[0]+.25,))
        original = self.runtime.request
        timeouts = []
        def stalled(method, path, body=None, **kwargs):
            if method == 'GET':
                timeouts.append(kwargs['timeout'])
                clock[0] += .3
                raise TimeoutError('engine stuck')
            return original(method, path, body, **kwargs)
        self.runtime.request = stalled
        with patch('agent_response_state.time.time', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                self.service.recovery.tick('ses_test', {})
        self.assertLessEqual(timeouts[0], .25)
        self.assertEqual(self.service.recovery.current('ses_test')['state'], 'timed_out')

    def test_recovery_deadline_is_persisted_even_when_engine_reads_fail(self):
        self.recovery_fixture()
        self.service.recovery.tick('ses_test', {})
        with self.service.database() as db:
            db.execute('UPDATE reply_recovery SET deadline=?', (time.time()-1,))
        original = self.runtime.request
        def unavailable(method, path, body=None, **kwargs):
            if method == 'GET':
                raise TimeoutError('engine stuck')
            return original(method, path, body, **kwargs)
        self.runtime.request = unavailable
        self.service.recovery.tick('ses_test', {})
        self.assertEqual(self.service.recovery.current('ses_test')['state'], 'timed_out')
        self.assertIn(('POST', '/session/ses_test/abort', None), self.runtime.calls)

    def test_late_dispatch_ack_cannot_overwrite_stop(self):
        self.recovery_fixture()
        original = self.runtime.request
        def stop_during_dispatch(method, path, body=None, **kwargs):
            if path.endswith('/prompt_async'):
                self.service.stop_response('ses_test')
            return original(method, path, body, **kwargs)
        self.runtime.request = stop_during_dispatch
        self.service.recovery.tick('ses_test', {})
        self.assertEqual(self.service.recovery.current('ses_test')['state'], 'cancelled')

    def test_recovery_guard_blocks_all_actions_after_completion_until_user_input(self):
        fixture = self.recovery_fixture()
        self.service.permissions.set('ses_test', 'develop', source='user_settings')
        self.service.recovery.tick('ses_test', {})
        row = self.service.recovery.current('ses_test')
        fixture['messages'].extend([
            {'info': {'id': row['message_id'], 'role': 'user'}, 'parts': []},
            {'info': {'id': 'supplement', 'role': 'assistant', 'finish': 'stop'},
             'parts': [{'type': 'text', 'text': '请告诉我视频数量。'}]}])
        self.service.recovery.tick('ses_test', {})
        self.assertEqual(self.service.recovery.current('ses_test')['state'], 'completed')
        for tool in TOOLS:
            if tool['name'] not in READ_TOOLS:
                with self.subTest(tool=tool['name']), self.assertRaisesRegex(ValueError, '补齐回答'):
                    self.service.call_tool(tool['name'], {}, context={'session_id': 'ses_test', 'call_id': tool['name']})
        self.assertTrue(self.service.call_tool('platform_status', {}, context={'session_id': 'ses_test', 'call_id': 'read'})['paused'])
        self.service.send('ses_test', {'text': '仅规划两条视频', 'request_id': 'next_request'})
        self.assertEqual(self.service.recovery.current('ses_test')['guarded'], 0)

    def test_question_phase_wins_over_pending_recovery_and_answer_is_idempotent(self):
        fixture = self.recovery_fixture()
        self.service.recovery.tick('ses_test', {})
        fixture['questions'] = [{'id': 'question_types', 'sessionID': 'ses_test', 'questions': [
            {'question': '设备？', 'options': [{'label': '设备2', 'description': ''}]},
            {'question': '入口？', 'multiple': True, 'options': [{'label': '首页', 'description': ''}, {'label': '搜索', 'description': ''}]},
            {'question': '搜索词？', 'custom': True, 'options': []}]}]
        self.assertEqual(self.service.messages('ses_test')['response']['phase'], 'waiting_user')
        body = {'question_id': 'question_types', 'answers': [['设备2'], ['首页', '搜索'], ['我自己的搜索词']]}
        self.service.answer('ses_test', body)
        self.service.answer('ses_test', body)
        self.assertEqual(sum(path == '/question/question_types/reply' for _, path, _ in self.runtime.calls), 1)
        self.assertEqual(self.service.recovery.current('ses_test')['guarded'], 0)

    def test_question_rejects_multiple_answers_to_single_choice_and_disabled_custom(self):
        fixture = self.recovery_fixture()
        fixture['questions'] = [{'id': 'strict_choice', 'sessionID': 'ses_test', 'questions': [
            {'question': '设备？', 'custom': False, 'options': [{'label': '设备2', 'description': ''}, {'label': '设备3', 'description': ''}]}]}]
        for answers in [[['设备2', '设备3']], [['未经列出的选项']]]:
            with self.subTest(answers=answers), self.assertRaises(ValueError):
                self.service.answer('ses_test', {'question_id': 'strict_choice', 'answers': answers})
        self.assertFalse(any(path.endswith('/reply') for _, path, _ in self.runtime.calls))

    def test_unknown_question_answer_survives_restart_without_replay(self):
        fixture = self.recovery_fixture()
        fixture['questions'] = [{'id': 'uncertain_answer', 'sessionID': 'ses_test', 'questions': [{'question': '多少条？'}]}]
        original = self.runtime.request
        def unknown(method, path, body=None, **kwargs):
            result = original(method, path, body, **kwargs)
            if path.endswith('/reply'):
                raise TimeoutError('upstream result unknown')
            return result
        self.runtime.request = unknown
        body = {'question_id': 'uncertain_answer', 'answers': [['两条']]}
        with self.assertRaises((AgentRuntimeError, TimeoutError)):
            self.service.answer('ses_test', body)
        second = AgentService(lambda: {}, root=self.service.root, runtime=self.runtime)
        self.addCleanup(second.close)
        with self.assertRaisesRegex(ValueError, '不会重复发送'):
            second.answer('ses_test', body)
        fixture['questions'] = []
        self.assertTrue(second.answer('ses_test', body)['ok'])
        self.assertEqual(sum(path == '/question/uncertain_answer/reply' for _, path, _ in self.runtime.calls), 1)

    def test_stop_response_leaves_submitted_tasks_untouched_and_reports_cancelled(self):
        self.recovery_fixture()
        self.service.recovery.tick('ses_test', {})
        self.service.platform = Mock()
        self.service.platform.plans.return_value = {'plans': []}
        self.service.stop_response('ses_test')
        self.service.platform.stop_session.assert_not_called()
        self.assertEqual(self.service.messages('ses_test')['response']['phase'], 'cancelled')
        with self.assertRaises(ValueError):
            self.service.call_tool('platform_status', {}, context={'session_id': 'ses_test', 'call_id': 'late'})

    def test_new_admitted_turn_does_not_classify_previous_missing_questions(self):
        fixture = self.recovery_fixture()
        self.service.send('ses_test', {'text': '另一个新问题', 'request_id': 'next_request'})
        result = self.service.messages('ses_test')['response']
        self.assertEqual(result['phase'], 'replying')
        self.assertFalse(result['auto_recoverable'])
        self.service.recovery.tick('ses_test', {})
        self.assertEqual(self.service.recovery.current('ses_test')['attempts'], 0)

    def test_old_unenrolled_history_is_not_automatically_replayed(self):
        self.recovery_fixture()
        with self.service.database() as db:
            db.execute('DELETE FROM reply_recovery')
        self.service.recovery.tick('ses_test', {})
        self.assertIsNone(self.service.recovery.current('ses_test'))
        self.assertEqual(sum(path.endswith('/prompt_async') for _, path, _ in self.runtime.calls), 1)
        self.service.recover_response('ses_test')
        self.assertEqual(self.service.recovery.current('ses_test')['attempts'], 1)

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
        self.runtime.request = Mock(return_value=[{'id': 'que_one', 'sessionID': 'ses_test', 'questions':[{'question':'多少条？'}]}])
        self.service.answer('ses_test', {'question_id': 'que_one', 'answers': [['两条']]})
        self.assertEqual(self.runtime.request.call_args.args[:2], ('POST', '/question/que_one/reply'))
        self.runtime.request.return_value = []
        self.assertTrue(self.service.answer('ses_test', {'question_id': 'que_one', 'answers': [['两条']]})['ok'])
        with self.assertRaisesRegex(ValueError, '其他回答'):
            self.service.answer('ses_test', {'question_id': 'que_one', 'answers': [['三条']]})
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
