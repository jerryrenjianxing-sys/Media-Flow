"""Observe MuMu's asynchronous archive work after its short RPC deadline.

MuMuManager can return -502 while its 7za child continues for minutes. Never
resend import/export. Observe the exact archive, then validate the final result.
"""
from __future__ import annotations
import json
import os
import shlex
import subprocess
import time
from pathlib import Path


def accept_dispatch(result):
    try:
        body = json.loads(result.stdout)
        code = body.get('errcode', 0)
    except (ValueError, AttributeError):
        code = None
    if code == -502:
        return  # Uncertain dispatch, NOT failure or permission to repeat.
    if result.returncode or code not in {None, 0}:
        raise RuntimeError('MuMu导入/导出未确认；现场保留，不自动重放：' + (result.stderr or result.stdout)[:300])


def archive_busy(archive):
    script = "$ErrorActionPreference='Stop'; Get-CimInstance Win32_Process -Filter \"Name = '7za.exe' OR Name = '7z.exe' OR Name = '7zz.exe'\" | Select-Object CommandLine | ConvertTo-Json -Compress"
    binary = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    environment = dict(os.environ)
    environment['PSModulePath'] = str(binary.parent / 'Modules')
    result = subprocess.run([str(binary), '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=environment)
    if result.returncode:
        raise RuntimeError('无法核对MuMu压缩进程，操作结果待确认；不会重复执行')
    text = result.stdout.decode('utf-8-sig', errors='replace').strip()
    rows = json.loads(text) if text else []
    if isinstance(rows, dict):
        rows = [rows]
    expected = str(Path(archive).resolve()).casefold()
    for row in rows:
        command = row.get('CommandLine')
        if not command:
            raise RuntimeError('无法读取压缩进程身份，不能确认模板已结束')
        tokens = [part.strip('"').casefold() for part in shlex.split(command, posix=False)]
        if expected in tokens:
            return True
    return False


def test_archive(manager, archive):
    install = Path(manager).parent.parent
    candidates = sorted((install / 'nx_device').glob('*/shell/7za.exe'))
    if not candidates:
        raise RuntimeError('MuMu缺少归档校验组件，不能确认模板完整')
    result = subprocess.run([str(candidates[-1]), 't', str(archive)], capture_output=True,
                            timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('MuMu模板归档完整性校验失败；保留文件，不重复导出')


def wait_export(manager, directory, before, *, deadline):
    while time.monotonic() < deadline:
        changed = [p.resolve() for p in directory.glob('*.mumudata')
                   if before.get(p.resolve()) != (p.stat().st_size, p.stat().st_mtime_ns)]
        if len(changed) > 1:
            raise RuntimeError('出现多个导出结果，无法唯一确认；不会重放')
        if len(changed) == 1:
            archive = changed[0]
            if archive.stat().st_size > 0 and not archive_busy(archive):
                test_archive(manager, archive)
                return archive
        time.sleep(2)
    raise RuntimeError('导出截止时间已到，后台可能仍在压缩；请核对现场，勿重复导出')


def wait_import(provider, archive, before, *, deadline):
    stable = None
    while time.monotonic() < deadline:
        created = [item for item in provider.list_instances() if item['provider_instance_id'] not in before]
        if len(created) > 1:
            raise RuntimeError('出现多个新实例，导入结果不唯一；现场保留，不重放')
        if len(created) == 1 and not archive_busy(archive):
            current = str(created[0]['provider_instance_id'])
            if stable == current:
                return created[0]
            stable = current
        else:
            stable = None
        time.sleep(2)
    raise RuntimeError('导入截止时间已到，实际结果待确认；保留实例，不重复导入')
