"""User-approved, hash-bound development updates with durable operation receipts."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from repair_materials import development_revision
from runtime_layout import IS_DISTRIBUTION


class AgentRepairUpdates:
    def __init__(self, root, repairs, permissions, *, launcher=None):
        self.root, self.repairs, self.permissions = Path(root), repairs, permissions
        self.launcher = launcher or self.launch

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'updates.db', timeout=5)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY,session TEXT,hash TEXT,base TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS approvals(request_id TEXT PRIMARY KEY,session TEXT,repair TEXT,hash TEXT,revision INTEGER);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,session TEXT,repair TEXT,hash TEXT,base TEXT,request_id TEXT UNIQUE,
                status TEXT,stage TEXT,message TEXT,created REAL,updated REAL,deadline REAL,cancelled INTEGER DEFAULT 0,
                target_revision TEXT,rollback_revision TEXT);
        """)
        columns = {row[1] for row in db.execute('PRAGMA table_info(jobs)')}
        for name, kind in (('worker_pid', 'INTEGER'), ('lease_until', 'REAL')):
            if name not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN ' + name + ' ' + kind)
        try:
            with db:
                yield db
        finally:
            db.close()

    def prepare(self, repair_id, session):
        self.permissions.require(session, 'repair_prepare_apply')
        diff = self.repairs.diff(repair_id, session)
        if not diff['changed_files']:
            raise ValueError('候选没有修改')
        if not re.fullmatch(r'[a-f0-9]{40}', diff['revision']):
            raise ValueError('修复基准不是干净提交，请在本机提交完成后创建新修复工作区')
        if development_revision(self.repairs.source_root) != diff['revision']:
            raise ValueError('主项目已有其他修改，不能覆盖；请先处理冲突并重新建立修复')
        tests = self.repairs.tests(repair_id, session)
        if not any(t['mode'] == 'all' and t['state'] == 'passed' and t['source_hash'] == diff['source_hash'] for t in tests):
            raise ValueError('当前补丁尚未通过完整验证，请运行 repair_validate 后再准备应用')
        self.repairs.freeze(repair_id, session, self.root / 'approved' / (diff['source_hash'] + '.json'), diff['source_hash'])
        with self.database() as db:
            db.execute('INSERT INTO offers VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET hash=excluded.hash,base=excluded.base,created=excluded.created',
                (repair_id, session, diff['source_hash'], diff['revision'], time.time()))
        return {'repair_id': repair_id, 'source_hash': diff['source_hash'], 'revision': diff['revision'],
                'changed_files': diff['changed_files'], 'status': 'waiting_user',
                'message': '完整验证已通过。请展示差异；用户回复“应用这个修复”后可调用 repair_apply，尚未更新程序'}

    def rollback_candidate(self, operation_id, context):
        session = context['session_id']
        self.permissions.require(session, 'repair_prepare_rollback')
        job = self.get(operation_id, session)
        if job['status'] != 'completed' or development_revision(self.repairs.source_root) != job['target_revision']:
            raise ValueError('只能为当前已生效且没有后续修改的版本准备回退；原程序未变')
        candidate = self.repairs.create({'purpose': '回退已批准更新 ' + operation_id}, context)
        if candidate['state'] != 'ready':
            return candidate
        previous = self.repairs.root / job['repair'] / 'baseline'
        original = self.repairs.diff(job['repair'], session)
        for name in original['changed_files']:
            if name == 'packaging/version.json':
                continue
            old = self.repairs.file(previous, name)
            current = self.repairs.file(self.repairs.workspace(candidate['id'], session), name)
            digest = hashlib.sha256(current.read_bytes() if current.is_file() else b'').hexdigest()
            args = {'repair_id': candidate['id'], 'path': name, 'expected_sha256': digest}
            if old.is_file():
                self.repairs.edit({**args, 'content': old.read_text(encoding='utf-8-sig')}, session)
            else:
                self.repairs.remove(args, session)
        return {**candidate, 'message': '已生成回退候选，未改变运行程序。验证、展示差异后，用户回复应用这个修复，再递增版本生效；数据不回滚'}

    def capture_user_approval(self, session, request_id, text):
        if not re.fullmatch(r'\s*(?:请|确认)?应用(?:这个修复|修复\s*[a-f0-9]{32})[。！!\s]*', text):
            return
        self.permissions.require(session, 'repair_apply')
        with self.database() as db:
            if db.execute('SELECT 1 FROM approvals WHERE request_id=?', (request_id,)).fetchone():
                return
            rows = db.execute("SELECT * FROM offers WHERE session=? AND id NOT IN (SELECT repair FROM jobs WHERE status='completed')", (session,)).fetchall()
            explicit = re.search(r'[a-f0-9]{32}', text)
            if explicit:
                rows = [row for row in rows if row['id'] == explicit[0]]
            if len(rows) != 1:
                raise ValueError('没有唯一的待应用修复，请先展示并指定修复编号；未更新')
            row = rows[0]
            diff = self.repairs.diff(row['id'], session)
            if diff['source_hash'] != row['hash']:
                raise ValueError('补丁已变化，请重新验证并展示差异后确认')
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,?)', (request_id, session, row['id'], row['hash'], self.permissions.get(session)['revision']))

    def apply(self, repair_id, session):
        self.permissions.require(session, 'repair_apply')
        if IS_DISTRIBUTION:
            raise ValueError('安装版独立更新链尚未验收，本次仅支持本机开发服务')
        request = self.permissions.current(session)
        if not request:
            raise ValueError('请在聊天中确认应用这份修复')
        with self.database() as db:
            approval = db.execute('SELECT * FROM approvals WHERE request_id=? AND session=? AND repair=?',
                (request['id'], session, repair_id)).fetchone()
            if not approval or approval['revision'] != self.permissions.get(session)['revision']:
                raise ValueError('尚无本补丁的用户应用确认，或授权等级已变；请重新确认')
            old = db.execute('SELECT id FROM jobs WHERE request_id=?', (request['id'],)).fetchone()
            if old:
                return self.get(old['id'], session)
        diff = self.repairs.diff(repair_id, session)
        if diff['source_hash'] != approval['hash']:
            raise ValueError('确认后补丁已变化，不能应用；请重新验证确认')
        self.prepare(repair_id, session)
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','waiting_user')").fetchone():
                raise ValueError('已有更新操作，请等待或取消')
            job_id, now = uuid.uuid4().hex, time.time()
            db.execute('INSERT INTO jobs(id,session,repair,hash,base,request_id,status,stage,message,created,updated,deadline) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (job_id, session, repair_id, diff['source_hash'], diff['revision'], request['id'], 'queued',
                 'waiting_idle', '正在等待任务与设备操作空闲；可取消', now, now, now + 1800))
        try:
            self.launcher(job_id)
        except Exception:
            self.update(job_id, status='failed', stage='launch_failed', message='更新进程未启动，旧程序未变；可重新展示并确认')
        return self.get(job_id, session)

    def launch(self, job_id):
        # No parent-owned Job Object: this process must survive the API restart.
        subprocess.Popen([sys.executable, str(Path(__file__).with_name('repair_update_worker.py')),
            str(self.root), str(self.repairs.root), str(self.repairs.source_root), job_id],
            cwd=self.repairs.source_root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))

    def update(self, job_id, **values):
        allowed = {'status', 'stage', 'message', 'target_revision', 'rollback_revision'}
        if set(values) - allowed:
            raise ValueError('无效更新字段')
        with self.database() as db:
            db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ',updated=? WHERE id=?',
                (*values.values(), time.time(), job_id))

    def claim(self, job_id):
        with self.database() as db:
            now = time.time()
            result = db.execute("UPDATE jobs SET status='running',worker_pid=?,lease_until=?,updated=? WHERE id=? AND status='queued' AND worker_pid IS NULL AND cancelled=0 AND deadline>?",
                                (os.getpid(), now + 45, now, job_id, now))
            return result.rowcount == 1

    def heartbeat(self, job_id):
        with self.database() as db:
            db.execute("UPDATE jobs SET lease_until=? WHERE id=? AND worker_pid=? AND status IN ('running','waiting_user')",
                       (time.time() + 45, job_id, os.getpid()))

    def get(self, job_id, session):
        with self.database() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND session=?', (job_id, session)).fetchone()
            if row and row['status'] in {'queued', 'running', 'waiting_user'}:
                limit = row['lease_until'] or row['created'] + 45
                if time.time() > limit or time.time() > row['deadline']:
                    db.execute("UPDATE jobs SET status='failed',stage='result_unknown',message=?,updated=? WHERE id=?",
                               ('更新进程已失联或超时；保留工作区及旧程序，需核对现场，不自动再次应用', time.time(), job_id))
                    row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('更新操作不存在或不属于当前会话')
        return dict(row)

    def list(self, session):
        with self.database() as db:
            rows = db.execute('SELECT id FROM jobs WHERE session=? ORDER BY created DESC LIMIT 20', (session,)).fetchall()
        return [self.get(row['id'], session) for row in rows]

    def cancel(self, job_id, session):
        job = self.get(job_id, session)
        if job['stage'] in {'switching', 'health_check', 'rolling_back'}:
            raise ValueError('正在原子切换或回退，请等待结果；不能中断一半')
        with self.database() as db:
            db.execute('UPDATE jobs SET cancelled=1,updated=? WHERE id=?', (time.time(), job_id))
        return self.get(job_id, session)
