"""Native OpenCode authentication, with metadata only in MediaFlow's ledger.

The engine owns auth.json (including OAuth refresh). This adapter never stores
credentials in its database or returns them through its public methods.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from urllib.parse import quote, urlsplit

from agent_runtime import AgentRuntimeError, PROVIDER_ID


def safe_authorization_url(value):
    parsed = urlsplit(str(value or ''))
    return (parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username
            or parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'})


class AgentProviders:
    def __init__(self, root, runtime, lock, *, reference_resolver):
        self.root, self.runtime, self.lock = Path(root), runtime, lock
        self.reference_resolver = reference_resolver
        # OAuth in-flight state lives in the engine and cannot be replayed after restart.
        with self.database() as db:
            db.execute("UPDATE auth_flows SET state='interrupted',message='授权过程已中断，请重新发起登录' WHERE state IN ('authorizing','waiting_user','running')")

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'providers.db', timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS provider_state(provider TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    source TEXT NOT NULL, updated REAL NOT NULL, test_status TEXT NOT NULL DEFAULT 'not_tested');
                CREATE TABLE IF NOT EXISTS auth_flows(id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    method INTEGER NOT NULL, state TEXT NOT NULL, deadline REAL NOT NULL, message TEXT NOT NULL,
                    url TEXT, callback_method TEXT);
                CREATE INDEX IF NOT EXISTS idx_agent_auth_state ON auth_flows(state);
            ''')
            with db:
                yield db
        finally:
            db.close()

    def native_auth(self, provider):
        # Private resolver, never used as an HTTP response.
        path = self.runtime.root / 'data/opencode/auth.json'
        if not path.is_file():
            return None
        try:
            result = json.loads(path.read_text(encoding='utf-8')).get(provider)
            return result if isinstance(result, dict) else None
        except (ValueError, OSError):
            raise AgentRuntimeError('auth_store_unreadable', '服务商登录文件无法读取，请在设置中重新保存') from None

    def state(self, provider):
        with self.database() as db:
            row = db.execute('SELECT * FROM provider_state WHERE provider=?', (provider,)).fetchone()
        return dict(row) if row else {'provider': provider, 'revision': 0, 'source': 'native', 'updated': None, 'test_status': 'not_tested'}

    def qwen_key(self):
        state = self.state(PROVIDER_ID)
        native = self.native_auth(PROVIDER_ID)
        if state['source'] != 'mediaflow_reference' and native and native.get('type') == 'api':
            return native['key']
        return self.reference_resolver()

    def catalog(self):
        payload = self.runtime.request('GET', '/provider')
        methods = self.runtime.request('GET', '/provider/auth') or {}
        connected = set(payload.get('connected', []))
        with self.database() as db:
            states = {row['provider']: dict(row) for row in db.execute('SELECT * FROM provider_state')}
        result = []
        for item in payload.get('all', []):
            provider = item['id']
            state = states.get(provider, {'provider': provider, 'revision': 0, 'source': 'native', 'updated': None, 'test_status': 'not_tested'})
            available = methods.get(provider) or [{'type': 'api', 'label': 'API Key'}]
            is_connected = provider in connected
            if provider == PROVIDER_ID:
                # The bridge's ephemeral password is not evidence of a real Key.
                try:
                    is_connected = bool(self.qwen_key())
                except Exception:
                    is_connected = False
            result.append({'id': provider, 'name': item.get('name', provider), 'connected': is_connected,
                'auth_methods': available, 'auth_state': state,
                'models': [{'id': key, 'name': model.get('name', key)} for key, model in item.get('models', {}).items()]})
        return {'providers': result}

    def _method(self, provider, index):
        item = next((p for p in self.catalog()['providers'] if p['id'] == provider), None)
        if not item or type(index) is not int or not 0 <= index < len(item['auth_methods']):
            raise ValueError('请选择目录中的服务商和登录方式')
        return item['auth_methods'][index]

    @staticmethod
    def _inputs(method, values):
        if not isinstance(values, dict) or len(values) > 20:
            raise ValueError('登录参数格式无效')
        allowed = {p['key'] for p in method.get('prompts', [])}
        if any(key not in allowed or not isinstance(value, str) or len(value) > 2000 for key, value in values.items()):
            raise ValueError('登录参数不属于所选方式')
        return values

    def _idle(self, *, flow_id=None):
        states = self.runtime.request('GET', '/session/status')
        if any(v.get('type') != 'idle' for v in states.values()):
            raise ValueError('助手仍在处理，请等待结束或停止对话后再修改服务商')
        self.assert_no_auth_flow(except_id=flow_id)

    def assert_no_auth_flow(self, *, except_id=None):
        with self.database() as db:
            db.execute("UPDATE auth_flows SET state='expired',message='登录等待超时，请重新发起登录' WHERE state IN ('authorizing','waiting_user') AND deadline<?", (time.time(),))
            active = db.execute("SELECT id FROM auth_flows WHERE state IN ('authorizing','waiting_user','running')").fetchall()
        if any(row['id'] != except_id for row in active):
            raise ValueError('正在配置服务商，请先完成或取消登录')

    def _saved(self, provider, source='native'):
        with self.database() as db:
            db.execute('''INSERT INTO provider_state(provider,revision,source,updated) VALUES(?,1,?,?)
                ON CONFLICT(provider) DO UPDATE SET revision=revision+1,source=excluded.source,updated=excluded.updated,test_status='not_tested' ''',
                (provider, source, time.time()))
        # Native provider instances cache auth. Dispose only at a verified idle boundary.
        try:
            self.runtime.request('POST', '/instance/dispose')
        except Exception:
            return {'state': 'saved', 'message': '凭证已保存，尚未验证；请重新连接助手以加载配置', 'auth_state': self.state(provider)}
        return {'state': 'saved', 'message': '已保存，尚未验证；可新建对话测试此模型', 'auth_state': self.state(provider)}

    def save(self, provider, body):
        with self.lock:
            self._idle()
            method = self._method(provider, body.get('method', 0))
            if method['type'] != 'api':
                raise ValueError('此方式需要网页登录，请使用登录按钮')
            key = body.get('key')
            if not isinstance(key, str) or not key.strip() or len(key) > 16000 or any(c in key for c in '\r\n\x00'):
                raise ValueError('请输入有效的API Key')
            inputs = self._inputs(method, body.get('inputs') or {})
            auth = {'type': 'api', 'key': key.strip()}
            if inputs:
                auth['metadata'] = inputs
            if self.runtime.request('PUT', '/auth/' + quote(provider, safe=''), auth) is not True:
                raise ValueError('原生认证保存未成功，请重试')
            stored = self.native_auth(provider)
            if not stored or stored.get('key') != auth['key']:
                raise ValueError('凭证保存后回读失败，请检查本机用户目录后重试')
            return self._saved(provider)

    def use_reference(self):
        with self.lock:
            self._idle()
            try:
                if not self.reference_resolver():
                    raise ValueError()
            except Exception:
                raise ValueError('当前用户无法读取既有千问配置；可直接在助手设置中填写专属Key，原配置不会被覆盖') from None
            return self._saved(PROVIDER_ID, 'mediaflow_reference')

    def authorize(self, provider, body):
        with self.lock:
            self._idle()
            index = body.get('method', 0)
            method = self._method(provider, index)
            if method['type'] != 'oauth':
                raise ValueError('此方式请直接保存API Key')
            inputs = self._inputs(method, body.get('inputs') or {})
            flow_id = uuid.uuid4().hex
            with self.database() as db:
                db.execute('INSERT INTO auth_flows VALUES(?,?,?,?,?,?,NULL,NULL)',
                    (flow_id, provider, index, 'authorizing', time.time() + 600, '正在准备登录'))
            try:
                auth = self.runtime.request('POST', '/provider/' + quote(provider, safe='') + '/oauth/authorize', {'method': index, 'inputs': inputs})
                if not safe_authorization_url(auth.get('url')) or auth.get('method') not in {'auto', 'code'}:
                    raise ValueError('登录服务未返回有效入口')
                with self.database() as db:
                    db.execute("UPDATE auth_flows SET state='waiting_user',url=?,callback_method=?,message=? WHERE id=?",
                        (auth['url'], auth['method'], str(auth.get('instructions') or '请完成网页登录后继续')[:4000], flow_id))
            except Exception:
                with self.database() as db:
                    db.execute("UPDATE auth_flows SET state='failed',message='登录入口获取失败，请重新发起' WHERE id=?", (flow_id,))
            return self.flow(flow_id)

    def flow(self, flow_id):
        with self.database() as db:
            db.execute("UPDATE auth_flows SET state='expired',message='登录等待超时，请重新发起登录' WHERE id=? AND state IN ('authorizing','waiting_user') AND deadline<?", (flow_id, time.time()))
            row = db.execute('SELECT * FROM auth_flows WHERE id=?', (flow_id,)).fetchone()
        if not row:
            raise ValueError('登录操作不存在')
        return dict(row)

    def flows(self):
        with self.database() as db:
            rows = db.execute('SELECT id FROM auth_flows ORDER BY deadline DESC LIMIT 20').fetchall()
        return {'flows': [self.flow(row['id']) for row in rows]}

    def callback(self, flow_id, body):
        with self.lock:
            flow = self.flow(flow_id)
            if flow['state'] != 'waiting_user':
                return flow  # Poll, never replay an uncertain callback.
            self._idle(flow_id=flow_id)
            code = body.get('code') or ''
            if not isinstance(code, str) or len(code) > 12000:
                raise ValueError('授权码格式无效')
            if flow['callback_method'] == 'code' and not code.strip():
                raise ValueError('请输入网页登录返回的授权码')
            with self.database() as db:
                db.execute("UPDATE auth_flows SET state='running',deadline=?,message='正在确认网页登录结果' WHERE id=?", (time.time() + 120, flow_id))
            threading.Thread(target=self._callback, args=(flow, code), daemon=True).start()
            return self.flow(flow_id)

    def _callback(self, flow, code):
        try:
            body = {'method': flow['method']}
            if code:
                body['code'] = code
            if self.runtime.request('POST', '/provider/' + quote(flow['provider'], safe='') + '/oauth/callback', body, timeout=110) is not True:
                raise ValueError('原生授权尚未完成')
            with self.lock:
                self._saved(flow['provider'])
                with self.database() as db:
                    db.execute("UPDATE auth_flows SET state='completed',message='登录已保存，尚未测试模型' WHERE id=?", (flow['id'],))
        except Exception:
            with self.database() as db:
                db.execute("UPDATE auth_flows SET state='failed',message='登录确认失败或超时，请检查服务商状态后重新登录；不会自动重发' WHERE id=?", (flow['id'],))

    def cancel(self, flow_id):
        with self.lock:
            flow = self.flow(flow_id)
            if flow['state'] == 'running':
                raise ValueError('正在完成原生授权回调，请等待本次结果；不会重复提交')
            if flow['state'] in {'waiting_user', 'authorizing'}:
                with self.database() as db:
                    db.execute("UPDATE auth_flows SET state='cancelled',message='已取消本次登录' WHERE id=?", (flow_id,))
            return self.flow(flow_id)
