"""Ship the official plugin SDK ahead of time; no first-launch npm install."""
import hashlib
import json
from pathlib import Path
import shutil
import time


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dependency_manifest(source, *, deadline=float('inf'), hash_file=digest, progress=None):
    source = Path(source)
    lock = json.loads((source / 'package-lock.json').read_text(encoding='utf-8'))
    if lock.get('packages', {}).get('node_modules/@opencode-ai/plugin', {}).get('version') != '1.18.29':
        raise ValueError('Plugin SDK version is not pinned to the engine')
    from itertools import chain
    paths = chain([source/'package.json', source/'package-lock.json'], (source/'node_modules').rglob('*'))
    files = {}
    for path in paths:
        if time.monotonic() >= deadline:
            raise TimeoutError('Dependency manifest deadline')
        if path.is_file():
            if not path.resolve().is_relative_to(source.resolve()):
                raise ValueError('Dependency points outside package')
            files[path.relative_to(source).as_posix()] = hash_file(path)
            if progress: progress('扫描插件资源', len(files), None)
    if 'node_modules/@opencode-ai/plugin/package.json' not in files:
        raise ValueError('Official plugin SDK is missing')
    return {'engine_version': '1.18.29', 'files': files}


def prepare_dependencies(app_root, engine_root, deadline, *, progress=None):
    source = Path(app_root) / 'runtime/opencode/dependencies'
    if not source.is_dir():
        source = Path(app_root) / 'packaging/agent-engine'
    manifest_path = source / 'dependency-manifest.json'
    cache_path = Path(engine_root)/'dependency-checks.json'
    try:
        cache = json.loads(cache_path.read_text(encoding='utf-8'))
        if not isinstance(cache, dict) or cache.get('source') != str(source.resolve()): cache = {}
    except (OSError, ValueError): cache = {}
    previous = cache.get('files', {})
    if not isinstance(previous, dict): previous = {}
    verified = {}
    def hash_file(path):
        if time.monotonic() >= deadline: raise TimeoutError('Dependency validation deadline')
        before = path.stat()
        signature = [before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino, before.st_dev]
        key = str(path.absolute())
        old = previous.get(key, {})
        if isinstance(old, dict) and old.get('signature') == signature and isinstance(old.get('sha256'), str) and len(old['sha256']) == 64:
            result = old['sha256']
        else:
            result = digest(path)
            after = path.stat()
            if signature != [after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_dev]:
                raise OSError('Dependency changed while reading')
        verified[key] = {'signature': signature, 'sha256': result}
        if time.monotonic() >= deadline: raise TimeoutError('Dependency validation deadline')
        return result
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else dependency_manifest(source, deadline=deadline, hash_file=hash_file, progress=progress)
    # Reuse hashes already read during development manifest generation this attempt.
    previous.update(verified)
    required = {'package.json', 'package-lock.json', 'node_modules/@opencode-ai/plugin/package.json'}
    if manifest.get('engine_version') != '1.18.29' or not required.issubset(manifest.get('files', {})):
        raise ValueError('插件资源清单不完整或版本不匹配，请修复安装')
    target = Path(engine_root) / 'config/opencode'
    target.mkdir(parents=True, exist_ok=True)
    # Copy only packaged files. auth.json and user's config are never candidates.
    for index, (name, expected) in enumerate(manifest['files'].items(), 1):
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or not (name.startswith('node_modules/') or name in {'package.json', 'package-lock.json'}):
            raise ValueError('Invalid dependency resource path')
        if time.monotonic() > deadline:
            raise TimeoutError('插件资源准备超时，请重试；已复制的资源可以复用')
        original, destination = source/relative, target/relative
        if progress: progress('校验插件资源', index, len(manifest['files']))
        if not original.resolve().is_relative_to(source.resolve()) or hash_file(original) != expected:
            raise ValueError('插件资源校验失败，请修复安装')
        if not destination.resolve().is_relative_to(target.resolve()):
            raise ValueError('插件目标目录指向外部，请修复安装')
        if destination.is_file() and hash_file(destination) == expected:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        if hash_file(destination) != expected:
            raise ValueError('插件资源复制校验失败，请重试')
    # Cache only complete verification. Changes in size/time/file identity rehash.
    # This is a local performance receipt, not a security boundary against its owner.
    temporary = cache_path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps({'source':str(source.resolve()), 'files':verified}), encoding='utf-8')
        temporary.replace(cache_path)
    except OSError:
        # The optimization is optional; verified usable resources may still start.
        pass
    # Native Npm.install checks the shipped lock and node_modules. No extra plugins disabled.
    return {'files': len(manifest['files']), 'engine_version': manifest['engine_version']}
