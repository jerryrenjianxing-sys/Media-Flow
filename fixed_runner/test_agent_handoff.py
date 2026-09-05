from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from agent_handoff import AgentHandoffs


class AgentHandoffTests(unittest.TestCase):
    def test_explicit_request_scope_idempotency_and_unknown_not_replayed(self):
        with tempfile.TemporaryDirectory() as root:
            inbox = AgentHandoffs(Path(root))
            body = {'request_id': 'req1', 'source': '用户选定的开发会话', 'prompt': '只分析指定问题，不操作设备'}
            self.assertEqual(inbox.receive(body)['state'], 'waiting_user')
            self.assertEqual(len(inbox.list()['handoffs']), 1)
            with self.assertRaises(ValueError):
                inbox.receive({**body, 'prompt': 'changed'})
            send = Mock(side_effect=TimeoutError('uncertain'))
            self.assertEqual(inbox.accept('req1', 'session1', send)['state'], 'unknown')
            restarted = AgentHandoffs(Path(root))
            self.assertEqual(restarted.accept('req1', 'session1', send)['state'], 'unknown')
            send.assert_called_once()
            with self.assertRaises(ValueError):
                restarted.accept('req1', 'session2', send)
            with self.assertRaises(ValueError):
                inbox.receive({**body, 'request_id': 'secret', 'prompt': 'sk-sp-'+'x'*40})


if __name__ == '__main__':
    unittest.main()
