"""Reply observation and bounded, read-only clarification recovery.

Engine admission is not completion. Recovery claims live alongside the original
turn and are committed before dispatch, including when dispatch becomes unknown.
"""
import re
import time
import uuid

READ_TOOLS = frozenset({'platform_status', 'workflow_guide', 'list_devices',
    'list_tasks', 'task_evidence', 'incident_evidence', 'memory_list',
    'session_permissions', 'plan_status', 'repair_update_status'})
ACTIVE_RECOVERY = ('dispatching', 'unknown', 'running')


def replies_after(messages, parent):
    positions = [i for i, message in enumerate(messages) if message.get('info', {}).get('id') == parent]
    return messages[positions[-1]+1:] if positions else []


def response_state(messages, engine_state, questions):
    result = {'phase': 'completed', 'reason_code': '', 'finish_reason': '',
              'auto_recoverable': False, 'message': '回答完成'}
    if questions:
        return {**result, 'phase': 'waiting_user', 'message': '等待你补充参数'}
    assistants = [m for m in messages if m.get('info', {}).get('role') == 'assistant']
    last = assistants[-1] if assistants else {}
    info = last.get('info') or {}
    parts = last.get('parts') or []
    if engine_state in {'busy', 'retry'}:
        tool = any(p.get('type') == 'tool' and p.get('state', {}).get('status') in {'pending', 'running'} for p in parts)
        return {**result, 'phase': 'tool_running' if tool else 'replying', 'message': '正在调用工具' if tool else '正在回答'}
    if info.get('error'):
        return {**result, 'phase': 'failed', 'reason_code': 'response_failed', 'message': '回答未完成，请检查模型连接后重试'}
    finish = info.get('finish')
    result['finish_reason'] = finish if finish in {'stop', 'length', 'tool-calls', 'content-filter', 'error', 'unknown'} else ''
    if finish == 'length':
        return {**result, 'phase': 'incomplete', 'reason_code': 'response_truncated', 'message': '回答达到长度上限，原文已保留'}
    text = '\n'.join(str(p.get('text') or '') for p in parts if p.get('type') == 'text').strip()
    # Deliberately narrow: a real question, list or normal colon is not a failure.
    missing = (finish == 'stop' and bool(re.search(r'(?:参数|几个问题|以下问题|以下信息|补充信息)', text))
               and bool(re.search(r'(?:请确认|请回答|请补充|请提供)[：:]\s*$', text)))
    if missing:
        return {**result, 'phase': 'incomplete', 'reason_code': 'missing_questions',
                'auto_recoverable': True, 'message': '回答缺少承诺的问题，原文已保留'}
    return result


