"""One OS-owned lifetime for native gateway and retained management console."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import time

from agent_process import ChildJob


def supervise(specs, job):
    children = []
    try:
        for command, env, cwd in specs:
            child = subprocess.Popen(command, env=env, cwd=cwd,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            children.append(child)
            job.assign(child)
        while True:
            for child in children:
                code = child.poll()
                if code is not None:
                    return code or 1
            time.sleep(.25)
    finally:
        # Closing the job also covers abnormal host termination on Windows.
        job.close()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def require_available_ports(ports):
    for number in ports:
        try:
            with socket.socket() as probe:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(('127.0.0.1', number))
        except OSError:
            raise RuntimeError(f'本机端口 {number} 已被占用，无法启动界面；请检查占用程序，不会结束其他进程') from None


def main():
    from runtime_control import ui_spec
    from runtime_layout import APP_ROOT, RUNTIME_ROOT
    legacy = ui_spec(native=False)
    legacy_port = 3001
    require_available_ports([int(os.environ.get('MEDIAFLOW_UI_PORT', '3000')), legacy_port])
    env = dict(os.environ, **(legacy.env or {}))
    env.update(PORT=str(legacy_port), HOST='127.0.0.1')
    command = [str(legacy_port) if part == '3000' else part for part in legacy.command]
    gateway_env = dict(env, PORT=os.environ.get('MEDIAFLOW_UI_PORT','3000'),
        MEDIAFLOW_LEGACY_UI_URL=f'http://127.0.0.1:{legacy_port}',
        MEDIAFLOW_NATIVE_CONNECTION=str(RUNTIME_ROOT/'agent/engine/gateway-connection.json'),
        MEDIAFLOW_NATIVE_DIST=str(APP_ROOT/'native_console/dist'))
    return supervise([(command, env, legacy.cwd),
        ([command[0], str(APP_ROOT/'native_console/server.mjs')], gateway_env, str(APP_ROOT))], ChildJob())


if __name__ == '__main__':
    raise SystemExit(main())
