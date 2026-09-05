from pathlib import Path
import tempfile
import unittest
from agent_bridge import AgentBridge
from agent_model_policy import AgentModelPolicy, ResponseUsage, classify_response


class AgentModelPolicyTests(unittest.TestCase):
    def test_refusal_persists_reset_does_not_erase_usage_and_new_key_is_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            bridge = AgentBridge(Path(temporary), lambda *_: {})
            for _ in range(3):
                bridge.policy.finish('key-fingerprint', 'permission')
            restart = AgentModelPolicy(bridge.database)
            self.assertEqual(restart.admit('key-fingerprint'), 'circuit_open')
            self.assertIsNone(restart.admit('other-key'))
            with bridge.database() as db:
                db.execute("INSERT INTO calls VALUES('old',1,2,'completed','{}')")
            restart.reset()
            self.assertIsNone(restart.admit('key-fingerprint'))
            with bridge.database() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM calls').fetchone()[0], 1)
            restart.finish('key-fingerprint', 'rate_limited')
            self.assertEqual(restart.admit('key-fingerprint'), 'cooldown')

    def test_numeric_usage_across_stream_chunks_omits_text_and_unknown_is_not_zero(self):
        reader = ResponseUsage()
        reader.feed(b'data: {"usage":{"total_tokens":42,')
        reader.feed(b'"private":"secret","credits":2.5}}\n\ndata: [DONE]\n\n')
        self.assertEqual(reader.finish(), {'total_tokens': 42, 'credits': 2.5})
        self.assertIsNone(ResponseUsage().finish())
        self.assertEqual(classify_response(429, b'{"code":"insufficient_quota"}'), 'quota_exhausted')
        self.assertEqual(classify_response(429, b'rate limit'), 'rate_limited')
        self.assertEqual(classify_response(403, b'Forbidden'), 'permission')
        self.assertEqual(classify_response(401, b'Invalid'), 'authentication')


if __name__ == '__main__':
    unittest.main()
