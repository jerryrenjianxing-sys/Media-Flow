import json
import time
import unittest
import sqlite3
import tempfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import closing
from unittest.mock import patch

import comment_ai as ai
import model_providers as providers
from model_budget import _TokenPlanResponse
from test_comment_ai import FakeCloudResponse
import test_model_providers as provider_fixtures


class ModelResilienceTests(unittest.TestCase):
    def topic(self):
        return ai.analyze_topic(None, 'technology', api_key='test',
                                base_url=providers.QWEN_BASE_URL, model=providers.QWEN_MODEL)

    def stream(self, content, finish='stop'):
        return FakeCloudResponse(200, lines=[('data: '+json.dumps({'choices':[
            {'delta': {'content': content}, 'finish_reason': finish}]})).encode(), b'data: [DONE]'])

    def test_qwen_transient_then_schema_failure_has_only_two_requests(self):
        with patch.object(ai, 'resolve_runtime_model', side_effect=lambda *a:a), patch.object(ai, 'encode_image', return_value=''), patch.object(ai, 'budgeted_post', side_effect=[providers.ProviderError('network_timeout'), self.stream('bad json')]) as post, patch.object(ai.time, 'sleep'):
            with self.assertRaises(ai.CloudModelError) as raised:
                self.topic()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(raised.exception.diagnostics['category'], 'json')
        self.assertEqual(raised.exception.attempts, 2)

    def test_qwen_schema_repair_and_topic_cap(self):
        valid = json.dumps(dict(relevance='unrelated', topic='other', evidence=[], reason='other', safe=True))
        with patch.object(ai, 'resolve_runtime_model', side_effect=lambda *a:a), patch.object(ai, 'encode_image', return_value=''), patch.object(ai, 'budgeted_post', side_effect=[self.stream('bad json'), self.stream(valid)]) as post:
            result = self.topic()
        self.assertEqual(result.relevance, 'unrelated')
        self.assertEqual(post.call_count, 2)
        body = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(body['max_tokens'], 1024)
        self.assertLessEqual(post.call_args.kwargs['request_deadline']-time.monotonic(), 60)

    def test_provider_categories(self):
        for code in ('network_timeout', 'rate_limited', 'provider_failure'):
            self.assertTrue(providers.ProviderError(code).retryable, code)
        for code in ('authentication', 'permission', 'quota_exhausted'):
            self.assertFalse(providers.ProviderError(code).retryable, code)
        for code in ('configuration_changed', 'credential_unreadable'):
            self.assertIn(providers.ProviderError(code).kind, ('authentication', 'invalid_request'))
            self.assertFalse(providers.ProviderError(code).retryable)

    def test_topic_invalid_fields_do_not_become_valid_uncertain_result(self):
        invalid = json.dumps(dict(relevance='invented', topic='x', evidence=[], reason='x', safe='false'))
        with patch.object(ai, 'resolve_runtime_model', side_effect=lambda *a:a), patch.object(ai, 'encode_image', return_value=''), patch.object(ai, 'budgeted_post', side_effect=[self.stream(invalid), self.stream(invalid)]):
            with self.assertRaises(ai.CloudModelError) as raised:
                self.topic()
        self.assertEqual(raised.exception.diagnostics['category'], 'field')

    def test_finish_reason_is_retained_for_success(self):
        content = ai.parse_streaming_response(self.stream('{}').iter_lines())
        self.assertEqual(content.finish_reason, 'stop')

    def test_stream_auth_error_is_permanent(self):
        raw = FakeCloudResponse(200, lines=[b'data: {"error":{"code":401,"message":"private-key"}}'])
        response = _TokenPlanResponse(raw, {'usage': None}, time.monotonic()+1)
        with self.assertRaises(providers.ProviderError) as raised:
            list(response.iter_lines())
        self.assertEqual(raised.exception.kind, 'authentication')
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn('private-key', str(raised.exception))

    def test_openrouter_usage_cost_survives_later_token_update(self):
        from model_budget import _openrouter_post
        receipt = {'usage': None}
        raw = FakeCloudResponse(200, lines=[b'data: {"usage":{"cost":0.1,"total_tokens":2}}', b'data: {"usage":{"total_tokens":3}}'])
        with patch('model_budget.requests.post', return_value=raw):
            with _openrouter_post('unused', receipt) as response:
                list(response.iter_lines())
        self.assertEqual(receipt['usage'], {'cost': .1, 'total_tokens': 3})

    def test_cloud_error_survives_stream_parser(self):
        error = providers.ProviderError('authentication')
        response = self.stream('{}')
        response.iter_lines = lambda: (_ for _ in ()).throw(error)
        with patch.object(ai, 'budgeted_post', return_value=response):
            with self.assertRaises(providers.ProviderError) as raised:
                ai._request_streaming_json(base_url=providers.QWEN_BASE_URL, api_key='test', payload={}, timeout_seconds=60)
        self.assertIs(raised.exception, error)

    def test_truncation_preserves_finish_reason(self):
        with self.assertRaises(ai.CloudModelError) as raised:
            ai.parse_streaming_response(self.stream('{}', 'length').iter_lines())
        self.assertEqual(raised.exception.diagnostics['category'], 'truncation')
        self.assertEqual(raised.exception.diagnostics['finish_reason'], 'length')

    def test_silent_stream_obeys_wall_clock_deadline(self):
        class Slow:
            def iter_lines(self):
                time.sleep(.5)
                yield b'data: {}'
            def close(self):
                pass
        response = _TokenPlanResponse(Slow(), {'usage': None}, time.monotonic()+.05)
        started = time.monotonic()
        with self.assertRaises(providers.ProviderError):
            list(response.iter_lines())
        self.assertLess(time.monotonic()-started, .25)

    def test_real_trickling_http_stream_discards_late_content(self):
        import requests
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                try:
                    for _ in range(30):
                        self.wfile.write(b':')
                        self.wfile.flush()
                        time.sleep(.02)
                except OSError:
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            raw = requests.get('http://127.0.0.1:%d' % server.server_port, stream=True, timeout=(.2, .2))
            response = _TokenPlanResponse(raw, {'usage': None}, time.monotonic()+.08)
            started = time.monotonic()
            with self.assertRaises(providers.ProviderError):
                list(response.iter_lines())
            self.assertLess(time.monotonic()-started, .3)
        finally:
            server.shutdown()
            server.server_close()


