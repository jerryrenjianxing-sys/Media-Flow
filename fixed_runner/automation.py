"""Local automation admission, independent of the legacy chat/session host."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
import uuid

from agent_platform import bounded_diagnostic

READ_ACTIONS = frozenset(('platform_status', 'list_devices', 'list_tasks', 'plan_status',
    'task_evidence', 'incident_evidence', 'request_status', 'virtual_operation_status',
    'memory_list', 'memory_history', 'repair_list', 'repair_status', 'repair_files',
    'repair_read', 'repair_diff', 'repair_test_status', 'repair_update_status'))
WRITE_ACTIONS = frozenset(('plan_tasks', 'execute_plan', 'resume_plan', 'repreview_plan',
    'pause_batch', 'stop_batch', 'plan_virtual_operation', 'execute_virtual_operation',
    'memory_save', 'memory_restore', 'repair_create', 'repair_test', 'repair_validate',
    'repair_export', 'repair_close', 'repair_cancel', 'repair_prepare_apply', 'repair_apply',
    'repair_prepare_rollback', 'repair_cancel_update'))


def failure(code, message, *, status='blocked', request_id=None):
    return {'ok': False, 'api_version': '1', 'request_id': request_id, 'status': status,
        'reason_code': code, 'user_message': message, 'retryable': False,
        'operation_id': None, 'plan_id': None, 'result': None}


class RequestContext:
    """Adapt a durable API request to existing patch binding, never chat authority."""
    def __init__(self, request_id):
        self.request_id = request_id

    def require(self, session, action):
        return None

    def current(self, session):
        return {'id': self.request_id, 'source': 'local_automation'}

    def get(self, session):
        return {'revision': 0}


class AutomationService:
    def __init__(self, service):
        self.host = service
        self.root = Path(service.root) / 'automation'

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root/'receipts.db', timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, '
                       'action TEXT NOT NULL, session TEXT NOT NULL, created REAL NOT NULL, result TEXT)')
            with db:
                yield db
        finally:
            db.close()

    def catalog(self):
        def connected(action):
            if action.startswith('repair_'):
                return bool(self.host.repairs and self.host.repair_updates)
            if action.startswith('memory_'):
                return self.host.memory is not None
            if 'virtual_operation' in action:
                return self.host.operations is not None
            return action in {'platform_status', 'request_status'} or self.host.platform is not None
        return {'api_version': '1', 'status': 'available', 'session_role': 'correlation_only',
            'actions': [{'action': action, 'mutates': action in WRITE_ACTIONS,
                'request_id_required': action in WRITE_ACTIONS, 'connected': connected(action)}
                for action in sorted(READ_ACTIONS | WRITE_ACTIONS)],
            'native_file_actions': ['repair_edit', 'repair_revert', 'repair_delete'],
            'user_message': '本机业务接口；原生文件工具编辑修复工作区，设备动作仍由固定执行器执行'}

    def receipt(self, request_id):
        with self.database() as db:
            row = db.execute('SELECT result FROM requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            return failure('request_not_found', '未找到接入回执；不要猜测执行成功', request_id=request_id)
        if row['result']:
            return json.loads(row['result'])
        return failure('result_unknown', '请求已接收，结果尚未确认；查询原计划或操作，不要重放',
                       status='unknown', request_id=request_id)

    def call(self, body):
        if not isinstance(body, dict):
            return failure('invalid_arguments', '请求必须是JSON对象')
        action = body.get('action')
        if not isinstance(action, str) or action not in READ_ACTIONS | WRITE_ACTIONS:
            return failure('unsupported_action', '该业务尚未接入；没有执行操作')
        args = body.get('arguments', {})
        session = body.get('session_id') or 'skill-local'
        request_id = body.get('request_id')
        if (not isinstance(args, dict) or not isinstance(session, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session)):
            return failure('invalid_arguments', '参数必须为对象；会话关联编号格式无效')
        write = action in WRITE_ACTIONS
        if write and (not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id)):
            return failure('request_id_required', '状态写入需要稳定唯一request_id')
        if write:
            try:
                fingerprint = hashlib.sha256(json.dumps([action, args, session], sort_keys=True,
                    ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            except (ValueError, TypeError):
                return failure('invalid_arguments', '参数需要有效JSON值', request_id=request_id)
            with self.database() as db:
                db.execute('BEGIN IMMEDIATE')
                prior = db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
                if prior:
                    if prior['fingerprint'] != fingerprint:
                        return failure('request_id_conflict', '同一请求编号已用于不同参数；未执行新操作', request_id=request_id)
                    return json.loads(prior['result']) if prior['result'] else failure('result_unknown',
                        '请求已接收，结果尚未确认；查询原计划或操作，不要重放', status='unknown', request_id=request_id)
                db.execute('INSERT INTO requests VALUES(?,?,?,?,?,NULL)', (request_id, fingerprint, action, session, time.time()))
        context = {'session_id': session, 'call_id': 'automation:' + (request_id if write else uuid.uuid4().hex)}
        try:
            result = self.dispatch(action, args, context, request_id)
            operation = result.get('operation') or {}
            status = operation.get('status') or result.get('status') or result.get('state') or ('completed' if write else 'observed')
            if action in {'execute_plan', 'resume_plan'}:
                status = result.get('state', 'submitted')
            unknown = (operation.get('stage') or result.get('stage')) == 'result_unknown'
            status = 'unknown' if unknown else status
            blocked = status in {'blocked', 'failed', 'unknown', 'waiting_user', 'expired', 'cancelled'}
            message = (operation.get('message') or operation.get('error') or result.get('user_message')
                or result.get('message') or result.get('error') or '已读取实际业务回执')
            if status == 'blocked' and (result.get('preview') or {}).get('blockers'):
                message = '；'.join(result['preview']['blockers'])
            response = {'ok': not blocked, 'api_version': '1', 'request_id': request_id,
                'status': status, 'reason_code': result.get('reason_code') or ('result_unknown' if unknown else status),
                'user_message': message,
                'retryable': False if unknown else bool(result.get('retryable', False)),
                'operation_id': result.get('operation_id') or (result.get('operation') or {}).get('id')
                    or (result.get('id') if action.startswith('repair_') else None),
                'plan_id': result.get('plan_id') or result.get('batch_id'), 'result': result}
        except (ValueError, KeyError, TypeError) as exc:
            response = failure('business_rejected', bounded_diagnostic(exc), request_id=request_id)
        except Exception:
            response = failure('result_unknown' if write else 'read_failed',
                '请求结果未确认，请查询原计划/操作回执；不会自动重放' if write else '读取失败，请稍后重新查询',
                status='unknown' if write else 'failed', request_id=request_id)
        if write:
            with self.database() as db:
                db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(response, ensure_ascii=False), request_id))
        return response

    def dispatch(self, action, args, context, request_id):
        host, session = self.host, context['session_id']
        if action == 'request_status':
            return self.receipt(args.get('request_id'))
        if action == 'platform_status':
            snapshot = host.status_reader()
            return {'observed_at': time.time(), 'paused': bool(snapshot.get('paused')),
                'product_version': snapshot.get('product_version'), 'task_summary': snapshot.get('task_summary'),
                'devices': [{k: device.get(k) for k in ('device_id', 'friendly_name', 'state', 'device_type', 'environment_status')}
                    for device in snapshot.get('devices', [])]}
        if action.startswith('memory_'):
            if action == 'memory_list':
                return host.memory.list()
            if action == 'memory_history':
                return host.memory.history(args.get('id'))
            if action == 'memory_restore':
                return host.memory.restore(args.get('id'), args, session_id=session)
            return host.memory.save(args, source='local_automation', session_id=session)
        if action.startswith('repair_'):
            return self.repair(action, args, context, request_id)
        if 'virtual_operation' in action:
            if host.operations is None:
                raise ValueError('虚拟机操作入口尚未接入')
            if action == 'plan_virtual_operation':
                return host.operations.propose(args, context)
            command = host.operations.get(str(args.get('command_id') or ''), session)
            if action == 'virtual_operation_status':
                return command
            return host.operations.confirm(command['command_id'], session, {'confirmed': True,
                'fingerprint': command['fingerprint'], 'confirmation_name': args.get('confirmation_name')})
        if host.platform is None:
            raise ValueError('平台任务存储尚未接入')
        platform = host.platform
        if action == 'incident_evidence':
            from agent_evidence import read_incident
            if not host.evidence_root:
                raise ValueError('异常证据尚未接入')
            return read_incident(platform.store, host.evidence_root, args)
        if action in {'list_devices', 'list_tasks', 'task_evidence', 'plan_tasks'}:
            return platform.tools(action, args, context)
        if action == 'plan_status' and not args.get('plan_id'):
            return platform.plans(session)
        if action == 'repreview_plan':
            return platform.repreview(str(args.get('plan_id') or ''), session, {'request_id': request_id})
        plan = platform.get(str(args.get('plan_id') or ''), session)
        if action == 'plan_status':
            return plan
        if action in {'pause_batch', 'stop_batch'}:
            return platform.store.control_agent_batch(plan['plan_id'], session, stop=action == 'stop_batch')
        return platform.confirm(plan['plan_id'], session, {'confirmed': True, 'confirm_writes': True,
            'plan_hash': plan['preview']['plan_hash'], 'resume_stopped_devices': args.get('resume_stopped_devices') is True})

    def repair(self, action, args, context, request_id):
        repairs, updates, session = self.host.repairs, self.host.repair_updates, context['session_id']
        if repairs is None:
            raise ValueError('修复工作区尚未接入')
        repair_id = args.get('repair_id')
        if action in {'repair_prepare_apply', 'repair_apply', 'repair_prepare_rollback',
                      'repair_update_status', 'repair_cancel_update'}:
            if updates is None:
                raise ValueError('修复更新入口尚未接入')
            from agent_repair_updates import AgentRepairUpdates
            local = AgentRepairUpdates(updates.root, repairs, RequestContext(context['call_id']), launcher=updates.launcher)
            if action == 'repair_update_status':
                return local.get(args.get('operation_id'), session)
            if action == 'repair_cancel_update':
                return local.cancel(args.get('operation_id'), session)
            if action == 'repair_prepare_rollback':
                return local.rollback_candidate(args.get('operation_id'), context)
            return (local.prepare if action == 'repair_prepare_apply' else local.apply)(repair_id, session)
        if action == 'repair_list':
            return {'repairs': repairs.list(session)}
        if action == 'repair_status':
            result = repairs.get(repair_id, session)
            return {**result, 'workspace_path': str(repairs.workspace(repair_id, session))} if result['state'] == 'ready' else result
        if action == 'repair_create':
            result = repairs.create(args, context)
            return {**result, 'workspace_path': str(repairs.workspace(result['id'], session))} if result['state'] == 'ready' else result
        if action == 'repair_validate':
            return repairs.test({'repair_id': repair_id, 'mode': 'all'}, context)
        if action == 'repair_close':
            return repairs.close(repair_id, session)
        if action == 'repair_cancel':
            repairs.get(repair_id, session)
            repairs.cancel_session(session, repair_id)
            return repairs.get(repair_id, session)
        return repairs.tool(action, args, context)


def handle_automation_http(handler, method, path, body=None):
    """Same loopback/JSON boundary as the existing control surface."""
    origin = handler.headers.get('Origin')
    host = handler.headers.get('Host', '')
    try:
        hostname = urlsplit('http://' + host).hostname
    except ValueError:
        hostname = None
    if (hostname not in {'127.0.0.1', 'localhost', '::1'} or
            handler.client_address[0] not in {'127.0.0.1', '::1'} or
            origin not in {None, 'http://' + host, 'http://127.0.0.1:3000', 'http://localhost:3000'}):
        handler._json(failure('local_only', '仅接受本机MediaFlow页面请求'), 403)
        return
    if method == 'POST' and not handler.headers.get('Content-Type', '').startswith('application/json'):
        handler._json(failure('json_required', '需要JSON请求'), 415)
        return
    service = AutomationService(handler.agent_service())
    if method == 'GET':
        handler._json(service.catalog())
    else:
        response = service.call(body)
        code = 409 if response['reason_code'] == 'request_id_conflict' else (200 if response['ok'] else 400)
        if response['status'] == 'unknown':
            code = 202
        handler._json(response, code)
