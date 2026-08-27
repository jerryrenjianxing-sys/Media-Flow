from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


TASK_TYPES = {
    "healthcheck",
    "douyin_benchmark",
    "douyin_comment_preview",
    "douyin_comment",
    "douyin_two_video_demo",
    "douyin_topic_session",
}
TASK_STATUSES = {"pending", "running", "completed", "failed", "stopped", "cancelled"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class TaskRecord:
    id: str
    task_type: str
    device_id: str
    payload: dict[str, Any]
    status: str
    created_at: str
    not_before: str
    started_at: str | None
    finished_at: str | None
    worker_id: str | None
    run_dir: str | None
    result: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class IncidentRecord:
    id: str
    task_id: str
    device_id: str
    video_index: int | None
    stage: str
    error_type: str
    error_message: str
    fingerprint: str
    outcome: str
    recovery_action: str | None
    screenshot_path: str | None
    ui_tree_path: str | None
    context: dict[str, Any]
    analysis_status: str
    analysis: dict[str, Any] | None
    created_at: str


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def connection(self):
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    not_before TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    worker_id TEXT,
                    run_dir TEXT,
                    result_json TEXT,
                    error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_profiles (
                    name TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    video_index INTEGER,
                    stage TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    recovery_action TEXT,
                    screenshot_path TEXT,
                    ui_tree_path TEXT,
                    context_json TEXT NOT NULL,
                    analysis_status TEXT NOT NULL,
                    analysis_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "not_before" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN not_before TEXT")
                connection.execute(
                    "UPDATE tasks SET not_before=created_at WHERE not_before IS NULL"
                )
            connection.execute("DROP INDEX IF EXISTS idx_tasks_claim")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_claim "
                "ON tasks(device_id, status, not_before, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_incidents_created "
                "ON incidents(created_at DESC, id DESC)"
            )
            connection.execute("PRAGMA optimize")

    @staticmethod
    def incident_fingerprint(stage: str, error_type: str, error_message: str) -> str:
        normalized = re.sub(r"\b\d+\b", "#", error_message.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()[:300]
        source = f"{stage.lower()}|{error_type.lower()}|{normalized}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

    def record_incident(
        self,
        *,
        task_id: str,
        device_id: str,
        video_index: int | None,
        stage: str,
        error_type: str,
        error_message: str,
        outcome: str,
        recovery_action: str | None,
        screenshot_path: str | None = None,
        ui_tree_path: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        if outcome not in {"recovered", "skipped", "device_fatal"}:
            raise ValueError(f"Unsupported incident outcome: {outcome}")
        incident_id = uuid.uuid4().hex
        fingerprint = self.incident_fingerprint(stage, error_type, error_message)
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO incidents "
                "(id, task_id, device_id, video_index, stage, error_type, "
                "error_message, fingerprint, outcome, recovery_action, "
                "screenshot_path, ui_tree_path, context_json, analysis_status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)",
                (
                    incident_id,
                    task_id,
                    device_id,
                    video_index,
                    stage,
                    error_type,
                    error_message[:1000],
                    fingerprint,
                    outcome,
                    recovery_action,
                    screenshot_path,
                    ui_tree_path,
                    json.dumps(context or {}, ensure_ascii=False),
                    now_iso(),
                ),
            )
        return incident_id

    def get_incident(self, incident_id: str) -> IncidentRecord:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE id=?", (incident_id,)
            ).fetchone()
        if row is None:
            raise KeyError(incident_id)
        return self._incident_record(row)

    def list_incidents(self, limit: int = 20, offset: int = 0) -> list[IncidentRecord]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM incidents ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._incident_record(row) for row in rows]

    def incident_statistics(self) -> dict[str, int]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT outcome, COUNT(*) AS count FROM incidents GROUP BY outcome"
            ).fetchall()
            analysis_rows = connection.execute(
                "SELECT analysis_status, COUNT(*) AS count FROM incidents "
                "GROUP BY analysis_status"
            ).fetchall()
        result = {"total": sum(int(row["count"]) for row in rows)}
        result.update({row["outcome"]: int(row["count"]) for row in rows})
        result.update(
            {
                f"analysis_{row['analysis_status']}": int(row["count"])
                for row in analysis_rows
            }
        )
        result["queued"] = result.get("analysis_queued", 0)
        return result

    def claim_incident_for_analysis(self) -> IncidentRecord | None:
        """Atomically claim the oldest queued incident for read-only analysis."""
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM incidents WHERE analysis_status='queued' "
                "ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                "UPDATE incidents SET analysis_status='analyzing' "
                "WHERE id=? AND analysis_status='queued'",
                (row["id"],),
            )
            if updated.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM incidents WHERE id=?", (row["id"],)
            ).fetchone()
        return self._incident_record(claimed)

    def finish_incident_analysis(
        self,
        incident_id: str,
        *,
        status: str,
        analysis: dict[str, Any],
    ) -> IncidentRecord:
        if status not in {"completed", "failed"}:
            raise ValueError(f"Unsupported incident analysis status: {status}")
        with self.connection() as connection:
            updated = connection.execute(
                "UPDATE incidents SET analysis_status=?, analysis_json=? "
                "WHERE id=? AND analysis_status='analyzing'",
                (
                    status,
                    json.dumps(analysis, ensure_ascii=False),
                    incident_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("Incident is not currently being analyzed")
        return self.get_incident(incident_id)

    def requeue_interrupted_incident_analyses(self) -> int:
        """Recover claims left behind when the single analyzer process exited."""
        with self.connection() as connection:
            updated = connection.execute(
                "UPDATE incidents SET analysis_status='queued' "
                "WHERE analysis_status='analyzing'"
            )
        return updated.rowcount

    def retry_failed_incident_analyses(self) -> int:
        """Explicitly retry failed read-only analyses after a model/parser repair."""
        with self.connection() as connection:
            updated = connection.execute(
                "UPDATE incidents SET analysis_status='queued', analysis_json=NULL "
                "WHERE analysis_status='failed'"
            )
        return updated.rowcount

    @staticmethod
    def validate_payload(task_type: str, payload: dict[str, Any]) -> None:
        if task_type not in TASK_TYPES:
            raise ValueError(f"Unsupported task type: {task_type}")
        if task_type == "douyin_benchmark":
            dwell = payload.get("dwell")
            if (
                not isinstance(dwell, list)
                or len(dwell) != 4
                or any(not isinstance(value, (int, float)) or value < 0 for value in dwell)
            ):
                raise ValueError("douyin_benchmark requires four non-negative dwell values")
            max_gate_skips = payload.get("max_gate_skips", 3)
            if not isinstance(max_gate_skips, int) or max_gate_skips < 0:
                raise ValueError("max_gate_skips must be a non-negative integer")
        if task_type == "douyin_two_video_demo":
            dwell = payload.get("dwell")
            if (
                not isinstance(dwell, list)
                or len(dwell) != 2
                or any(not isinstance(value, (int, float)) or value < 0 for value in dwell)
            ):
                raise ValueError(
                    "douyin_two_video_demo requires two non-negative dwell values"
                )
            max_gate_skips = payload.get("max_gate_skips", 3)
            if not isinstance(max_gate_skips, int) or max_gate_skips < 0:
                raise ValueError("max_gate_skips must be a non-negative integer")
        if task_type in {"douyin_comment_preview", "douyin_comment"}:
            dwell_seconds = payload.get("dwell_seconds")
            if not isinstance(dwell_seconds, (int, float)) or dwell_seconds < 0:
                raise ValueError("douyin_comment_preview requires non-negative dwell_seconds")
            max_gate_skips = payload.get("max_gate_skips", 3)
            if not isinstance(max_gate_skips, int) or max_gate_skips < 0:
                raise ValueError("max_gate_skips must be a non-negative integer")
        if task_type == "douyin_topic_session":
            video_count = payload.get("video_count")
            if not isinstance(video_count, int) or not 1 <= video_count <= 200:
                raise ValueError("video_count must be between 1 and 200")
            round_count = payload.get("round_count", 1)
            if not isinstance(round_count, int) or not 1 <= round_count <= 20:
                raise ValueError("round_count must be between 1 and 20")
            interval = payload.get("round_interval_minutes", 0)
            if not isinstance(interval, int) or not 0 <= interval <= 1440:
                raise ValueError("round_interval_minutes must be between 0 and 1440")
            max_gate_skips = payload.get("max_gate_skips", 3)
            if not isinstance(max_gate_skips, int) or not 1 <= max_gate_skips <= 50:
                raise ValueError("max_gate_skips must be between 1 and 50")
            dwell_min = payload.get("dwell_min")
            dwell_max = payload.get("dwell_max")
            if (
                not isinstance(dwell_min, (int, float))
                or not isinstance(dwell_max, (int, float))
                or dwell_min < 0
                or dwell_max < dwell_min
            ):
                raise ValueError("dwell_min/dwell_max must be a valid non-negative range")
            topic_filter_enabled = payload.get("topic_filter_enabled", True)
            if not isinstance(topic_filter_enabled, bool):
                raise ValueError("topic_filter_enabled must be a boolean")
            for name in ("engagement_requires_topic", "comment_requires_topic"):
                value = payload.get(name, topic_filter_enabled)
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be a boolean")
            content_mode = payload.get(
                "content_mode", "mixed" if topic_filter_enabled else "general"
            )
            if content_mode not in {"general", "mixed", "search"}:
                raise ValueError("content_mode must be general, mixed, or search")
            topic_prompt = payload.get("topic_prompt")
            if content_mode != "general" and (
                not isinstance(topic_prompt, str) or not topic_prompt.strip()
            ):
                raise ValueError("topic_prompt is required")
            if content_mode == "search" and not str(payload.get("search_query", "")).strip():
                raise ValueError("search_query is required in search mode")
            for name in (
                "like_probability",
                "favorite_probability",
                "comment_probability",
                "matched_like_probability",
                "matched_favorite_probability",
                "matched_comment_probability",
            ):
                fallback = payload.get(name.removeprefix("matched_"))
                value = payload.get(name, fallback)
                if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise ValueError(f"{name} must be between 0 and 1")
            seed = payload.get("seed")
            if not isinstance(seed, int) or seed < 0:
                raise ValueError("seed must be a non-negative integer")
            if not isinstance(payload.get("preview_only"), bool):
                raise ValueError("preview_only must be a boolean")
            max_gate_skips = payload.get("max_gate_skips", 3)
            if not isinstance(max_gate_skips, int) or max_gate_skips < 0:
                raise ValueError("max_gate_skips must be a non-negative integer")

    def save_profile(self, name: str, config: dict[str, Any]) -> None:
        if not name.strip():
            raise ValueError("Profile name is required")
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO automation_profiles(name, config_json, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "config_json=excluded.config_json, updated_at=excluded.updated_at",
                (name, json.dumps(config, ensure_ascii=False), now_iso()),
            )

    def get_profile(self, name: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT config_json FROM automation_profiles WHERE name=?", (name,)
            ).fetchone()
        return json.loads(row["config_json"]) if row else None

    def list_profiles(self, prefix: str = "") -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT name, config_json, updated_at FROM automation_profiles "
                "WHERE name LIKE ? ORDER BY updated_at DESC, name ASC",
                (f"{prefix}%",),
            ).fetchall()
        return [
            {
                "name": row["name"],
                "config": json.loads(row["config_json"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def delete_profile(self, name: str) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM automation_profiles WHERE name=?", (name,)
            )
        return cursor.rowcount > 0

    def set_paused(self, paused: bool) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO system_state(key, value, updated_at) VALUES ('paused', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                ("1" if paused else "0", now_iso()),
            )

    def is_paused(self) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM system_state WHERE key='paused'"
            ).fetchone()
        return bool(row and row["value"] == "1")

    def request_stop(self, device_ids: list[str]) -> int:
        unique_ids = list(dict.fromkeys(str(value).strip() for value in device_ids if str(value).strip()))
        if not unique_ids:
            raise ValueError("至少需要一台设备")
        changed = 0
        with self.connection() as connection:
            for device_id in unique_ids:
                key = f"stop:{device_id}"
                previous = connection.execute(
                    "SELECT value FROM system_state WHERE key=?", (key,)
                ).fetchone()
                connection.execute(
                    "INSERT INTO system_state(key, value, updated_at) VALUES (?, '1', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value='1', updated_at=excluded.updated_at",
                    (key, now_iso()),
                )
                if previous is None or previous["value"] != "1":
                    changed += 1
        return changed

    def clear_stop_requests(self, device_ids: list[str] | None = None) -> int:
        with self.connection() as connection:
            if device_ids is None:
                cursor = connection.execute(
                    "DELETE FROM system_state WHERE key LIKE 'stop:%'"
                )
                return cursor.rowcount
            keys = [f"stop:{str(value).strip()}" for value in device_ids if str(value).strip()]
            deleted = 0
            for key in dict.fromkeys(keys):
                deleted += connection.execute(
                    "DELETE FROM system_state WHERE key=?", (key,)
                ).rowcount
            return deleted

    def is_stop_requested(self, device_id: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM system_state WHERE key=?",
                (f"stop:{device_id}",),
            ).fetchone()
        return bool(row and row["value"] == "1")

    def cancel_pending(
        self, task_ids: list[str] | None = None, *, device_ids: list[str] | None = None
    ) -> int:
        clauses = ["status='pending'"]
        parameters: list[Any] = [now_iso()]
        if task_ids is not None:
            clean_ids = list(dict.fromkeys(str(value).strip() for value in task_ids if str(value).strip()))
            if not clean_ids:
                return 0
            clauses.append("id IN (" + ",".join("?" for _ in clean_ids) + ")")
            parameters.extend(clean_ids)
        if device_ids is not None:
            clean_devices = list(dict.fromkeys(str(value).strip() for value in device_ids if str(value).strip()))
            if not clean_devices:
                return 0
            clauses.append("device_id IN (" + ",".join("?" for _ in clean_devices) + ")")
            parameters.extend(clean_devices)
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='cancelled', finished_at=?, "
                "error='cancelled_by_user' WHERE " + " AND ".join(clauses),
                parameters,
            )
        return cursor.rowcount

    def reconcile_orphaned_running(
        self, is_worker_alive: Callable[[str], bool]
    ) -> int:
        """Close tasks left running after their owning worker disappeared."""
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT id, worker_id FROM tasks WHERE status='running'"
            ).fetchall()

        orphaned_ids = [
            row["id"]
            for row in rows
            if not row["worker_id"] or not is_worker_alive(str(row["worker_id"]))
        ]
        if not orphaned_ids:
            return 0

        finished_at = now_iso()
        reconciled = 0
        with self.connection() as connection:
            for task_id in orphaned_ids:
                cursor = connection.execute(
                    "UPDATE tasks SET status='failed', finished_at=?, "
                    "error='worker_interrupted; worker process is no longer running' "
                    "WHERE id=? AND status='running'",
                    (finished_at, task_id),
                )
                reconciled += cursor.rowcount
        return reconciled

    def clear_all_tasks(self) -> dict[str, int]:
        """Delete task and incident records while protecting active executions."""
        with self.connection() as connection:
            running = connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE status='running'"
            ).fetchone()
            running_count = int(running["count"])
            if running_count:
                raise ValueError(
                    f"仍有 {running_count} 个任务正在执行，请先暂停并等待当前任务结束"
                )
            incident_cursor = connection.execute("DELETE FROM incidents")
            task_cursor = connection.execute("DELETE FROM tasks")
        return {
            "tasks_deleted": task_cursor.rowcount,
            "incidents_deleted": incident_cursor.rowcount,
        }

    def task_status_counts(self) -> dict[str, int]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
        return {
            status: next(
                (int(row["count"]) for row in rows if row["status"] == status), 0
            )
            for status in TASK_STATUSES
        }

    def running_count(self, device_ids: list[str] | None = None) -> int:
        with self.connection() as connection:
            if device_ids is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM tasks WHERE status='running'"
                ).fetchone()
                return int(row["count"])
            clean_ids = list(dict.fromkeys(str(value).strip() for value in device_ids if str(value).strip()))
            if not clean_ids:
                return 0
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE status='running' AND device_id IN ("
                + ",".join("?" for _ in clean_ids)
                + ")",
                clean_ids,
            ).fetchone()
            return int(row["count"])

    def submit(
        self,
        task_type: str,
        device_id: str,
        payload: dict[str, Any] | None = None,
        *,
        not_before: str | None = None,
    ) -> str:
        payload = payload or {}
        self.validate_payload(task_type, payload)
        task_id = uuid.uuid4().hex
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO tasks "
                "(id, task_type, device_id, payload_json, status, created_at, not_before) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (
                    task_id,
                    task_type,
                    device_id,
                    json.dumps(payload, ensure_ascii=False),
                    now_iso(),
                    not_before or now_iso(),
                ),
            )
        return task_id

    def recover_interrupted(self, device_id: str) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='failed', finished_at=?, "
                "error='worker_interrupted; task was not retried automatically' "
                "WHERE device_id=? AND status='running'",
                (now_iso(), device_id),
            )
            return cursor.rowcount

    def claim_next(self, device_id: str, worker_id: str) -> TaskRecord | None:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            paused = connection.execute(
                "SELECT value FROM system_state WHERE key='paused'"
            ).fetchone()
            if paused and paused["value"] == "1":
                connection.commit()
                return None
            stopped = connection.execute(
                "SELECT value FROM system_state WHERE key=?",
                (f"stop:{device_id}",),
            ).fetchone()
            if stopped and stopped["value"] == "1":
                connection.commit()
                return None
            row = connection.execute(
                "SELECT id FROM tasks WHERE device_id=? AND status='pending' "
                "AND not_before<=? ORDER BY not_before, created_at, id LIMIT 1",
                (device_id, now_iso()),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            task_id = row["id"]
            connection.execute(
                "UPDATE tasks SET status='running', started_at=?, worker_id=? "
                "WHERE id=? AND status='pending'",
                (now_iso(), worker_id, task_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(task_id)

    def has_ready(self, device_id: str) -> bool:
        with self.connection() as connection:
            paused = connection.execute(
                "SELECT value FROM system_state WHERE key='paused'"
            ).fetchone()
            if paused and paused["value"] == "1":
                return False
            stopped = connection.execute(
                "SELECT value FROM system_state WHERE key=?",
                (f"stop:{device_id}",),
            ).fetchone()
            if stopped and stopped["value"] == "1":
                return False
            row = connection.execute(
                "SELECT 1 FROM tasks WHERE device_id=? AND status='pending' "
                "AND not_before<=? LIMIT 1",
                (device_id, now_iso()),
            ).fetchone()
        return row is not None

    def finish(
        self,
        task_id: str,
        *,
        status: str,
        run_dir: str | None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if status not in {"completed", "failed", "stopped"}:
            raise ValueError("finish status must be completed, failed, or stopped")
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status=?, finished_at=?, run_dir=?, "
                "result_json=?, error=? WHERE id=? AND status='running'",
                (
                    status,
                    now_iso(),
                    run_dir,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    task_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Task is not running or does not exist: {task_id}")

    def get(self, task_id: str) -> TaskRecord:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._record(row)

    def list(self, limit: int = 20, offset: int = 0) -> list[TaskRecord]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._record(row) for row in rows]

    def statistics(self) -> dict[str, Any]:
        with self.connection() as connection:
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
            type_rows = connection.execute(
                "SELECT task_type, status, COUNT(*) AS count FROM tasks "
                "GROUP BY task_type, status"
            ).fetchall()
            completed_rows = connection.execute(
                "SELECT result_json FROM tasks WHERE status='completed' "
                "AND result_json IS NOT NULL"
            ).fetchall()
            failure_rows = connection.execute(
                "SELECT id, task_type, error, finished_at FROM tasks "
                "WHERE status='failed' ORDER BY finished_at DESC LIMIT 10"
            ).fetchall()
        wall_times: list[float] = []
        for row in completed_rows:
            result = json.loads(row["result_json"])
            wall_s = result.get("wall_s")
            if isinstance(wall_s, (int, float)):
                wall_times.append(float(wall_s))
        return {
            "generated_at": now_iso(),
            "by_status": {row["status"]: row["count"] for row in status_rows},
            "by_type_and_status": [dict(row) for row in type_rows],
            "completed_wall_s": {
                "count": len(wall_times),
                "average": round(sum(wall_times) / len(wall_times), 3)
                if wall_times
                else None,
                "minimum": round(min(wall_times), 3) if wall_times else None,
                "maximum": round(max(wall_times), 3) if wall_times else None,
            },
            "recent_failures": [dict(row) for row in failure_rows],
        }

    @staticmethod
    def _record(row: sqlite3.Row) -> TaskRecord:
        status = row["status"]
        if status not in TASK_STATUSES:
            raise ValueError(f"Unknown task status in database: {status}")
        return TaskRecord(
            id=row["id"],
            task_type=row["task_type"],
            device_id=row["device_id"],
            payload=json.loads(row["payload_json"]),
            status=status,
            created_at=row["created_at"],
            not_before=row["not_before"] or row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            worker_id=row["worker_id"],
            run_dir=row["run_dir"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
        )

    @staticmethod
    def _incident_record(row: sqlite3.Row) -> IncidentRecord:
        return IncidentRecord(
            id=row["id"],
            task_id=row["task_id"],
            device_id=row["device_id"],
            video_index=row["video_index"],
            stage=row["stage"],
            error_type=row["error_type"],
            error_message=row["error_message"],
            fingerprint=row["fingerprint"],
            outcome=row["outcome"],
            recovery_action=row["recovery_action"],
            screenshot_path=row["screenshot_path"],
            ui_tree_path=row["ui_tree_path"],
            context=json.loads(row["context_json"]),
            analysis_status=row["analysis_status"],
            analysis=json.loads(row["analysis_json"]) if row["analysis_json"] else None,
            created_at=row["created_at"],
        )
