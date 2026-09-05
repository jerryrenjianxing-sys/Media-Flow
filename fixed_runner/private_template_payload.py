"""Private-only installer overlay. Bounded memory and signed-length-safe extents.

Hashes detect corruption, not authenticity: distribute only through the approved
private channel and verify the complete EXE hash out of band.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
from pathlib import Path

MAGIC = b'MF-TEMPLATE-001!'
TRAILER = struct.Struct('<Q16s')
MAX_MANIFEST = 65536
BUFFER = 1024 * 1024


def validate_manifest(value):
    if not isinstance(value, dict):
        raise ValueError('私人模板清单格式错误')
    if (value.get('format_version') != 1 or value.get('source') != 'private_snapshot'
            or value.get('private_data_possible') is not True
            or value.get('file_name') != 'template.mumudata'
            or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('sha256', '')))
            or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', str(value.get('template_version', '')))):
        raise ValueError('私人模板来源、版本或校验清单无效')
    for field in ('size_bytes', 'expanded_size_bytes'):
        if type(value.get(field)) is not int or not 0 < value[field] < 2**63:
            raise ValueError('模板大小无效')
    if value['expanded_size_bytes'] < value['size_bytes']:
        raise ValueError('模板展开大小不能小于载荷大小')
    if (value.get('width'), value.get('height'), value.get('dpi')) != (900, 1600, 320):
        raise ValueError('模板显示环境不符合900×1600、320 DPI')
    return value


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(BUFFER), b''):
            result.update(block)
    return result.hexdigest()


def read_payload(executable):
    with Path(executable).open('rb') as stream:
        total = stream.seek(0, 2)
        if total < TRAILER.size:
            raise ValueError('安装包没有私人模板载荷')
        stream.seek(-TRAILER.size, 2)
        length, magic = TRAILER.unpack(stream.read(TRAILER.size))
        if magic != MAGIC or not 0 < length <= MAX_MANIFEST or length > total - TRAILER.size:
            raise ValueError('安装包私人模板尾部损坏')
        start = total - TRAILER.size - length
        stream.seek(start)
        value = validate_manifest(json.loads(stream.read(length).decode('utf-8')))
        offset = value.get('offset')
        if type(offset) is not int or offset < 2 or offset + value['size_bytes'] != start:
            raise ValueError('模板载荷范围不正确')
        return value


def append_payload(executable, snapshot, manifest, *, enforce_executable_limit=True):
    value = dict(validate_manifest(manifest))
    path = Path(executable)
    # Refuse content replacement under the same installer identity.
    with path.open('rb') as stream:
        if path.stat().st_size >= len(MAGIC):
            stream.seek(-len(MAGIC), 2)
            if stream.read() == MAGIC:
                raise ValueError('安装包已有模板，不允许覆盖或重复追加')
    if Path(snapshot).stat().st_size != value['size_bytes'] or digest(snapshot) != value['sha256']:
        raise ValueError('模板文件大小或哈希不符')
    value['offset'] = path.stat().st_size
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    if len(data) > MAX_MANIFEST:
        raise ValueError('模板清单过大')
    if enforce_executable_limit and value['offset'] + value['size_bytes'] + len(data) + TRAILER.size >= 2**32:
        raise ValueError('单文件EXE将超过Windows可启动大小；请使用软件EXE与独立模板文件，不生成打不开的安装包')
    with path.open('ab') as target, Path(snapshot).open('rb') as source:
        shutil.copyfileobj(source, target, BUFFER)
        target.write(data)
        target.write(TRAILER.pack(len(data), MAGIC))


def extract_payload(executable, directory, *, progress=lambda _: None):
    value = read_payload(executable)
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < value['size_bytes'] + 512 * 1024**2:
        raise ValueError('模板磁盘空间不足；未修改默认模板')
    output = root / value['sha256']
    output.mkdir(exist_ok=True)
    destination = output / 'template.mumudata'
    if destination.exists() and digest(destination) == value['sha256']:
        return destination
    temporary = output / 'template.mumudata.partial'
    sha = hashlib.sha256()
    with Path(executable).open('rb') as source, temporary.open('wb') as target:
        source.seek(value['offset'])
        remaining = value['size_bytes']
        while remaining:
            block = source.read(min(BUFFER, remaining))
            if not block:
                raise ValueError('模板载荷被截断')
            target.write(block)
            sha.update(block)
            remaining -= len(block)
            progress(100 * (value['size_bytes'] - remaining) // value['size_bytes'])
    if sha.hexdigest() != value['sha256']:
        raise ValueError('模板SHA-256不一致；残片保留，不会激活')
    temporary.replace(destination)
    manifest_path = output / 'manifest.json'
    manifest_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return destination


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--append', required=True)
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    append_payload(args.append, manifest_path.parent / 'template.mumudata', manifest)
