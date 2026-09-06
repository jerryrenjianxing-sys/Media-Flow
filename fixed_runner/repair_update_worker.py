"""Detached development updater. Never runs model-provided command strings."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from agent_permissions import AgentPermissions
from agent_repair_updates import AgentRepairUpdates
from agent_repairs import AgentRepairs
from repair_materials import development_revision
from agent_platform import bounded_diagnostic
from repair_dependencies import DependencySwitch


def run_job(updates, job, driver, *, sleep=time.sleep):
    """Small state machine, exercised with an isolated driver in regression tests."""
    switched = False
    stopped = False
    try:
        while True:
            current = updates.get(job['id'], job['session'])
            if current['status'] not in {'queued', 'running', 'waiting_user'}:
                return
            if current['cancelled']:
                updates.update(job['id'], status='cancelled', stage='cancelled', message='更新已取消，旧程序未变')
                return
            if time.time() >= current['deadline']:
                raise ValueError('更新等待超时，未强行中断任务')
            driver.check_authorization()
            if driver.idle():
                break
            updates.update(job['id'], status='waiting_user', stage='waiting_idle', message='等待运行任务或设备操作结束，可取消；不会强停任务')
            sleep(2)
        updates.update(job['id'], status='running', stage='staging', message='正在准备唯一提交及构建，旧服务仍可使用')
        revision = driver.stage()
        updates.update(job['id'], target_revision=revision)
        current = updates.get(job['id'], job['session'])
        if current['status'] not in {'queued', 'running', 'waiting_user'} or time.time() >= current['deadline']:
            raise ValueError('更新已超时或执行者失联，未切换服务')
        if current['cancelled']:
            updates.update(job['id'], status='cancelled', stage='cancelled', message='切换前已取消，旧服务未变')
            return
        driver.check_authorization()
        if not driver.idle():
            raise ValueError('准备期间出现新的任务或设备占用，未切换；请空闲后重新确认')
        updates.update(job['id'], status='running', stage='switching', message='正在切换服务，原会话将在重连后恢复')
        driver.stop()
        stopped = True
        driver.activate(revision)
        switched = True
        driver.start()
        updates.update(job['id'], stage='health_check', message='正在核对新服务、页面及版本')
        driver.health(revision)
        updates.update(job['id'], status='completed', stage='completed', message='新版本已生效，会话与运行数据保留；任务保持暂停')
    except Exception as exc:
        message = bounded_diagnostic(exc)
        if stopped:
            updates.update(job['id'], stage='rolling_back', message='新服务未通过检查，正在恢复旧程序')
            try:
                rollback = driver.rollback(switched)
                updates.update(job['id'], status='failed', stage='rolled_back', rollback_revision=rollback,
                               message='更新未通过，旧程序已恢复：' + message)
                return
            except Exception as recovery:
                message += '；自动恢复未完成：' + bounded_diagnostic(recovery)
        updates.update(job['id'], status='failed', stage='failed', message=message)


class DevelopmentDriver:
    def __init__(self, updates, job):
        self.u, self.job = updates, job
        self.source = updates.repairs.source_root.resolve()
        self.folder = self.source / 'work/agent-updates' / job['id']
        self.stage_root = self.folder / 'source'
        self.log = self.folder / 'update.log'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.merged = None
        self.dependencies = DependencySwitch(self.source, self.stage_root, self.folder / 'previous-dependencies')

    def command(self, args, *, cwd=None, timeout=120, env=None):
        result = subprocess.run(args, cwd=cwd or self.source, env=env, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=timeout,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with self.log.open('a', encoding='utf-8') as output:
            output.write(bounded_diagnostic(result.stdout + result.stderr) + '\n')
        if result.returncode:
            raise ValueError('更新步骤执行失败：' + bounded_diagnostic(result.stderr or result.stdout))
        return result.stdout.strip()

    def check_authorization(self):
        self.u.permissions.require(self.job['session'], 'repair_apply')
        with self.u.database() as db:
            row = db.execute('SELECT * FROM approvals WHERE request_id=?', (self.job['request_id'],)).fetchone()
        if not row or row['revision'] != self.u.permissions.get(self.job['session'])['revision']:
            raise ValueError('更新授权已撤销或改变')
        if self.u.repairs.diff(self.job['repair'], self.job['session'])['source_hash'] != self.job['hash']:
            raise ValueError('已确认补丁发生变化，未应用')

    @staticmethod
    def snapshot():
        with urllib.request.urlopen('http://127.0.0.1:48138/api/status', timeout=5) as response:
            return json.load(response)

    def idle(self):
        snapshot = self.snapshot()
        summary = snapshot.get('task_summary') or {}
        if summary.get('running', 0) or summary.get('pending', 0) or not snapshot.get('paused'):
            return False
        # Check authoritative control and maintenance leases, not device labels.
        from worker import DEFAULT_DB
        with sqlite3.connect(Path(DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True) as db:
            for table, condition in (
                ('device_initializations', "status IN ('queued','running')"),
                ('virtual_device_operations', "status IN ('queued','running')"),
                ('device_view_sessions', "mode='control' AND status IN ('created','connected','disconnected')")):
                if db.execute('SELECT 1 FROM ' + table + ' WHERE ' + condition + ' LIMIT 1').fetchone():
                    return False
        return True

    def stage(self):
        if development_revision(self.source) != self.job['base']:
            raise ValueError('主项目不是已确认的干净基准，不能覆盖其他修改')
        if self.stage_root.exists():
            raise ValueError('已有更新工作区，结果需要核对；不会重复创建或应用')
        self.command(['git', 'worktree', 'add', '--detach', str(self.stage_root), self.job['base']])
        frozen = json.loads((self.u.root / 'approved' / (self.job['hash'] + '.json')).read_text(encoding='utf-8'))
        if frozen['source_hash'] != self.job['hash']:
            raise ValueError('已确认补丁快照不匹配，未应用')
        for name, content in frozen['files'].items():
            target = self.u.repairs.file(self.stage_root, name)
            if content is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content.encode('utf-8'))
            elif target.is_file():
                # Only an exact tracked file from the approved diff is removed.
                target.unlink()
        version_file = self.stage_root / 'packaging/version.json'
        version = json.loads(version_file.read_text(encoding='utf-8'))
        base_version = json.loads((self.source / 'packaging/version.json').read_text(encoding='utf-8'))
        version['development_iteration'] = int(base_version['development_iteration']) + 1
        version_file.write_text(json.dumps(version, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Trusted validation runner from the old program; never candidate shell.
        self.command([sys.executable, str(Path(__file__).with_name('repair_validation.py')),
            str(self.stage_root), str(self.source), 'all'], timeout=1200)
        self.command(['git', 'add', '--all'], cwd=self.stage_root)
        self.command(['git', 'commit', '-m', 'Apply approved MediaFlow repair ' + self.job['id']], cwd=self.stage_root)
        revision = self.command(['git', 'rev-parse', 'HEAD'], cwd=self.stage_root)
        old_dist = self.source / 'control_console/dist'
        if old_dist.is_dir():
            shutil.copytree(old_dist, self.folder / 'previous-dist')
        return revision

    def stop(self):
        self.command(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                      str(self.source / 'manage-mediaflow.ps1'), '-Action', 'Stop', '-NoBrowser'], timeout=90)

    def activate(self, revision):
        if development_revision(self.source) != self.job['base']:
            raise ValueError('切换前主项目已变化，未覆盖')
        self.dependencies.activate()
        self.command(['git', 'merge', '--ff-only', revision])
        self.merged = revision
        shutil.copytree(self.stage_root / 'control_console/dist', self.source / 'control_console/dist', dirs_exist_ok=True)

    def start(self):
        self.command(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                      str(self.source / 'manage-mediaflow.ps1'), '-Action', 'Start', '-NoBrowser'], timeout=90)

    def health(self, revision):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                status = self.snapshot()
                product = status['product_version']
                with urllib.request.urlopen('http://127.0.0.1:3000/', timeout=3) as response:
                    if response.status == 200 and revision.startswith(product['source_revision']) and not product['source_dirty'] and status['paused']:
                        return
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(1)
        raise ValueError('新版本API或页面健康检查未通过')

    def rollback(self, switched):
        # Preserve main history; never reset --hard or delete user data.
        self.stop()
        self.dependencies.rollback()
        if self.merged:
            if development_revision(self.source) != self.merged:
                raise ValueError('更新后源码出现其他修改，请人工处理；不覆盖')
            self.command(['git', 'revert', '--no-edit', self.merged])
        if (self.folder / 'previous-dist').is_dir():
            shutil.copytree(self.folder / 'previous-dist', self.source / 'control_console/dist', dirs_exist_ok=True)
        self.start()
        revision = development_revision(self.source)
        self.health(revision)
        return revision


def main():
    updates_root, repairs_root, source, job_id = sys.argv[1:]
    permissions = AgentPermissions(Path(updates_root).parent / 'permissions')
    # Do not instantiate AgentRepairs: its startup recovery belongs to the API.
    repairs = object.__new__(AgentRepairs)
    repairs.root, repairs.source_root = Path(repairs_root), Path(source)
    updates = AgentRepairUpdates(updates_root, repairs, permissions)
    with updates.database() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row or not updates.claim(job_id):
        return
    job = dict(row)
    stopped = threading.Event()
    def pulse():
        while not stopped.wait(10):
            updates.heartbeat(job_id)
    threading.Thread(target=pulse, daemon=True).start()
    try:
        run_job(updates, job, DevelopmentDriver(updates, job))
    finally:
        stopped.set()


if __name__ == '__main__':
    main()
