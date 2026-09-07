"""Independent official OpenCode process. No chat proxy or platform lifecycle calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from agent_native import native_config
from agent_process import ChildJob, DirectoryLease
from agent_runtime import ENGINE_VERSION, ENGINE_SHA256, install_platform_skill, isolated_environment
from runtime_control import ProcessSpec, RuntimeControl
from runtime_layout import APP_ROOT, RUNTIME_ROOT


def host_event(root, event, **fields):
    root.mkdir(parents=True, exist_ok=True)
    with (root/'independent-host.log').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'time': time.time(), 'event': event, **fields})+'\n')


def launch_spec(root, binary, *, python=None, port=3000):
    root, binary = Path(root), Path(binary)
    client_python = Path(python or sys.executable)
    if client_python.name.lower() == 'pythonw.exe':
        client_python = client_python.with_name('python.exe')
    env = isolated_environment(root)
    for name in list(env):
        if name.startswith('MEDIAFLOW_AGENT_') or name in {
            'OPENCODE_DISABLE_PROJECT_CONFIG', 'OPENCODE_DISABLE_MODELS_FETCH'}:
            env.pop(name, None)
    env.update(OPENCODE_CONFIG_CONTENT=json.dumps(native_config(), ensure_ascii=False),
        MEDIAFLOW_API_URL='http://127.0.0.1:48138', MEDIAFLOW_PYTHON=str(client_python),
        PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    workspace = root/'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    return ProcessSpec(role='opencode-engine',
        command=(str(binary), 'serve', '--hostname', '127.0.0.1', '--port', str(port)),
        cwd=str(workspace), log_path=str(root/'official-web.log'),
        expected_executable=str(binary.resolve()), required_markers=('serve', '--port', str(port)), env=env)


def main():
    parser = argparse.ArgumentParser(description='Independent MediaFlow Agent (official OpenCode Web UI)')
    parser.add_argument('action', choices=('run', 'stop', 'status', 'request-start'))
    parser.add_argument('--root', type=Path, default=RUNTIME_ROOT/'agent/engine')
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--port', type=int, default=3000)
    args = parser.parse_args()
    if args.action == 'run':
        host_event(args.root, 'host_started', pid=os.getpid(), port=args.port)
    # Never put the Agent into the platform's process registry.
    control = RuntimeControl(registry_root=args.root/'independent-processes')
    stop_intent = args.root/'independent-stop'
    if args.action == 'request-start':
        stop_intent.unlink(missing_ok=True)
        return 0
    if args.action == 'stop':
        args.root.mkdir(parents=True, exist_ok=True)
        stop_intent.touch()
    if args.action != 'run':
        result = control.stop('opencode-engine') if args.action == 'stop' else control.status('opencode-engine')
        print(json.dumps(result, ensure_ascii=False))
        return 0
    lease = DirectoryLease(args.root/'engine.lock')
    if not lease.acquire():
        raise RuntimeError('Agent数据目录已由另一实例使用；未重复启动。')
    job = ChildJob()
    children, logs = [], []
    try:
        if stop_intent.exists():
            return 0
        binary = args.binary or APP_ROOT/'runtime/opencode/opencode.exe'
        if not binary.is_file():
            binary = APP_ROOT/f'packaging/tools/opencode/{ENGINE_VERSION}/expanded/opencode.exe'
        with binary.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != ENGINE_SHA256:
            raise RuntimeError('官方OpenCode文件校验不符；未启动。')
        from native_console_host import require_available_ports
        require_available_ports([args.port])
        install_platform_skill(args.root)
        spec = launch_spec(args.root, binary, port=args.port)
        def launch(spec):
            log = open(spec.log_path, 'ab')
            logs.append(log)
            child = subprocess.Popen(spec.command, cwd=spec.cwd, env=spec.env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            children.append(child)
            job.assign(child)
            return child.pid
        control = RuntimeControl(registry_root=args.root/'independent-processes', launcher=launch)
        if stop_intent.exists():
            return 0
        result = control.start(spec)
        if not result.get('running'):
            raise RuntimeError('官方OpenCode未启动，请检查独立Agent日志。')
        host_event(args.root, 'engine_started', pid=children[0].pid if children else None)
        while children and children[0].poll() is None:
            if stop_intent.exists():
                control.stop('opencode-engine')
                break
            time.sleep(.5)
        # Explicit stop is not an automatic restart request.
        code = children[0].poll() if children else 1
        host_event(args.root, 'engine_exited', code=code, requested_stop=stop_intent.exists())
        return 0 if stop_intent.exists() else (code or 0)
    except Exception:
        # Fixed diagnostic classification, never serialize upstream responses or credentials.
        host_event(args.root, 'host_failed')
        raise
    finally:
        job.close()
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)
        for log in logs:
            log.close()
        lease.close()


if __name__ == '__main__':
    raise SystemExit(main())
