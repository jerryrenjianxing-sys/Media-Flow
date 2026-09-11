"""Assemble external-Agent Skill and common repair resources; never load an engine."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'fixed_runner'))
from repair_materials import build_bundle, read_bundle

REQUIRED = ('SKILL.md', 'README.md', 'references/setup.md', 'references/api.md',
            'references/workflows.md', 'references/content-guide.md',
            'references/troubleshooting.md', 'scripts/mediaflow.py',
            'scripts/mediaflow.ps1', 'scripts/_common.py')


def assemble(root, stage, revision):
    root, stage = Path(root).resolve(), Path(stage).resolve()
    source = root / 'fixed_runner/assets/agent/skills/mediaflow-platform'
    missing = [name for name in REQUIRED if not (source / name).is_file()]
    if missing:
        raise ValueError('Platform Skill resources missing: ' + ', '.join(missing))
    if stage == root or stage in root.parents:
        raise ValueError('Stage must not overwrite source')
    # An old stage must not silently keep a retired engine in the new release.
    if any((stage / name).exists() for name in ('runtime/opencode', 'runtime/pi', 'native_console')):
        raise ValueError('Stage contains retired engine resources; use a fresh stage')
    target = stage / 'fixed_runner/assets/agent/skills/mediaflow-platform'
    shutil.copytree(source, target, dirs_exist_ok=True)
    bundle = stage / 'assets/agent/repair-source.zip'
    receipt = build_bundle(root, bundle, revision)
    manifest, _ = read_bundle(bundle)
    if manifest['source_revision'] != revision:
        raise ValueError('Repair source revision mismatch')
    (bundle.parent / 'repair-source-manifest.json').write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'agent_integration': 'external-skill', 'embedded_agent': False,
            'skill_files': {name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                            for name in REQUIRED},
            'repair_source_sha256': receipt['sha256'], 'repair_source_bytes': receipt['bytes'],
            'files': len(receipt['files'])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    print(json.dumps(assemble(ROOT, args.stage, args.revision)))


if __name__ == '__main__':
    main()
