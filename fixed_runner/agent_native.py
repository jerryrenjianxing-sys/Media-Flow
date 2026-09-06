"""Native OpenCode configuration and one-way, non-destructive migration.

No conversation, tool-dispatch or permission state lives here. OpenCode owns it.
Only integration receipts are retained; credentials stay in native auth.json.
"""
from __future__ import annotations

import json
import re
from contextlib import closing
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from agent_runtime import PROVIDER_ID, MODEL_ID


def native_config():
    return {
        '$schema': 'https://opencode.ai/config.json',
        'autoupdate': False, 'share': 'disabled',
        'model': f'{PROVIDER_ID}/{MODEL_ID}',
        'small_model': f'{PROVIDER_ID}/{MODEL_ID}',
        'provider': {PROVIDER_ID: {
            'npm': '@ai-sdk/openai-compatible', 'name': '千问AI平台 · Token Plan',
            'options': {'baseURL': 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'},
            'models': {MODEL_ID: {
                'name': 'Qwen 3.8 Flash', 'tool_call': True, 'attachment': True,
                'modalities': {'input': ['text', 'image'], 'output': ['text']},
                'limit': {'context': 128000, 'output': 8192},
                'options': {'enable_thinking': False},
            }},
        }},
    }


class NativeMigration:
    def __init__(self, root, auth, key_resolver):
        self.root, self.auth, self.key_resolver = Path(root), auth, key_resolver
        self._lock = threading.RLock()

    def _read(self):
        path = self.root/'migration.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}

    def _save(self, key, value):
        self.root.mkdir(parents=True, exist_ok=True)
        receipt = self._read()
        receipt[key] = value
        pending = self.root/'migration.json.tmp'
        pending.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(self.root/'migration.json')
        return value

    def backup(self, databases):
        """SQLite backup includes WAL; repeated invocation preserves the first snapshot."""
        with self._lock:
            saved = self._read().get('databases')
            if saved:
                if not all(Path(p).is_file() for p in saved.values()):
                    raise RuntimeError('迁移备份缺失；请恢复备份后重试，原数据未删除')
                for path in saved.values():
                    self._check_backup(Path(path))
                return saved
            backups = {}
            for name, source in databases.items():
                source = Path(source)
                if not source.is_file():
                    continue
                target = self.root/'backup'/f'{name}.db'
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.is_file():
                    pending = target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
                    try:
                        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src:
                            with closing(sqlite3.connect(pending)) as dst:
                                src.backup(dst)
                        self._check_backup(pending)
                        pending.replace(target)
                    finally:
                        pending.unlink(missing_ok=True)
                self._check_backup(target)
                backups[name] = str(target)
            return self._save('databases', backups)

    @staticmethod
    def _check_backup(path):
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('invalid backup')
        except (sqlite3.Error, ValueError, OSError):
            raise RuntimeError('数据库备份校验失败；请保留现场并重新准备备份，原数据未更改') from None

    def credentials(self):
        """Optional startup step: inability to read/save Key never blocks native UI."""
        with self._lock:
            try:
                native = self.auth.native_auth(PROVIDER_ID)
                if native:
                    result = {'state': 'preserved', 'message': '保留原生服务商配置'}
                else:
                    key = self.key_resolver()
                    if not key:
                        result = {'state': 'not_configured', 'message': '可新建会话；发送前请在模型设置中配置千问'}
                    else:
                        saved = self.auth.runtime.request('PUT', '/auth/'+PROVIDER_ID, {'type': 'api', 'key': key})
                        if saved is not True or self.auth.native_auth(PROVIDER_ID) != {'type': 'api', 'key': key}:
                            raise ValueError('native credential readback did not match')
                        self.auth.runtime.request('POST', '/instance/dispose')
                        result = {'state': 'migrated', 'message': '当前千问配置已迁入原生凭证目录'}
            except Exception:
                # Upstream exceptions can echo Key. No exception text enters this receipt.
                result = {'state': 'failed', 'reason_code': 'native_credential_migration_failed',
                          'message': '千问配置迁移未完成；旧配置保留，可在设置中重试。新建会话和历史仍可用',
                          'retryable': True}
            return self._save('credentials', {**result, 'updated': time.time()})

    def status(self):
        # Backup filenames and credentials are never exposed through this view.
        return self._read().get('credentials', {'state': 'pending', 'message': '等待原生引擎就绪'})

    def archive_cleared(self, retained):
        """Use only a previously authorized cleanup receipt, never a list difference."""
        with self._lock:
            request = self.root/'archive-request.json'
            if not request.is_file():
                return {}
            ids = json.loads(request.read_text(encoding='utf-8')).get('session_ids', [])
            receipt = self._read().get('archives', {})
            for sid in ids:
                if not isinstance(sid, str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]+', sid):
                    continue
                if sid in retained or receipt.get(sid) == 'archived':
                    continue
                try:
                    current = self.auth.runtime.request('GET', '/session/'+sid)
                    if not current.get('time', {}).get('archived'):
                        self.auth.runtime.request('PATCH', '/session/'+sid,
                            {'time': {'archived': int(time.time()*1000)}})
                    check = self.auth.runtime.request('GET', '/session/'+sid)
                    receipt[sid] = 'archived' if check.get('time', {}).get('archived') else 'retryable'
                except Exception:
                    receipt[sid] = 'retryable'
                self._save('archives', receipt)
            return receipt
