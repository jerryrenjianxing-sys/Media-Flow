"""Host-owned user requests and operation receipts, without prose permission gates.

The platform Skill interprets conversation intent. Legacy grants and request
history remain readable without enforcing tiers.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

WRITE_FIELDS = ('like_probability', 'favorite_probability', 'comment_probability',
                'matched_like_probability', 'matched_favorite_probability', 'matched_comment_probability')


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class AgentPermissions:
    def __init__(self, root):
        self.root = Path(root)

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'permissions.db', timeout=5)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS grants(session TEXT PRIMARY KEY, level TEXT, revision INTEGER, source TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, session TEXT, intent TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS current_requests(session TEXT PRIMARY KEY, request_id TEXT);
            CREATE TABLE IF NOT EXISTS bindings(request_id TEXT, target TEXT, fingerprint TEXT, receipt TEXT, PRIMARY KEY(request_id,target));
            CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY, session TEXT);
            CREATE TABLE IF NOT EXISTS receipt_history(hash TEXT PRIMARY KEY, request_id TEXT, target TEXT, receipt TEXT, created REAL);
        """)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, session):
        with self.database() as db:
            row = db.execute('SELECT * FROM grants WHERE session=?', (session,)).fetchone()
        result = dict(row) if row else {'session': session, 'revision': 0, 'source': 'default', 'updated': None}
        return {**result, 'level': 'full', 'label': '平台操作', 'scope': 'MediaFlow project', 'persistent': 'session'}

    def set(self, session, level, *, source):
        # Accept obsolete client selections without overwriting historical grants.
        if level not in {'full', 'operate', 'maintain', 'develop'} or source not in {'user_settings', 'user_chat'}:
            raise ValueError('无效的平台操作设置')
        return self.get(session)

    def require(self, session, tool):
        # Tool/session admission is checked by the host, independently of tiers.
        if not session:
            raise ValueError('工具需要有效会话')

    def record(self, session, request_id, text):
        """Called once after a validated user message is admitted, before dispatch."""
        if not session or not request_id:
            raise ValueError('需要真实用户会话与请求编号')
        with self.database() as db:
            existing = db.execute('SELECT session FROM requests WHERE id=?', (request_id,)).fetchone()
            if existing:
                if existing['session'] != session:
                    raise ValueError('请求不属于当前会话')
                return
            db.execute('INSERT INTO requests VALUES(?,?,?,?)', (request_id, session, json.dumps({'statement': text[:12000]}), time.time()))
            db.execute('INSERT INTO current_requests VALUES(?,?) ON CONFLICT(session) DO UPDATE SET request_id=excluded.request_id', (session, request_id))

    def record_answer(self, session, question_id, answers, questions):
        # Question labels cannot manufacture a user request or a receipt.
        with self.database() as db:
            existing = db.execute('SELECT session FROM answers WHERE id=?', (question_id,)).fetchone()
            if existing:
                if existing['session'] != session:
                    raise ValueError('问题不属于当前会话')
                return
            row = db.execute('SELECT r.* FROM requests r JOIN current_requests c ON c.request_id=r.id WHERE c.session=?', (session,)).fetchone()
            if row:
                intent = json.loads(row['intent'])
                intent.setdefault('answers', []).append({'question_id': question_id, 'answers': answers})
                db.execute('UPDATE requests SET intent=? WHERE id=?', (json.dumps(intent), row['id']))
            db.execute('INSERT INTO answers VALUES(?,?)', (question_id, session))

    def current(self, session):
        with self.database() as db:
            row = db.execute('SELECT r.* FROM requests r JOIN current_requests c ON c.request_id=r.id WHERE c.session=?', (session,)).fetchone()
        return {**dict(row), 'intent': json.loads(row['intent'])} if row else None

    def cancel_intent(self, session):
        with self.database() as db:
            db.execute('DELETE FROM current_requests WHERE session=?', (session,))

    def receipt(self, session, target):
        with self.database() as db:
            row = db.execute('SELECT b.receipt FROM bindings b JOIN requests r ON r.id=b.request_id WHERE r.session=? AND b.target=? ORDER BY r.created DESC LIMIT 1', (session, target)).fetchone()
        return json.loads(row['receipt']) if row else None

    def authorize_operation(self, session, command, *, tool_call_id=''):
        return self._bind(session, command['command_id'], command['fingerprint'],
                          {'operation': command['request'], 'config_hash': content_hash(command['request'])},
                          tool_call_id=tool_call_id, single_target=False)

    def authorize(self, session, target, fingerprint, config, *, stopped=False, resume_stopped_devices=None, tool_call_id=''):
        # execute_plan consumes current conversation context. Explicit false
        # keeps a scoped stop in place; recovery does not require a magic phrase.
        resume = bool(stopped) if resume_stopped_devices is None else bool(resume_stopped_devices)
        if stopped and not resume:
            raise ValueError('本批次仍处于任务安全停止，请通过本批次恢复操作继续')
        return self._bind(session, target, fingerprint,
                          {'config_hash': content_hash(config),
                           'writes': {field: float(config.get(field) or 0) for field in WRITE_FIELDS},
                           'resume_stopped_devices': resume}, tool_call_id=tool_call_id)

    def _bind(self, session, target, plan_hash, details, *, tool_call_id='', single_target=True):
        if not target or not plan_hash:
            raise ValueError('操作缺少目标或配置指纹')
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT r.* FROM requests r JOIN current_requests c ON c.request_id=r.id WHERE c.session=?', (session,)).fetchone()
            if not row:
                raise ValueError('操作需要当前已登记的真实用户请求')
            prior = db.execute('SELECT * FROM bindings WHERE request_id=?', (row['id'],)).fetchall()
            if single_target and any(item['target'] != target and 'writes' in json.loads(item['receipt']) for item in prior):
                raise ValueError('本次请求已绑定另一份计划，不能重复生成任务；请查看原批次或明确新要求')
            for item in prior:
                if item['target'] != target:
                    continue
                old = json.loads(item['receipt'])
                if item['fingerprint'] != plan_hash or (old.get('config_hash') and old['config_hash'] != details.get('config_hash')):
                    raise ValueError('本次请求已绑定另一份配置，不能重复生成任务')
                if details.get('resume_stopped_devices') and not old.get('resume_stopped_devices'):
                    previous = old.get('receipt_hash') or content_hash(old)
                    db.execute('INSERT OR IGNORE INTO receipt_history VALUES(?,?,?,?,?)',
                               (previous, row['id'], target, json.dumps(old), time.time()))
                    receipt = {**old, 'resume_stopped_devices': True, 'previous_receipt_hash': previous,
                               'tool_call_id': tool_call_id or old.get('tool_call_id', '')}
                    receipt.pop('receipt_hash', None)
                    receipt['receipt_hash'] = content_hash(receipt)
                    db.execute('UPDATE bindings SET receipt=? WHERE request_id=? AND target=?',
                               (json.dumps(receipt), row['id'], target))
                    db.execute('INSERT INTO receipt_history VALUES(?,?,?,?,?)',
                               (receipt['receipt_hash'], row['id'], target, json.dumps(receipt), time.time()))
                    return receipt
                return old
            receipt = {'request_id': row['id'], 'source': 'user_chat', 'session_id': session,
                       'target': target, 'fingerprint': plan_hash, 'tool_call_id': tool_call_id, **details}
            receipt['receipt_hash'] = content_hash(receipt)
            db.execute('INSERT INTO bindings VALUES(?,?,?,?)', (row['id'], target, plan_hash, json.dumps(receipt)))
            db.execute('INSERT INTO receipt_history VALUES(?,?,?,?,?)',
                       (receipt['receipt_hash'], row['id'], target, json.dumps(receipt), time.time()))
        return receipt
