"""Offline context inventory and link guard; never import platform/runtime code."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = 'docs/context-inventory.json'
SUFFIXES = {'.md', '.yaml', '.yml', '.toml'}
CURRENT = {
    'AGENTS.md', 'README.md', 'PROJECT.md', 'STATUS.md', 'RUNBOOK.md', 'CONTEXT.md',
    'control_console/README.md', 'control_console/DESIGN.md', 'fixed_runner/README.md',
    'launcher/README.md', 'packaging/README.md', 'docs/context-cleanup.md',
    'docs/mbh-development.md', 'docs/current-followup.md',
    'docs/content-prompt-guide.md', 'docs/topic-policy-standard.md',
    'docs/real-device-agent-initialization.md',
}
# Negative/historical explanations are intentional; match obsolete instructions,
# not every mention of a retired product, port, or protection.
RETIRED = [
    (r'https?://(?:127\.0\.0\.1|localhost):3000(?:[/\s`]|$)', '旧产品入口3000'),
    (r'(?:完成|运行)\s*3\s*条零写入.*(?:才|交付为)', '逐设备三条使用门槛'),
    (r'必须(?:先|由用户)?(?:点击|点选).*确认卡', '强制确认卡'),
    (r'抖音已安装并登录', '登录作为统一前提'),
    (r'(?i)(?:manage-mediaflow-agent[^\n]*-Action\s+(?:Start|Register|Restart))', '退役Agent启动命令'),
    (r'每台(?:设备|虚拟机).{0,12}必须.{0,8}三次', '逐设备三次门槛'),
]


def inventory_paths(root):
    raw = subprocess.check_output(
        ['git', '-c', 'core.quotepath=false', 'ls-files', '-z', '--cached',
         '--others', '--exclude-standard'], cwd=root)
    paths = set(raw.decode('utf-8').split('\0')) - {''}
    return sorted(p for p in paths if (root / p).is_file() and (
        Path(p).suffix in SUFFIXES or
        (p.startswith('control_console/app/') and p.endswith('/page.tsx')) or
        p.startswith('fixed_runner/assets/agent/skills/mediaflow-platform/')))


def classify(path):
    if path in CURRENT or path.startswith('docs/decisions/'):
        return 'current', 'update', '现行入口/操作依据；统一最新要求，差距如实列出'
    if path.startswith('fixed_runner/assets/agent/skills/mediaflow-platform/'):
        return 'skill', 'retain', '唯一交付Skill来源；入口说明更新，脚本/参考按需复用'
    if path.startswith('openspec/specs/'):
        return 'contract', 'retain', '现行契约；混合旧兼容行为用适用范围隔离，不伪造验收'
    if path.startswith('openspec/changes/') and '/archive/' not in path:
        return 'change', 'retain', '活动变更/历史开发证据；适用范围见清理清单，未验任务不勾选'
    if path.startswith('control_console/app/'):
        return 'page-help', 'retain', '当前页面说明/历史结果兼容文案；不修改业务动作'
    if path in {'docs/agent-workbench.md', 'docs/opencode-contract.md',
                'docs/dev30-pi-runtime.md', 'native_console/README.md'}:
        return 'redirect', 'archive', '旧操作移至历史快照，原地址只指向当前入口和历史'
    if (path.startswith(('docs/history/', 'research/', 'outputs/',
                         'openspec/changes/archive/', 'packaging/release-notes/',
                         'docs/superpowers/')) or
        path in {'CHANGELOG.md', 'DESIGN.md'} or
        (path.startswith('docs/') and ('acceptance' in path or re.search(r'/dev\d', path)
                                      or 'audit' in path or 'batch-review' in path))):
        return 'historical', 'retain', '历史事实/已归档方案/原文快照；不作为当前操作，不改验收结论'
    return 'reference', 'retain', '工具说明/配置/素材；不独立决定产品流程，保留原职责'


def make_inventory(root):
    return {'schema': 1, 'scope': 'Git可见维护文档、配置、平台Skill及页面帮助；不读取运行数据',
            'files': [dict(zip(('path', 'role', 'action', 'reason'), (p, *classify(p))))
                      for p in inventory_paths(root)]}


def prose(text):
    """Ignore fenced examples for local-link checking, not for retired commands."""
    output, fence = [], None
    for line in text.splitlines():
        match = re.match(r'^\s*(`{3,}|~{3,})', line)
        if match:
            if fence is None:
                fence = match[1]
            elif match[1][0] == fence[0] and len(match[1]) >= len(fence):
                fence = None
            continue
        if fence is None:
            output.append(line)
    return '\n'.join(output)


def check_text(root, path, text, retired=True):
    errors = []
    if retired:
        for pattern, label in RETIRED:
            if re.search(pattern, text):
                errors.append(f'{path}: {label}')
    # Inline Markdown links, including fragments; reference-style links below.
    links = re.findall(r'\[[^\]\n]*\]\((<[^>]+>|[^)\n]+)\)', prose(text))
    links += re.findall(r'^\s*\[[^\]]+\]:\s*(\S+)', prose(text), re.M)
    for raw in links:
        href = raw.strip().strip('<>')
        if not href or href.startswith('#'):
            continue
        if ' "' in href:
            href = href.split(' "', 1)[0]
        parsed = urlsplit(href)
        if parsed.scheme or href.startswith('/'):
            continue
        target = (root / path).parent / unquote(parsed.path)
        if not target.exists():
            errors.append(f'{path}: 本地链接不存在: {href}')
        elif parsed.fragment and target.suffix == '.md':
            headings = re.findall(r'^#{1,6}\s+(.+)$', target.read_text(encoding='utf-8-sig'), re.M)
            anchors = {re.sub(r'[^\w\-\s]', '', h.lower()).replace(' ', '-') for h in headings}
            if unquote(parsed.fragment) not in anchors:
                errors.append(f'{path}: 标题锚点不存在: {href}')
    return errors


def validate(root, manifest):
    errors = []
    rows = manifest['files']
    registered = {r['path'] for r in rows}
    actual = set(inventory_paths(root))
    errors += [f'未登记: {p}' for p in sorted(actual - registered)]
    errors += [f'登记文件已失效: {p}' for p in sorted(registered - actual)]
    if len(registered) != len(rows):
        errors.append('重复登记路径')
    for row in rows:
        path = row['path']
        if row['role'] not in {'current', 'skill', 'contract', 'redirect', 'page-help', 'reference', 'change', 'historical'}:
            errors.append(f'{path}: 未知资料角色')
        if not row.get('reason') or row.get('action') not in {'retain', 'update', 'archive', 'delete'}:
            errors.append(f'{path}: 缺少有效处置依据')
        if path not in actual:
            continue
        if row['role'] in {'current', 'skill', 'contract', 'redirect'} and path.endswith('.md'):
            text = (root / path).read_text(encoding='utf-8-sig')
            errors += check_text(root, path, text, retired=row['role'] in {'current', 'skill'})
        if path == 'control_console/app/devices/guide/page.tsx':
            errors += check_text(root, path, (root / path).read_text(encoding='utf-8-sig'))
    return errors


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', action='store_true', help='仅输出登记清单，不写文件')
    args = parser.parse_args()
    if args.inventory:
        print(json.dumps(make_inventory(ROOT), ensure_ascii=False, separators=(',', ':')))
        return 0
    manifest = json.loads((ROOT / REGISTRY).read_text(encoding='utf-8-sig'))
    errors = validate(ROOT, manifest)
    for error in errors:
        print(error)
    print(f'context: {len(manifest["files"])} files; {len(errors)} errors')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
