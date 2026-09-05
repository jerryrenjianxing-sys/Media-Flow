"""Installer adapter into the SAME durable template service used by the console.

Never imports task/Windows account state from the snapshot. No direct SQL writes.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path


def execute(manifest_path, store, *, custom_path=None, cancel_file=None, resume_instance_id=None):
    from local_vm_template import LocalVmTemplate
    from private_vm_template import load_manifest
    counts = store.task_status_counts()
    if not store.is_paused() or counts.get('running', 0) or counts.get('pending', 0):
        raise ValueError('请先暂停业务队列并等待任务池为空；安装器不会删除或领取任务')
    if store.list_active_virtual_operations():
        raise ValueError('仍有虚拟机操作未结束；请在平台确认现场，不会自动重放')
    manifest, _ = load_manifest(manifest_path)
    previous = store.get_profile('private-template-import:' + manifest['sha256']) or {}
    if previous.get('status') == 'ready':
        from private_vm_template import import_private
        result = import_private(LocalVmTemplate(store, custom_path), previous['operation_id'], manifest_path)
        return {'operation_id': previous['operation_id'], 'status': 'completed', 'reused': True,
                'template_version': result['template_version'], 'sha256': result['sha256']}
    from dataclasses import asdict
    from runtime_control import SystemProcessInspector
    owner = SystemProcessInspector().snapshot(os.getpid())
    if owner is None:
        raise RuntimeError('无法登记安装执行者身份，模板尚未导入')
    operation, created = store.create_virtual_operation('template_prepare', {
        'virtual_device_id': 'local-template-creation', 'from_template': True, 'prepare_only': True,
        'private_manifest': str(Path(manifest_path).resolve()), 'installer_pid': os.getpid(), 'cancel_file': cancel_file,
        'installer_owner': asdict(owner),
        'resume_instance_id': resume_instance_id,
        'progress_file': str(Path(manifest_path).parent / 'import-progress.json'),
    }, idempotency_key='private-template-install:' + manifest['sha256'] + (':resume:' + str(resume_instance_id) + ':' + str(previous.get('operation_id', 'none')) if resume_instance_id is not None else ''))
    if not created and operation['status'] != 'completed':
        raise ValueError('上次私人模板操作未完成；现场保留，请核对后处理，不会重复导入')
    if not created:
        # Re-installation verifies the immutable seal without re-importing.
        from private_vm_template import import_private
        result = import_private(LocalVmTemplate(store, custom_path), operation['id'], manifest_path)
        return {'operation_id': operation['id'], 'status': 'completed', 'reused': True,
                'template_version': result['template_version'], 'sha256': result['sha256']}
    try:
        result = LocalVmTemplate(store, custom_path).create(operation['id'], '私人模板', 0,
                                                          prepare_only=True, private_manifest=manifest_path, resume_instance_id=resume_instance_id)
        store.update_virtual_operation(operation['id'], status='completed', stage='completed', progress=100,
                                       result=result, message='私人快照已验证并设为默认；旧模板和实例保留')
        return {'operation_id': operation['id'], 'status': 'completed', **result}
    except Exception as exc:
        store.update_virtual_operation(operation['id'], status='failed', stage='template_failed', progress=100,
                                       error=str(exc), message='模板未激活，旧默认及已产生的现场保留', retryable=False)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--result', required=True)
    parser.add_argument('--mumu-manager')
    parser.add_argument('--cancel-file')
    parser.add_argument('--resume-instance')
    parser.add_argument('--new-attempt', action='store_true')
    args = parser.parse_args()
    try:
        from template_installer_bridge import execute as submit
        result = submit(args.manifest, custom_path=args.mumu_manager, cancel_file=args.cancel_file,
                        resume_instance_id=args.resume_instance, new_attempt=args.new_attempt)
        code = 0
    except Exception as exc:
        result = {'status': 'failed', 'stage': 'template_import', 'message': str(exc),
                  'old_template_preserved': True, 'automatic_replay': False}
        code = 20
    output = Path(args.result).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
