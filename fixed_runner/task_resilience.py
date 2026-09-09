"""Durable progress and waits in the task database, not an alternate queue.

Every transition is fenced by the original task status. A model probe only
publishes health; it cannot claim tasks or clear a user's stop flag.
"""
import json
import time
import uuid

WAIT_STATUSES = ('waiting_model', 'waiting_device', 'waiting_user')
WAIT_SQL = "('waiting_model','waiting_device','waiting_user')"
VERSION = 'v1'
BACKOFF = (30, 60, 120, 300)


class TaskWaiting(RuntimeError):
    def __init__(self, status, reason, *, model_key='', result=None):
        super().__init__(reason)
        self.status, self.reason, self.model_key = status, reason, model_key
        self.result = result or {}


class ResilienceStoreMixin:
    @staticmethod
    def initialize_resilience(db):
        db.execute('''CREATE TABLE IF NOT EXISTS task_checkpoints(
            task_id TEXT PRIMARY KEY,state_json TEXT NOT NULL,updated_at REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS task_waits(
            task_id TEXT PRIMARY KEY,reason_code TEXT NOT NULL,model_key TEXT NOT NULL,
            next_check REAL,updated_at REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS task_action_blocks(
            scope TEXT NOT NULL,device_id TEXT NOT NULL,action TEXT NOT NULL,reason TEXT NOT NULL,
            PRIMARY KEY(scope,device_id,action))''')
        db.execute('''CREATE TABLE IF NOT EXISTS model_recovery_probes(
            model_key TEXT PRIMARY KEY,failures INTEGER NOT NULL,next_check REAL NOT NULL,
            token TEXT,lease_until REAL NOT NULL,success_at REAL,reason_code TEXT NOT NULL)''')

    def get_task_checkpoint(self, task_id):
        with self.connection() as db:
            row = db.execute('SELECT state_json FROM task_checkpoints WHERE task_id=?', (task_id,)).fetchone()
            return json.loads(row[0]) if row else {}

    @staticmethod
    def _save_checkpoint(db, task_id, state):
        db.execute('INSERT INTO task_checkpoints VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE '
                   'SET state_json=excluded.state_json,updated_at=excluded.updated_at',
                   (task_id, json.dumps(state, ensure_ascii=False), time.time()))

    def save_task_checkpoint(self, task_id, state):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row or row[0] != 'running':
                raise RuntimeError('执行租约已结束，不能写入检查点')
            self._save_checkpoint(db, task_id, state)

    @staticmethod
    def _action_scope(db, task_id):
        row = db.execute('SELECT t.device_id,COALESCE(a.batch_id,t.id) AS scope FROM tasks t '
                         'LEFT JOIN agent_batch_tasks a ON a.task_id=t.id WHERE t.id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('任务不存在')
        return row['scope'], row['device_id']

    def disabled_task_actions(self, task_id):
        with self.connection() as db:
            scope, device = self._action_scope(db, task_id)
            return [r[0] for r in db.execute('SELECT action FROM task_action_blocks WHERE scope=? AND device_id=? ORDER BY action', (scope, device))]

    def disable_task_action(self, task_id, action, reason='action_result_unknown'):
        with self.connection() as db:
            scope, device = self._action_scope(db, task_id)
            db.execute('INSERT OR IGNORE INTO task_action_blocks VALUES(?,?,?,?)', (scope, device, action, reason))

    def begin_task_action(self, task_id, *, slot, action):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM tasks WHERE id=? AND status='running'", (task_id,)).fetchone():
                raise RuntimeError('动作执行租约已失效')
            row = db.execute('SELECT state_json FROM task_checkpoints WHERE task_id=?', (task_id,)).fetchone()
            state = json.loads(row[0]) if row else {}
            if state.get('pending_action'):
                raise RuntimeError('上次动作结果未知，禁止重放')
            scope, device = self._action_scope(db, task_id)
            if db.execute('SELECT 1 FROM task_action_blocks WHERE scope=? AND device_id=? AND action=?', (scope, device, action)).fetchone():
                raise RuntimeError('本设备本批次的此项互动已暂停')
            state['pending_action'] = {'slot': slot, 'action': action, 'phase': state.get('summary', {}).get('feed_phase')}
            self._save_checkpoint(db, task_id, state)

    def finish_task_action(self, task_id, *, confirmed, outcome=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM tasks WHERE id=? AND status='running'", (task_id,)).fetchone():
                raise RuntimeError('动作结果租约已失效')
            row = db.execute('SELECT state_json FROM task_checkpoints WHERE task_id=?', (task_id,)).fetchone()
            state = json.loads(row[0]) if row else {}
            action = state.pop('pending_action', None)
            if action:
                state.setdefault('action_receipts', []).append({**action, 'confirmed': bool(confirmed), 'outcome': outcome})
                if confirmed and outcome:
                    name = {'like': 'likes', 'favorite': 'favorites', 'comment': 'comments_sent'}[action['action']]
                    counts = state.setdefault('confirmed_action_counts', {})
                    counts[name] = int(counts.get(name, 0)) + 1
                    if action.get('phase'):
                        phase = state.setdefault('confirmed_phase_actions', {}).setdefault(action['phase'], {})
                        phase[name] = int(phase.get(name, 0)) + 1
                if not confirmed:
                    self._mark_unknown_action(db, task_id, state, action)
                self._save_checkpoint(db, task_id, state)

    @classmethod
    def _mark_unknown_action(cls, db, task_id, state, action):
        scope, device = cls._action_scope(db, task_id)
        db.execute('INSERT OR IGNORE INTO task_action_blocks VALUES(?,?,?,?)', (scope, device, action['action'], 'action_result_unknown'))
        counts = state.setdefault('summary', {})
        counts['unknown_actions'] = int(counts.get('unknown_actions', 0)) + 1
        state.setdefault('unknown_action_receipts', []).append(action)

    def wait_task(self, task_id, status, reason, *, model_key='', now=None, result=None):
        if status not in WAIT_STATUSES:
            raise ValueError('未知等待状态')
        now = time.time() if now is None else now
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._terminalize_requested_stop(db, task_id):
                return False
            updated = db.execute('UPDATE tasks SET status=?,worker_id=NULL,error=?,result_json=COALESCE(?,result_json) '
                                 "WHERE id=? AND status='running'",
                                 (status, reason, json.dumps(result, ensure_ascii=False) if result is not None else None, task_id)).rowcount
            if updated != 1:
                return False
            next_check = now + 30 if status == 'waiting_model' else None
            db.execute('INSERT INTO task_waits VALUES(?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET '
                       'reason_code=excluded.reason_code,model_key=excluded.model_key,next_check=excluded.next_check,updated_at=excluded.updated_at',
                       (task_id, reason, model_key, next_check, now))
            if status == 'waiting_model':
                db.execute('INSERT OR IGNORE INTO model_recovery_probes VALUES(?,0,?,NULL,0,NULL,?)', (model_key, next_check, reason))
                # A health result predating this failure is not proof of recovery.
                db.execute('UPDATE model_recovery_probes SET success_at=NULL,next_check=MAX(next_check,?) WHERE model_key=? AND success_at IS NOT NULL',
                           (next_check, model_key))
            return True

    def _terminalize_requested_stop(self, db, task_id):
        """Stop wins over failure/recovery in the same lease-release transaction."""
        from task_store import now_iso
        row = db.execute("SELECT device_id FROM tasks WHERE id=? AND status='running'", (task_id,)).fetchone()
        if not row:
            return False
        device_stop = db.execute("SELECT 1 FROM system_state WHERE key=? AND value='1'", ('stop:' + row[0],)).fetchone()
        global_stop = db.execute("SELECT config_json FROM automation_profiles WHERE name='automation-stop'").fetchone()
        batch = db.execute("SELECT b.state,b.deadline,COALESCE(p.stop_policy,'deadline') AS policy FROM agent_batch_tasks a "
                           "JOIN agent_task_batches b ON b.id=a.batch_id LEFT JOIN agent_batch_policies p ON p.batch_id=b.id WHERE a.task_id=?", (task_id,)).fetchone()
        batch_stop = bool(batch and (batch['state'] == 'cancelled' or (batch['policy'] != 'round_count' and batch['deadline'] <= time.time())))
        if not (device_stop or (global_stop and json.loads(global_stop[0]).get('stopped')) or batch_stop):
            return False
        checkpoint = db.execute('SELECT state_json FROM task_checkpoints WHERE task_id=?', (task_id,)).fetchone()
        if checkpoint:
            state = json.loads(checkpoint[0])
            pending = state.pop('pending_action', None)
            if pending:
                self._mark_unknown_action(db, task_id, state, pending)
                self._save_checkpoint(db, task_id, state)
        db.execute('UPDATE tasks SET status=?,worker_id=NULL,finished_at=?,error=? WHERE id=?',
                   ('cancelled' if batch_stop else 'stopped', now_iso(),
                    'agent_batch_expired_or_stopped; not replayed' if batch_stop else 'stopped_by_user', task_id))
        db.execute('DELETE FROM task_waits WHERE task_id=?', (task_id,))
        return True

    def waiting_task(self, device_id):
        with self.connection() as db:
            row = db.execute('SELECT t.id,t.status,t.device_id,w.reason_code,w.model_key,w.next_check,w.updated_at '
                             'FROM tasks t JOIN task_waits w ON w.task_id=t.id WHERE t.device_id=? AND t.status IN ' + WAIT_SQL +
                             ' ORDER BY t.created_at,t.rowid LIMIT 1', (device_id,)).fetchone()
            return dict(row) if row else None

    def task_progress(self, task_id):
        with self.connection() as db:
            row = db.execute('SELECT t.status,t.device_id,t.payload_json,t.result_json,w.reason_code,w.model_key,w.next_check,w.updated_at,c.state_json '
                             ',c.updated_at AS checkpoint_at FROM tasks t LEFT JOIN task_waits w ON w.task_id=t.id '
                             'LEFT JOIN task_checkpoints c ON c.task_id=t.id WHERE t.id=?', (task_id,)).fetchone()
            if not row:
                return {}
            state = json.loads(row['state_json']) if row['state_json'] else {}
            summary = json.loads(row['result_json']) if row['result_json'] else {}
            if row['status'] in ('pending', 'running', *WAIT_STATUSES):
                summary = {**(summary or {}), **state.get('summary', {})}
            elif json.loads(row['payload_json']).get('resilience_version') == VERSION:
                checkpoint_summary = state.get('summary', {})
                if 'processed_slots' not in (summary or {}):
                    summary = {**checkpoint_summary, **(summary or {})}
                elif row['status'] in ('stopped', 'cancelled') and int(checkpoint_summary.get('processed_slots', -1)) >= int(summary['processed_slots']):
                    # A previous wait's result can survive resume. Slots never
                    # decrease within this task; use its latest checkpoint on
                    # interruption, including unknown writes at the same slot.
                    # finish() results ahead of the checkpoint remain intact.
                    summary = {**summary, **checkpoint_summary}
            summary = summary or {}
            next_check = row['next_check']
            if row['status'] == 'waiting_model' and row['model_key']:
                probe = db.execute('SELECT next_check FROM model_recovery_probes WHERE model_key=?', (row['model_key'],)).fetchone()
                if probe:
                    next_check = probe[0]
            active_wait = row['status'] in WAIT_STATUSES
            return {'schema_supported': 'processed_slots' in summary or json.loads(row['payload_json']).get('resilience_version') == VERSION, **{name: int(summary.get(name, 0)) for name in (
                        'processed_slots', 'successful_slots', 'failed_slots', 'unavailable_slots', 'unknown_actions')},
                    'skipped_slots': int(summary.get('known_safe_skips', 0)),
                    'waiting_reason': row['reason_code'] if active_wait else None,
                    'next_check_at': next_check if active_wait else None,
                    'last_progress_at': row['checkpoint_at'] or row['updated_at'], 'next_slot': state.get('next_slot'),
                    'affected_device': row['device_id'], 'affected_capabilities': self.disabled_task_actions(task_id),
                    'available_actions': ['resume_task', 'stop_task'] if active_wait else [],
                    'evidence_dirs': state.get('evidence_dirs', [])}

    def resilient_worker_devices(self):
        with self.connection() as db:
            return [row[0] for row in db.execute(
                "SELECT DISTINCT t.device_id FROM tasks t JOIN agent_batch_tasks a ON a.task_id=t.id "
                "JOIN agent_task_batches b ON b.id=a.batch_id LEFT JOIN agent_batch_policies p ON p.batch_id=b.id "
                "WHERE json_extract(t.payload_json,'$.resilience_version')='v1' "
                "AND t.status IN ('pending','running','waiting_model','waiting_device','waiting_user') "
                "AND b.state='active' AND (p.stop_policy='round_count' OR b.deadline>?)", (time.time(),))]

    @staticmethod
    def _waiting_blocks(db, device_id):
        return bool(db.execute('SELECT 1 FROM tasks WHERE device_id=? AND status IN ' + WAIT_SQL + ' LIMIT 1', (device_id,)).fetchone())

    def resume_waiting_task(self, task_id, *, model_key=None, now=None):
        from task_store import now_iso
        now = time.time() if now is None else now
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.expire_agent_batches(db)
            self._expire_device_view_sessions(db)
            row = db.execute('SELECT t.*,w.model_key,w.updated_at AS waiting_since FROM tasks t '
                             'LEFT JOIN task_waits w ON w.task_id=t.id WHERE t.id=?', (task_id,)).fetchone()
            if not row or row['status'] not in WAIT_STATUSES:
                return False
            if db.execute("SELECT 1 FROM system_state WHERE key=? AND value='1'", ('stop:' + row['device_id'],)).fetchone():
                return False
            stopped = db.execute("SELECT config_json FROM automation_profiles WHERE name='automation-stop'").fetchone()
            if stopped and json.loads(stopped[0]).get('stopped'):
                return False
            if db.execute("SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' AND status IN ('created','connected','disconnected')", (row['device_id'],)).fetchone():
                return False
            batch = db.execute('SELECT b.state,b.deadline,COALESCE(p.stop_policy,\'deadline\') AS policy FROM agent_batch_tasks a '
                               'JOIN agent_task_batches b ON b.id=a.batch_id LEFT JOIN agent_batch_policies p ON p.batch_id=b.id WHERE a.task_id=?', (task_id,)).fetchone()
            if batch and (batch['state'] != 'active' or (batch['policy'] != 'round_count' and batch['deadline'] <= now)):
                return False
            if row['status'] in ('waiting_model', 'waiting_user') and row['model_key']:
                from model_recovery import model_configuration_key
                key = model_configuration_key()
                if row['model_key'] != key or (model_key and model_key != key):
                    return False
                probe = db.execute('SELECT success_at FROM model_recovery_probes WHERE model_key=?', (key,)).fetchone()
                if not probe or not probe[0] or probe[0] < row['waiting_since']:
                    return False
                checkpoint = db.execute('SELECT state_json FROM task_checkpoints WHERE task_id=?', (task_id,)).fetchone()
                if checkpoint:
                    state = json.loads(checkpoint[0])
                    state['model_failure_streak'] = 0
                    self._save_checkpoint(db, task_id, state)
            checkpoint = db.execute('SELECT state_json FROM task_checkpoints WHERE task_id=?', (task_id,)).fetchone()
            state = json.loads(checkpoint[0]) if checkpoint else {}
            state['resume_requires_verification'] = True
            self._save_checkpoint(db, task_id, state)
            db.execute("UPDATE tasks SET status='pending',worker_id=NULL,error=NULL,not_before=? WHERE id=?", (now_iso(), task_id))
            db.execute('DELETE FROM task_waits WHERE task_id=?', (task_id,))
            return True

    def get_model_probe(self, model_key):
        with self.connection() as db:
            row = db.execute('SELECT * FROM model_recovery_probes WHERE model_key=?', (model_key,)).fetchone()
            return dict(row) if row else None

    def claim_model_probe(self, model_key, *, now=None):
        now = time.time() if now is None else now
        token = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE model_recovery_probes SET token=?,lease_until=? WHERE model_key=? '
                                 'AND next_check<=? AND lease_until<=? AND success_at IS NULL',
                                 (token, now + 90, model_key, now, now)).rowcount
            return token if changed else None

    def finish_model_probe(self, model_key, token, *, success, reason='', permanent=False, now=None):
        now = time.time() if now is None else now
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT failures FROM model_recovery_probes WHERE model_key=? AND token=? AND lease_until>?', (model_key, token, now)).fetchone()
            if not row:
                return False
            failures = 0 if success else row[0] + 1
            next_check = now if success else now + BACKOFF[min(failures, len(BACKOFF) - 1)]
            db.execute('UPDATE model_recovery_probes SET failures=?,next_check=?,token=NULL,lease_until=0,success_at=?,reason_code=? WHERE model_key=?',
                       (failures, next_check, now if success else None, reason, model_key))
            db.execute('UPDATE task_waits SET next_check=?,reason_code=? WHERE model_key=? AND task_id IN '
                       "(SELECT id FROM tasks WHERE status='waiting_model')", (None if permanent else next_check, reason, model_key))
            if permanent:
                db.execute("UPDATE tasks SET status='waiting_user',error=? WHERE status='waiting_model' AND id IN (SELECT task_id FROM task_waits WHERE model_key=?)", (reason, model_key))
            return True

    def recover_resilient_task(self, db, task_id):
        """Called only after a proved dead worker; legacy records stay legacy."""
        row = db.execute("SELECT payload_json FROM tasks WHERE id=? AND status='running'", (task_id,)).fetchone()
        if not row or json.loads(row[0]).get('resilience_version') != VERSION:
            return False
        if self._terminalize_requested_stop(db, task_id):
            return True
        cp = db.execute('SELECT state_json FROM task_checkpoints WHERE task_id=?', (task_id,)).fetchone()
        state = json.loads(cp[0]) if cp else {}
        action = state.pop('pending_action', None)
        if action:
            self._mark_unknown_action(db, task_id, state, action)
        state.setdefault('recovery_history', []).append({'reason': 'worker_interrupted', 'at': time.time(),
                                                        'unknown_action': action})
        state['resume_requires_verification'] = True
        self._save_checkpoint(db, task_id, state)
        db.execute("UPDATE tasks SET status='waiting_device',worker_id=NULL,error='worker_interrupted; progress preserved' WHERE id=?", (task_id,))
        db.execute('INSERT OR REPLACE INTO task_waits VALUES(?,?,?,NULL,?)', (task_id, 'worker_interrupted', '', time.time()))
        return True
