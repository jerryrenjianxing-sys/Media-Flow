"""One durable submission boundary for UI and Agent virtual-device commands."""
import threading

from virtual_device_inventory import VirtualDeviceInventory


SUPPORTED = {'start', 'stop', 'restart', 'clone', 'backup', 'repair_standard', 'settings', 'delete'}


def submit_virtual_command(store, virtual_device_id, request, *, executor):
    action = str(request.get('action') or '')
    key = str(request.get('idempotency_key') or '')
    if action not in SUPPORTED or not key or len(key) > 200:
        raise ValueError('虚拟机操作或请求编号无效')
    with store.connection() as db:
        old = db.execute('SELECT id FROM virtual_device_operations WHERE idempotency_key=?', (key,)).fetchone()
    if old:
        operation = store.get_virtual_operation(old['id'])
        previous = operation.get('request') or {}
        if operation['operation_type'] != action or previous.get('virtual_device_id') != virtual_device_id:
            raise ValueError('此请求编号已用于另一项操作')
        if action == 'settings' and previous.get('settings') != request.get('settings'):
            raise ValueError('此请求编号已用于其他配置，不会覆盖原操作')
        return {'ok': True, 'created': False, 'operation': operation}
    device = store.get_virtual_device(virtual_device_id)
    if action != 'stop':
        VirtualDeviceInventory(store).assert_idle(device)
    payload = {'virtual_device_id': virtual_device_id, 'action': action,
               'mumu_path': str(request.get('mumu_path') or '').strip() or None}
    if action == 'delete':
        if request.get('confirmation_name') != device['name']:
            raise ValueError('请输入完整虚拟机名称以确认删除')
        payload.update(confirmation_name=device['name'], backup=request.get('backup') is not False)
    if action == 'settings':
        if not isinstance(request.get('settings'), dict) or not request['settings']:
            raise ValueError('请提供要修改的配置')
        payload['settings'] = request['settings']
    if action == 'clone':
        payload.update(provider='mumu', provider_install_id=device['provider_install_id'])
        operation, created = store.create_numbered_virtual_operation(action, payload, idempotency_key=key)
    else:
        operation, created = store.create_virtual_operation(action, payload, idempotency_key=key)
    if created:
        try:
            threading.Thread(target=executor, kwargs={'store': store, 'operation_id': operation['id'],
                'virtual_device_id': virtual_device_id, 'action': action, 'custom_path': payload['mumu_path']},
                name='virtual-' + operation['id'][:8], daemon=True).start()
        except Exception:
            store.update_virtual_operation(operation['id'], status='failed', stage='failed', progress=100,
                                           error='执行线程未启动；请查看记录，不会自动重放')
            raise
    return {'ok': True, 'created': created, 'operation': operation}
