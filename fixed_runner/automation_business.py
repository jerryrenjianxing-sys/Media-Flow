"""Thin automation adapters for existing content, model, and notification services."""
from __future__ import annotations

from typing import Any


READ_ACTIONS = frozenset({
    'content_plan_list', 'content_plan_get', 'preset_list', 'model_status',
    'notification_list',
})
WRITE_ACTIONS = frozenset({
    'content_plan_save', 'content_plan_archive', 'preset_save', 'model_test',
    'model_activate', 'notification_acknowledge',
})
ACTIONS = READ_ACTIONS | WRITE_ACTIONS
PROVIDERS = frozenset({'openrouter', 'qwen_token_plan'})
_SECRET_FIELDS = frozenset({'api_key', 'key', 'key_ref', 'active_key', 'authorization'})


def dispatch(host: Any, action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call the canonical service; no storage or model transport is duplicated here."""
    platform = getattr(host, 'platform', None)
    store = getattr(platform, 'store', None)
    if store is None:
        raise ValueError('平台业务存储尚未接入')
    if action == 'content_plan_list':
        _only(args, {'include_archived'})
        include_archived = args.get('include_archived', False)
        if type(include_archived) is not bool:
            raise ValueError('include_archived必须是布尔值')
        return {'content_plans': store.list_content_plans(include_archived=include_archived)}
    if action == 'content_plan_get':
        _only(args, {'content_plan_id', 'revision_id'})
        plan_id = _identifier(args.get('content_plan_id'), '请选择内容计划')
        revision_id = _identifier(args.get('revision_id'), '请选择内容计划版本')
        revision = store.get_content_plan_revision(revision_id)
        if revision['plan_id'] != plan_id:
            raise ValueError('内容计划与版本不匹配')
        return {'content_plan': revision}
    if action == 'content_plan_save':
        _only(args, {'content_plan_id', 'document'})
        plan_id = args.get('content_plan_id')
        if plan_id is not None:
            plan_id = _identifier(plan_id, '内容计划编号无效')
        revision = store.save_content_plan(args.get('document'), plan_id=plan_id)
        return {'status': 'completed', 'reason_code': 'content_plan_saved',
                'user_message': '内容计划已保存为不可变新版本', 'content_plan': revision}
    if action == 'content_plan_archive':
        _only(args, {'content_plan_id'})
        plan_id = _identifier(args.get('content_plan_id'), '请选择需要归档的内容计划')
        if not store.archive_content_plan(plan_id):
            raise ValueError('内容计划不存在或已经归档')
        return {'status': 'completed', 'reason_code': 'content_plan_archived',
                'user_message': '内容计划已归档；历史版本仍可读取',
                'archived': True, 'content_plan_id': plan_id}
    if action == 'preset_list':
        _only(args, set())
        from control_config import list_presets
        return {'presets': list_presets(store)}
    if action == 'preset_save':
        _only(args, {'name', 'config'})
        from control_config import list_presets, save_preset
        preset = save_preset(store, args.get('name'), args.get('config'))
        return {'status': 'completed', 'reason_code': 'preset_saved',
                'user_message': '任务参数预设已保存；没有创建或启动任务',
                'preset': preset, 'presets': list_presets(store)}
    if action == 'model_status':
        _only(args, {'provider'})
        from model_providers import status
        provider = _provider(args.get('provider'), required=False)
        return {'model': _without_secrets(status(provider))}
    if action == 'model_test':
        _only(args, {'provider', 'upload_consent'})
        return _test_model(args)
    if action == 'model_activate':
        _only(args, {'provider'})
        from model_providers import activate
        provider = _provider(args.get('provider'))
        model = _without_secrets(activate(provider, task_db=store.path))
        return {'status': 'completed', 'reason_code': 'model_activated',
                'user_message': model.get('message') or '模型已启用', 'model': model}
    if action == 'notification_list':
        _only(args, {'status', 'limit', 'offset'})
        status = args.get('status')
        if status not in {None, 'unread', 'viewed'}:
            raise ValueError('status必须是unread或viewed')
        limit, offset = args.get('limit', 50), args.get('offset', 0)
        if type(limit) is not int or type(offset) is not int:
            raise ValueError('提醒分页参数无效')
        result = store.list_interaction_alerts(status=status, limit=limit, offset=offset)
        notifications = result.pop('alerts')
        return {**result, 'notifications': notifications}
    if action == 'notification_acknowledge':
        _only(args, {'notification_ids'})
        notification_ids = args.get('notification_ids')
        if not isinstance(notification_ids, list):
            raise ValueError('notification_ids必须是列表')
        result = store.acknowledge_interaction_alerts(notification_ids)
        return {'status': 'completed', 'reason_code': 'notifications_acknowledged',
                'user_message': '平台提醒已确认；这不等于抖音消息已读', **result}
    raise ValueError('该业务尚未接入；没有执行操作')


def _test_model(args: dict[str, Any]) -> dict[str, Any]:
    from model_providers import ERRORS, OPENROUTER, ProviderError, QWEN, status, test_candidate
    provider = _provider(args.get('provider'))
    consent = args.get('upload_consent', False)
    if type(consent) is not bool:
        raise ValueError('upload_consent必须是布尔值')
    try:
        if provider == QWEN:
            model = test_candidate(consent=consent)
        else:
            if consent:
                raise ValueError('OpenRouter测试不接受图片上传同意参数')
            from model_connection import test_current_model
            test_current_model()
            model = status(OPENROUTER)
    except ProviderError as exc:
        return {'status': 'blocked', 'reason_code': exc.code,
                'user_message': ERRORS.get(exc.code, str(exc)), 'model': _without_secrets(status(provider))}
    model = _without_secrets(model)
    passed = model.get('model_test_status') == 'passed'
    return {'status': 'passed' if passed else 'failed',
            'reason_code': 'model_test_passed' if passed else model.get('reason_code') or 'model_test_failed',
            'user_message': model.get('message') or ('模型测试通过' if passed else '模型测试失败'),
            'model': model}


def _provider(value: Any, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    provider = str(value or '').strip()
    if provider not in PROVIDERS:
        raise ValueError('provider必须是openrouter或qwen_token_plan')
    return provider


def _identifier(value: Any, message: str) -> str:
    text = str(value or '').strip()
    if not text or len(text) > 100 or not all(ch.isalnum() or ch in '_-' for ch in text):
        raise ValueError(message)
    return text


def _only(args: dict[str, Any], allowed: set[str]) -> None:
    if set(args) - allowed:
        raise ValueError('参数包含不支持的字段；模型Key只能由用户在平台界面填写')


def _without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_secrets(item) for key, item in value.items()
                if str(key).lower() not in _SECRET_FIELDS}
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value
