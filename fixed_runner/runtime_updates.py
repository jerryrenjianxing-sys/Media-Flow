"""Durable update admission, shared with every device action claim."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager, closing


ACTIVE_UPDATE_SQL = "('downloaded','waiting_idle','ready')"


class RuntimeUpdateConflict(ValueError):
    def __init__(self, operation):
        super().__init__("软件正在准备更新，请等待或取消本次更新")
        self.operation = operation


class RuntimeUpdatesMixin:
    @staticmethod
    def initialize_runtime_updates(db):
        db.execute('''CREATE TABLE IF NOT EXISTS runtime_updates(
            id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,status TEXT NOT NULL,
            target_version TEXT NOT NULL,sha512 TEXT NOT NULL,original_state_json TEXT NOT NULL,
            blockers_json TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS runtime_activity_leases(
            id TEXT PRIMARY KEY,kind TEXT NOT NULL,device_id TEXT,description TEXT NOT NULL,
            owner_pid INTEGER NOT NULL,owner_creation TEXT NOT NULL,created_at TEXT NOT NULL)''')
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_runtime_update ON runtime_updates((1)) "
                   "WHERE status IN " + ACTIVE_UPDATE_SQL)

    @staticmethod
    def _runtime_update_payload(row):
        status = row['status']
        return {'id': row['id'], 'operation_id': row['id'], 'request_id': row['request_id'],
                'status': status, 'ready': status == 'ready', 'target_version': row['target_version'],
                'sha512': row['sha512'], 'original_state': json.loads(row['original_state_json']),
                'blockers': json.loads(row['blockers_json']), 'created_at': row['created_at'],
                'updated_at': row['updated_at'], 'message': {
                    'downloaded': '更新已下载，正在核对后台占用',
                    'waiting_idle': '等待当前任务、维护或人工操作结束；可以取消更新',
                    'ready': '后台空闲，可以安全停止并安装',
                    'cancelled': '更新准备已取消', 'completed': '新版已确认，更新完成'}[status]}

    def _active_runtime_update(self, db):
        return db.execute('SELECT * FROM runtime_updates WHERE status IN ' + ACTIVE_UPDATE_SQL + ' LIMIT 1').fetchone()

    def _assert_runtime_available(self, db):
        row = self._active_runtime_update(db)
        if row:
            raise RuntimeUpdateConflict(self._runtime_update_payload(row))
        if db.execute("SELECT 1 FROM system_state WHERE key='runtime-shutdown' AND value='1'").fetchone():
            raise ValueError('后台正在安全停止，请重新启动平台后操作')

    def _runtime_claims_blocked(self, db):
        return bool(self._active_runtime_update(db) or db.execute(
            "SELECT 1 FROM system_state WHERE key='runtime-shutdown' AND value='1'").fetchone())

    def assert_runtime_available(self):
        with self.connection() as db:
            self._assert_runtime_available(db)

    def runtime_updating(self):
        with self.connection() as db:
            return self._runtime_claims_blocked(db)

    def begin_runtime_shutdown(self, operation_id=None):
        from task_store import now_iso
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if operation_id:
                row = db.execute('SELECT * FROM runtime_updates WHERE id=?', (operation_id,)).fetchone()
                if not row or row['status'] not in {'downloaded', 'waiting_idle', 'ready'}:
                    raise ValueError('更新停机请求不存在或已结束')
            blockers = self._runtime_blockers(db)
            if blockers:
                return blockers
            db.execute("INSERT INTO system_state VALUES('runtime-shutdown','1',?) "
                       "ON CONFLICT(key) DO UPDATE SET value='1',updated_at=excluded.updated_at", (now_iso(),))
            return []

    def release_runtime_shutdown(self):
        with self.connection() as db:
            db.execute("DELETE FROM system_state WHERE key='runtime-shutdown'")

    def active_runtime_update(self):
        with self.connection() as db:
            row = self._active_runtime_update(db)
            return self._runtime_update_payload(row) if row else None

    def _runtime_blockers(self, db, *, device_id=None):
        from task_store import now_iso
        from runtime_control import SystemProcessInspector
        blockers = []
        for table, condition, kind, message in (
            ('tasks', "status='running'", 'task', '业务任务正在执行'),
            ('device_initializations', "status='running'", 'initialization', '设备初始化正在执行'),
            ('task_recoveries', "status='running'", 'recovery', '设备维护复验正在执行'),
            ('virtual_device_operations', "status IN ('queued','running')", 'virtual_operation', '虚拟机操作尚未结束'),
            ('incidents', "analysis_status IN ('running','analyzing')", 'analysis', '模型分析尚未结束'),
        ):
            for row in db.execute('SELECT * FROM ' + table + ' WHERE ' + condition):
                owner = row['device_id'] if 'device_id' in row.keys() else str(json.loads(row['request_json']).get('virtual_device_id') or '')
                if device_id and owner != device_id:
                    continue
                blockers.append({'kind': kind, 'id': row['id'], 'device_id': owner,
                                 'message': message, 'status': row['status'] if 'status' in row.keys() else 'running'})
        timestamp = now_iso()
        for row in db.execute("SELECT * FROM device_view_sessions WHERE mode='control' "
                              "AND status IN ('created','connected','disconnected') AND expires_at>? "
                              "AND (lease_expires_at IS NULL OR lease_expires_at>?)", (timestamp, timestamp)):
            if not device_id or row['device_id'] == device_id:
                blockers.append({'kind': 'manual_control', 'id': row['id'], 'device_id': row['device_id'],
                                 'message': '设备仍在人工控制', 'lease_expires_at': row['lease_expires_at']})
        if not device_id:
            for row in db.execute('SELECT * FROM model_recovery_probes WHERE token IS NOT NULL AND lease_until>?', (time.time(),)):
                blockers.append({'kind': 'model_probe', 'id': row['model_key'], 'message': '模型连接复查尚未结束'})
        inspector = SystemProcessInspector()
        for row in db.execute('SELECT * FROM runtime_activity_leases').fetchall():
            snapshot = inspector.snapshot(row['owner_pid'])
            if snapshot is None or snapshot.creation_token != row['owner_creation']:
                db.execute('DELETE FROM runtime_activity_leases WHERE id=?', (row['id'],))
                continue
            if not device_id or row['device_id'] == device_id:
                blockers.append({'kind': row['kind'], 'id': row['id'], 'device_id': row['device_id'],
                                 'message': row['description'], 'owner_pid': row['owner_pid']})
        if not device_id:
            model_ledger = self.path.parent / 'model-providers.db'
            if model_ledger.is_file():
                try:
                    with closing(sqlite3.connect(model_ledger.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as models:
                        for call_id, owner_pid in models.execute('SELECT id,pid FROM calls WHERE finished IS NULL'):
                            if owner_pid and inspector.snapshot(int(owner_pid)) is not None:
                                blockers.append({'kind': 'model_call', 'id': call_id, 'owner_pid': owner_pid,
                                                 'message': '模型或模型配置操作尚未结束'})
                except sqlite3.Error:
                    blockers.append({'kind': 'model_state', 'id': 'model-providers', 'message': '无法核对模型操作状态，请检查本地记录'})
            # These legacy maintenance ledgers remain independent stores. Read
            # them without constructing services that recover or rewrite jobs.
            for path, table, condition in (
                (self.path.parent/'agent/updates/updates.db', 'jobs', "status IN ('queued','running')"),
                (self.path.parent/'agent/repairs/repairs.db', 'tests', "state IN ('queued','running')"),
                (self.path.parent/'agent/repairs/repairs.db', 'repairs', "state='creating'"),
            ):
                if not path.is_file():
                    continue
                try:
                    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as maintenance:
                        for row in maintenance.execute('SELECT id FROM ' + table + ' WHERE ' + condition):
                            blockers.append({'kind': 'maintenance', 'id': row[0], 'message': '维护或修复操作尚未结束'})
                except sqlite3.Error:
                    blockers.append({'kind': 'maintenance_state', 'id': table, 'message': '无法核对维护状态，请检查本地记录'})
        return blockers

    def runtime_blockers(self, device_id=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._runtime_blockers(db, device_id=device_id)

    def begin_runtime_activity(self, kind, description, *, device_id=None):
        from task_store import now_iso
        from runtime_control import SystemProcessInspector
        snapshot = SystemProcessInspector().snapshot(os.getpid())
        if snapshot is None:
            raise RuntimeError('无法核验当前操作进程')
        activity_id = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._assert_runtime_available(db)
            db.execute('INSERT INTO runtime_activity_leases VALUES(?,?,?,?,?,?,?)',
                       (activity_id, kind, device_id, description, os.getpid(), snapshot.creation_token, now_iso()))
        return activity_id

    def end_runtime_activity(self, activity_id):
        with self.connection() as db:
            db.execute('DELETE FROM runtime_activity_leases WHERE id=?', (activity_id,))

    @contextmanager
    def runtime_activity(self, kind, description, *, device_id=None):
        activity_id = self.begin_runtime_activity(kind, description, device_id=device_id)
        try:
            yield activity_id
        finally:
            self.end_runtime_activity(activity_id)

    def _refresh_runtime_update(self, db, row):
        from task_store import now_iso
        if row['status'] in {'downloaded', 'waiting_idle', 'ready'}:
            blockers = self._runtime_blockers(db)
            status = 'waiting_idle' if blockers else 'ready'
            db.execute('UPDATE runtime_updates SET status=?,blockers_json=?,updated_at=? WHERE id=?',
                       (status, json.dumps(blockers, ensure_ascii=False), now_iso(), row['id']))
            row = db.execute('SELECT * FROM runtime_updates WHERE id=?', (row['id'],)).fetchone()
        return self._runtime_update_payload(row)

    def prepare_runtime_update(self, request_id, *, target_version='', sha512=''):
        from task_store import now_iso
        request_id = str(request_id or '').strip()
        if not request_id or len(request_id) > 200:
            raise ValueError('更新准备需要有效request_id')
        target_version, sha512 = str(target_version or ''), str(sha512 or '')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM runtime_updates WHERE request_id=?', (request_id,)).fetchone()
            if row:
                if (row['target_version'], row['sha512']) != (target_version, sha512):
                    raise ValueError('request_id已绑定另一份更新')
                return self._refresh_runtime_update(db, row)
            self._assert_runtime_available(db)
            system = {r['key']: r['value'] for r in db.execute('SELECT key,value FROM system_state')}
            original = {'paused': system.get('paused') == '1', 'system_flags': system,
                        'automation_flags': {r['name']: json.loads(r['config_json']) for r in db.execute('SELECT name,config_json FROM automation_profiles')},
                        'batch_states': {r['id']: r['state'] for r in db.execute('SELECT id,state FROM agent_task_batches')}}
            operation_id, timestamp = uuid.uuid4().hex, now_iso()
            db.execute('INSERT INTO runtime_updates(id,request_id,status,target_version,sha512,original_state_json,created_at,updated_at) '
                       "VALUES(?,?,'downloaded',?,?,?,?,?)", (operation_id, request_id, target_version, sha512,
                       json.dumps(original, ensure_ascii=False), timestamp, timestamp))
            return self._refresh_runtime_update(db, db.execute('SELECT * FROM runtime_updates WHERE id=?', (operation_id,)).fetchone())

    def runtime_update_status(self, operation_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM runtime_updates WHERE id=?', (operation_id,)).fetchone()
            if not row:
                raise KeyError(operation_id)
            return self._refresh_runtime_update(db, row)

    def finish_runtime_update(self, operation_id, *, cancelled=False):
        from task_store import now_iso
        status = 'cancelled' if cancelled else 'completed'
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM runtime_updates WHERE id=?', (operation_id,)).fetchone()
            if not row:
                raise KeyError(operation_id)
            if row['status'] == status:
                return self._runtime_update_payload(row)
            if row['status'] in {'cancelled', 'completed'}:
                raise ValueError('本次更新准备已经结束')
            if not cancelled and self._refresh_runtime_update(db, row)['status'] != 'ready':
                raise ValueError('后台仍被占用，不能完成更新')
            db.execute('UPDATE runtime_updates SET status=?,blockers_json=\'[]\',updated_at=? WHERE id=?',
                       (status, now_iso(), operation_id))
            # Gate removal never resumes, cancels, expires or replays a task.
            return self._runtime_update_payload(db.execute('SELECT * FROM runtime_updates WHERE id=?', (operation_id,)).fetchone())
