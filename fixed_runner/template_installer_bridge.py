"""Installer submits to the existing desktop action owner; never launches MuMu."""
from __future__ import annotations
import json
import time
import uuid
import urllib.request
from pathlib import Path


def request(path, body=None):
    req = urllib.request.Request('http://127.0.0.1:48138' + path,
        data=None if body is None else json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def execute(manifest_path, *, custom_path=None, cancel_file=None, resume_instance_id=None, new_attempt=False):
    from product_version import product_version
    expected = product_version()
    deadline = time.monotonic() + 120
    while True:
        try:
            actual = request('/api/status')['product_version']
            if all(actual.get(k) == expected.get(k) for k in ('version', 'source_revision')):
                break
        except (OSError, ValueError, KeyError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('同版本MediaFlow后台尚未就绪；请打开软件后重试模板准备。尚未导入，不会从SSH直接控制MuMu')
        time.sleep(2)
    body = {'private_manifest': str(Path(manifest_path).resolve()),
        'confirmation': '导入私人快照，保留旧模板和实例',
        'idempotency_key': 'private-installer:' + uuid.uuid4().hex,
        'mumu_path': custom_path, 'resume_instance_id': resume_instance_id,
        'new_attempt': new_attempt,
        'new_attempt_confirmation': '保留失败副本并新建一次' if new_attempt else None,
        'installer_bridge': True}
    # Exactly one POST. If its response is lost, the durable server operation
    # remains visible; never replay a potentially dispatched import here.
    try:
        op = request('/api/virtual-device-template', body)['operation']
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError('模板提交结果未确认，请在平台查看操作记录；未自动重发') from exc
    deadline = time.monotonic() + 3600
    failures = 0
    cancelled = False
    progress_file = Path(manifest_path).parent / 'import-progress.json'
    while True:
        progress_file.write_text(json.dumps({'stage': op['stage'], 'progress': op['progress'],
            'message': op.get('message') or '正在准备模板', 'operation_id': op['id']}, ensure_ascii=False), encoding='utf-8')
        if op['status'] == 'completed':
            return {'operation_id': op['id'], 'status': 'completed', **(op.get('result') or {})}
        if op['status'] not in {'queued', 'running'}:
            raise RuntimeError(op.get('error') or op.get('message') or '模板等待处理，请在平台继续')
        if time.monotonic() >= deadline:
            raise RuntimeError('模板等待超过截止时间；现场保留，请在平台查看操作，不会重复导入')
        if cancel_file and Path(cancel_file).exists() and not cancelled:
            cancelled = True
            request('/api/virtual-device-template/cancel', {'operation_id': op['id']})
        time.sleep(2)
        try:
            op = request('/api/virtual-device-operations/' + op['id'])['operation']
            failures = 0
        except (OSError, ValueError, KeyError) as exc:
            failures += 1
            if failures >= 3:
                raise RuntimeError('连续三次无法读取模板进度；请恢复后台后查看现场，不会重放导入') from exc
