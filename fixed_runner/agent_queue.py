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
        db.execute('''CREATE TABLE IF NOT EXISTS agent_batch_policies(
            batch_id TEXT PRIMARY KEY, stop_policy TEXT NOT NULL)''')

    @staticmethod
    def agent_queue_filter(paused):
        # Unrelated pending jobs stay paused. Revoked/expired batches never run,
        # including after a later global Resume. An explicit global Pause revokes
        # the pause exemption but preserves the batch for a normal Resume.
        active = "b.state='active'" if paused else "b.state IN ('active','paused')"
        scoped = ("EXISTS (SELECT 1 FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id "
                  "WHERE a.task_id=tasks.id AND " + active + " AND (EXISTS "
                  "(SELECT 1 FROM agent_batch_policies p WHERE p.batch_id=b.id AND p.stop_policy='round_count') OR b.deadline>?))")
        return scoped if paused else "(NOT EXISTS (SELECT 1 FROM agent_batch_tasks a WHERE a.task_id=tasks.id) OR " + scoped + ")"

    @staticmethod
    def expire_agent_batches(db):
        from task_store import now_iso
        db.execute("UPDATE agent_task_batches SET state='cancelled' WHERE deadline<=? AND state!='cancelled' AND id NOT IN "
                   "(SELECT batch_id FROM agent_batch_policies WHERE stop_policy='round_count')", (time.time(),))
        db.execute("UPDATE tasks SET status='cancelled',finished_at=?,error='agent_batch_expired_or_stopped; not replayed' WHERE status IN ('pending','waiting_model','waiting_user','waiting_device') AND id IN (SELECT a.task_id FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id WHERE b.state='cancelled')", (now_iso(),))

    def _check_agent_admission(self, db, devices, *, resume_stopped_devices=False, batch_id=None):
        self._expire_device_view_sessions(db)
        stopped = db.execute("SELECT config_json FROM automation_profiles WHERE name='automation-stop'").fetchone()
        if stopped and json.loads(stopped[0]).get('stopped'):
            raise ValueError('所有自动操作已停止，请先在设备页解除停止所有自动操作；未恢复任务')
        for device in devices:
            if db.execute("SELECT 1 FROM tasks WHERE device_id=? AND status IN ('pending','running','waiting_model','waiting_user','waiting_device') AND id NOT IN (SELECT task_id FROM agent_batch_tasks WHERE batch_id=?) LIMIT 1", (device, batch_id or '')).fetchone():
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
            batch = db.execute("SELECT b.*,COALESCE(p.stop_policy,'deadline') AS stop_policy FROM agent_task_batches b "
                               "LEFT JOIN agent_batch_policies p ON p.batch_id=b.id WHERE b.id=? AND b.session_id=?", (batch_id, session_id)).fetchone()
            if not batch or batch['state'] == 'cancelled' or (batch['stop_policy'] == 'deadline' and batch['deadline'] <= time.time()):
                raise ValueError('本批次已停止或过期，不能恢复或重放')
            devices = {row[0] for row in db.execute("SELECT DISTINCT device_id FROM tasks WHERE status IN ('pending','running','waiting_model','waiting_user','waiting_device') AND id IN (SELECT task_id FROM agent_batch_tasks WHERE batch_id=?)", (batch_id,))}
            self._check_agent_admission(db, devices, resume_stopped_devices=resume_stopped_devices, batch_id=batch_id)
            db.execute("UPDATE agent_task_batches SET state='active' WHERE id=?", (batch_id,))

    def submit_agent_batch(self, batch_id, session_id, tasks, *, fingerprint, deadline, resume_stopped_devices=False, stop_policy='deadline'):
        from task_store import now_iso
        tasks = list(tasks)
        if stop_policy not in {'deadline', 'round_count'}:
            raise ValueError('未知批次结束策略')
        if not tasks or len(tasks) > 1000 or (stop_policy == 'deadline' and
                (not isinstance(deadline, (int, float)) or not time.time() < deadline <= time.time() + 86400)):
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
            db.execute('INSERT INTO agent_task_batches(id,session_id,fingerprint,state,deadline,created) VALUES(?,?,?,?,?,?)',
                       (batch_id, session_id, fingerprint, 'active', deadline if stop_policy == 'deadline' else 0, time.time()))
            db.execute('INSERT INTO agent_batch_policies VALUES(?,?)', (batch_id,stop_policy))
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
            batch = db.execute("SELECT b.*,COALESCE(p.stop_policy,'deadline') AS stop_policy FROM agent_task_batches b "
                               "LEFT JOIN agent_batch_policies p ON p.batch_id=b.id WHERE b.id=? AND b.session_id=?", (batch_id, session_id)).fetchone()
            if not batch:
                return None
            rows = db.execute('SELECT t.id,t.status,t.device_id FROM tasks t JOIN agent_batch_tasks a ON a.task_id=t.id WHERE a.batch_id=? ORDER BY a.position', (batch_id,)).fetchall()
            tasks = [{**dict(row), 'progress': self.task_progress(row['id'])} for row in rows]
            counters = ('processed_slots', 'successful_slots', 'failed_slots', 'unavailable_slots', 'unknown_actions', 'skipped_slots')
            def total(items):
                return {'schema_supported': bool(items) and all(task['progress'].get('schema_supported') for task in items),
                        **{key: sum(task['progress'].get(key, 0) for task in items) for key in counters}}
            return {'batch_id': batch_id, 'state': batch['state'], 'stop_policy': batch['stop_policy'],
                    'deadline': batch['deadline'] if batch['stop_policy'] == 'deadline' else None,
                    'tasks': tasks, 'progress': total(tasks),
                    'device_progress': {device: total([task for task in tasks if task['device_id'] == device])
                                        for device in sorted({task['device_id'] for task in tasks})},
                    'task_ids': [row['id'] for row in rows]}

    def agent_task_may_run_paused(self, task_id):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id "
                "LEFT JOIN agent_batch_policies p ON p.batch_id=b.id WHERE a.task_id=? AND b.state='active' "
                "AND (p.stop_policy='round_count' OR b.deadline>?)", (task_id, time.time())).fetchone())

    def agent_task_stop_requested(self, task_id):
        with self.connection() as db:
            row = db.execute("SELECT b.state,b.deadline,COALESCE(p.stop_policy,'deadline') AS stop_policy "
                "FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id LEFT JOIN agent_batch_policies p ON p.batch_id=b.id "
                "WHERE a.task_id=?", (task_id,)).fetchone()
            return bool(row and (row['state'] == 'cancelled' or (row['stop_policy'] == 'deadline' and row['deadline'] <= time.time())))

    def stop_agent_session(self, session_id):
        from task_store import now_iso
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE agent_task_batches SET state='cancelled' WHERE session_id=?", (session_id,))
            stopped = db.execute("UPDATE tasks SET status='cancelled',finished_at=?,error='agent_session_stopped; not replayed' WHERE status IN ('pending','waiting_model','waiting_user','waiting_device') AND id IN (SELECT a.task_id FROM agent_batch_tasks a JOIN agent_task_batches b ON b.id=a.batch_id WHERE b.session_id=?) RETURNING id", (now_iso(), session_id)).fetchall()
            db.executemany('DELETE FROM task_waits WHERE task_id=?', [(row[0],) for row in stopped])
            return len(stopped)

    def control_agent_batch(self, batch_id, session_id, *, stop=False):
        from task_store import now_iso
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM agent_task_batches WHERE id=? AND session_id=?', (batch_id, session_id)).fetchone()
            if not row:
                raise ValueError('本会话没有该执行批次')
            if row['state'] != 'cancelled':
                db.execute('UPDATE agent_task_batches SET state=? WHERE id=?', ('cancelled' if stop else 'paused', batch_id))
            if stop:
                stopped = db.execute("UPDATE tasks SET status='cancelled',finished_at=?,error='agent_batch_stopped; not replayed' WHERE status IN ('pending','waiting_model','waiting_user','waiting_device') AND id IN (SELECT task_id FROM agent_batch_tasks WHERE batch_id=?) RETURNING id", (now_iso(), batch_id)).fetchall()
                db.executemany('DELETE FROM task_waits WHERE task_id=?', [(task[0],) for task in stopped])
        return {**self.agent_batch_receipt(batch_id, session_id),
                'message': '本批次已请求安全停止，当前动作在检查点收口' if stop else '本批次已暂停后续领取，当前视频仍可完成'}

    @staticmethod
    def close_agent_batch_after_failure(db, task_id):
        # A failed device is not a revoked batch. Keep every other device live,
        # block this device's first successor and preserve all historical states.
        row = db.execute("SELECT t.id FROM tasks t JOIN agent_batch_tasks a ON a.task_id=t.id "
                         "WHERE t.status='pending' AND t.device_id=(SELECT device_id FROM tasks WHERE id=?) "
                         "AND a.batch_id=(SELECT batch_id FROM agent_batch_tasks WHERE task_id=?) ORDER BY a.position LIMIT 1",
                         (task_id, task_id)).fetchone()
        if row:
            db.execute("UPDATE tasks SET status='waiting_device',error='previous_device_task_failed' WHERE id=?", (row[0],))
            db.execute("INSERT OR REPLACE INTO task_waits VALUES(?,?,'',NULL,?)", (row[0], 'previous_device_task_failed', time.time()))
