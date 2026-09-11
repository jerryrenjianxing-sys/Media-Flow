"""Real loopback HTTP only; no provider request, credential or device."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch
import requests
from model_budget import budgeted_post
from model_providers import QWEN_BASE_URL, QWEN_MODEL, ProviderError
from model_errors import public_model_error


@contextmanager
def admitted(*args, **kwargs):
    yield {'usage': None}


class ModelDiagnosticsTests(unittest.TestCase):
    def test_loopback_failures_keep_stage_and_do_not_retry(self):
        class Handler(BaseHTTPRequestHandler):
            mode = ''
            calls = 0
            def log_message(self, *args):
                pass
            def do_POST(self):
                Handler.calls += 1
                self.rfile.read(int(self.headers['Content-Length']))
                if Handler.mode == 'disconnect':
                    self.connection.close()
                    return
                if Handler.mode == 'headers':
                    time.sleep(.15)
                try:
                    self.send_response(429 if Handler.mode == 'limit' else 200)
                    self.end_headers()
                    self.wfile.flush()
                    if Handler.mode == 'body':
                        time.sleep(.15)
                    self.wfile.write(b'{"error":{"code":"rate_limit_exceeded"}}' if Handler.mode == 'limit' else b'data: INVALID\n\n')
                except OSError:
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for mode, kind, stage in [('headers', 'transient_network', 'connect_or_headers'),
                                     ('body', 'transient_network', 'response_read'),
                                     ('disconnect', 'transient_network', 'connect_or_headers'),
                                     ('limit', 'rate_limited', 'response_read'),
                                     ('format', 'invalid_response', 'response_read')]:
                Handler.mode, Handler.calls = mode, 0
                def local_post(url, **kwargs):
                    return requests.post('http://127.0.0.1:%d' % server.server_port, **kwargs)
                with self.subTest(mode=mode), patch('model_providers.admitted_request', admitted), patch('model_budget._post_request', local_post):
                    with self.assertRaises(ProviderError) as caught:
                        with budgeted_post(QWEN_BASE_URL+'/chat/completions', json={'model': QWEN_MODEL}, stream=True,
                                           request_deadline=time.monotonic()+(.08 if mode in ('body','headers') else 1)) as response:
                            list(response.iter_lines())
                    self.assertEqual(caught.exception.kind, kind)
                    self.assertEqual(caught.exception.diagnostics['stage'], stage)
                    self.assertGreaterEqual(caught.exception.diagnostics['elapsed_ms'], 0)
                    self.assertEqual(Handler.calls, 1)
                time.sleep(.17)  # finish the isolated server's delayed response
        finally:
            server.shutdown()
            server.server_close()

    def test_public_diagnostics_are_whitelisted(self):
        result = public_model_error({'kind':'transient_network', 'attempts':2,
            'raw':'secret', 'diagnostics':{'stage':'request_write','transport_error':'write_timeout',
                'elapsed_ms':3500,'attempt':2,'body':'secret','finish_reason':'secret'}})
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(result['diagnostics']['stage'], 'request_write')
