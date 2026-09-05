"""Explicitly authorized private snapshots; never evidence of a clean image.

All MuMu actions reuse the local template/provider boundary. The old default is
untouched until a new stopped instance has passed real control checks.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from private_template_payload import digest, validate_manifest
from template_apks import adb, package_installed, package_version


def settings_input_check(endpoint, evidence_directory):
    """Write/read only in the observed Android Settings search EditText."""
    import uiautomator2 as u2
    from douyin_fixed_runner import DeviceLock
    from runtime_layout import RUNTIME_ROOT
    from task_preparation import effective_density

    directory = Path(evidence_directory)
    directory.mkdir(parents=True, exist_ok=True)
    with DeviceLock(RUNTIME_ROOT, endpoint):
        device = u2.connect(endpoint)
        device.http_timeout = 15
        image = device.screenshot(format='pillow')
        image.save(directory / 'connected.png')
        dpi = effective_density(adb(endpoint, 'shell', 'wm', 'density'))
        if image.size != (900, 1600) or dpi != 320:
            raise ValueError(f'模板实际显示不是900×1600、320 DPI：当前{image.size[0]}×{image.size[1]}、{dpi} DPI；未修改配置')
        if not package_installed(endpoint):
            raise ValueError('模板没有安装抖音，不能激活')
        app = package_version(endpoint)
        device.app_start('com.android.settings')
        deadline = time.monotonic() + 25
        clicked = False
        try:
            while time.monotonic() < deadline:
                xml = device.dump_hierarchy(compressed=False, pretty=False)
                (directory / 'settings.xml').write_text(xml, encoding='utf-8')
                current_package = device.app_current().get('package')
                if current_package not in {'com.android.settings', 'com.android.settings.intelligence'}:
                    time.sleep(.5)
                    continue
                nodes = list(ET.fromstring(xml).iter('node'))
                fields = [node for node in nodes if node.get('class') in {'android.widget.EditText', 'android.widget.AutoCompleteTextView'}
                          and node.get('package') == current_package and node.get('resource-id') == 'android:id/search_src_text']
                if len(fields) == 1:
                    field = device(resourceId=fields[0].get('resource-id'), className=fields[0].get('class'))
                    phrase = 'MediaFlow中文输入验收'
                    field.set_text(phrase)
                    if field.get_text() != phrase:
                        raise RuntimeError('设置搜索框中文输入读回不一致；不以启用输入法代替成功')
                    device.screenshot(format='pillow').save(directory / 'chinese-input.png')
                    (directory / 'chinese-input.xml').write_text(device.dump_hierarchy(), encoding='utf-8')
                    field.clear_text()
                    # Android's accessibility Text can equal Hint for an empty
                    # AutoCompleteTextView. Use the observed hint, never a guessed literal.
                    if field.get_text() not in {'', fields[0].get('hint', '')}:
                        raise RuntimeError('设置搜索框测试文字未清除，请人工检查')
                    device.press('home')
                    return {**app, 'chinese_input_verified': True, 'input_mode': 'uiautomator_selector',
                            'width': 900, 'height': 1600, 'dpi': 320,
                            'android_engine': adb(endpoint, 'shell', 'getprop', 'ro.build.version.release')}
                if not clicked:
                    candidates = [node for node in nodes if node.get('package') == 'com.android.settings'
                                  and node.get('resource-id') and
                                  (node.get('text') in {'在设置中搜索', '搜索设置', '搜索', 'Search settings', 'Search'}
                                   or node.get('content-desc') in {'在设置中搜索', '搜索设置', '搜索', 'Search settings', 'Search'})]
                    ids = {node.get('resource-id') for node in candidates}
                    if len(ids) == 1:
                        device(resourceId=next(iter(ids))).click()
                        clicked = True  # Unknown click result is never replayed.
                time.sleep(.5)
            raise RuntimeError('未唯一识别系统设置搜索框；模板未激活，请查看现场')
        finally:
            # Capture the actual failure page, not a fabricated recovery screenshot.
            failures = []
            try:
                device.screenshot(format='pillow').save(directory / 'final.png')
            except Exception as exc:
                failures.append('截图失败: ' + type(exc).__name__)
            try:
                (directory / 'final.xml').write_text(device.dump_hierarchy(), encoding='utf-8')
            except Exception as exc:
                failures.append('UI读取失败: ' + type(exc).__name__)
            if failures:
                (directory / 'evidence-errors.json').write_text(json.dumps(failures, ensure_ascii=False), encoding='utf-8')


def load_manifest(path):
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > 65536:
        raise ValueError('模板清单过大')
    value = validate_manifest(json.loads(path.read_text(encoding='utf-8-sig')))
    snapshot = path.parent / value['file_name']
    if snapshot.is_symlink() or snapshot.stat().st_size != value['size_bytes'] or digest(snapshot) != value['sha256']:
        raise ValueError('私人模板哈希不一致；默认模板未改动')
    return value, snapshot


def import_private(service, operation_id, manifest_path, *, validator=None, resume_instance_id=None, new_attempt=False):
    from local_vm_template import PROFILE, disk_seal
    from virtual_device_inventory import manager_identity
    validator = validator or settings_input_check
    manifest, snapshot = load_manifest(manifest_path)
    receipt_key = 'private-template-import:' + manifest['sha256']
    previous = service.store.get_profile(receipt_key) or {}
    if new_attempt:
        old = service.store.get_virtual_operation(previous.get('operation_id', '')) if previous else None
        known_control_failure = str((old or {}).get('error') or '').removeprefix('ValueError: ').startswith('模板实际显示不是900×1600、320 DPI')
        if resume_instance_id is not None or previous.get('status') != 'failed' or not known_control_failure or not previous.get('virtual_device_id'):
            raise ValueError('只能显式新建已确认显示验收失败的模板；未知导入结果不可重放')
        current = next((v for v in service.provider.list_instances() if str(v['provider_instance_id']) == str(previous['provider_instance_id'])), None)
        saved = service.store.get_virtual_device(previous['virtual_device_id'])
        if not current or current.get('state') != 'stopped' or not saved.get('recipe', {}).get('is_template'):
            raise ValueError('失败副本尚未停止或身份不符；请先停止该模板，不操作原有实例')
        from mumu_archive import archive_busy
        if archive_busy(snapshot):
            raise ValueError('归档操作仍在进行，不允许新建')
        service.store.save_profile(receipt_key + ':failed:' + previous['operation_id'], previous)
        previous = {}  # Explicit new import only; old receipt/instance/operation retained.
    if resume_instance_id is not None and (not previous or previous.get('virtual_device_id')):
        raise ValueError('没有可接续的未登记导入现场；不会重新导入或覆盖已登记实例')
    resume = previous and resume_instance_id is not None
    if previous and not resume:
        if previous.get('status') != 'ready':
            raise ValueError('此前模板导入结果未完成；请核对已保留的实例，不会自动重复导入')
        current = next((item for item in service.provider.list_instances()
                        if str(item['provider_instance_id']) == str(previous['provider_instance_id'])), None)
        if not current or current.get('state') != 'stopped' or disk_seal(previous['storage']) != previous['disk_seal']:
            raise ValueError('已导入模板被打开、修改或移除；不会重复导入，请检查现场')
        service.store.save_profile(PROFILE, previous)
        return previous
    if not service.manager or not service.provider.probe(service.custom_path).get('compatible'):
        raise ValueError('未找到兼容的MuMu管理能力，请安装或选择MuMu目录')
    install_root = service.manager.parent.parent if service.manager.parent.name.lower() in {'nx_main', 'shell'} else service.manager.parent
    vm_root = install_root / 'vms'
    if not vm_root.is_dir():
        raise ValueError('不能确认MuMu实际存储目录；不会猜测导入位置')
    if shutil.disk_usage(vm_root).free < manifest['expanded_size_bytes'] * 2 + 2 * 1024**3:
        raise ValueError('MuMu磁盘空间不足以导入模板并创建验收副本')
    service._checkpoint(operation_id, 'template_importing', 10, '正在导入私人快照；可能包含历史缓存，不是公共干净模板')
    before_ids = [str(item['provider_instance_id']) for item in service.provider.list_instances()]
    before_dirs = {p.resolve() for p in vm_root.iterdir() if p.is_dir()}
    resumed = None
    recovery_of = None
    origin_operation = service.store.get_virtual_operation(operation_id)
    if resume:
        latest = service.store.get_virtual_operation(previous['operation_id'])
        if latest['status'] != 'failed':
            raise ValueError('上次核验仍未结束，不会重入或重放')
        old = service.store.get_virtual_operation(previous.get('recovery_of') or previous['operation_id'])
        origin_operation = old
        if old['status'] != 'failed' or old['id'] == operation_id or previous['status'] != 'failed':
            raise ValueError('只能用新核验操作接续已失败的导入，不修改旧终态')
        before_ids = list(previous['before_ids'])
        added = [v for v in service.provider.list_instances() if str(v['provider_instance_id']) not in before_ids]
        if len(added) != 1 or str(added[0]['provider_instance_id']) != str(resume_instance_id) or not str(resume_instance_id).isdigit():
            raise ValueError('指定实例不是原导入后唯一新增实例；不会自动接续或重复导入')
        resumed = added[0]
        if resumed.get('state') != 'stopped':
            raise ValueError('待核验实例必须已停止；不会抢占运行中的实例')
        created_at = float(resumed.get('created_timestamp') or 0) / 1_000_000
        if not (datetime.fromisoformat(old['created_at']).timestamp() <= created_at <= datetime.fromisoformat(old['finished_at']).timestamp() + 60):
            raise ValueError('实例创建时间与原导入操作不符；不能确认来源')
        matches = [p.resolve() for p in vm_root.glob('MuMuPlayer-*-' + str(resume_instance_id)) if p.is_dir()]
        if len(matches) != 1:
            raise ValueError('已导入实例磁盘位置不唯一；现场保留')
        from mumu_archive import archive_busy, test_archive
        if archive_busy(snapshot):
            raise ValueError('MuMu仍在解压；请等待结束后核验，不重复导入')
        test_archive(service.manager, snapshot)
        before_dirs.discard(matches[0])
        recovery_of = old['id']
    state = {**manifest, 'status': 'importing', 'operation_id': operation_id,
             'message': '私人模板导入尚未完成，原默认模板保留', 'before_ids': before_ids, 'recovery_of': recovery_of}
    service.store.save_profile(receipt_key, state)  # Durable intent BEFORE the irreversible command.
    try:
        new = resumed if resumed is not None else service.provider.import_backup(snapshot)
        # The inventory may have observed the retained import after the failed
        # installer exited. Reuse only its fresh, never-connected local identity.
        registered = next((v for v in service.store.list_virtual_devices()
                           if v['provider'] == 'mumu' and str(v['provider_instance_id']) == str(new['provider_instance_id'])), None)
        def from_this_import(timestamp):
            try:
                start = datetime.fromisoformat(origin_operation['created_at']).timestamp()
                end = datetime.fromisoformat(origin_operation['finished_at']).timestamp() + 60 if origin_operation.get('finished_at') else time.time()
                return start <= float(timestamp) / 1_000_000 <= end
            except (ValueError, TypeError):
                return False
        if registered and (registered.get('discovery_source') != 'provider_auto_discovery'
                           or registered.get('android_identity') or registered.get('last_adb_endpoint')
                           or registered.get('adb_endpoint') or registered.get('display_index') is not None
                           or registered.get('profile_status') != 'requires_verification'
                           or not from_this_import(new.get('created_timestamp'))
                           or not from_this_import((registered.get('provider_snapshot') or {}).get('created_timestamp'))):
            raise ValueError('导入实例已有非空身份或历史状态；不会覆盖已有档案，请核对现场')
        new.update(virtual_device_id=registered['virtual_device_id'] if registered else uuid.uuid4().hex, provider='mumu',
                   provider_install_id=manager_identity(service.manager), managed=True,
                   discovery_source='private_template', presence_status='present', profile_status='template_only',
                   adb_endpoint=None, last_adb_endpoint=None, android_identity=None,
                   recipe={**(new.get('recipe') or {}), 'is_template': True,
                           'template_source': 'private_snapshot', 'template_version': manifest['template_version']})
        service.store.save_virtual_device(new, fresh_creation={'operation_id': operation_id,
            'before_ids': before_ids, 'created_ids': [str(new['provider_instance_id'])]})
        state.update(virtual_device_id=new['virtual_device_id'], provider_instance_id=new['provider_instance_id'])
        service.store.save_profile(receipt_key, state)
        dirs = {p.resolve() for p in vm_root.iterdir() if p.is_dir()} - before_dirs
        if len(dirs) != 1:
            raise RuntimeError('导入实例已保留，但磁盘位置不唯一；未切换默认模板')
        storage = dirs.pop()
        service._checkpoint(operation_id, 'template_control_check', 55, '正在验证模板画面、UI和系统设置中文输入')
        endpoint = service.provider.start_and_resolve_adb(str(new['provider_instance_id']))
        checks = validator(endpoint, snapshot.parent / 'import-evidence')
        if checks.get('version_name') != manifest.get('app_version'):
            raise ValueError('导入后的抖音版本与快照清单不一致')
        service._checkpoint(operation_id, 'template_sealing', 80, '检查已通过，正在停止并校验模板；原实例不受影响')
        service._stop_confirmed(new['provider_instance_id'])
        seal = disk_seal(storage)
        service.store.save_virtual_device({**new, 'state': 'stopped', 'adb_endpoint': None})
        state.update(status='ready', storage=str(storage), disk_seal=seal, checks=checks,
                     message='私人快照已设为默认模板；可能保留缓存与账号标识，仅用于私人测试')
        service._checkpoint(operation_id, 'template_activating', 95, '正在切换默认模板，保留旧模板和现有虚拟机')
        service.store.save_profile(receipt_key, state)
        service.store.save_profile(PROFILE + ':' + manifest['template_version'], state)
        service.store.save_profile(PROFILE, state)  # One atomic DB update; only after complete validation.
        return state
    except Exception as exc:
        service.store.save_profile(receipt_key, {**state, 'status': 'failed', 'message': str(exc)})
        raise
