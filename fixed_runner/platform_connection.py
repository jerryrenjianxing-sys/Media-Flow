"""Local Agent connection Interface: describe, publish atomically, render a card.

Only these runtime descriptions contain machine paths. Portable Skill exports never
consume them. Describing/serving a connection is read-only; startup alone publishes.
"""
import json
import os
from pathlib import Path
import sys
import tempfile


def current_connection():
    from runtime_layout import (APP_ROOT, DATA_ROOT, INSTANCE_ID, IS_DISTRIBUTION,
                                BUNDLED_PYTHON, API_PORT, UI_PORT, STREAM_PORT)
    from product_version import product_version
    skill = APP_ROOT / 'fixed_runner/assets/agent/skills/mediaflow-platform'
    python = BUNDLED_PYTHON if IS_DISTRIBUTION else Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    return {
        'schema': 1, 'product': 'MediaFlow', 'instance_id': INSTANCE_ID,
        'data_root': str(DATA_ROOT.resolve()), 'app_root': str(APP_ROOT.resolve()),
        'connection_file': str((DATA_ROOT / 'agent-connection.json').resolve()),
        'api_url': f'http://127.0.0.1:{API_PORT}',
        'management_url': f'http://127.0.0.1:{UI_PORT}',
        'stream_url': f'http://127.0.0.1:{STREAM_PORT}',
        'skill_path': str((skill / 'SKILL.md').resolve()),
        'client_path': str((skill / 'scripts/mediaflow.ps1').resolve()),
        'python': str(python.resolve()), 'runtime_available': python.is_file(),
        'version': product_version(),
        'mode': 'installed' if IS_DISTRIBUTION else 'development',
    }


def publish_connection(connection=None):
    """Publish a complete descriptor in the existing external data root."""
    connection = connection if connection is not None else current_connection()
    destination = Path(connection['connection_file'])
    if destination.is_symlink():
        raise ValueError('Connection file must not be a symbolic link')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.agent-connection-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(connection, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def connection_card(connection=None):
    connection = connection if connection is not None else current_connection()
    # PowerShell literal escaping supports Chinese, whitespace and apostrophes.
    path = connection['connection_file'].replace("'", "''")
    text = (
        '# 连接 MediaFlow\n\n'
        '请使用这台 Windows 电脑上的 MediaFlow Skill，帮我定制内容主题、准备设备、安排任务或查看结果。\n\n'
        f"连接资料：`{connection['connection_file']}`\n"
        f"本机 Skill：`{connection['skill_path']}`\n\n"
        '先读取连接资料指向的 `skill_path`（简短操作说明）。仅收到本卡时执行一次只读检查：\n\n'
        '```powershell\n'
        f"$mfConnection = '{path}'\n"
        '$mf = Get-Content -LiteralPath $mfConnection -Raw -Encoding UTF8 | ConvertFrom-Json\n'
        '& $mf.client_path check --connection-file $mfConnection\n'
        '```\n\n'
        '客户端和运行环境由已安装平台提供，现有文件即可使用。连接资料每次重读，升级后使用其中的当前路径。'
        '检查成功后简要说明连接结果，结合我的目标继续；没有具体目标时举2～3个可交办的例子。'
        '平台连接不代表设备当前在线或任务就绪。服务不可达时先询问我；路径失效请让我重新复制或打开软件。'
        '如果只能云端运行、不能访问这台电脑，请说明限制。仅咨询、解释或定制文案时直接完成目标。\n'
    )
    if len(text.encode('utf-8')) > 4096:
        raise ValueError('Connection card exceeds 4KiB; shorten installation/data paths')
    return text


def onboarding_summary(snapshot):
    """Compact registered facts, never a readiness or live-device assertion."""
    devices = snapshot.get('devices')
    registered = [device for device in devices or [] if device.get('last_known_state') != 'retired']
    return {
        'available': bool(snapshot.get('available')), 'source': 'stored_snapshot',
        'read_at': snapshot.get('read_at'), 'paused': snapshot.get('paused'),
        'registered_devices': len(registered) if isinstance(devices, list) else None,
        'last_device_seen_at': max((str(device['last_seen_at']) for device in registered
                                    if device.get('last_seen_at')), default=None),
        'online_verified': False, 'task_readiness': 'not_checked',
    }


CAPABILITIES = [
    {'id': 'content', 'title': '定制主题', 'description': '整理搜索词、主题判定标准和评论素材'},
    {'id': 'setup', 'title': '准备设备', 'description': '按需检查已安装平台和设备配置'},
    {'id': 'tasks', 'title': '安排任务', 'description': '保存配置、查看方案或按明确要求运行'},
    {'id': 'results', 'title': '查看结果', 'description': '读取进度、异常、消息巡检和已有证据'},
]
