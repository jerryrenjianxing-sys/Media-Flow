"""Session-bound VM command proposals; approval is a UI operation, not a tool."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid
from agent_platform import bounded_diagnostic
from virtual_commands import SUPPORTED
from virtual_device_inventory import STANDARD_MUTABLE_SETTINGS

LABELS = {'start': '启动', 'stop': '停止', 'restart': '重启', 'clone': '复制', 'backup': '备份',
          'repair_standard': '修复显示配置', 'settings': '修改配置', 'delete': '删除'}


class AgentOperations:
    def __init__(self, root, store, dispatch):
        self.root, self.store, self.dispatch = Path(root), store, dispatch

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'commands.db', timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, session_id TEXT, call_id TEXT UNIQUE,
            payload TEXT, identity TEXT, fingerprint TEXT, state TEXT, deadline REAL, created REAL)''')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def identity(device):
        return {k: device.get(k) for k in ('virtual_device_id', 'provider_install_id', 'provider_instance_id', 'android_identity', 'name')}

    def propose(self, args, context):
        if set(args) - {'virtual_device_id', 'action', 'settings', 'backup'} or args.get('action') not in SUPPORTED:
            raise ValueError('请指定支持的虚拟机操作')
        device_id = args.get('virtual_device_id')
        if not isinstance(device_id, str):
            raise ValueError('请先指定虚拟机永久编号')
        device = self.store.get_virtual_device(device_id)
        settings = args.get('settings') or {}
        if not isinstance(settings, dict) or set(settings) - STANDARD_MUTABLE_SETTINGS:
            raise ValueError('配置项无效，请使用设备设置支持的字段')
        if 'backup' in args and type(args['backup']) is not bool:
            raise ValueError('备份选项必须明确选择')
        payload = {'virtual_device_id': device_id, 'action': args['action'], 'settings': settings,
                   'backup': args.get('backup', True), 'name': device['name']}
        identity = self.identity(device)
        fingerprint = hashlib.sha256(json.dumps({'request': payload, 'identity': identity}, sort_keys=True).encode()).hexdigest()
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id FROM commands WHERE call_id=?', (context['call_id'],)).fetchone()
            command_id = old['id'] if old else uuid.uuid4().hex
            if not old:
                db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?)', (command_id, context['session_id'], context['call_id'],
                    json.dumps(payload, ensure_ascii=False), json.dumps(identity, sort_keys=True), fingerprint, 'awaiting_confirmation', time.time()+600, time.time()))
        return self.get(command_id, context['session_id'])

    def operation(self, command_id):
        with self.store.connection() as db:
            row = db.execute('SELECT id FROM virtual_device_operations WHERE idempotency_key=?', ('agent-command:' + command_id,)).fetchone()
        if not row:
            return None
        raw = self.store.get_virtual_operation(row['id'])
        return {**{k: raw.get(k) for k in ('id', 'status', 'stage', 'progress', 'updated_at', 'deadline_at', 'retryable')},
                'message': bounded_diagnostic(raw.get('message')), 'error': bounded_diagnostic(raw.get('error'))}

    def get(self, command_id, session_id):
        with self.database() as db:
            row = db.execute('SELECT * FROM commands WHERE id=? AND session_id=?', (command_id, session_id)).fetchone()
        if not row:
            raise ValueError('操作计划不存在或不属于当前会话')
        state = 'expired' if row['state'] == 'awaiting_confirmation' and row['deadline'] < time.time() else row['state']
        payload = json.loads(row['payload'])
        return {'command_id': command_id, 'state': state, 'fingerprint': row['fingerprint'], 'request': payload,
                'label': LABELS[payload['action']], 'operation': self.operation(command_id),
                'message': '仅制定计划，尚未操作虚拟机；请确认目标后执行'}

    def list(self, session_id):
        with self.database() as db:
            ids = [r['id'] for r in db.execute('SELECT id FROM commands WHERE session_id=? ORDER BY created DESC LIMIT 30', (session_id,))]
        return [self.get(i, session_id) for i in ids]

    def confirm(self, command_id, session_id, body):
        command = self.get(command_id, session_id)
        if body.get('confirmed') is not True or body.get('fingerprint') != command['fingerprint']:
            raise ValueError('请核对并明确确认虚拟机操作')
        if command['operation']:
            return command
        if command['state'] != 'awaiting_confirmation':
            raise ValueError('计划已取消或过期，请重新规划')
        payload = command['request']
        device = self.store.get_virtual_device(payload['virtual_device_id'])
        with self.database() as db:
            identity = json.loads(db.execute('SELECT identity FROM commands WHERE id=?', (command_id,)).fetchone()[0])
        if identity != self.identity(device):
            raise ValueError('虚拟机身份或名称发生变化，请重新核对，不会操作旧目标')
        if payload['action'] == 'delete' and body.get('confirmation_name') != device['name']:
            raise ValueError('请输入完整名称以确认删除；账号和数据可能不可恢复')
        if self.dispatch is None:
            raise ValueError('虚拟机操作入口尚未就绪，没有执行任何动作')
        self.dispatch(payload['virtual_device_id'], {**payload, 'confirmation_name': body.get('confirmation_name'),
                      'idempotency_key': 'agent-command:' + command_id})
        with self.database() as db:
            db.execute("UPDATE commands SET state='submitted' WHERE id=?", (command_id,))
        return self.get(command_id, session_id)

    def cancel_unconfirmed(self, session_id):
        with self.database() as db:
            db.execute("UPDATE commands SET state='cancelled' WHERE session_id=? AND state='awaiting_confirmation'", (session_id,))
