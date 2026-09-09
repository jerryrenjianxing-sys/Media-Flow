"""No local quota gates; transport usage and deadlines remain observable."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import model_providers as providers
from model_budget import budgeted_post


class ModelTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = patch.object(providers, 'CONFIG_DB', self.root / 'providers.db')
        self.paths.start()
        self.addCleanup(self.paths.stop)

    def response(self, value=None):
        response = MagicMock(status_code=200)
        response.__enter__.return_value = response
        response.json.return_value = value or {}
        return response

    def usages(self):
        with providers.database() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT usage FROM calls ORDER BY started')]

    def test_old_exhausted_budget_and_missing_price_never_block_or_change_payload(self):
        path = self.root / 'old-budget.db'
        with closing(sqlite3.connect(path)) as db:
            db.executescript("CREATE TABLE budget(id INTEGER PRIMARY KEY,ceiling TEXT); INSERT INTO budget VALUES(1,'5'); CREATE TABLE charges(id TEXT,model TEXT,reserved TEXT,actual TEXT,created REAL); INSERT INTO charges VALUES('old','test','7',NULL,0);")
        before = path.read_bytes()
        payload = {'model': 'test', 'messages': []}
        with patch.dict(os.environ, {'MEDIAFLOW_MODEL_BUDGET_PATH': str(path)}), patch('model_budget.requests.get', side_effect=AssertionError('no pricing lookup')), patch('model_budget._post_request', return_value=self.response({'result': 'sent'})) as post:
            with budgeted_post('https://openrouter.ai/api/v1/chat/completions', json=payload) as response:
                self.assertEqual(response.json()['result'], 'sent')
        self.assertEqual(post.call_args.kwargs['json'], payload)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.usages(), [None])

    def test_actual_cost_and_tokens_recorded_without_budget_configuration(self):
        response = self.response({'usage': {'cost': 12.5, 'total_tokens': 900, 'secret': 'omit'}})
        with patch.dict(os.environ, {'MEDIAFLOW_MODEL_BUDGET_PATH': ''}), patch('model_budget._post_request', return_value=response):
            with budgeted_post('https://openrouter.ai/api/v1/chat/completions', json={'model': 'test'}) as result:
                result.json()
        self.assertEqual(self.usages(), [{'cost': 12.5, 'total_tokens': 900}])

    def test_stream_usage_records_final_cost_and_unknown_stays_unknown(self):
        response = self.response()
        response.iter_lines.return_value = [b'data: {"usage":{"cost":0}}', b'data: {"usage":{"cost":0.12,"total_tokens":8}}']
        with patch('model_budget._post_request', return_value=response):
            with budgeted_post('https://openrouter.ai/api/v1/chat/completions', json={}) as stream:
                list(stream.iter_lines())
            with budgeted_post('https://openrouter.ai/api/v1/chat/completions', json={}):
                pass
        self.assertEqual(self.usages(), [{'cost': .12, 'total_tokens': 8}, None])

    def test_expired_request_never_transmits(self):
        with patch('model_budget._post_request') as post:
            with self.assertRaises(providers.ProviderError) as raised:
                with budgeted_post('https://openrouter.ai/api/v1/chat/completions', json={}, request_deadline=time.monotonic()-1):
                    self.fail('expired request')
        self.assertEqual(raised.exception.kind, 'transient_network')
        self.assertEqual(raised.exception.diagnostics['reason_code'], 'network_timeout')
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
