from pathlib import Path
import tempfile
import unittest

from agent_memory import AgentMemory
from agent_tool_context import ToolContexts


class AgentMemoryTests(unittest.TestCase):
    def test_versions_restart_disable_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = AgentMemory(Path(folder))
            first = memory.save({'title': '视频数量', 'body': '默认每轮20条'})
            memory.save({'id': first['id'], 'expected_version': 1, 'title': '视频数量', 'body': '改为6条', 'enabled': False}, source='agent_request', session_id='ses_fixture')
            with self.assertRaises(ValueError):
                memory.save({'id': first['id'], 'expected_version': 1, 'title': '失效编辑', 'body': '不能覆盖'})
            restarted = AgentMemory(Path(folder))
            self.assertEqual(restarted.prompt_context(), '')
            restarted.restore(first['id'], {'version': 1, 'expected_version': 2})
            self.assertEqual(len(restarted.history(first['id'])['versions']), 3)
            self.assertIn('默认每轮20条', restarted.prompt_context())
            self.assertIn('不授权操作', restarted.prompt_context())
            self.assertEqual(restarted.list()['memories'][0]['version'], 3)

    def test_secret_not_persisted_and_context_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = AgentMemory(Path(folder))
            with self.assertRaises(ValueError):
                memory.save({'title': 'key', 'body': 'sk-sp-' + 'x' * 40})
            self.assertEqual(memory.list()['memories'], [])
            for n in range(4):
                memory.save({'title': str(n), 'body': 'x' * 7900})
            self.assertLess(len(memory.prompt_context()), 12500)


class ToolContextTests(unittest.TestCase):
    def test_wrong_session_arguments_or_replay_are_rejected(self):
        active = {'ses_real'}
        def guard(session):
            if session not in active:
                raise ValueError('stopped')
        contexts = ToolContexts(guard)
        args = {'title': 'test', 'body': 'hello'}
        token = contexts.issue({'session_id': 'ses_real', 'call_id': 'call1', 'name': 'memory_save', 'arguments': args})
        context, clean = contexts.consume('memory_save', {**args, '_mediaflow_context': token})
        self.assertEqual(context['session_id'], 'ses_real')
        self.assertEqual(clean, args)
        with self.assertRaises(ValueError):
            contexts.consume('memory_save', {**args, '_mediaflow_context': token})
        token = contexts.issue({'session_id': 'ses_real', 'call_id': 'call2', 'name': 'memory_save', 'arguments': args})
        with self.assertRaises(ValueError):
            contexts.consume('memory_save', {**args, 'body': 'tampered', '_mediaflow_context': token})
        token = contexts.issue({'session_id': 'ses_real', 'call_id': 'call3', 'name': 'memory_save', 'arguments': args})
        active.clear()
        with self.assertRaises(ValueError):
            contexts.consume('memory_save', {**args, '_mediaflow_context': token})


if __name__ == '__main__':
    unittest.main()
