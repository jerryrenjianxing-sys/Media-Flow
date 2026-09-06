"""MediaFlow-facing conversational boundary, isolated from task drafts and keys.

Read-only queries, confirmed platform plans and isolated repairs share receipts.
Unimplemented tools are never reported as successful operations. Mutations use the same
persisted confirmation/operation boundary, not a generic HTTP or shell proxy.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

from agent_bridge import AgentBridge, current_qwen_key
from agent_providers import AgentProviders
from agent_memory import AgentMemory
from agent_platform import AgentPlatform
from agent_operations import AgentOperations
from agent_repairs import AgentRepairs
from agent_handoff import AgentHandoffs
from agent_evidence import read_incident
from agent_runtime import AgentRuntime, AgentRuntimeError, PROVIDER_ID, MODEL_ID, build_config
from runtime_layout import RUNTIME_ROOT, BUNDLED_PYTHON

ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
GUIDE = Path(__file__).parent / 'assets/agent/workflow.md'


def public_messages(messages):
    """Whitelist browser payloads. Never proxy model request/config objects."""
    result = []
    for message in messages[-80:]:
        info = message.get('info') or {}
        parts = []
        for part in message.get('parts', []):
            if part.get('type') == 'text':
                parts.append({'type': 'text', 'text': str(part.get('text') or '')[:24000]})
            elif part.get('type') == 'tool':
                state = part.get('state') or {}
                parts.append({'type': 'tool', 'tool': part.get('tool'), 'call_id': part.get('callID'),
                              'status': state.get('status'), 'title': state.get('title')})
        result.append({'id': info.get('id'), 'role': info.get('role'), 'parts': parts,
                       'error': '模型或工具请求失败，请检查服务商状态后重新提问' if info.get('error') else None})
    return result


class AgentService:
    def __init__(self, status_reader, *, root=None, runtime=None, bridge=None, store=None, model_status_reader=None, worker_launcher=None, virtual_dispatch=None, evidence_root=None):
        self.root = Path(root or RUNTIME_ROOT / 'agent')
        self.status_reader = status_reader
        self.evidence_root = evidence_root
        self.runtime = runtime or AgentRuntime(self.root / 'engine')
        self.bridge = bridge
        self._lock = threading.RLock()
        self.auth = AgentProviders(self.root / 'provider-state', self.runtime, self._lock, reference_resolver=current_qwen_key)
        self.memory = AgentMemory(self.root / 'memory')
        self.platform = AgentPlatform(self.root / 'platform', store, status_reader,
            model_status_reader=model_status_reader or (lambda: {}), worker_launcher=worker_launcher) if store else None
        self.operations = AgentOperations(self.root / 'commands', store, virtual_dispatch) if store else None
        self.repairs = AgentRepairs(self.root / 'repairs')
        self.handoffs = AgentHandoffs(self.root / 'handoffs')
        self._starting = False
        self._error = ''
        self._shutdown = threading.Event()
        self._watcher = None
        atexit.register(self.close)

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.root / 'sessions.db', timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, provider TEXT, model TEXT, title TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY, session TEXT, message_id TEXT, state TEXT, created REAL, UNIQUE(session,id));
            ''')
            if 'config_revision' not in {row['name'] for row in conn.execute('PRAGMA table_info(sessions)')}:
                conn.execute('ALTER TABLE sessions ADD COLUMN config_revision INTEGER NOT NULL DEFAULT 0')
            if 'fingerprint' not in {row['name'] for row in conn.execute('PRAGMA table_info(turns)')}:
                conn.execute("ALTER TABLE turns ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
            with conn:
                yield conn
        finally:
            conn.close()

    def status(self):
        from agent_mcp import TOOLS
        result = self.runtime.status()
        if self._starting:
            result = {**result, 'state': 'starting', 'reason_code':'',
                      'message':result.get('message','') if result['state']=='starting' else '正在准备连接助手'}
        return {**result, 'message': self._error or result.get('message', ''),
                'capability_stage': 'platform_integration',
                'supported_tools': [tool['name'] for tool in TOOLS],
                'pending_capabilities': ['模板创建及恢复的对话入口', '修复包独立安装验收', '服务商实际登录验收'],
                'notice': '助手可检查平台、保存偏好和制定计划；任务需在下方计划卡片确认，再由固定执行程序运行。原任务台保持可用。'}

    def start(self):
        with self._lock:
            if self._starting or self.runtime.status()['state'] == 'ready':
                return self.status()
            self._starting, self._error = True, ''
            threading.Thread(target=self._start, name='mediaflow-agent-start', daemon=True).start()
        return self.status()

    def _start(self):
        try:
            if self.bridge is None:
                self.bridge = AgentBridge(self.root / 'bridge', self.call_tool, key_resolver=self.auth.qwen_key, context_guard=self._tool_session)
            self.bridge.start()
            python = BUNDLED_PYTHON if BUNDLED_PYTHON.is_file() else Path(sys.executable)
            config = build_config(self.bridge.url, self.bridge.token, [str(python), str(Path(__file__).with_name('agent_mcp.py'))], tool_context=True)
            self.runtime.start(config)
            self._shutdown.clear()
            if not self._watcher or not self._watcher.is_alive():
                self._watcher = threading.Thread(target=self._watch_deadlines, name='agent-deadlines', daemon=True)
                self._watcher.start()
        except Exception as exc:
            self._error = str(exc) if isinstance(exc, AgentRuntimeError) else '对话引擎启动失败，请重试或使用原任务台'
            if self.bridge:
                self.bridge.stop()
        finally:
            self._starting = False

    def _tool_session(self, session_id):
        self._session(session_id)
        with self.database() as conn:
            turn = conn.execute('SELECT state,created FROM turns WHERE session=? ORDER BY created DESC LIMIT 1', (session_id,)).fetchone()
        if not turn or turn['state'] not in {'accepted', 'dispatching'} or time.time() - turn['created'] > 600:
            raise ValueError('会话已停止或没有有效请求')

    def _watch_deadlines(self):
        while not self._shutdown.wait(5):
            try:
                self.expire_turns()
            except Exception:
                # Tool admission also checks the deadline, including while the engine is unreachable.
                pass

    def expire_turns(self):
        with self._lock:
            with self.database() as db:
                rows = db.execute("SELECT id,session,state FROM turns t WHERE state IN ('accepted','dispatching','timed_out') AND created<? AND id=(SELECT id FROM turns newest WHERE newest.session=t.session ORDER BY created DESC LIMIT 1)", (time.time()-600,)).fetchall()
            if not rows:
                return
            questions = self.runtime.request('GET', '/question') or []
            waiting = {q.get('sessionID') for q in questions}
            states = self.runtime.request('GET', '/session/status') or {}
            for row in rows:
                if row['session'] in waiting:
                    continue
                if states.get(row['session'], {}).get('type', 'idle') == 'idle':
                    if row['state'] != 'timed_out':
                        with self.database() as db:
                            db.execute("UPDATE turns SET state='completed' WHERE id=?", (row['id'],))
                    continue
                with self.database() as db:
                    db.execute("UPDATE turns SET state='timed_out' WHERE id=?", (row['id'],))
                self.repairs.cancel_session(row['session'])
                self.runtime.request('POST', f'/session/{row["session"]}/abort')

    def call_tool(self, name, arguments, *, context=None):
        with self._lock:
            if context:
                self._tool_session(context['session_id'])
                # Provider call IDs need only be unique within their conversation.
                # Keep the original ID in the bridge audit; use a scoped key for idempotency.
                context = {**context, 'call_id': hashlib.sha256(json.dumps([context['session_id'], context['call_id']], separators=(',', ':')).encode()).hexdigest()}
            return self._call_tool(name, arguments, context=context)

    def _call_tool(self, name, arguments, *, context=None):
        if name == 'incident_evidence':
            if not context or not self.platform or not self.evidence_root:
                raise ValueError('异常证据尚未接入，不能读取任意本机文件')
            return read_incident(self.platform.store, self.evidence_root, arguments)
        if name.startswith('repair_'):
            if not context:
                raise ValueError('修复工具需要当前有效会话')
            return self.repairs.tool(name, arguments, context)
        if name == 'plan_virtual_operation':
            if not context or not self.operations:
                raise ValueError('操作计划需要有效会话与平台服务')
            return self.operations.propose(arguments, context)
        if name in {'list_devices', 'list_tasks', 'task_evidence', 'plan_tasks'}:
            if not context or not self.platform:
                raise ValueError('平台工具尚未连接真实会话与任务存储')
            return self.platform.tools(name, arguments, context)
        if name in {'memory_list', 'memory_save'}:
            if not context:
                raise ValueError('记忆工具需要真实会话关联')
            if name == 'memory_list':
                if arguments:
                    raise ValueError('读取记忆不接受额外参数')
                return self.memory.list()
            return self.memory.save(arguments, source='agent_request', session_id=context['session_id'])
        if arguments:
            raise ValueError('此工具不接受额外参数')
        if name == 'workflow_guide':
            return {'guide_version': '1', 'guide': GUIDE.read_text(encoding='utf-8')}
        if name != 'platform_status':
            raise ValueError('工具未接入，未执行任何操作')
        snapshot = self.status_reader()
        return {'observed_at': time.time(), 'paused': bool(snapshot.get('paused')),
                'product_version': snapshot.get('product_version'), 'task_summary': snapshot.get('task_summary'),
                'devices': [{k: device.get(k) for k in ('device_id', 'friendly_name', 'state', 'device_type', 'environment_status')}
                            for device in snapshot.get('devices', [])]}

    def providers(self):
        return self.auth.catalog()

    def usage(self):
        path = self.root / 'bridge/calls.db'
        if not path.is_file():
            return {'calls': [], 'message': '尚无本机Agent千问请求记录；用量未知时不显示为零费用'}
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM calls ORDER BY started DESC LIMIT 50').fetchall()
            counts = dict(db.execute('SELECT status,count(*) FROM calls GROUP BY status'))
        return {'calls': [{**dict(row), 'usage': json.loads(row['usage']) if row['usage'] else None} for row in rows],
                'counts': counts, 'message': '千问Credits以工作台为准；其他服务商用量见原生会话及账单。原MediaFlow用量与OpenRouter预算未重置',
                'fixed_total_request_cap': None}

    def sessions(self):
        with self.database() as conn:
            return {'sessions': [dict(row) for row in conn.execute('SELECT * FROM sessions ORDER BY created DESC LIMIT 100')]}

    def create_session(self, body):
        with self._lock:
            return self._create_session(body)

    def _create_session(self, body):
        provider = str(body.get('provider') or PROVIDER_ID)
        model = str(body.get('model') or MODEL_ID)
        catalog = self.providers()['providers']
        match = next((p for p in catalog if p['id'] == provider), None)
        if not match or model not in {m['id'] for m in match['models']}:
            raise ValueError('请选择服务商目录中的模型')
        # Preserve the catalog, but do not pretend missing credentials are usable.
        if not match['connected']:
            raise ValueError('此服务商尚未配置，请打开服务商与模型完成登录或保存Key')
        title = str(body.get('title') or 'MediaFlow对话')[:80]
        session = self.runtime.request('POST', '/session', {'title': title, 'agent': 'mediaflow'})
        with self.database() as conn:
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', (session['id'], provider, model, title, time.time(), self.auth.state(provider)['revision']))
        return {'session': {'id': session['id'], 'provider': provider, 'model': model, 'title': title}}

    def _session(self, session_id):
        if not ID.fullmatch(session_id):
            raise ValueError('会话编号无效')
        with self.database() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise ValueError('该会话不属于MediaFlow')
        return dict(row)

    def messages(self, session_id):
        self._session(session_id)
        messages = self.runtime.request('GET', f'/session/{session_id}/message?limit=80')
        observed = {item.get('info', {}).get('id') for item in messages}
        # A timed-out POST can have reached OpenCode. Reconcile its actual message
        # identity, never resubmit it as a fresh turn merely because the UI reloaded.
        with self.database() as db:
            unknown = db.execute("SELECT id,message_id FROM turns WHERE session=? AND state IN ('unknown','dispatching')", (session_id,)).fetchall()
            for turn in unknown:
                if turn['message_id'] in observed:
                    db.execute("UPDATE turns SET state='accepted' WHERE id=?", (turn['id'],))
        states = self.runtime.request('GET', '/session/status')
        state = states.get(session_id, {}).get('type', 'idle')
        questions = self.runtime.request('GET', '/question')
        questions = [q for q in questions if q.get('sessionID') == session_id]
        if questions:
            state = 'waiting_user'
        with self.database() as db:
            latest = db.execute('SELECT state FROM turns WHERE session=? ORDER BY created DESC LIMIT 1', (session_id,)).fetchone()
            turns = [dict(row) for row in db.execute('SELECT id,state,message_id FROM turns WHERE session=? ORDER BY created DESC LIMIT 20', (session_id,))]
        notice = '任务和维护操作请查看下方回执。'
        if latest and latest['state'] == 'timed_out':
            state, notice = 'idle', '这次对话已超过10分钟，已停止新增调用。请查看已有操作回执后重新提问，不会自动重放。'
        return {'messages': public_messages(messages), 'state': state, 'questions': questions, 'turns': turns,
                'plans': self.platform.plans(session_id)['plans'] if self.platform else [],
                'commands': self.operations.list(session_id) if self.operations else [],
                'repairs': self.repairs.list(session_id),
                'notice': notice}

    def send(self, session_id, body):
        session = self._session(session_id)
        text = str(body.get('text') or '').strip()
        request_id = str(body.get('request_id') or '')
        if not text or len(text) > 12000 or not ID.fullmatch(request_id):
            raise ValueError('请输入有效内容和请求编号')
        # Reject common key formats before persisting or uploading chat text.
        if re.search(r'sk-[A-Za-z0-9._-]{16,}', text):
            raise ValueError('请在模型设置中输入Key，不要发送到聊天')
        with self._lock:
            self.auth.assert_no_auth_flow()
            if session['config_revision'] != self.auth.state(session['provider'])['revision']:
                raise ValueError('此对话使用的服务商配置已改变，请新建对话；旧记录仍保留')
            with self.database() as conn:
                conn.execute('BEGIN IMMEDIATE')
                existing = conn.execute('SELECT * FROM turns WHERE id=?', (request_id,)).fetchone()
                if existing:
                    if existing['session'] != session_id:
                        raise ValueError('请求编号已用于其他会话')
                    if existing['fingerprint'] and existing['fingerprint'] != hashlib.sha256(text.encode()).hexdigest():
                        raise ValueError('同一请求编号不能对应不同内容，请使用新请求')
                    return {'request_id': request_id, 'state': existing['state'], 'replayed': False}
                states = self.runtime.request('GET', '/session/status')
                if states.get(session_id, {}).get('type', 'idle') != 'idle':
                    raise ValueError('当前会话仍在处理，请等待或先停止')
                conn.execute("UPDATE turns SET state='completed' WHERE session=? AND state='accepted'", (session_id,))
                message_id = 'msg' + uuid.uuid4().hex
                conn.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)', (request_id, session_id, message_id, 'dispatching', time.time(), hashlib.sha256(text.encode()).hexdigest()))
            try:
                self.runtime.request('POST', f'/session/{session_id}/prompt_async', {
                    'messageID': message_id, 'agent': 'mediaflow',
                    'model': {'providerID': session['provider'], 'modelID': session['model']},
                    'parts': [{'type': 'text', 'text': text}], 'system': self.memory.prompt_context()})
            except Exception:
                with self.database() as conn:
                    conn.execute("UPDATE turns SET state='unknown' WHERE id=?", (request_id,))
                raise AgentRuntimeError('dispatch_unknown', '消息发送结果待确认，请刷新原会话；不会自动重复发送') from None
            with self.database() as conn:
                conn.execute("UPDATE turns SET state='accepted' WHERE id=?", (request_id,))
            return {'request_id': request_id, 'state': 'accepted'}

    def answer(self, session_id, body):
        with self._lock:
            return self._answer(session_id, body)

    def _answer(self, session_id, body):
        self._session(session_id)
        request_id = str(body.get('question_id') or '')
        questions = self.runtime.request('GET', '/question')
        if not any(q.get('id') == request_id and q.get('sessionID') == session_id for q in questions):
            raise ValueError('问题已处理或不属于当前会话')
        answers = body.get('answers')
        if not isinstance(answers, list) or len(answers) > 10 or any(not isinstance(row, list) or any(not isinstance(x, str) or len(x) > 3000 for x in row) for row in answers):
            raise ValueError('问题回答格式无效')
        with self.database() as db:
            db.execute("UPDATE turns SET created=? WHERE id=(SELECT id FROM turns WHERE session=? ORDER BY created DESC LIMIT 1) AND state='accepted'", (time.time(), session_id))
        self.runtime.request('POST', f'/question/{request_id}/reply', {'answers': answers})
        return {'ok': True}

    def stop_session(self, session_id):
        self._session(session_id)
        cancelled = self.platform.stop_session(session_id) if self.platform else 0
        if self.operations:
            self.operations.cancel_unconfirmed(session_id)
        self.repairs.cancel_session(session_id)
        with self.database() as conn:
            conn.execute("UPDATE turns SET state='cancelled' WHERE session=? AND state IN ('accepted','dispatching','unknown')", (session_id,))
        self.runtime.request('POST', f'/session/{session_id}/abort')
        return {'ok': True, 'message': f'已停止新增Agent调用，取消本会话未执行任务{cancelled}条；业务动作在安全检查点停止。已启动的虚拟机维护独立收口，请在操作回执查看，不会强杀或自动重放'}

    def close(self):
        self._shutdown.set()
        self.repairs.cancel_all()
        self.runtime.stop()
        if self.bridge:
            self.bridge.stop()


def handle_agent_http(handler, method, path, body=None):
    """Explicit route adapter. Called only for /api/agent paths."""
    try:
        origin = handler.headers.get('Origin')
        host = urlsplit('http://' + handler.headers.get('Host', '')).hostname
        if host not in {'127.0.0.1', 'localhost', '::1'} or handler.client_address[0] not in {'127.0.0.1', '::1'} or origin not in {None, 'http://127.0.0.1:3000', 'http://localhost:3000'}:
            handler._json({'error': '仅接受本机MediaFlow页面请求'}, 403)
            return
        # JSON-only mutations prevent cross-origin form submissions without Origin.
        if method == 'POST' and not handler.headers.get('Content-Type', '').startswith('application/json'):
            handler._json({'error': '需要JSON请求'}, 415)
            return
        service = handler.agent_service()
        if method == 'GET' and path == '/api/agent/status':
            result = service.status()
        elif method == 'GET' and path == '/api/agent/providers':
            result = service.providers()
        elif method == 'GET' and path == '/api/agent/usage':
            result = service.usage()
        elif path == '/api/agent/handoffs' and method in {'GET', 'POST'}:
            result = service.handoffs.list() if method == 'GET' else service.handoffs.receive(body or {})
        elif method == 'POST' and (match := re.fullmatch(r'/api/agent/handoffs/([A-Za-z0-9_-]+)/accept', path)):
            session_id = str((body or {}).get('session_id') or '')
            service._session(session_id)
            with service._lock:
                result = service.handoffs.accept(match[1], session_id, service.send)
        elif method == 'POST' and path == '/api/agent/model-policy/reset':
            if not service.bridge:
                raise ValueError('请先连接助手')
            result = service.bridge.policy.reset()
        elif method == 'GET' and path == '/api/agent/sessions':
            result = service.sessions()
        elif method == 'GET' and path == '/api/agent/auth-flows':
            result = service.auth.flows()
        elif method == 'GET' and path == '/api/agent/memories':
            result = service.memory.list()
        elif method == 'POST' and path == '/api/agent/memories':
            result = service.memory.save(body or {})
        elif (match := re.fullmatch(r'/api/agent/memories/([a-f0-9]+)/(history|restore)', path)):
            if method == 'GET' and match[2] == 'history':
                result = service.memory.history(match[1])
            elif method == 'POST' and match[2] == 'restore':
                result = service.memory.restore(match[1], body or {})
            else:
                handler._json({'error': '不支持此操作'}, 405)
                return
        elif method == 'POST' and path == '/api/agent/providers/reference':
            result = service.auth.use_reference()
        elif (match := re.fullmatch(r'/api/agent/providers/([A-Za-z0-9_.-]+)/(key|authorize)', path)) and method == 'POST':
            result = service.auth.save(match[1], body or {}) if match[2] == 'key' else service.auth.authorize(match[1], body or {})
        elif (match := re.fullmatch(r'/api/agent/auth-flows/([a-f0-9]+)(?:/(callback|cancel))?', path)):
            if method == 'GET' and not match[2]:
                result = service.auth.flow(match[1])
            elif method == 'POST' and match[2] == 'callback':
                result = service.auth.callback(match[1], body or {})
            elif method == 'POST' and match[2] == 'cancel':
                result = service.auth.cancel(match[1])
            else:
                handler._json({'error': '不支持此操作'}, 405)
                return
        elif method == 'POST' and path == '/api/agent/start':
            result = service.start()
        elif method == 'POST' and path == '/api/agent/sessions':
            result = service.create_session(body or {})
        elif method == 'POST' and (match := re.fullmatch(r'/api/agent/sessions/([A-Za-z0-9_-]+)/plans/([a-f0-9]+)/(confirm|repreview)', path)):
            service._session(match[1])
            if not service.platform:
                raise ValueError('平台任务接口未就绪')
            with service._lock:
                action = service.platform.confirm if match[3] == 'confirm' else service.platform.repreview
                result = action(match[2], match[1], body or {})
        elif method == 'POST' and (match := re.fullmatch(r'/api/agent/sessions/([A-Za-z0-9_-]+)/commands/([a-f0-9]+)/confirm', path)):
            service._session(match[1])
            if not service.operations:
                raise ValueError('虚拟机操作接口未就绪')
            with service._lock:
                result = service.operations.confirm(match[2], match[1], body or {})
        elif (match := re.fullmatch(r'/api/agent/sessions/([A-Za-z0-9_-]+)/repairs/([a-f0-9]+)/(files|diff|tests|close|export|patch|cancel)', path)):
            service._session(match[1])
            session_id, repair_id, action = match.groups()
            if method == 'GET' and action in {'files', 'diff', 'tests'}:
                result = getattr(service.repairs, action)(repair_id, session_id)
                if action == 'diff':
                    result = {**result, 'patch': result['patch'][:30000], 'truncated': len(result['patch']) > 30000}
            elif method == 'POST' and action in {'close', 'export'}:
                result = getattr(service.repairs, action)(repair_id, session_id)
            elif method == 'POST' and action == 'cancel':
                service.repairs.get(repair_id, session_id)
                result = service.repairs.cancel_session(session_id, repair_id)
            elif method == 'GET' and action == 'patch':
                service.repairs.get(repair_id, session_id)
                path = service.repairs.root / repair_id / 'candidate.patch'
                if not path.is_file():
                    raise ValueError('请先导出已测试的候选补丁')
                data = path.read_bytes()
                handler.send_response(200)
                handler.send_header('Content-Type', 'text/plain; charset=utf-8')
                handler.send_header('Content-Disposition', 'attachment; filename="MediaFlow-candidate.patch"')
                handler.send_header('Content-Length', str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)
                return
            else:
                raise ValueError('不支持此修复操作')
        else:
            match = re.fullmatch(r'/api/agent/sessions/([A-Za-z0-9_-]+)/(?P<action>messages|stop|answer)', path)
            if not match:
                handler._json({'error': '请求的助手接口不存在，请检查软件版本；原会话仍保留', 'reason_code': 'agent_route_not_found'}, 404)
                return
            session_id, action = match.group(1), match.group('action')
            if method == 'GET' and action == 'messages':
                result = service.messages(session_id)
            elif method == 'POST' and action == 'messages':
                result = service.send(session_id, body or {})
            elif method == 'POST' and action == 'stop':
                with service._lock:
                    result = service.stop_session(session_id)
            elif method == 'POST' and action == 'answer':
                result = service.answer(session_id, body or {})
            else:
                handler._json({'error': '不支持此操作'}, 405)
                return
        handler._json(result)
    except AgentRuntimeError as exc:
        handler._json({'error': str(exc), 'reason_code': exc.code}, 409)
    except ValueError as exc:
        handler._json({'error': str(exc)}, 400)
    except Exception:
        handler._json({'error': '对话服务暂时不可用，请刷新状态；旧任务台不受影响'}, 503)
