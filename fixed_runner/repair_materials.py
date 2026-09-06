"""First-party source bundle, separate from runtime state and private templates."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
import zipfile

EXTENSIONS = {'.py', '.ts', '.tsx', '.js', '.mjs', '.mts', '.css', '.json', '.md', '.ps1', '.cs', '.toml', '.txt', '.yaml', '.yml', '.svg', '.html', '.csproj', '.manifest', '.cmd', '.bat', '.xml'}
EXCLUDED = {'runtime', '.secrets', 'secrets', 'node_modules', 'dist', 'out', 'work', '.git', '__pycache__', 'tools', '_dependencies', '_test_scratch'}
ROOTS = ('fixed_runner', 'control_console', 'device_stream_host/src',
         'device_stream_host/scripts', 'launcher', 'installer', 'scripts', 'openspec', 'packaging', 'docs')
LOCAL_FILES = {'device_profiles.json', 'platform_profiles.json', 'installation.json', 'auth.json', 'model-connection.json'}
SECRET = re.compile(rb'sk-(?:or-v1-|sp-|proj-)[A-Za-z0-9._-]{24,}')


def development_revision(root):
    try:
        options = {'cwd': str(root), 'stderr': subprocess.DEVNULL, 'text': True, 'timeout': 10,
                   'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], **options).strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain'], **options).strip()
        return revision + ('.dirty' if dirty else '')
    except (OSError, subprocess.SubprocessError):
        return 'development-working-copy'


def source_path(value):
    if not isinstance(value, str) or '\\' in value or ':' in value:
        raise ValueError('修复文件路径无效')
    path = PurePosixPath(value)
    special = value == 'control_console/.openai/hosting.json' or (value.startswith('openspec/') and path.name == '.openspec.yaml')
    if path.is_absolute() or not path.parts or any(x in {'.', '..'} or (x.startswith('.') and not special) for x in path.parts):
        raise ValueError('修复文件不能离开工作区')
    if any(PureWindowsPath(part).is_reserved() or part.endswith((' ', '.')) for part in path.parts):
        raise ValueError('修复文件名不符合Windows路径规则')
    if path.suffix.lower() not in EXTENSIONS or any(x.lower() in EXCLUDED for x in path.parts) or path.name in LOCAL_FILES:
        raise ValueError('修复材料不包含凭证、运行数据或二进制文件')
    return path.as_posix()


def build_bundle(root, output, revision):
    root, output = Path(root).resolve(), Path(output)
    entries = {}
    for prefix in ROOTS:
        folder = root / prefix
        if not folder.is_dir():
            continue
        paths = []
        for directory, children, names in os.walk(folder, followlinks=False):
            children[:] = [name for name in children if name.lower() not in EXCLUDED and not name.startswith('.')]
            paths.extend(Path(directory) / name for name in names)
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            name = path.relative_to(root).as_posix()
            try:
                source_path(name)
            except ValueError:
                continue
            if not path.resolve().is_relative_to(root) or path.stat().st_size > 2_000_000:
                continue
            data = path.read_bytes()
            if SECRET.search(data):
                raise ValueError('修复源码包含疑似凭证，请先完成发行扫描：' + name)
            entries[name] = data
    top_level = [p.name for p in root.iterdir() if p.is_file() and p.suffix in {'.md', '.ps1', '.cmd', '.bat'} and not p.name.startswith('.')]
    for name in (*top_level, 'packaging/version.json', 'control_console/package.json', 'control_console/package-lock.json',
                 'AGENTS.md', 'PROJECT.md', 'manage-mediaflow.ps1', 'run-mediaflow-console.ps1',
                 'control_console/vite.config.ts', 'control_console/tsconfig.json', 'control_console/eslint.config.mjs',
                 'control_console/.openai/hosting.json'):
        path = root / name
        if path.is_file():
            data = path.read_bytes()
            if SECRET.search(data):
                raise ValueError('修复材料包含疑似凭证')
            entries[name] = data
    if not entries:
        raise ValueError('缺少修复源码材料')
    manifest = {'schema': 1, 'source_revision': revision, 'contains_runtime_data': False,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(entries.items())}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return {**manifest, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size}


def read_bundle(bundle):
    with zipfile.ZipFile(bundle) as archive:
        infos = archive.infolist()
        if len(infos) > 5000 or sum(x.file_size for x in infos) > 100_000_000 or any(x.file_size > 3_000_000 for x in infos):
            raise ValueError('修复源码载荷过大')
        if len({x.filename.casefold() for x in infos}) != len(infos):
            raise ValueError('修复材料存在重复路径')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('schema') != 1 or manifest.get('contains_runtime_data') is not False:
            raise ValueError('修复材料清单不兼容')
        files = manifest.get('files')
        if not isinstance(files, dict) or set(files) != {x.filename for x in infos} - {'manifest.json'}:
            raise ValueError('修复材料与清单不一致')
        result = {}
        for name, digest in files.items():
            source_path(name)
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != digest or SECRET.search(data):
                raise ValueError('修复材料校验失败')
            result[name] = data
        return manifest, result
