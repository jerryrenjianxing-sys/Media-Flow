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
        if image.size != (900, 1600) or effective_density(adb(endpoint, 'shell', 'wm', 'density')) != 320:
            raise ValueError('模板实际显示不是900×1600、320 DPI，未修改配置')
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


def import_private(service, operation_id, manifest_path, *, validator=None):
    from local_vm_template import PROFILE, disk_seal
    from virtual_device_inventory import manager_identity
    validator = validator or settings_input_check
    manifest, snapshot = load_manifest(manifest_path)
    receipt_key = 'private-template-import:' + manifest['sha256']
    previous = service.store.get_profile(receipt_key) or {}
    if previous:
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
    state = {**manifest, 'status': 'importing', 'operation_id': operation_id,
             'message': '私人模板导入尚未完成，原默认模板保留', 'before_ids': before_ids}
    service.store.save_profile(receipt_key, state)  # Durable intent BEFORE the irreversible command.
    try:
        new = service.provider.import_backup(snapshot)
        new.update(virtual_device_id=uuid.uuid4().hex, provider='mumu',
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
