"""Offline release-directory smoke test; no registration, Worker or device access."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', type=Path)
    args = parser.parse_args()
    stage = args.stage.resolve()
    python = stage / 'runtime/python/python.exe'
    node = stage / 'runtime/node/node.exe'
    manifest = json.loads((stage / 'release-manifest.json').read_text(encoding='utf-8-sig'))
    assert manifest['embedded_agent'] is False
    assert not (stage / 'runtime/opencode').exists()
    assert not (stage / 'runtime/pi').exists()
    windows = Path(os.environ['WINDIR'])
    with tempfile.TemporaryDirectory(prefix='mediaflow-release-smoke-') as folder:
        root = Path(folder)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('MEDIAFLOW_', 'RISKFLOW_', 'PYTHON', 'NODE_', 'CODEX_', 'HERMES_'))}
        env.update(PATH=str(windows / 'System32') + os.pathsep + str(windows),
                   MEDIAFLOW_APP_ROOT=str(stage), MEDIAFLOW_DATA_ROOT=str(root / 'data'),
                   LOCALAPPDATA=str(root / 'local'), PYTHONUTF8='1')
        code = r'''
import json, sys, threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import runtime_layout
assert runtime_layout.IS_DISTRIBUTION
assert str(runtime_layout.BUNDLED_PYTHON) == str(Path(sys.executable))
import badge_glyphs
assert badge_glyphs._templates()
import control_api
def forbidden(*args, **kwargs):
    raise AssertionError('Smoke test must not access devices or start Workers')
control_api.adb_device_states = forbidden
control_api.ensure_worker = forbidden
control_api.ensure_workers = forbidden
# Intentionally use the production handler, not main(): no startup ADB reconciliation.
server = control_api.ThreadingHTTPServer(('127.0.0.1', 0), control_api.Handler)
print(json.dumps({'port': server.server_port}), flush=True)
server.serve_forever()
'''
        api = subprocess.Popen([str(python), '-I', '-u', '-c', code, str(stage / 'fixed_runner')],
                               env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ui = None
        try:
            # Bounded readiness without waiting indefinitely on a pipe.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(api.stdout.readline)
                try:
                    line = future.result(timeout=30)
                except TimeoutError:
                    api.terminate()
                    raise
            if not line:
                raise RuntimeError(api.stderr.read())
            port = json.loads(line)['port']
            base = f'http://127.0.0.1:{port}'
            def read(path):
                with urllib.request.urlopen(base + path, timeout=10) as response:
                    return response.read()
            catalog = json.loads(read('/api/automation'))
            assert catalog['api_version'] == '1', catalog
            assert b'SKILL.md' in read('/api/platform-skill?format=markdown')
            assert read('/api/platform-skill').startswith(b'PK')
            assert json.loads(read('/api/content-plans'))['content_plans'] == []
            skill = stage / 'fixed_runner/assets/agent/skills/mediaflow-platform/scripts/mediaflow.ps1'
            env.update(MEDIAFLOW_API_URL=base, MEDIAFLOW_SKILL_CONFIG=str(root / 'absent.json'))
            client = subprocess.run([str(windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                                     '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(skill), 'check'],
                                    env=env, cwd=root, capture_output=True, timeout=20)
            if client.returncode:
                raise RuntimeError(client.stdout.decode('utf-8', errors='replace') + client.stderr.decode('utf-8', errors='replace'))
            # This route is read-only and exercises PowerShell's bundled-Python discovery.
            client = subprocess.run([str(windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                                     '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(skill), 'list_tasks'],
                                    env=env, cwd=root, capture_output=True, timeout=20)
            if client.returncode:
                raise RuntimeError(client.stdout.decode('utf-8', errors='replace') + client.stderr.decode('utf-8', errors='replace'))
            # Port 0 lets Windows allocate a test port, not the production 3001.
            env.update(PORT='0', HOST='127.0.0.1')
            with (root / 'ui.log').open('w', encoding='utf-8') as log:
                ui = subprocess.Popen([str(node), str(stage / 'control_console/standalone/server.js')],
                                      env=env, cwd=root, stdout=log, stderr=log)
                deadline = time.monotonic() + 25
                import re
                address = None
                while time.monotonic() < deadline:
                    text = (root / 'ui.log').read_text(encoding='utf-8')
                    match = re.search(r'http://(?:localhost|127\.0\.0\.1):(\d+)', text)
                    if match and match[1] != '0':
                        address = 'http://127.0.0.1:' + match[1]
                        break
                    if ui.poll() is not None:
                        raise RuntimeError(text)
                    time.sleep(.2)
                if not address:
                    raise RuntimeError('UI did not report a test address: ' + text)
                for route in ('/', '/workbench', '/manage', '/records', '/settings'):
                    with urllib.request.urlopen(address + route, timeout=15) as response:
                        html = response.read()
                        assert b'MediaFlow' in html, route
            print(json.dumps({'ok': True, 'version': manifest['version'],
                              'api': 'production handler / isolated data', 'skill': 'PowerShell, no system Python',
                              'glyphs': 'v1+v2 loaded', 'ui': 'five server-rendered routes',
                              'path': 'Windows only; no Codex/Hermes', 'device_actions': 0}))
        finally:
            for process in (ui, api):
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    main()
