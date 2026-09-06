"""Candidate-only dependency preparation and reversible local activation."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def changed(workspace, source, name):
    a, b = Path(workspace) / name, Path(source) / name
    return (a.read_bytes() if a.is_file() else b'') != (b.read_bytes() if b.is_file() else b'')


def prepare(workspace, source, node, env):
    workspace, source = Path(workspace), Path(source)
    root = workspace / '_dependencies'
    root.mkdir(exist_ok=True)
    python = source / '.venv/Scripts/python.exe'
    requirements = workspace / 'fixed_runner/requirements.txt'
    if changed(workspace, source, 'fixed_runner/requirements.txt'):
        if not requirements.is_file():
            raise ValueError('不能移除项目Python依赖清单')
        candidate = workspace / '.venv'
        if not candidate.exists():
            shutil.copytree(source / '.venv', candidate)
        python = candidate / 'Scripts/python.exe'
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        marker = root / 'python.json'
        if not marker.exists() or json.loads(marker.read_text()) != digest:
            subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(requirements)],
                cwd=workspace, env=env, check=True, timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            marker.write_text(json.dumps(digest), encoding='utf-8')
    console = workspace / 'control_console'
    if any(changed(workspace, source, p) for p in ('control_console/package.json', 'control_console/package-lock.json')):
        lock = console / 'package-lock.json'
        if not lock.is_file():
            raise ValueError('修改前端依赖必须同时提供锁定文件')
        digest = hashlib.sha256(lock.read_bytes() + (console / 'package.json').read_bytes()).hexdigest()
        marker = root / 'node.json'
        if not marker.exists() or json.loads(marker.read_text()) != digest:
            npm = Path(node).parent / 'node_modules/npm/bin/npm-cli.js'
            if not npm.is_file():
                raise ValueError('本机缺少npm依赖准备工具')
            subprocess.run([str(node), str(npm), 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', str(root / 'npm-cache')],
                cwd=console, env=env, check=True, timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            marker.write_text(json.dumps(digest), encoding='utf-8')
    return python


class DependencySwitch:
    def __init__(self, source, candidate, backup):
        self.source, self.candidate, self.backup = Path(source), Path(candidate), Path(backup)
        self.swapped = []

    def activate(self):
        for name, manifests in (('.venv', ['fixed_runner/requirements.txt']),
                                ('control_console/node_modules', ['control_console/package.json', 'control_console/package-lock.json'])):
            if not any(changed(self.candidate, self.source, m) for m in manifests):
                continue
            origin, new = self.source / name, self.candidate / name
            if not new.is_dir():
                raise ValueError('候选依赖未完成准备，未切换')
            old = self.backup / name
            old.parent.mkdir(parents=True, exist_ok=True)
            if old.exists():
                raise ValueError('已有依赖切换现场，拒绝重复覆盖')
            if origin.exists():
                origin.rename(old)
            self.swapped.append((origin, old))
            shutil.copytree(new, origin)

    def rollback(self):
        for origin, old in reversed(self.swapped):
            failed = old.with_name(old.name + '-failed-candidate')
            if origin.exists():
                origin.rename(failed)
            if old.exists():
                old.rename(origin)
