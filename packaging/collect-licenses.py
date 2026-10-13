"""Collect licensing and pin the bytes of the already-tested runtime payload."""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess


def digest(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def collect(stage, project_root):
    stage, project_root = Path(stage).resolve(), Path(project_root).resolve()
    licenses = stage / 'licenses'
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project_root / 'LICENSE', licenses / 'MediaFlow-MIT.txt')
    copied, seen = [], set()
    for area in ('runtime/python', 'runtime/node', 'runtime/platform-tools',
                 'control_console/standalone/node_modules', 'device_stream_host/node_modules'):
        for path in sorted((stage / area).rglob('*')):
            if not path.is_file() or path.name.lower().split('.')[0] not in {
                'license', 'licence', 'licenses', 'copying', 'notice', 'notices', 'copyright'}:
                continue
            sha = digest(path)
            relative = path.relative_to(stage).as_posix()
            target = licenses / 'third-party' / (sha[:16] + '-' + path.name)
            if sha not in seen:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                seen.add(sha)
            copied.append({'source': relative, 'sha256': sha, 'license_file': target.relative_to(licenses).as_posix()})
    for required in ('runtime/python/LICENSE.txt', 'runtime/node/LICENSE.txt', 'runtime/platform-tools/NOTICE.txt'):
        if not (stage / required).is_file():
            raise ValueError('Runtime license is missing: ' + required)
    python = stage / 'runtime/python/python.exe'
    node = stage / 'runtime/node/node.exe'
    python_version = subprocess.check_output([str(python), '-I', '-c', 'import platform;print(platform.python_version())'], text=True).strip()
    node_version = subprocess.check_output([str(node), '--version'], text=True).strip()
    packages = sorted([{'name': dist.metadata['Name'], 'version': dist.version}
                      for dist in metadata.distributions(path=[str(stage / 'runtime/python/Lib/site-packages')])],
                      key=lambda item: item['name'].lower())
    locked = {}
    for area in ('runtime', 'device_stream_host/vendor'):
        for path in sorted((stage / area).rglob('*')):
            if path.is_file() and path.suffix.lower() not in {'.pyc', '.pyo'}:
                locked[path.relative_to(stage).as_posix()] = {'sha256': digest(path), 'bytes': path.stat().st_size}
    lock = {'schema': 1, 'platform': 'win-x64', 'python': python_version, 'node': node_version,
            'python_packages': packages, 'files': locked}
    (stage / 'runtime-lock.json').write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding='utf-8')
    (licenses / 'license-manifest.json').write_text(json.dumps(copied, ensure_ascii=False, indent=2), encoding='utf-8')
    (licenses / 'THIRD-PARTY-NOTICES.txt').write_text(
        'MediaFlow includes the following tested runtimes and dependencies.\n'
        'Python ' + python_version + '\nNode ' + node_version + '\n'
        + '\n'.join(item['name'] + ' ' + item['version'] for item in packages)
        + '\n\nThe complete license texts and their source locations are indexed in license-manifest.json.\n'
        + 'Electron and Chromium license texts are delivered beside MediaFlow.exe and in the versioned licenses ZIP.\n', encoding='utf-8')
    return {'licenses': len(copied), 'unique_license_texts': len(seen), 'runtime_files': len(locked),
            'python': python_version, 'node': node_version}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--project-root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.stage, args.project_root)))
