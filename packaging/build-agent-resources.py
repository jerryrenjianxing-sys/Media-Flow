"""Assemble pinned Agent and repair resources without reading any user data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'fixed_runner'))
from agent_runtime import ENGINE_VERSION, ENGINE_SHA256
from repair_materials import build_bundle, read_bundle
from agent_dependencies import dependency_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    stage = args.stage.resolve()
    lock = json.loads((ROOT / 'packaging/opencode.lock.json').read_text())
    if lock['version'] != ENGINE_VERSION:
        raise ValueError('Engine version lock disagrees with runtime')
    binary = ROOT / f'packaging/tools/opencode/{ENGINE_VERSION}/expanded/opencode.exe'
    with binary.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != ENGINE_SHA256:
            raise ValueError('OpenCode binary checksum mismatch')
    target = stage / 'runtime/opencode'
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, target / 'opencode.exe')
    shutil.copy2(ROOT / 'packaging/licenses/OpenCode.txt', target / 'LICENSE.txt')
    shutil.copy2(ROOT / 'packaging/opencode.lock.json', target / 'source-lock.json')
    dependencies = ROOT / 'packaging/agent-engine'
    dependency_lock = dependency_manifest(dependencies)
    for name in dependency_lock['files']:
        destination = target / 'dependencies' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dependencies/name, destination)
    (target/'dependencies/dependency-manifest.json').write_text(json.dumps(dependency_lock, indent=2), encoding='utf-8')
    shutil.copytree(ROOT / 'fixed_runner/assets/agent', stage / 'fixed_runner/assets/agent', dirs_exist_ok=True)
    bundle = stage / 'assets/agent/repair-source.zip'
    receipt = build_bundle(ROOT, bundle, args.revision)
    manifest, _ = read_bundle(bundle)
    if manifest['source_revision'] != args.revision:
        raise ValueError('Repair source revision mismatch')
    (bundle.parent / 'repair-source-manifest.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'engine_version': ENGINE_VERSION, 'engine_sha256': ENGINE_SHA256,
                      'repair_source_sha256': receipt['sha256'], 'repair_source_bytes': receipt['bytes'], 'files': len(receipt['files'])}))


if __name__ == '__main__':
    main()
