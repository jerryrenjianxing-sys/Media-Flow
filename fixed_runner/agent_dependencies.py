"""Ship the official plugin SDK ahead of time; no first-launch npm install."""
import hashlib
import json
from pathlib import Path
import shutil
import time


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dependency_manifest(source):
    source = Path(source)
    lock = json.loads((source / 'package-lock.json').read_text(encoding='utf-8'))
    if lock.get('packages', {}).get('node_modules/@opencode-ai/plugin', {}).get('version') != '1.18.29':
        raise ValueError('Plugin SDK version is not pinned to the engine')
    paths = [source/'package.json', source/'package-lock.json', *(source/'node_modules').rglob('*')]
    files = {}
    for path in paths:
        if path.is_file():
            if not path.resolve().is_relative_to(source.resolve()):
                raise ValueError('Dependency points outside package')
            files[path.relative_to(source).as_posix()] = digest(path)
    if 'node_modules/@opencode-ai/plugin/package.json' not in files:
        raise ValueError('Official plugin SDK is missing')
    return {'engine_version': '1.18.29', 'files': files}


def prepare_dependencies(app_root, engine_root, deadline):
    source = Path(app_root) / 'runtime/opencode/dependencies'
    if not source.is_dir():
        source = Path(app_root) / 'packaging/agent-engine'
    manifest_path = source / 'dependency-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else dependency_manifest(source)
    required = {'package.json', 'package-lock.json', 'node_modules/@opencode-ai/plugin/package.json'}
    if manifest.get('engine_version') != '1.18.29' or not required.issubset(manifest.get('files', {})):
        raise ValueError('插件资源清单不完整或版本不匹配，请修复安装')
    target = Path(engine_root) / 'config/opencode'
    target.mkdir(parents=True, exist_ok=True)
    # Copy only packaged files. auth.json and user's config are never candidates.
    for name, expected in manifest['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or not (name.startswith('node_modules/') or name in {'package.json', 'package-lock.json'}):
            raise ValueError('Invalid dependency resource path')
        if time.monotonic() > deadline:
            raise TimeoutError('插件资源准备超时，请重试；已复制的资源可以复用')
        original, destination = source/relative, target/relative
        if not original.resolve().is_relative_to(source.resolve()) or digest(original) != expected:
            raise ValueError('插件资源校验失败，请修复安装')
        if not destination.resolve().is_relative_to(target.resolve()):
            raise ValueError('插件目标目录指向外部，请修复安装')
        if destination.is_file() and digest(destination) == expected:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    # Native Npm.install checks the shipped lock and node_modules. No extra plugins disabled.
    return {'files': len(manifest['files']), 'engine_version': manifest['engine_version']}
