"""Read persisted first-contact information without scans or state reconciliation."""
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3


def stored_onboarding(database_path):
    result = {'available': False, 'source': 'stored_snapshot',
        'read_at': datetime.now(timezone.utc).isoformat(),
        'paused': None, 'task_summary': None, 'devices': None,
        'reason_code': 'snapshot_unavailable',
        'user_message': '平台已连接；登记快照暂不可读，尚未确认设备现状'}
    if database_path is None:
        return result
    try:
        # mode=ro refuses missing databases; query_only protects future accidental writes.
        with closing(sqlite3.connect(Path(database_path).resolve().as_uri() + '?mode=ro',
                                     uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            paused = db.execute("SELECT value FROM system_state WHERE key='paused'").fetchone()
            tasks = dict(db.execute('SELECT status, count(*) FROM tasks GROUP BY status').fetchall())
            devices = [dict(row) for row in db.execute(
                'SELECT id, name, provider, provider_instance_id, state AS last_known_state, '
                'standard_status AS last_known_environment, last_seen_at, updated_at '
                'FROM virtual_devices ORDER BY created_at DESC, id DESC')]
            for device in devices:
                device['online'] = None
        result.update(available=True, paused=(paused['value'] == '1') if paused else None,
            task_summary=tasks, devices=devices, reason_code='snapshot_read',
            user_message='平台已连接；设备为上次登记快照，不代表当前在线。首次检查未扫描或操作设备')
    except (OSError, ValueError, sqlite3.Error):
        pass
    return result
