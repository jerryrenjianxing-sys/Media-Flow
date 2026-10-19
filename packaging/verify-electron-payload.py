"""Pure file audit for the Electron platform payload; no backend or device actions."""
import argparse
import hashlib
import json
from pathlib import Path

REQUIRED = ('runtime/python/python.exe', 'runtime/python/pythonw.exe', 'runtime/node/node.exe',
            'runtime/platform-tools/adb.exe', 'control_console/standalone/server.js',
            'device_stream_host/vendor/scrcpy-server-v3.3.3', 'fixed_runner/background_host.py', 'fixed_runner/background_startup.py',
            'fixed_runner/assets/home_badge_glyphs_v1.json', 'fixed_runner/assets/home_badge_glyphs_v2.json',
            'fixed_runner/assets/agent/skills/mediaflow-platform/SKILL.md',
            'fixed_runner/platform_connection.py',
            'fixed_runner/assets/agent/skills/mediaflow-platform/scripts/mediaflow.ps1',
            'assets/agent/repair-source.zip', 'licenses/MediaFlow-MIT.txt', 'licenses/license-manifest.json',
            'installer/electron/instance-profile.ps1', 'installer/electron/manager-diagnostics.ps1', 'manage-mediaflow.ps1',
            'install-mediaflow-background.ps1', 'uninstall-mediaflow-background.ps1')
FORBIDDEN = {'tasks.db', 'device_profiles.json', 'platform_profiles.json', 'auth.json', 'agent-connection.json',
             'openrouter-api-key.dpapi', 'openrouter-api-key.pending.dpapi', 'model-connection.json'}

def verify(stage):
    stage = Path(stage).resolve()
    missing = [name for name in REQUIRED if not (stage / name).is_file() or not (stage / name).stat().st_size]
    if missing:
        raise ValueError('Incomplete Electron payload: ' + ', '.join(missing))
    for path in stage.rglob('*'):
        if any(part.lower() == 'installer-backups' for part in path.relative_to(stage).parts):
            raise ValueError('Local installer recovery data was bundled: ' + path.relative_to(stage).as_posix())
        if path.is_file() and (path.name in FORBIDDEN or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.log', '.pid'}):
            raise ValueError('Local state was bundled: ' + path.relative_to(stage).as_posix())
    for retired in ('MediaFlow.exe','WebView2Loader.dll','Microsoft.Web.WebView2.Core.dll',
                    'runtime/opencode','runtime/pi','native_console'):
        if (stage / retired).exists():
            raise ValueError('Retired shell/engine was bundled: ' + retired)
    lock = json.loads((stage / 'runtime-lock.json').read_text(encoding='utf-8'))
    for name, identity in lock['files'].items():
        path = stage / name
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != identity['sha256'] or path.stat().st_size != identity['bytes']:
            raise ValueError('Runtime payload changed after lock: ' + name)
    return {'ok': True, 'required_resources': len(REQUIRED), 'locked_runtime_files': len(lock['files']),
            'device_actions': 0, 'backend_started': False}

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage', type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(args.stage)))
