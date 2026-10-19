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
    'examples/content-plan-nut-factory.json',
    'examples/execute-arguments.json',
    'examples/plan-arguments.json',
    'examples/preset-zero-write.json',
    'examples/request-status-arguments.json',
    'references/api.md',
    'references/content-guide.md',
    'references/setup.md',
    'references/troubleshooting.md',
    'references/workflows.md',
    'scripts/_common.py',
    'scripts/mediaflow.py',
    'scripts/mediaflow.ps1',
)
_MACHINE_PATH = re.compile(
    r'(?i)(?<![a-z0-9])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+'
    r'|/(?:home|users)/[^/\s]+(?:/|$)|/root(?:/|$))')


def _skill_contents(source=None):
    """Read the same portable, allowlisted material for every representation."""
    source = Path(source or SKILL_SOURCE)
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
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ValueError('%s is not UTF-8 text' % name) from exc
        if _MACHINE_PATH.search(text) or str(source.resolve()) in text:
            raise ValueError('%s contains a machine-specific absolute path' % name)
        if name == 'SKILL.md' and len(text.encode('utf-8')) > 8192:
            raise ValueError('Entry Skill exceeds 8KiB; disclose branch details in references')
        contents.append((name, data))
    return contents


def build_skill_markdown(*, source=None):
    """Full offline reference export; not the default Agent connection card."""
    sections = ['# MediaFlow Skill\n\n'
                '这是一份完整的本地平台技能包，包含操作说明、接口参考和客户端脚本。\n\n'
                '这是阅读、审阅与离线参考导出。接入Agent请使用首页“复制Skill”的短连接说明，'
                '或下载ZIP解压后直接使用目录里的SKILL.md和客户端。各节保留原文件供核对，'
                '无需在首次检查前逐字重建文件。咨询或定制文案直接按目标完成，服务启动仍先询问用户。\n']
    languages = {'.md': 'markdown', '.json': 'json', '.py': 'python', '.ps1': 'powershell'}
    for name, data in sorted(_skill_contents(source)):
        text = data.decode('utf-8-sig')
        fence = '`' * max(4, max((len(run) + 1 for run in re.findall(r'`+', text)), default=4))
        sections.append(f'### `mediaflow-platform/{name}`\n\n{fence}{languages.get(Path(name).suffix, "text")}\n{text}\n{fence}\n')
    return '\n'.join(sections)


def build_skill_bundle(destination, *, source=None):
    """Write a deterministic allowlisted Skill ZIP and return its path."""
    contents = _skill_contents(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or bool(getattr(destination, 'is_junction', lambda: False)()):
        raise ValueError('Skill bundle destination is a symbolic link')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(contents):
            info = zipfile.ZipInfo('mediaflow-platform/' + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o755 if name.endswith(('.py', '.ps1')) else 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)
    return destination
