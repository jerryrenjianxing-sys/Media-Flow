"""Host-owned conversation grants and user-origin operation receipts.

Only HTTP user input enters record/set. MCP can consume a grant, never create
one. This is application authorization, not an operating-system sandbox.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time

LEVELS = {'operate': '操作模式', 'maintain': '维护模式', 'develop': '开发模式'}
WRITE_FIELDS = {'点赞': ('like_probability', 'matched_like_probability'),
                '收藏': ('favorite_probability', 'matched_favorite_probability'),
                '评论': ('comment_probability', 'matched_comment_probability')}


def user_intent(text):
    # Quoted instructions and questions about execution are not commands.
    plain = re.sub(r'```[\s\S]*?```|“[^”]*”|「[^」]*」|"[^"]*"', '', text).strip()
    # Excluding other devices/history is a scope restriction, not cancellation
    # of the separately requested task. An exclusion alone never authorizes it.
    action_text = re.sub(r'(?:不要|不|别)(?:启动|执行|运行|开跑)\s*(?:[0-9一二三四五六七八九十]+号|其他|旧|历史|另外)[^，,。；;!?\n]*', '', plain)
    preview = bool(re.search(r'只.{0,5}(计划|方案|预览)|先.{0,5}(方案|计划)|(?:不要|别|不必|不需要|暂不|先不|不)(?:直接)?(?:启动|执行|运行|开跑)|^(?:请)?(?:暂停|停止|取消)|如何|怎么|示例|举例', action_text))
    execute = not preview and bool(re.search(r'启动|执行|运行|开跑|开始|帮我跑|跑一|跑两|跑[0-9]|恢复.{0,12}(任务|批次)|^(?:继续|确认|可以|开始吧|按这个来)[。！!\s]*$', action_text))
    resume = execute and bool(re.search(r'(恢复|解除).{0,16}(任务|批次|停止)', plain))
    writes = {}
    for label, fields in WRITE_FIELDS.items():
        match = re.search(label + r'(?:概率|比例)?\s*(?:为|是|[:：])?\s*(\d+(?:\.\d+)?)\s*[%％]', plain)
        value = float(match[1]) / 100 if match else 0
        if re.search(r'(?:不|零|禁止|不要)' + label, plain):
            value = 0
        for field in fields:
            writes[field] = value
    return {'execute': execute, 'resume': resume, 'writes': writes, 'statement': plain[:12000]}


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
        """)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, session):
        with self.database() as db:
            row = db.execute('SELECT * FROM grants WHERE session=?', (session,)).fetchone()
        result = dict(row) if row else {'session': session, 'level': 'operate', 'revision': 0, 'source': 'default', 'updated': None}
        return {**result, 'label': LEVELS[result['level']], 'scope': 'MediaFlow project', 'persistent': 'session'}

    def set(self, session, level, *, source):
        if level not in LEVELS or source not in {'user_settings', 'user_chat'}:
            raise ValueError('请由用户选择有效权限等级')
        with self.database() as db:
            db.execute("INSERT INTO grants VALUES(?,?,1,?,?) ON CONFLICT(session) DO UPDATE SET level=excluded.level,revision=grants.revision+1,source=excluded.source,updated=excluded.updated",
                       (session, level, source, time.time()))
        return self.get(session)

    def require(self, session, tool):
        required = 'develop' if tool.startswith('repair_') else 'maintain' if tool in {'plan_virtual_operation', 'execute_virtual_operation'} else 'operate'
        if list(LEVELS).index(self.get(session)['level']) < list(LEVELS).index(required):
            raise ValueError('此操作需要' + LEVELS[required] + '。请在当前会话说“开启' + LEVELS[required] + '”或在助手设置中选择；原任务不受影响')

    def record(self, session, request_id, text):
        """Called once after a validated user message is admitted, before dispatch."""
        with self.database() as db:
            existing = db.execute('SELECT session FROM requests WHERE id=?', (request_id,)).fetchone()
            if existing:
                if existing['session'] != session:
                    raise ValueError('请求不属于当前会话')
                return
            intent = user_intent(text)
            # Preserve the user-origin consent statement, not model/tool output.
            db.execute('INSERT INTO requests VALUES(?,?,?,?)', (request_id, session, json.dumps(intent), time.time()))
            db.execute('INSERT INTO current_requests VALUES(?,?) ON CONFLICT(session) DO UPDATE SET request_id=excluded.request_id', (session, request_id))
        match = re.fullmatch(r'\s*(?:请)?(?:开启|切换到|启用)(操作模式|维护模式|开发模式)[。！!\s]*', text)
        if match:
            self.set(session, next(key for key, value in LEVELS.items() if value == match[1]), source='user_chat')
        elif re.fullmatch(r'\s*(?:请)?(?:撤销|关闭)(?:高权限|开发模式|维护模式)[。！!\s]*', text):
            self.set(session, 'operate', source='user_chat')

    def record_answer(self, session, question_id, answers, questions):
        # A genuine user's answer may supply missing probabilities. Question text
        # is model generated and never grants execution or elevates permission.
        with self.database() as db:
            if db.execute('SELECT 1 FROM answers WHERE id=?', (question_id,)).fetchone():
                return
            row = db.execute('SELECT r.* FROM requests r JOIN current_requests c ON c.request_id=r.id WHERE c.session=?', (session,)).fetchone()
            if row:
                intent = json.loads(row['intent'])
                answer_text = '；'.join(' '.join(answer) for answer in answers)
                parsed = user_intent(answer_text)
                for label, fields in WRITE_FIELDS.items():
                    if label in answer_text:
                        for field in fields:
                            intent['writes'][field] = parsed['writes'][field]
                # Never infer write authority from a model-supplied option label.
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

    def authorize_operation(self, session, command):
        row = self.current(session)
        text = row['intent']['statement'] if row else ''
        payload = command['request']
        aliases = {'start': '启动|打开', 'stop': '停止|关闭', 'restart': '重启',
                   'clone': '复制|克隆', 'backup': '备份', 'settings': '设置|配置|修改',
                   'repair_standard': '修复|标准|分辨率', 'delete': '删除'}
        verb = aliases.get(payload['action'])
        if not verb or not re.search(verb, text) or re.search(r'不要|别|仅预览|只做计划|只读|如何|怎么', text):
            raise ValueError('请在聊天明确这次虚拟机操作；尚未操作设备')
        if payload['action'] == 'delete' and (payload['name'] not in text or not re.search('不可恢复|不备份|删除数据|确认删除', text)):
            raise ValueError('删除需要明确完整名称及数据后果，请回复“确认删除' + payload['name'] + '，数据不可恢复”')
        return {'request_id': row['id'], 'source': 'user_chat', 'session_id': session,
                'target': command['command_id'], 'fingerprint': command['fingerprint']}

    def authorize(self, session, target, fingerprint, config, *, stopped=False):
        row = self.current(session)
        if not row or not row['intent']['execute']:
            raise ValueError('当前用户未要求执行。请说明要启动的计划；仅策划不会运行任务')
        intent = row['intent']
        if stopped and not intent['resume']:
            raise ValueError('这是任务安全停止，不是虚拟机关机。请说“恢复这批任务”后继续')
        approved_writes = intent['writes']
        if not any(label in intent['statement'] for label in WRITE_FIELDS) and (intent['resume'] or re.fullmatch(r'(?:继续|确认)[。！!\s]*', intent['statement'])):
            with self.database() as db:
                old = db.execute('SELECT b.receipt FROM bindings b JOIN requests r ON r.id=b.request_id WHERE r.session=? AND b.target=? AND b.fingerprint=? LIMIT 1',
                                 (session, target, fingerprint)).fetchone()
            if old:
                approved_writes = json.loads(old['receipt']).get('writes', approved_writes)
        for fields in WRITE_FIELDS.values():
            for field in fields:
                value = float(config.get(field) or 0)
                if value > approved_writes.get(field, 0):
                    raise ValueError('真实互动比例超出本次聊天授权，请明确点赞、收藏和评论的百分比；未指定则为零')
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM bindings WHERE request_id=?', (row['id'],)).fetchall()
            if prior:
                if len(prior) != 1 or prior[0]['target'] != target or prior[0]['fingerprint'] != fingerprint:
                    raise ValueError('本次请求已绑定另一份计划，不能重复生成任务；请查看原批次或明确新要求')
                return json.loads(prior[0]['receipt'])
            receipt = {'request_id': row['id'], 'source': 'user_chat', 'session_id': session,
                       'target': target, 'fingerprint': fingerprint, 'permission_revision': self.get(session)['revision'],
                       'writes': approved_writes,
                       'resume_stopped_devices': bool(intent['resume'])}
            db.execute('INSERT INTO bindings VALUES(?,?,?,?)', (row['id'], target, fingerprint, json.dumps(receipt)))
        return receipt
