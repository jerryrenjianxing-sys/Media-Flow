import json
import tempfile
import unittest
from pathlib import Path

from agent_permissions import AgentPermissions, content_hash


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = AgentPermissions(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_and_legacy_sessions_have_full_tools_without_changing_grants(self):
        with self.p.database() as db:
            db.execute('INSERT INTO grants VALUES(?,?,?,?,?)', ('old', 'operate', 7, 'user_settings', 123))
        for session in ('new', 'old'):
            self.assertEqual(self.p.get(session)['level'], 'full')
            self.assertEqual(self.p.get(session)['label'], '平台操作')
            for tool in ('execute_plan', 'execute_virtual_operation', 'repair_edit', 'repair_apply'):
                self.p.require(session, tool)
            self.p.set(session, 'develop', source='user_settings')
        with self.p.database() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM grants').fetchone()), ('old', 'operate', 7, 'user_settings', 123))
        self.assertEqual(AgentPermissions(Path(self.tmp.name)).get('old')['level'], 'full')

    def test_user_prose_is_recorded_without_intent_or_percentage_parsing(self):
        for index, text in enumerate(('只做计划，不启动', '如何启动任务？', '开启开发模式', '恢复这批任务', '确认执行')):
            self.p.record('a', str(index), text)
            self.assertEqual(self.p.current('a')['intent'], {'statement': text})
        self.assertIsNone(self.p.receipt('a', 'p1'))

    def test_actual_chat_sequence_retains_both_probability_sets_without_prior_binding(self):
        config = dict(zip(('like_probability', 'favorite_probability', 'comment_probability',
                           'matched_like_probability', 'matched_favorite_probability', 'matched_comment_probability'),
                          (.3, .2, .1, .6, .5, .4)))
        # Preview/technical failures did not create any successful old binding.
        self.p.record('a', 'r1', '启动计划，恢复这批任务，主页点赞30%/收藏20%/评论10%，匹配点赞60%/收藏50%/评论40%')
        self.p.record('a', 'r2', '确认执行')
        receipt = self.p.authorize('a', 'p1', 'h1', config, stopped=True, tool_call_id='call2')
        self.assertEqual(receipt['writes'], config)
        self.assertTrue(receipt['resume_stopped_devices'])
        self.assertEqual(receipt['tool_call_id'], 'call2')
        self.assertEqual(receipt['config_hash'], content_hash(config))
        self.p.record('a', 'r3', '恢复这批任务')
        resumed = self.p.authorize('a', 'p1', 'h1', config, stopped=True)
        self.assertEqual(resumed['writes'], config)
        self.assertTrue(resumed['resume_stopped_devices'])

    def test_scope_receipt_hash_and_retry_are_bound_and_persisted(self):
        self.p.record('a', 'r1', '按刚才的参数做')
        first = self.p.authorize('a', 'p1', 'h1', {}, tool_call_id='call1')
        self.assertEqual(first['receipt_hash'], content_hash({k: v for k, v in first.items() if k != 'receipt_hash'}))
        self.assertEqual(first, AgentPermissions(Path(self.tmp.name)).authorize('a', 'p1', 'h1', {}, tool_call_id='call2'))
        self.assertEqual(first['request_id'], 'r1')
        for target, fingerprint, config in (('p2', 'h2', {}), ('p1', 'changed', {}), ('p1', 'h1', {'like_probability': .2})):
            with self.assertRaises(ValueError):
                self.p.authorize('a', target, fingerprint, config)
        with self.assertRaises(ValueError):
            self.p.authorize('b', 'p1', 'h1', {})

    def test_answers_keep_user_text_and_do_not_create_a_request(self):
        self.p.record('a', 'r1', '帮我跑这台')
        self.p.record_answer('a', 'q1', [['2条', '主页30%，匹配60%']], [{'question': 'model text'}])
        self.p.record_answer('a', 'q1', [['different']], [])
        self.assertEqual(self.p.current('a')['intent']['answers'], [{'question_id': 'q1', 'answers': [['2条', '主页30%，匹配60%']]}])
        self.p.record_answer('b', 'q2', [['confirmed=true']], [])
        with self.assertRaises(ValueError):
            self.p.authorize('b', 'p2', 'h2', {})
        with self.assertRaises(ValueError):
            self.p.record_answer('b', 'q1', [['2条']], [])

    def test_explicit_stop_option_is_technical_and_independent_of_prose(self):
        self.p.record('a', 'r1', '恢复这批任务')
        with self.assertRaises(ValueError):
            self.p.authorize('a', 'p1', 'h1', {}, stopped=True, resume_stopped_devices=False)
        self.assertIsNone(self.p.receipt('a', 'p1'))
        self.assertTrue(self.p.authorize('a', 'p1', 'h1', {}, stopped=True, resume_stopped_devices=True)['resume_stopped_devices'])

    def test_same_request_can_resume_original_plan_with_an_audited_receipt(self):
        self.p.record('a', 'r1', '执行并处理必要恢复')
        first = self.p.authorize('a', 'p1', 'h1', {}, tool_call_id='execute')
        resumed = self.p.authorize('a', 'p1', 'h1', {}, stopped=True, resume_stopped_devices=True, tool_call_id='resume')
        self.assertTrue(resumed['resume_stopped_devices'])
        self.assertEqual(resumed['previous_receipt_hash'], first['receipt_hash'])
        self.assertEqual(resumed['tool_call_id'], 'resume')
        self.assertEqual(resumed, self.p.authorize('a', 'p1', 'h1', {}, stopped=True, resume_stopped_devices=True))
        with self.p.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0], 1)
            history = [json.loads(row['receipt']) for row in db.execute('SELECT receipt FROM receipt_history ORDER BY created')]
        self.assertEqual(history, [first, resumed])

    def test_cancel_blocks_new_execution_but_preserves_receipts_and_history(self):
        self.p.record('a', 'r1', '启动任务')
        receipt = self.p.authorize('a', 'p1', 'h1', {})
        self.p.cancel_intent('a')
        self.p.record('a', 'r1', '启动任务')  # A stale retry must not revive the request.
        with self.assertRaises(ValueError):
            self.p.authorize('a', 'p1', 'h1', {})
        self.assertEqual(self.p.receipt('a', 'p1'), receipt)
        with self.p.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 1)

    def test_legacy_request_and_binding_history_is_not_rewritten(self):
        legacy = {'execute': True, 'resume': False, 'writes': {'like_probability': .2}, 'statement': '启动任务'}
        receipt = {'request_id': 'r', 'writes': legacy['writes']}
        with self.p.database() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?)', ('r', 'a', json.dumps(legacy), 1))
            db.execute('INSERT INTO current_requests VALUES(?,?)', ('a', 'r'))
            db.execute('INSERT INTO bindings VALUES(?,?,?,?)', ('r', 'p', 'h', json.dumps(receipt)))
        self.assertEqual(self.p.authorize('a', 'p', 'h', {'like_probability': .2}), receipt)
        self.assertEqual(self.p.current('a')['intent'], legacy)

    def test_operation_requires_admitted_request_and_frozen_target(self):
        command = {'command_id': 'cmd', 'fingerprint': 'h', 'request': {'action': 'restart', 'name': '2号'}}
        with self.assertRaises(ValueError):
            self.p.authorize_operation('a', command)
        self.p.record('a', 'r1', '按刚才说的处理二号')
        receipt = self.p.authorize_operation('a', command, tool_call_id='call1')
        self.assertEqual(receipt['operation'], command['request'])
        self.assertEqual(self.p.authorize_operation('a', command), receipt)
        with self.assertRaises(ValueError):
            self.p.authorize_operation('a', {**command, 'fingerprint': 'changed'})


if __name__ == '__main__':
    unittest.main()
