from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class EvidenceGovernance:
    def __init__(self, db_path: Path, roots: Iterable[Path], backup_root: Path) -> None:
        self.db_path = Path(db_path).resolve()
        resolved = [Path(root).resolve() for root in roots]
        self.roots = [
            root for root in resolved
            if not any(other != root and other in root.parents for other in resolved)
        ]
        self.backup_root = Path(backup_root).resolve()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence_governance (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO evidence_governance(key, value, updated_at) "
                "VALUES ('retention_days', '30', ?)", (_now_iso(),)
            )
            connection.commit()

    def policy(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value, updated_at FROM evidence_governance "
                "WHERE key='retention_days'"
            ).fetchone()
        return {
            "retention_days": int(row["value"] if row else 30),
            "auto_delete": False,
            "updated_at": row["updated_at"] if row else None,
        }

    def save_policy(self, retention_days: int) -> dict[str, Any]:
        if not isinstance(retention_days, int) or not 1 <= retention_days <= 3650:
            raise ValueError("证据保留天数必须是 1-3650 的整数")
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO evidence_governance(key, value, updated_at) VALUES "
                "('retention_days', ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (str(retention_days), _now_iso()),
            )
            connection.commit()
        return self.policy()

    def inventory(self) -> dict[str, Any]:
        files: list[tuple[Path, int, float]] = []
        for root in self.roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or self.backup_root == path.parent or self.backup_root in path.parents:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append((path, stat.st_size, stat.st_mtime))
        timestamps = [item[2] for item in files]
        return {
            "file_count": len(files),
            "total_bytes": sum(item[1] for item in files),
            "oldest_at": datetime.fromtimestamp(min(timestamps)).astimezone().isoformat(timespec="seconds") if timestamps else None,
            "newest_at": datetime.fromtimestamp(max(timestamps)).astimezone().isoformat(timespec="seconds") if timestamps else None,
            "database_bytes": self.db_path.stat().st_size if self.db_path.is_file() else 0,
            "roots": [str(root) for root in self.roots],
            "policy": self.policy(),
            "generated_at": _now_iso(),
        }

    def create_backup(self) -> dict[str, Any]:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        backup = self.backup_root / f"riskflow-{stamp}.db"
        manifest = self.backup_root / f"riskflow-{stamp}.json"
        with closing(sqlite3.connect(self.db_path, timeout=30)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        payload = {
            "created_at": _now_iso(),
            "database_backup": str(backup),
            "database_bytes": backup.stat().st_size,
            "inventory": self.inventory(),
        }
        manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {**payload, "manifest": str(manifest)}

    def backups(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.backup_root.exists():
            return []
        result = []
        for path in sorted(self.backup_root.glob("riskflow-*.db"), reverse=True)[:limit]:
            result.append({
                "name": path.name,
                "bytes": path.stat().st_size,
                "created_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            })
        return result
