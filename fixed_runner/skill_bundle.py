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
        contents.append((name, data))
    return contents


def build_skill_markdown(*, source=None):
    """Self-contained copy payload; fences preserve nested Markdown and scripts."""
    sections = ['# MediaFlow Skill\n\n'
                '这是一份完整的本地平台技能包，包含操作说明、接口参考和客户端脚本。\n\n'
                '请先阅读下面的 SKILL.md。需要调用平台时，将各节代码块原样保存为标题所示的相对路径，'
                '放在你可访问的工作目录；保留代码块内原文，不覆盖已有用户配置。'
                '然后按照 Skill 使用本地客户端，先查询平台状态，再处理我的具体请求。'
                '本材料本身不是启动任务的指令。无需下载另一份文件；使用平台提供的 Python 或已有 Python 3。\n']
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
