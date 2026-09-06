"""Transactional, session-owned task batches; existing Workers remain the executor."""
import json
import time
import uuid


class AgentQueueMixin:
    @staticmethod
    def initialize_agent_queue(db):
        db.execute('''CREATE TABLE IF NOT EXISTS agent_task_batches(
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            state TEXT NOT NULL, deadline REAL NOT NULL, created REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS agent_batch_tasks(
            task_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, position INTEGER NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_agent_batch_tasks ON agent_batch_tasks(batch_id,position)')

    @staticmethod
    def agent_queue_filter(paused):
        # Unrelated pending jobs stay paused. Revoked/expired batches never run,
        # including after a later global Resume. An explicit global Pause revokes
        # the pause exemption but preserves the batch for a normal Resume.
        active = "b.state='active'" if paused else "b.state IN ('active','paused')"
        scoped = ("EXISTS (SELECT 1 FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id "
                  "WHERE a.task_id=tasks.id AND " + active + " AND b.deadline>?)")
        return scoped if paused else "(NOT EXISTS (SELECT 1 FROM agent_batch_tasks a WHERE a.task_id=tasks.id) OR " + scoped + ")"

    @staticmethod
    def expire_agent_batches(db):
        from task_store import now_iso
        db.execute("UPDATE agent_task_batches SET state='cancelled' WHERE deadline<=? AND state!='cancelled'", (time.time(),))
        db.execute("UPDATE tasks SET status='cancelled',finished_at=?,error='agent_batch_expired_or_stopped; not replayed' WHERE status='pending' AND id IN (SELECT a.task_id FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id WHERE b.state='cancelled')", (now_iso(),))

    def _check_agent_admission(self, db, devices, *, resume_stopped_devices=False, batch_id=None):
        self._expire_device_view_sessions(db)
        stopped = db.execute("SELECT config_json FROM automation_profiles WHERE name='automation-stop'").fetchone()
        if stopped and json.loads(stopped[0]).get('stopped'):
            raise ValueError('所有自动操作已停止，请先在设备页解除停止所有自动操作；未恢复任务')
        for device in devices:
            if db.execute("SELECT 1 FROM tasks WHERE device_id=? AND status IN ('pending','running') AND id NOT IN (SELECT task_id FROM agent_batch_tasks WHERE batch_id=?) LIMIT 1", (device, batch_id or '')).fetchone():
                raise ValueError('所选设备已有其他排队或运行任务，请处理后重新确认')
            if db.execute("SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' AND status IN ('created','connected','disconnected')", (device,)).fetchone():
                raise ValueError('设备正在人工接管，请退出后重新确认')
            if db.execute("SELECT 1 FROM device_initializations WHERE device_id=? AND status IN ('queued','running')", (device,)).fetchone():
                raise ValueError('设备正在准备，请完成或取消后重新确认')
            if not resume_stopped_devices and db.execute("SELECT 1 FROM system_state WHERE key=? AND value='1'", ('stop:' + device,)).fetchone():
                raise ValueError('所选设备的任务保留安全停止标志，不代表虚拟机关机；请勾选恢复本计划设备后确认')
        if resume_stopped_devices:
            for device in devices:
                db.execute('DELETE FROM system_state WHERE key=?', ('stop:' + device,))

    def resume_agent_batch(self, batch_id, session_id, *, resume_stopped_devices=False):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = db.execute('SELECT * FROM agent_task_batches WHERE id=? AND session_id=?', (batch_id, session_id)).fetchone()
            if not batch or batch['state'] == 'cancelled' or batch['deadline'] <= time.time():
                raise ValueError('本批次已停止或过期，不能恢复或重放')
            devices = {row[0] for row in db.execute("SELECT DISTINCT device_id FROM tasks WHERE status IN ('pending','running') AND id IN (SELECT task_id FROM agent_batch_tasks WHERE batch_id=?)", (batch_id,))}
            self._check_agent_admission(db, devices, resume_stopped_devices=resume_stopped_devices, batch_id=batch_id)
            db.execute("UPDATE agent_task_batches SET state='active' WHERE id=?", (batch_id,))

    def submit_agent_batch(self, batch_id, session_id, tasks, *, fingerprint, deadline, resume_stopped_devices=False):
        from task_store import now_iso
        tasks = list(tasks)
        if not tasks or len(tasks) > 1000 or not time.time() < deadline <= time.time() + 86400:
            raise ValueError('本次计划为空、过大或执行期限无效，请重新规划')
        for item in tasks:
            self.validate_payload(item.task_type, item.payload)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM agent_task_batches WHERE id=?', (batch_id,)).fetchone()
            if existing:
                if existing['session_id'] != session_id or existing['fingerprint'] != fingerprint:
                    raise ValueError('同一计划不能改换参数后重复提交')
                return [r[0] for r in db.execute('SELECT task_id FROM agent_batch_tasks WHERE batch_id=? ORDER BY position', (batch_id,))]
            self._check_agent_admission(db, {item.device_id for item in tasks}, resume_stopped_devices=resume_stopped_devices)
            db.execute('INSERT INTO agent_task_batches VALUES(?,?,?,?,?,?)',
                       (batch_id, session_id, fingerprint, 'active', deadline, time.time()))
            result = []
            for position, item in enumerate(tasks):
                task_id = uuid.uuid4().hex
                db.execute("INSERT INTO tasks(id,task_type,device_id,payload_json,status,created_at,not_before) VALUES(?,?,?,?,'pending',?,?)",
                           (task_id, item.task_type, item.device_id, json.dumps(item.payload, ensure_ascii=False), now_iso(), item.not_before))
                db.execute('INSERT INTO agent_batch_tasks VALUES(?,?,?)', (task_id, batch_id, position))
                result.append(task_id)
            return result

    def agent_batch_receipt(self, batch_id, session_id):
        with self.connection() as db:
            self.expire_agent_batches(db)
            batch = db.execute('SELECT * FROM agent_task_batches WHERE id=? AND session_id=?', (batch_id, session_id)).fetchone()
            if not batch:
                return None
            rows = db.execute('SELECT t.id,t.status,t.device_id FROM tasks t JOIN agent_batch_tasks a ON a.task_id=t.id WHERE a.batch_id=? ORDER BY a.position', (batch_id,)).fetchall()
            return {'batch_id': batch_id, 'state': batch['state'], 'deadline': batch['deadline'],
                    'tasks': [dict(row) for row in rows], 'task_ids': [row['id'] for row in rows]}

    def agent_task_may_run_paused(self, task_id):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id WHERE a.task_id=? AND b.state='active' AND b.deadline>?", (task_id, time.time())).fetchone())

    def agent_task_stop_requested(self, task_id):
        with self.connection() as db:
            row = db.execute('SELECT b.state,b.deadline FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id WHERE a.task_id=?', (task_id,)).fetchone()
            return bool(row and (row['state'] == 'cancelled' or row['deadline'] <= time.time()))

    def stop_agent_session(self, session_id):
        from task_store import now_iso
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE agent_task_batches SET state='cancelled' WHERE session_id=?", (session_id,))
            return db.execute("UPDATE tasks SET status='cancelled',finished_at=?,error='agent_session_stopped; not replayed' WHERE status='pending' AND id IN (SELECT a.task_id FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id WHERE b.session_id=?)", (now_iso(), session_id)).rowcount

    @staticmethod
    def close_agent_batch_after_failure(db, task_id):
        from task_store import now_iso
        db.execute("UPDATE agent_task_batches SET state='cancelled' WHERE id IN (SELECT batch_id FROM agent_batch_tasks WHERE task_id=?)", (task_id,))
        db.execute("UPDATE tasks SET status='cancelled',finished_at=?,error='agent_batch_failed; remaining tasks not replayed' WHERE status='pending' AND id IN (SELECT a.task_id FROM agent_batch_tasks a WHERE a.batch_id IN (SELECT batch_id FROM agent_batch_tasks WHERE task_id=?))", (now_iso(), task_id))
