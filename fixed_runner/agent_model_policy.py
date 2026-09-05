"""Bounded response metadata and persistent refusal protection for Token Plan."""
import hashlib
import json
import time

PERMANENT = {'authentication', 'permission', 'quota_exhausted', 'model_unavailable'}
MESSAGES = {'authentication': '千问Key无效，请在服务商设置重新保存',
            'permission': '千问套餐或权限不允许此请求，请检查工作台；不会切换服务商',
            'quota_exhausted': '千问套餐额度已耗尽，请检查工作台；不会切换按量计费',
            'model_unavailable': '当前千问套餐无法使用此模型，请检查模型权限',
            'rate_limited': '千问请求限流，请稍后重试；不会切换服务商',
            'provider_failure': '千问服务暂时异常，请检查工作台后重试',
            'circuit_open': '同一配置已连续三次被拒绝，已停止新增请求；处理配置后点击重置故障保护',
            'cooldown': '请求正在冷却，请稍后重试'}


def scope_for(key):
    return hashlib.sha256(key.encode()).hexdigest()


def classify_response(code, data):
    detail = data.decode('utf-8', errors='replace').lower()
    if code == 401:
        return 'authentication'
    if code == 402 or (code == 429 and any(x in detail for x in ('insufficient_quota', 'quota_exhausted', 'quota exceeded', 'allocated quota', '额度'))):
        return 'quota_exhausted'
    if code == 403:
        return 'permission'
    if code == 404 or (code == 400 and any(x in detail for x in ('model_not_found', 'invalid model', 'unsupported model'))):
        return 'model_unavailable'
    return 'rate_limited' if code == 429 else 'provider_failure'


class ResponseUsage:
    """Only bounded numeric usage survives; model text is never recorded here."""
    def __init__(self):
        self.buffer = b''
        self.usage = None

    def feed(self, chunk):
        self.buffer += chunk
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line.startswith(b'data:'):
                self.parse(line[5:].strip())
        if len(self.buffer) > 1_000_000:
            self.buffer = b''

    def parse(self, data):
        try:
            raw = json.loads(data)
            usage = raw.get('usage')
            if isinstance(usage, dict):
                clean = {k: v for k, v in usage.items() if k in {'prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens', 'credits'} and type(v) in {int, float} and 0 <= v < 10**12}
                if clean:
                    self.usage = clean
        except (ValueError, AttributeError):
            pass

    def finish(self):
        self.parse(self.buffer)
        return self.usage


class AgentModelPolicy:
    def __init__(self, database):
        self.database = database
        with self.database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS request_policy(scope TEXT PRIMARY KEY, failures INTEGER, reason TEXT, cooldown REAL)')

    def admit(self, scope):
        with self.database() as db:
            row = db.execute('SELECT failures,reason,cooldown FROM request_policy WHERE scope=?', (scope,)).fetchone()
        if row and row[0] >= 3 and row[1] in PERMANENT:
            return 'circuit_open'
        if row and row[2] > time.time():
            return 'cooldown'
        return None

    def finish(self, scope, reason):
        if not scope:
            return
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT failures,reason FROM request_policy WHERE scope=?', (scope,)).fetchone()
            failures = ((old[0] if old and old[1] == reason else 0) + 1) if reason in PERMANENT else 0
            if reason != 'completed' and reason not in PERMANENT and reason != 'rate_limited':
                return
            db.execute('INSERT OR REPLACE INTO request_policy VALUES(?,?,?,?)',
                       (scope, failures, reason, time.time()+30 if reason == 'rate_limited' else 0))

    def reset(self):
        with self.database() as db:
            db.execute('UPDATE request_policy SET failures=0,cooldown=0')
        return {'ok': True, 'message': '已重置Agent千问故障保护；历史用量保留，没有自动重试请求'}
