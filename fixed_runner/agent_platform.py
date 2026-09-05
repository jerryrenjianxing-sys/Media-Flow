"""Narrow platform tool facade; reuses planning and the existing task store."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from control_config import normalized_config, PRESET_FIELDS, build_scheduled_plan, inspection_profiles_for_store
from run_planning import build_preview


def bounded_diagnostic(value):
    text = str(value or '')[:4000]
    text = re.sub(r'sk-(?:or-v1-|sp-|proj-)?[A-Za-z0-9._-]{16,}', '[凭证已隐藏]', text)
    text = re.sub(r'[A-Za-z]:[\\/][^\s\"\'<>]+', '[本机路径]', text)
    return text[:1000]


class AgentPlatform:
    def __init__(self, root, store, status_reader, *, model_status_reader=lambda: {}, worker_launcher=None):
        self.root, self.store = Path(root), store
        self.status_reader, self.model_status_reader = status_reader, model_status_reader
        self.worker_launcher = worker_launcher

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'plans.db', timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, session_id TEXT,
                call_id TEXT UNIQUE, config TEXT, preview TEXT, identity TEXT, state TEXT, deadline REAL,
                created REAL, result TEXT); CREATE INDEX IF NOT EXISTS idx_agent_plan_session ON plans(session_id,created);''')
            with db:
                yield db
        finally:
            db.close()

    def tools(self, name, args, context):
        if name == 'list_devices':
            snapshot = self.status_reader()
            virtual = (snapshot.get('virtualization') or {}).get('devices') or []
            fields = ('virtual_device_id', 'name', 'state', 'provider_instance_id', 'adb_endpoint', 'connection_status',
                      'environment_status', 'capabilities', 'user_message', 'available_actions')
            return {'devices': [{k: row.get(k) for k in fields} for row in virtual[:100]],
                    'online': [{k: row.get(k) for k in ('device_id', 'device_type', 'friendly_name', 'state', 'environment_status', 'capabilities')}
                               for row in snapshot.get('devices', [])[:100]]}
        if name == 'list_tasks':
            limit, offset = args.get('limit', 10), args.get('offset', 0)
            if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or not 0 <= offset <= 100000:
                raise ValueError('分页参数无效')
            tasks = self.store.list(limit, offset)
            return {'tasks': [self._task(row) for row in tasks], 'offset': offset, 'limit': limit,
                    'counts': self.store.task_status_counts()}
        if name == 'task_evidence':
            task_id = args.get('task_id')
            if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', task_id):
                raise ValueError('请指定有效任务编号')
            task = self.store.get(task_id)
            incidents = self.store.list_incidents_for_tasks([task_id])
            return {'task': self._task(task), 'incidents': [{
                'id': row.id, 'stage': row.stage, 'reason_code': row.error_type,
                'message': bounded_diagnostic(row.error_message), 'outcome': row.outcome,
                'recovery_action': bounded_diagnostic(row.recovery_action), 'has_screenshot': bool(row.screenshot_path),
                'has_ui_tree': bool(row.ui_tree_path), 'analysis_status': row.analysis_status,
                'evidence_url': '/api/incident-image?id=' + row.id if row.screenshot_path else None,
            } for row in incidents[:30]], 'evidence_status': 'available' if incidents else 'not_captured_historical',
                'message': '仅返回已有证据，不重放任务、不重新分析全部历史；没有现场时不能推断根因'}
        if name == 'plan_tasks':
            return self.plan(args, context)
        raise ValueError('此平台工具尚未接入，未执行操作')

    @staticmethod
    def _task(task):
        return {'task_id': task.id, 'device_id': task.device_id, 'task_type': task.task_type,
                'status': task.status, 'created_at': task.created_at, 'finished_at': task.finished_at,
                'error': bounded_diagnostic(task.error), 'round_index': task.payload.get('round_index'),
                'video_count': task.payload.get('video_count')}

    @staticmethod
    def identities(snapshot, device_ids):
        virtual = (snapshot.get('virtualization') or {}).get('devices') or []
        result = {}
        for endpoint in device_ids:
            row = next((item for item in virtual if item.get('adb_endpoint') == endpoint), None)
            if not row:
                raise ValueError('所选设备未找到当前MuMu实例映射，请刷新设备后重新预览')
            result[endpoint] = {key: row.get(key) for key in ('virtual_device_id', 'provider_install_id', 'provider_instance_id', 'android_identity')}
        return result

    def plan(self, args, context):
        raw = args.get('config')
        if not isinstance(raw, dict):
            raise ValueError('请提供任务参数')
        allowed = set(PRESET_FIELDS) | {'device_ids', 'preview_only', 'seed', 'engagement_inspection_enabled', 'inspection_every_rounds'}
        if set(raw) - allowed:
            raise ValueError('任务参数包含不支持的内部字段')
        for key in ('engagement_inspection_enabled', 'preview_only', 'topic_filter_enabled', 'search_trust_results', 'comment_policy_enabled'):
            if key in raw and type(raw[key]) is not bool:
                raise ValueError('任务开关必须是明确的布尔值')
        required = {'device_ids', 'video_count', 'round_count', 'content_mode', 'engagement_inspection_enabled'}
        missing = sorted(required - raw.keys())
        if raw.get('content_mode') in {'search', 'hybrid'} and not raw.get('search_query'):
            missing.append('search_query')
        if raw.get('content_mode') == 'mixed' and not raw.get('topic_prompt'):
            missing.append('topic_prompt')
        if raw.get('engagement_inspection_enabled') and 'inspection_every_rounds' not in raw:
            missing.append('inspection_every_rounds')
        if missing:
            return {'status': 'waiting_user', 'reason_code': 'missing_parameters', 'missing': missing,
                    'user_message': '请补齐这些关键参数；未创建任务'}
        # Do not inherit the user's old high-write draft or hidden write defaults.
        defaults = {key: 0 for key in ('like_probability', 'favorite_probability', 'comment_probability',
                                       'matched_like_probability', 'matched_favorite_probability', 'matched_comment_probability')}
        config = normalized_config({**defaults, 'preview_only': True, **raw})
        selected = config['device_ids']
        if not selected:
            raise ValueError('请选择要运行的虚拟机')
        snapshot = self.status_reader()
        if any(row.get('device_type') != 'virtual' for row in snapshot.get('devices', []) if row.get('device_id') in selected):
            raise ValueError('本版本Agent任务仅使用MuMu虚拟机')
        identity = self.identities(snapshot, selected)
        draft = {'revision': 1, 'config': config}
        preview = build_preview(self.store, draft, devices=snapshot.get('devices', []), paused=self.store.is_paused(),
                                model_status=self.model_status_reader())
        if self.store.is_paused():
            preview['warnings'] = [w for w in preview['warnings'] if '任务领取当前已暂停' not in w]
            preview['warnings'].append('确认后只执行本次计划，其他任务继续暂停；再次点击平台暂停会暂停本次计划')
        # Never silently drop an unavailable device from a user-confirmed scope.
        if set(preview['eligible_device_ids']) != set(selected):
            preview['ready'] = False
            preview['blockers'].append('部分所选虚拟机不可用，请处理后重新预览；不会自动缩减设备范围')
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id FROM plans WHERE call_id=?', (context['call_id'],)).fetchone()
            if row:
                return self.get(row['id'], context['session_id'])
            plan_id = uuid.uuid4().hex
            db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,NULL)', (plan_id, context['session_id'], context['call_id'],
                json.dumps(config, ensure_ascii=False), json.dumps(preview, ensure_ascii=False), json.dumps(identity, sort_keys=True),
                'awaiting_confirmation' if preview['ready'] else 'blocked', time.time() + 600, time.time()))
        return self.get(plan_id, context['session_id'])

    def get(self, plan_id, session_id):
        with self.database() as db:
            row = db.execute('SELECT * FROM plans WHERE id=? AND session_id=?', (plan_id, session_id)).fetchone()
        if not row:
            raise ValueError('计划不存在或不属于当前会话')
        state = 'expired' if row['state'] == 'awaiting_confirmation' and row['deadline'] < time.time() else row['state']
        return {'plan_id': row['id'], 'session_id': session_id, 'state': state, 'deadline': row['deadline'],
                'config': json.loads(row['config']), 'preview': json.loads(row['preview']),
                'result': self.store.agent_batch_receipt(plan_id, session_id) or (json.loads(row['result']) if row['result'] else None),
                'message': '计划已保存，尚未提交；需要用户在首页确认后执行' if state == 'awaiting_confirmation' else '请查看计划状态和阻断原因'}

    def plans(self, session_id):
        with self.database() as db:
            rows = db.execute('SELECT id FROM plans WHERE session_id=? ORDER BY created DESC LIMIT 20', (session_id,)).fetchall()
        return {'plans': [self.get(row['id'], session_id) for row in rows]}

    def confirm(self, plan_id, session_id, body):
        """UI-only approval; a model tool cannot invoke this route."""
        plan = self.get(plan_id, session_id)
        if body.get('plan_hash') != plan['preview']['plan_hash'] or body.get('confirmed') is not True:
            raise ValueError('请核对这份计划并明确确认')
        if plan['preview'].get('requires_confirmation') and body.get('confirm_writes') is not True:
            raise ValueError('此计划包含真实互动，请勾选明确确认；否则不会执行')
        if self.worker_launcher is None:
            raise ValueError('固定执行程序启动入口不可用，尚未提交任务')
        receipt = self.store.agent_batch_receipt(plan_id, session_id)
        if not receipt:
            if plan['state'] != 'awaiting_confirmation':
                raise ValueError('计划已过期、取消或受阻，请重新生成计划')
            snapshot = self.status_reader()
            config = plan['config']
            current_identity = self.identities(snapshot, config['device_ids'])
            with self.database() as db:
                identity = json.loads(db.execute('SELECT identity FROM plans WHERE id=?', (plan_id,)).fetchone()[0])
            if current_identity != identity:
                raise ValueError('设备身份或连接发生变化，请重新预览；未提交任务')
            preview = build_preview(self.store, {'revision': 1, 'config': config}, devices=snapshot.get('devices', []),
                                    paused=self.store.is_paused(), model_status=self.model_status_reader())
            if not preview['ready'] or preview['plan_hash'] != body['plan_hash'] or set(preview['eligible_device_ids']) != set(config['device_ids']):
                raise ValueError('设备或模型条件已变化，请重新预览：' + '；'.join(preview['blockers']))
            config = {**config, 'run_draft_snapshot': {'revision': 1, 'plan_hash': preview['plan_hash'],
                       'source': 'agent_confirmed_plan', 'write_actions': preview['write_actions'], 'comment_mode': preview['comment_mode']}}
            revision = self.store.get_content_plan_revision(config['content_plan_revision_id']) if config.get('content_plan_revision_id') else None
            scheduled = build_scheduled_plan(config, submission_id=plan_id, plan_revision=revision,
                                            inspection_profiles=inspection_profiles_for_store(self.store))
            duration = max(3600, int(preview.get('estimated_seconds') or 0) * 2 + 1800)
            if duration > 86400:
                raise ValueError('这份计划超过一天，请拆分成较短的批次')
            self.store.submit_agent_batch(plan_id, session_id, scheduled.tasks,
                                          fingerprint=preview['plan_hash'], deadline=time.time() + duration)
            # A crash between these two databases is reconciled using the unique
            # batch receipt, never by re-creating the scheduled tasks.
            with self.database() as db:
                db.execute("UPDATE plans SET state='submitted' WHERE id=?", (plan_id,))
            receipt = self.store.agent_batch_receipt(plan_id, session_id)
        if receipt['state'] == 'cancelled' or receipt['deadline'] <= time.time():
            return {**receipt, 'message': '本次执行已停止或到期，不会自动重新执行'}
        try:
            self.worker_launcher(sorted({row['device_id'] for row in receipt['tasks'] if row['status'] == 'pending'}))
            message = '计划已交给固定执行程序；查看任务回执了解实际结果，尚未声明任务成功'
        except Exception:
            message = '任务已保存，但执行者启动未确认；可再次点击确认恢复执行者，不会重复创建任务'
        return {**receipt, 'message': message}

    def stop_session(self, session_id):
        count = self.store.stop_agent_session(session_id)
        with self.database() as db:
            db.execute("UPDATE plans SET state='cancelled' WHERE session_id=? AND state IN ('awaiting_confirmation','blocked')", (session_id,))
        return count
