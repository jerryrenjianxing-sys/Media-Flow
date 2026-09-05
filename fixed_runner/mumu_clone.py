"""One clone dispatch, then bounded proof of the unique copied disk."""
from __future__ import annotations
import hashlib
import time
from pathlib import Path


def _hash(path, deadline):
    digest = hashlib.sha256()
    before = path.stat()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            if time.monotonic() >= deadline:
                raise TimeoutError('克隆完整性检查超时；副本保留，不会重复克隆')
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return None
    return digest.hexdigest()


def _directories(root):
    return {p.resolve() for p in root.iterdir() if p.is_dir() and not p.is_symlink()}


def snapshot(manager, source_id, *, deadline):
    root = Path(manager).resolve().parent.parent / 'vms'
    before = _directories(root)
    source = [p for p in before if p.name.endswith('-' + str(source_id)) and (p / 'data.vdi').is_file()]
    if len(source) != 1:
        raise RuntimeError('无法唯一核对来源磁盘；尚未克隆')
    disk = source[0] / 'data.vdi'
    if disk.is_symlink() or disk.stat().st_size <= 0:
        raise RuntimeError('来源磁盘无效；尚未克隆')
    digest = _hash(disk, deadline)
    if digest is None:
        raise RuntimeError('来源磁盘正在变化；尚未克隆')
    return {'root': root, 'directories': before, 'source': str(source_id), 'disk': disk,
            'sha256': digest, 'size': disk.stat().st_size, 'mtime': disk.stat().st_mtime_ns}


def wait_clone(provider, before_ids, evidence, *, deadline):
    while time.monotonic() < deadline:
        instances = provider.list_instances()
        source = next((x for x in instances if x['provider_instance_id'] == evidence['source']), None)
        stat = evidence['disk'].stat()
        if not source or source.get('state') != 'stopped' or (stat.st_size, stat.st_mtime_ns) != (evidence['size'], evidence['mtime']):
            raise RuntimeError('复制期间来源状态改变；副本保留，不会启动或重复克隆')
        created = [x for x in instances if x['provider_instance_id'] not in before_ids]
        directories = _directories(evidence['root']) - evidence['directories']
        if len(created) > 1 or len(directories) > 1:
            raise RuntimeError('克隆结果不唯一；现场保留，不会重复克隆')
        if len(created) == 1 and len(directories) == 1:
            directory = next(iter(directories))
            instance = created[0]
            disk = directory / 'data.vdi'
            if not directory.name.endswith('-' + instance['provider_instance_id']):
                raise RuntimeError('克隆实例与磁盘目录不匹配；现场保留')
            if instance.get('state') == 'stopped' and disk.is_file() and not disk.is_symlink():
                if disk.stat().st_size == evidence['size'] and _hash(disk, deadline) == evidence['sha256']:
                    return instance
        time.sleep(2)
    raise RuntimeError('克隆截止时间已到，完整副本未确认；请核对现场，不会重复克隆')