class ReplyRecovery:
    def __init__(self, database, runtime):
        self.database, self.runtime = database, runtime

    @staticmethod
    def schema(db):
        db.execute('''CREATE TABLE IF NOT EXISTS reply_recovery(
            request_id TEXT PRIMARY KEY, session TEXT NOT NULL, original_message TEXT NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, message_id TEXT,
            deadline REAL, guarded INTEGER NOT NULL DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS question_answers(
            question_id TEXT PRIMARY KEY, session TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL)''')

    def enroll(self, db, request_id, session, message):
        db.execute("UPDATE reply_recovery SET guarded=0,state='superseded' WHERE session=?", (session,))
        db.execute('INSERT INTO reply_recovery(request_id,session,original_message,state) VALUES(?,?,?,?)',
                   (request_id, session, message, 'watching'))

    def current(self, session):
        with self.database() as db:
            row = db.execute('''SELECT r.* FROM reply_recovery r JOIN turns t ON t.id=r.request_id
                WHERE r.session=? AND t.id=(SELECT id FROM turns WHERE session=? ORDER BY created DESC LIMIT 1)''', (session, session)).fetchone()
        return dict(row) if row else None

    def require_read_only(self, session, name):
        record = self.current(session)
        if record and record['guarded']:
            if record['deadline'] <= time.time() or name not in READ_TOOLS:
                raise ValueError('补齐回答只允许读取状态和提出问题，不能执行任务或修改配置')

    def snapshot(self, session, observed):
        row = self.current(session)
        if not row:
            return {**observed, 'recovery': None}
        public = {k: row[k] for k in ('request_id', 'state', 'attempts', 'deadline')}
        if row['state'] == 'cancelled':
            return {**observed, 'phase': 'cancelled', 'auto_recoverable': False,
                    'message': '回答已停止，已提交的设备任务不受影响', 'recovery': public}
        if observed['phase'] == 'waiting_user':
            return {**observed, 'recovery': public}
        if row['state'] in ACTIVE_RECOVERY:
            if time.time() >= row['deadline']:
                return {**observed, 'phase': 'incomplete', 'auto_recoverable': False,
                        'message': '补齐已超时，可以重新补齐或直接补充参数',
                        'recovery': {**public, 'state': 'timed_out'}}
            return {**observed, 'phase': 'supplementing', 'message': '正在补齐回答', 'recovery': public}
        if row['state'] in {'failed', 'timed_out'}:
            return {**observed, 'phase': 'incomplete', 'message': '补齐未完成，可以重新补齐或直接补充参数', 'recovery': public}
        return {**observed, 'recovery': public}

    def cancel(self, db, session):
        db.execute("UPDATE reply_recovery SET state='cancelled',guarded=1,deadline=0 WHERE session=?", (session,))

    def expire(self, session):
        """Persist the deadline before contacting an unavailable engine."""
        row = self.current(session)
        if not row or row['state'] not in ACTIVE_RECOVERY or time.time() < row['deadline']:
            return False
        with self.database() as db:
            expired = db.execute("""UPDATE reply_recovery SET state='timed_out'
                WHERE request_id=? AND message_id=? AND state IN ('dispatching','unknown','running')""",
                (row['request_id'], row['message_id'])).rowcount
        if expired:
            try:
                self.runtime.request('POST', f'/session/{session}/abort', timeout=1)
            except Exception:
                # The durable guard rejects tools even if abort cannot reach the engine.
                pass
        return bool(expired)

    def tick(self, session, model, *, manual=False):
        row = self.current(session)
        if not row:
            return
        active = row['state'] in ACTIVE_RECOVERY
        if self.expire(session):
            return
        def read(path):
            remaining = row['deadline'] - time.time() if active else 5
            if remaining <= 0:
                raise TimeoutError('clarification deadline')
            return self.runtime.request('GET', path, timeout=min(5, remaining))
        try:
            messages = read(f'/session/{session}/message?limit=80') or []
            engine = (read('/session/status') or {}).get(session, {}).get('type', 'idle')
            questions = [q for q in (read('/question') or []) if q.get('sessionID') == session]
        except Exception:
            self.expire(session)
            raise
        parent = row['message_id'] if row['attempts'] else row['original_message']
        # Never classify an earlier assistant reply while a new POST is in transit.
        relevant = replies_after(messages, parent)
        observed = response_state(relevant, engine, questions)
        if self.expire(session):
            return
        if active:
            if questions or (engine == 'idle' and any(m.get('info', {}).get('role') == 'assistant' for m in relevant)):
                state = 'failed' if observed['phase'] in {'incomplete', 'failed'} else 'completed'
                with self.database() as db:
                    db.execute("""UPDATE reply_recovery SET state=? WHERE request_id=? AND message_id=?
                        AND state IN ('dispatching','unknown','running')""", (state, row['request_id'], row['message_id']))
            return
        if not manual and row['state'] != 'watching':
            return
        if engine != 'idle' or questions:
            if manual:
                raise ValueError('当前回答或问题尚未结束，请先处理当前内容')
            return
        if not observed['auto_recoverable']:
            if manual and row['state'] not in {'failed', 'timed_out'}:
                raise ValueError('当前没有需要补齐的不完整回答')
            if not manual:
                return
        message_id = 'msg' + uuid.uuid4().hex
        with self.database() as db:
            claimed = db.execute("""UPDATE reply_recovery SET state='dispatching',attempts=attempts+1,message_id=?,deadline=?,guarded=1
                WHERE request_id=? AND state=? AND attempts=? AND request_id=(
                    SELECT id FROM turns WHERE session=? ORDER BY created DESC LIMIT 1)""",
                       (message_id, time.time()+90, row['request_id'], row['state'], row['attempts'], session)).rowcount
            if not claimed:
                return
            db.execute("UPDATE turns SET state='accepted',created=? WHERE id=?", (time.time(), row['request_id']))
        try:
            self.runtime.request('POST', f'/session/{session}/prompt_async', {
                'messageID': message_id, 'agent': 'mediaflow', 'model': model,
                'parts': [{'type': 'text', 'text': '【系统补齐原回答】上一回答宣布追问却没有列出问题。请仅补齐原需求缺失的说明，缺参数请调用 question 提出具体问题。不要扩大原意图，不执行任何任务、恢复、配置修改或权限操作。原用户咨询仍是咨询。'}],
                'system': '这是受限回答补齐，不是新的用户授权。只允许读取状态、补充说明或使用 question 追问。'}, timeout=15)
        except Exception:
            state = 'unknown'
        else:
            state = 'running'
        with self.database() as db:
            db.execute("UPDATE reply_recovery SET state=? WHERE request_id=? AND message_id=? AND state='dispatching'",
                       (state, row['request_id'], message_id))
        self.expire(session)
