"""Exercise the actual conversation -> tool -> planning -> queue boundary."""
from pathlib import Path
import tempfile
import unittest
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

    def test_preview_does_not_authorize_even_with_model_confirmed(self):
        self.s.send('ses_test', {'text': '只做计划，不启动', 'request_id': 'r1'})
        plan = self.tool('plan_tasks', {'config': self.config})
        with self.assertRaises(ValueError):
            self.tool('execute_plan', {'plan_id': plan['plan_id'], 'confirmed': True}, 'two')
        self.assertEqual(self.store.list(), [])

    def test_grants_enforced_in_actual_tool_entry(self):
        self.s.send('ses_test', {'text': '读取状态', 'request_id': 'r1'})
        with self.assertRaises(ValueError):
            self.tool('repair_create', {'purpose': 'fixture'})
        self.s.send('ses_test', {'text': '开启开发模式', 'request_id': 'r2'})
        self.assertEqual(self.tool('session_permissions', {}, 'perm')['level'], 'develop')
        self.s.permissions.set('ses_test', 'operate', source='user_settings')
        with self.assertRaises(ValueError):
            self.tool('repair_create', {'purpose': 'fixture'}, 'repair')

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