class ConfigurationRecoveryTests(unittest.TestCase):
    setUp = provider_fixtures.TokenPlanTests.setUp
    encrypt_fake = staticmethod(provider_fixtures.TokenPlanTests.encrypt_fake)
    def test_waiting_model_with_pending_successor_permits_configuration_repair(self):
        with closing(sqlite3.connect(providers.TASK_DB)) as db:
            # Only admission columns matter; the real store schema stays isolated.
            db.execute('DROP TABLE tasks')
            db.execute('CREATE TABLE tasks(status TEXT)')
            db.executemany('INSERT INTO tasks VALUES (?)', [('waiting_model',), ('pending',)])
            db.commit()
        with providers.configuration_edit():
            pass
        with closing(sqlite3.connect(providers.TASK_DB)) as db:
            db.execute("INSERT INTO tasks VALUES ('running')")
            db.commit()
        with self.assertRaises(ValueError):
            with providers.configuration_edit():
                pass

    def test_each_physical_attempt_records_usage_even_schema_failure(self):
        with providers.database() as db:
            db.execute("UPDATE state SET provider=?, active_key='fake', active_contract=?", (providers.QWEN, providers.QWEN_CONTRACT))
        first = FakeCloudResponse(200, lines=[b'data: {"choices":[{"delta":{"content":"bad"},"finish_reason":"stop"}],"usage":{"total_tokens":11}}', b'data: [DONE]'])
        second = FakeCloudResponse(200, lines=[b'data: {"choices":[{"delta":{"content":"bad"},"finish_reason":"stop"}],"usage":{"total_tokens":12}}', b'data: [DONE]'])
        with patch.object(providers, '_key', return_value='test'), patch.object(ai, 'encode_image', return_value=''), patch('model_budget.requests.post', side_effect=[first, second]) as post:
            with self.assertRaises(ai.CloudModelError) as raised:
                ai.analyze_topic(None, 'technology')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(raised.exception.diagnostics['finish_reason'], 'stop')
        self.assertEqual(post.call_args.kwargs['timeout'], (5, 25))
        with providers.database() as db:
            calls = db.execute('SELECT usage,finished FROM calls ORDER BY started').fetchall()
        self.assertEqual([json.loads(row[0])['total_tokens'] for row in calls], [11,12])
        self.assertTrue(all(row[1] for row in calls))

    def test_coordinate_and_configuration_default_keep_twenty_second_deadline(self):
        from model_budget import budgeted_post
        with providers.database() as db:
            db.execute("UPDATE state SET provider=?, active_key='fake', active_contract=?", (providers.QWEN, providers.QWEN_CONTRACT))
        with patch.object(providers, '_key', return_value='test'), patch('model_budget.requests.post', return_value=FakeCloudResponse(200)):
            for explicit in (False, True):
                started = time.monotonic()
                kwargs = {'request_deadline': started+20} if explicit else {}
                with budgeted_post(providers.QWEN_BASE_URL+'/chat/completions', json={'model': providers.QWEN_MODEL}, headers={'Authorization': 'Bearer test'}, **kwargs) as response:
                    self.assertGreater(response.deadline-started, 19.9)
                    self.assertLess(response.deadline-started, 20.1)
