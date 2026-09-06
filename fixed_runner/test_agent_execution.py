"""Exercise the actual conversation -> tool -> planning -> queue boundary."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
import zipfile
from unittest.mock import Mock
from agent_service import AgentService
from task_store import TaskStore
from test_agent_service import FakeRuntime


class ConversationExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = TaskStore(root / 'tasks.db')
        self.store.set_paused(True)
        self.runtime = FakeRuntime()
        self.runtime.root = root / 'engine'
        self.launch = Mock(return_value=[{'running': True}])
        self.snapshot = {'paused': True, 'devices': [{'device_id': 'vm-one', 'device_type': 'virtual',
            'state': 'device', 'profile_verified': True, 'initialization_status': 'legacy'}],
            'virtualization': {'devices': [{'virtual_device_id': 'uuid', 'provider_instance_id': '0',
            'adb_endpoint': 'vm-one', 'android_identity': 'android-one'}]}}
        self.s = AgentService(lambda: self.snapshot, root=root / 'agent', runtime=self.runtime,
            store=self.store, model_status_reader=lambda: {'model_ready': True}, worker_launcher=self.launch)
        self.s.auth.reference_resolver = lambda: 'fixture'
        self.s.create_session({})
        self.config = {'device_ids': ['uuid'], 'video_count': 2, 'round_count': 1,
            'round_interval_minutes': 0, 'content_mode': 'general', 'engagement_inspection_enabled': False}

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def tool(self, name, arguments, call='one'):
        return self.s.call_tool(name, arguments, context={'session_id': 'ses_test', 'call_id': call})

    def test_chat_to_real_queue_without_card_and_no_duplicate(self):
        self.s.send('ses_test', {'text': '帮我跑这台两条视频，零点赞零收藏零评论', 'request_id': 'r1'})
        plan = self.tool('plan_tasks', {'config': self.config})
        result = self.tool('execute_plan', {'plan_id': plan['plan_id']}, 'two')
        again = self.tool('execute_plan', {'plan_id': plan['plan_id']}, 'three')
        self.assertEqual(result['task_ids'], again['task_ids'])
        self.assertEqual(len(self.store.list()), 1)
        self.assertTrue(self.store.is_paused())
        self.assertEqual(result['authorization']['source'], 'user_chat')
        self.assertEqual(self.store.list()[0].payload['like_probability'], 0)
        self.assertTrue(self.launch.called)

    def test_preview_creates_no_execution_receipt_and_requires_admitted_request(self):
        self.s.send('ses_test', {'text': '只做计划，不启动', 'request_id': 'r1'})
        plan = self.tool('plan_tasks', {'config': self.config})
        self.assertEqual(self.store.list(), [])
        self.assertIsNone(self.s.permissions.receipt('ses_test', plan['plan_id']))
        self.s.permissions.cancel_intent('ses_test')
        with self.assertRaisesRegex(ValueError, '真实用户请求'):
            self.tool('execute_plan', {'plan_id': plan['plan_id'], 'confirmed': True}, 'two')
        self.assertEqual(self.store.list(), [])
        self.s.send('ses_test', {'text': '就照刚才安排的做吧', 'request_id': 'r2'})
        result = self.tool('execute_plan', {'plan_id': plan['plan_id']}, 'three')
        self.assertEqual(result['authorization']['request_id'], 'r2')
        self.assertTrue(result['authorization']['tool_call_id'])
        self.assertEqual(len(self.store.list()), 1)

    def test_repair_tools_need_live_request_but_no_permission_tier(self):
        contents = b'VALUE = 1\n'
        bundle = Path(self.tmp.name) / 'repair-source.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('fixed_runner/fixture.py', contents)
            archive.writestr('manifest.json', json.dumps({'schema': 1, 'source_revision': 'fixture',
                'contains_runtime_data': False, 'files': {'fixed_runner/fixture.py': hashlib.sha256(contents).hexdigest()}}))
        self.s.repairs.bundle = bundle
        self.s.repairs.explicit_bundle = True
        with self.assertRaises(ValueError):
            self.tool('repair_create', {'purpose': 'fixture'})
        self.s.send('ses_test', {'text': '检查并准备这个夹具的修复', 'request_id': 'r1'})
        repair = self.tool('repair_create', {'purpose': 'fixture'}, 'create')
        self.assertEqual(repair['state'], 'ready')
        self.s.permissions.set('ses_test', 'operate', source='user_settings')
        read = self.tool('repair_read', {'repair_id': repair['id'], 'path': 'fixed_runner/fixture.py'}, 'read')
        self.assertEqual(read['lines'], ['VALUE = 1'])
        self.s.stop_response('ses_test')
        with self.assertRaises(ValueError):
            self.tool('repair_read', {'repair_id': repair['id'], 'path': 'fixed_runner/fixture.py'}, 'late')

    def test_multiple_question_rounds_preserve_original_request_and_resume_one_batch(self):
        self.s.send('ses_test', {'text': '按我们讨论的安排这台，细节请问我', 'request_id': 'r1'})
        missing = self.tool('plan_tasks', {'config': {'device_ids': ['uuid']}}, 'incomplete')
        self.assertEqual(missing['reason_code'], 'missing_parameters')
        self.assertEqual(self.store.list(), [])
        questions = []
        original = self.runtime.request
        def request(method, path, body=None, **kwargs):
            if path == '/question':
                return questions
            return original(method, path, body, **kwargs)
        self.runtime.request = request
        for number, (prompt, answer) in enumerate([('几条？', '两条，1轮'), ('入口和互动？', '首页，零点赞零收藏零评论')]):
            question_id = 'question_' + str(number)
            questions[:] = [{'id': question_id, 'sessionID': 'ses_test', 'questions': [{'question': prompt}]}]
            body = {'question_id': question_id, 'answers': [[answer]]}
            self.s.answer('ses_test', body)
            self.s.answer('ses_test', body)
        questions.clear()
        current = self.s.permissions.current('ses_test')
        self.assertEqual(current['id'], 'r1')
        self.assertEqual(current['intent']['answers'], [
            {'question_id': 'question_0', 'answers': [['两条，1轮']]},
            {'question_id': 'question_1', 'answers': [['首页，零点赞零收藏零评论']]}])
        self.assertEqual(sum(path.endswith('/reply') for _, path, _ in self.runtime.calls), 2)
        plan = self.tool('plan_tasks', {'config': self.config}, 'ready')
        self.assertEqual(self.tool('plan_status', {}, 'list')['plans'][0]['plan_id'], plan['plan_id'])
        first = self.tool('execute_plan', {'plan_id': plan['plan_id']}, 'start')
        self.assertEqual(first['authorization']['request_id'], 'r1')
        self.assertTrue(first['authorization']['tool_call_id'])
        self.assertEqual(self.tool('execute_plan', {'plan_id': plan['plan_id']}, 'retry')['task_ids'], first['task_ids'])
        self.tool('pause_batch', {'plan_id': plan['plan_id']}, 'pause')
        self.store.request_stop(['vm-one', 'unrelated-device'])
        self.s.send('ses_test', {'text': '现在可以接着做了', 'request_id': 'r2'})
        resumed = self.tool('execute_plan', {'plan_id': plan['plan_id']}, 'resume')
        self.assertEqual(resumed['task_ids'], first['task_ids'])
        self.assertEqual(resumed['authorization']['request_id'], 'r2')
        self.assertTrue(resumed['authorization']['resume_stopped_devices'])
        self.assertEqual(self.tool('execute_plan', {'plan_id': plan['plan_id']}, 'resume_again')['task_ids'], first['task_ids'])
        self.assertEqual(len(self.store.list()), 1)
        self.assertFalse(self.store.is_stop_requested('vm-one'))
        self.assertTrue(self.store.is_stop_requested('unrelated-device'))
        self.assertTrue(self.store.is_paused())
        self.assertEqual(self.store.list()[0].payload['video_count'], 2)
        self.assertEqual(self.store.list()[0].payload['like_probability'], 0)

    def test_pause_resume_same_batch_and_stop_no_replay(self):
        self.s.send('ses_test', {'text': '启动任务', 'request_id': 'r1'})
        plan = self.tool('plan_tasks', {'config': self.config})
        args = {'plan_id': plan['plan_id']}
        first = self.tool('execute_plan', args, 'start')
        self.tool('pause_batch', args, 'pause')
        self.assertIsNone(self.store.claim_next('vm-one', 'worker'))
        self.s.send('ses_test', {'text': '继续', 'request_id': 'r2'})
        self.assertEqual(self.tool('execute_plan', args, 'continue')['task_ids'], first['task_ids'])
        self.s.send('ses_test', {'text': '恢复这批任务', 'request_id': 'r3'})
        self.assertEqual(self.tool('execute_plan', args, 'resume')['task_ids'], first['task_ids'])
        self.tool('stop_batch', args, 'stop')
        self.assertEqual(self.store.list()[0].status, 'cancelled')
        self.assertEqual(self.tool('execute_plan', args, 'repeat')['state'], 'cancelled')

if __name__ == '__main__':
    unittest.main()
