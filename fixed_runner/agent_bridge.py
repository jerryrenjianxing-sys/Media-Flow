"""Private loopback bridge. Real provider credentials stay in this process."""
from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from agent_tool_context import ToolContexts
from agent_model_policy import AgentModelPolicy, ResponseUsage, classify_response, scope_for, MESSAGES
import secrets
import sqlite3
import threading
import time
import uuid

import requests

QWEN_URL = 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions'


def current_qwen_key():
    import model_providers
    selection = model_providers.selection()
    if selection.get('provider') != model_providers.QWEN:
        raise ValueError('当前未启用千问凭证，请在模型设置中保存并测试')
    return model_providers._key(selection['active_key'])


class AgentBridge:
    def __init__(self, root: Path, tool_callback, *, key_resolver=current_qwen_key, context_guard=None):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(40)
        self.tool_callback = tool_callback
        self.key_resolver = key_resolver
        self.contexts = ToolContexts(context_guard) if context_guard else None
        self._slots = threading.BoundedSemaphore(2)
        self._server = None
        self._thread = None
        with self.database() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, started REAL, finished REAL, status TEXT, usage TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS tools(id TEXT PRIMARY KEY, name TEXT, started REAL, status TEXT)')
            columns = {row[1] for row in conn.execute('PRAGMA table_info(tools)')}
            for column in ('session_id', 'engine_call_id'):
                if column not in columns:
                    conn.execute('ALTER TABLE tools ADD COLUMN ' + column + ' TEXT')
        self.policy = AgentModelPolicy(self.database)

    @contextmanager
    def database(self):
        conn = sqlite3.connect(self.root / 'calls.db', timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def start(self):
        if self._server:
            return self.url
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, body):
                encoded = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_POST(self):
                if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + owner.token):
                    return self.reply(401, {'error': {'message': 'Unauthorized'}})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16_000_000:
                        return self.reply(413, {'error': {'message': 'Request too large'}})
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError('invalid request')
                except (ValueError, TypeError):
                    return self.reply(400, {'error': {'message': 'Invalid request'}})
                if self.path == '/tools/context':
                    if not owner.contexts:
                        return self.reply(409, {'error': {'message': '工具上下文未启用'}})
                    try:
                        token = owner.contexts.issue(body)
                        return self.reply(200, {'token': token})
                    except Exception:
                        return self.reply(409, {'error': {'message': '会话已停止或调用身份无效'}})
                if self.path == '/tools/call':
                    call_id = uuid.uuid4().hex
                    name = str(body.get('name') or '')
                    context = {}
                    try:
                        args = body.get('arguments') or {}
                        if owner.contexts:
                            context, args = owner.contexts.consume(name, args)
                            result = owner.tool_callback(name, args, context=context)
                        else:
                            result = owner.tool_callback(name, args)
                        result = {'status': 'completed', 'reason_code': 'ok', 'user_message': result.get('message') or '工具已返回，请以具体计划、操作或测试状态判断结果',
                                  'operation_id': None, 'retryable': False, 'evidence_refs': [], **result, 'tool_call_id': call_id}
                    except ValueError as exc:
                        from agent_platform import bounded_diagnostic
                        result = {'status': 'failed', 'reason_code': 'invalid_or_blocked', 'user_message': bounded_diagnostic(exc), 'tool_call_id': call_id}
                    except Exception:
                        result = {'status': 'failed', 'reason_code': 'tool_failed', 'user_message': '平台工具失败，请查询当前状态后重试', 'tool_call_id': call_id}
                    with owner.database() as conn:
                        conn.execute('INSERT INTO tools(id,name,started,status,session_id,engine_call_id) VALUES(?,?,?,?,?,?)',
                                     (call_id, name[:80], time.time(), result['status'], context.get('session_id'), context.get('call_id')))
                    return self.reply(200, result)
                if self.path != '/model/chat/completions':
                    return self.reply(404, {'error': {'message': 'Unknown bridge route'}})
                if body.get('model') != 'qwen3.8-flash' or body.get('plugins') or body.get('models'):
                    return self.reply(400, {'error': {'message': '不支持的模型请求', 'code': 'invalid_request'}})
                if not owner._slots.acquire(blocking=False):
                    return self.reply(429, {'error': {'message': '已有模型请求，请等待完成', 'code': 'rate_limited'}})
                call_id = uuid.uuid4().hex
                status = 'unknown'
                usage = None
                scope = None
                usage_reader = ResponseUsage()
                headers_sent = False
                started = time.monotonic()
                try:
                    with owner.database() as conn:
                        conn.execute('INSERT INTO calls VALUES(?,?,NULL,?,NULL)', (call_id, time.time(), 'running'))
                    try:
                        key = owner.key_resolver()
                    except Exception:
                        status = 'credential_unreadable'
                        return self.reply(401, {'error': {'message': '请在当前Windows用户下保存并测试千问Key', 'code': status}})
                    scope = scope_for(key)
                    blocked = owner.policy.admit(scope)
                    if blocked:
                        status = blocked
                        return self.reply(400, {'error': {'message': MESSAGES[blocked], 'code': blocked}})
                    # Unlike the business vision route, preserve native tools/tool_choice.
                    body['enable_thinking'] = False
                    body['max_tokens'] = min(int(body.get('max_tokens') or 4096), 8192)
                    with requests.Session() as http:
                        response = http.post(QWEN_URL, headers={'Authorization': 'Bearer ' + key}, json=body, stream=True, timeout=(10, 30))
                        with response:
                            if not response.ok:
                                error_bytes = next(response.iter_content(chunk_size=16000), b'')
                                status = classify_response(response.status_code, error_bytes)
                                public_status = 402 if status == 'quota_exhausted' else response.status_code
                                return self.reply(public_status, {'error': {'message': MESSAGES[status], 'code': status}})
                            self.send_response(200)
                            self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                            self.send_header('Connection', 'close')
                            self.end_headers()
                            headers_sent = True
                            self.close_connection = True
                            for chunk in response.iter_content(chunk_size=4096):
                                if time.monotonic() - started > 90:
                                    raise requests.Timeout()
                                if chunk:
                                    usage_reader.feed(chunk)
                                    self.wfile.write(chunk)
                                    self.wfile.flush()
                            status = 'completed'
                except requests.RequestException:
                    status = 'network_timeout'
                    if not headers_sent:
                        self.reply(504, {'error': {'message': '千问连接超时，请检查网络后重试；不会切换服务商', 'code': status}})
                    self.close_connection = True
                except (OSError, ValueError):
                    status = 'response_interrupted'
                    if not headers_sent:
                        self.reply(502, {'error': {'message': '模型响应中断，请查看会话状态后重试', 'code': status}})
                    self.close_connection = True
                finally:
                    try:
                        usage = usage_reader.finish()
                        owner.policy.finish(scope, status)
                        with owner.database() as conn:
                            conn.execute('UPDATE calls SET finished=?,status=?,usage=? WHERE id=?', (time.time(), status, json.dumps(usage), call_id))
                    finally:
                        owner._slots.release()

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self._server.server_address[1]}'
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
