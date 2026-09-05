"""Bind platform tools to engine-issued session/call identity, not model text."""
import hashlib
import json
import secrets
import threading
import time


def fingerprint(arguments):
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class ToolContexts:
    def __init__(self, guard):
        self.guard = guard
        self._contexts = {}
        self._lock = threading.Lock()

    def issue(self, body):
        session, call, name = (body.get(k) for k in ('session_id', 'call_id', 'name'))
        if any(not isinstance(x, str) or not 0 < len(x) <= 200 for x in (session, call, name)):
            raise ValueError('工具调用身份无效')
        self.guard(session)
        args = body.get('arguments') or {}
        if not isinstance(args, dict) or '_mediaflow_context' in args:
            raise ValueError('工具参数无效')
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.monotonic()
            self._contexts = {key: value for key, value in self._contexts.items() if value['deadline'] > now}
            if len(self._contexts) >= 100:
                raise ValueError('待处理工具过多')
            self._contexts[token] = {'session_id': session, 'call_id': call, 'name': name,
                                      'fingerprint': fingerprint(args), 'deadline': now + 60}
        return token

    def consume(self, name, arguments):
        args = dict(arguments)
        token = args.pop('_mediaflow_context', None)
        if not isinstance(token, str):
            raise ValueError('缺少引擎调用身份，未执行操作')
        with self._lock:
            context = self._contexts.pop(token, None)
        if not context or context['deadline'] < time.monotonic() or context['name'] != name or context['fingerprint'] != fingerprint(args):
            raise ValueError('工具上下文过期或不匹配，未执行操作')
        self.guard(context['session_id'])
        return context, args
