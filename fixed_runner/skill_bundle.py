"""Build the downloadable, runtime-free MediaFlow Skill archive."""
from pathlib import Path
import re
import zipfile


SKILL_SOURCE = Path(__file__).parent/'assets/agent/skills/mediaflow-platform'
SKILL_FILES = (
    'NOTICE.md',
    'README.md',
    'SKILL.md',
    'config.example.json',
    'examples/execute-arguments.json',
    'examples/plan-arguments.json',
    'examples/request-status-arguments.json',
    'references/api.md',
    'references/troubleshooting.md',
    'references/workflows.md',
    'scripts/_common.py',
    'scripts/mediaflow.py',
    'scripts/mediaflow.ps1',
)
_WINDOWS_USER_PATH = re.compile(rb'(?i)[a-z]:[\\/](?:users|documents and settings)[\\/]')


def build_skill_bundle(destination, *, source=None):
    """Write a deterministic allowlisted Skill ZIP and return its path."""
    source = Path(source or SKILL_SOURCE)
    destination = Path(destination)
    is_link = lambda path: path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())
    if is_link(source) or any(is_link(path) for path in source.rglob('*')):
        raise ValueError('Skill bundle source contains a symbolic link')
    files = [(name, source/name) for name in SKILL_FILES]
    missing = [name for name, path in files if not path.is_file()]
    if missing:
        raise ValueError('Skill bundle source is incomplete: ' + ', '.join(missing))
    contents = []
    for name, path in files:
        data = path.read_bytes()
        if _WINDOWS_USER_PATH.search(data) or str(source.resolve()).encode() in data:
            raise ValueError('%s contains a machine-specific absolute path' % name)
        contents.append((name, data))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if is_link(destination):
        raise ValueError('Skill bundle destination is a symbolic link')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(contents):
            info = zipfile.ZipInfo('mediaflow-platform/' + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o755 if name.endswith(('.py', '.ps1')) else 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)
    return destination
