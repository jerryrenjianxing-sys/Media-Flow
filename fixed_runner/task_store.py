from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable


TASK_TYPES = {
    "healthcheck",
    "douyin_benchmark",
    "douyin_comment_preview",
    "douyin_comment",
    "douyin_two_video_demo",
    "douyin_topic_session",
    "douyin_engagement_inspection",
}
TASK_STATUSES = {
    "pending",
    "running",
    "completed",
    "degraded",
    "failed",
    "stopped",
    "cancelled",
}
INCIDENT_OUTCOMES = {
    "recovered",
    "skipped",
    "device_fatal",
    "model_failed",
    "model_circuit_open",
}
INITIALIZATION_STATUSES = {
    "queued",
    "running",
    "waiting_user",
    "ready",
    "stale",
    "failed",
    "cancelled",
}
TASK_RECOVERY_STATUSES = {"queued", "running", "ready", "waiting_user", "failed"}
DEVICE_VIEW_SESSION_MODES = {"read_only", "control"}
DEVICE_VIEW_SESSION_PROFILES = {"wall", "focus"}
DEVICE_VIEW_SESSION_ACTIVE_STATUSES = {"created", "connected", "disconnected"}
VIRTUAL_OPERATION_TIMEOUT_SECONDS = {
    "template_create": 1800,
    "template_prepare": 3600,
    "create": 600,
    "clone": 600,
    "backup": 1800,
    "restore": 1800,
    "start": 240,
    "restart": 300,
    "stop": 180,
    "settings": 300,
    "delete": 600,
    "adopt": 300,
    "show_window": 60,
    "hide_window": 60,
    "configure_pool": 7200,
    "unmanaged_start": 300,
    "unmanaged_stop": 180,
    "unmanaged_delete": 600,
}
VIRTUAL_OPERATION_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
VIRTUAL_OPERATION_WAITING_SAFE_ACTIONS = {"stop", "show_window", "hide_window"}
VIRTUAL_OPERATION_TRANSITIONS = {
    "queued": {"queued", "running", "waiting_user", "completed", "failed", "cancelled"},
    "running": {"running", "waiting_user", "completed", "failed", "cancelled"},
    "waiting_user": {"waiting_user", "running", "completed", "failed", "cancelled"},
    "completed": {"completed"},
    "failed": {"failed"},
    "cancelled": {"cancelled"},
}
VIRTUAL_OPERATION_STAGE_MESSAGES = {
    "queued": "操作已排队",
    "creating": "正在创建虚拟机",
    "starting_mumu": "正在启动MuMu",
    "waiting_android": "正在等待Android启动",
    "connecting_adb": "正在连接ADB",
    "verifying_identity": "正在核对设备身份",
    "adb_ready": "ADB已连接",
    "waiting_app_install": "等待安装抖音",
    "checking_app_install": "正在检查抖音安装状态",
    "initialization_queued": "已进入初始化复验",
    "applying_settings": "正在写入并回读配置",
    "cloning": "正在克隆虚拟机",
    "backing_up": "正在备份虚拟机",
    "backup_verified": "备份已经校验",
    "restoring": "正在恢复为新虚拟机",
    "deleting": "正在删除虚拟机",
    "stopping": "正在停止虚拟机",
    "restarting": "正在重启虚拟机",
    "ready": "虚拟机已就绪",
    "completed": "操作已完成",
    "failed": "操作失败",
    "planning_pool": "正在核对标准虚拟机池",
    "deleting_pool": "正在清理旧虚拟机",
    "verifying_pool_empty": "正在确认旧虚拟机已清理",
    "creating_pool": "正在创建标准虚拟机",
    "pool_waiting_onboarding": "标准虚拟机已创建，正在等待逐台接入",
}


class RunDraftConflict(ValueError):
    """Raised when a stale workbench draft attempts to overwrite a newer one."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("任务草稿已在其他页面更新，请使用最新版本")
        self.current = current


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


@dataclass(frozen=True)
class InitializationRecord:
    id: str
    device_id: str
    platform_id: str
    options: dict[str, Any]
    status: str
    stage: str
    progress_current: int
    progress_total: int
    message: str
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    worker_id: str | None
    report_path: str | None
    result: dict[str, Any] | None
    error: str | None
    cancel_requested: bool


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
                CREATE TABLE IF NOT EXISTS run_drafts (
                    name TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
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
                CREATE TABLE IF NOT EXISTS content_plans (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    current_revision_id TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS content_plan_revisions (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    document_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(plan_id, revision_number)
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_initializations (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    platform_id TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress_current INTEGER NOT NULL,
                    progress_total INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    worker_id TEXT,
                    report_path TEXT,
                    result_json TEXT,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS interaction_alerts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    viewed_at TEXT,
                    UNIQUE(device_id, fingerprint)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS interaction_inspections (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_kind TEXT NOT NULL,
                    restored INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    run_dir TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS visitor_baselines (
                    device_id TEXT PRIMARY KEY,
                    app_version TEXT NOT NULL,
                    display_signature TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    first_row_hash TEXT NOT NULL,
                    visual_hash TEXT NOT NULL,
                    first_row_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_recoveries (
                    id TEXT PRIMARY KEY,
                    origin_task_id TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 3,
                    expected_json TEXT NOT NULL,
                    actual_json TEXT NOT NULL,
                    replacement_task_id TEXT,
                    message TEXT NOT NULL,
                    evidence_dir TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS virtual_devices (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_instance_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    recipe_json TEXT NOT NULL,
                    provider_snapshot_json TEXT NOT NULL,
                    adb_endpoint TEXT,
                    last_adb_endpoint TEXT,
                    android_identity TEXT,
                    discovery_source TEXT NOT NULL DEFAULT 'mediaflow_created',
                    provider_install_id TEXT NOT NULL DEFAULT '',
                    presence_status TEXT NOT NULL DEFAULT 'present',
                    profile_status TEXT NOT NULL DEFAULT 'requires_verification',
                    standard_status TEXT NOT NULL DEFAULT 'requires_verification',
                    standard_message TEXT,
                    managed INTEGER NOT NULL DEFAULT 1,
                    display_index INTEGER,
                    last_seen_at TEXT,
                    last_connected_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_instance_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS virtual_device_operations (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    operation_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    message TEXT NOT NULL DEFAULT '',
                    retryable INTEGER NOT NULL DEFAULT 0,
                    deadline_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS virtual_device_backups (
                    id TEXT PRIMARY KEY,
                    virtual_device_id TEXT,
                    source_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_instance_id TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_view_sessions (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    virtual_device_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    stream_profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_heartbeat_at TEXT,
                    expires_at TEXT NOT NULL,
                    lease_expires_at TEXT,
                    connected INTEGER NOT NULL DEFAULT 0,
                    closed_at TEXT,
                    close_reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
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
            visitor_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(visitor_baselines)"
                ).fetchall()
            }
            if "first_row_json" not in visitor_columns:
                connection.execute(
                    "ALTER TABLE visitor_baselines "
                    "ADD COLUMN first_row_json TEXT NOT NULL DEFAULT '{}'"
                )
            virtual_device_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(virtual_devices)"
                ).fetchall()
            }
            virtual_device_additions = {
                "last_adb_endpoint": "TEXT",
                "discovery_source": "TEXT NOT NULL DEFAULT 'mediaflow_created'",
                "provider_install_id": "TEXT NOT NULL DEFAULT ''",
                "presence_status": "TEXT NOT NULL DEFAULT 'present'",
                "profile_status": "TEXT NOT NULL DEFAULT 'requires_verification'",
                "standard_status": "TEXT NOT NULL DEFAULT 'requires_verification'",
                "standard_message": "TEXT",
                "managed": "INTEGER NOT NULL DEFAULT 1",
                "display_index": "INTEGER",
                "last_seen_at": "TEXT",
                "last_connected_at": "TEXT",
                "last_error": "TEXT",
            }
            for column, declaration in virtual_device_additions.items():
                if column not in virtual_device_columns:
                    connection.execute(
                        f"ALTER TABLE virtual_devices ADD COLUMN {column} {declaration}"
                    )
            virtual_operation_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(virtual_device_operations)"
                ).fetchall()
            }
            virtual_operation_additions = {
                "message": "TEXT NOT NULL DEFAULT ''",
                "retryable": "INTEGER NOT NULL DEFAULT 0",
                "deadline_at": "TEXT",
                "started_at": "TEXT",
                "finished_at": "TEXT",
            }
            for column, declaration in virtual_operation_additions.items():
                if column not in virtual_operation_columns:
                    connection.execute(
                        f"ALTER TABLE virtual_device_operations ADD COLUMN {column} {declaration}"
                    )
            connection.execute(
                "UPDATE virtual_devices SET managed=1 "
                "WHERE discovery_source IN ('provider_discovery','provider_auto_discovery') "
                "AND state!='retired'"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS virtual_device_sequences (
                    provider TEXT NOT NULL,
                    provider_install_id TEXT NOT NULL,
                    last_value INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, provider_install_id)
                )
                """
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

            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_initializations_device "
                "ON device_initializations(device_id, created_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_initializations_claim "
                "ON device_initializations(device_id, status, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_recoveries_device "
                "ON task_recoveries(device_id, created_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_virtual_devices_provider "
                "ON virtual_devices(provider, provider_instance_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_virtual_operations_created "
                "ON virtual_device_operations(created_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_virtual_backups_device "
                "ON virtual_device_backups(virtual_device_id, created_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_device_view_sessions_device "
                "ON device_view_sessions(device_id, status, expires_at, lease_expires_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_content_plan_revisions "
                "ON content_plan_revisions(plan_id, revision_number DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_interaction_alerts_status "
                "ON interaction_alerts(status, detected_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_interaction_alerts_task "
                "ON interaction_alerts(task_id, detected_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_interaction_inspections_device "
                "ON interaction_inspections(device_id, finished_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_interaction_inspections_result "
                "ON interaction_inspections(result_kind, finished_at DESC, id DESC)"
            )
            connection.execute("PRAGMA optimize")

    def create_virtual_operation(
        self,
        operation_type: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("虚拟机操作缺少幂等键")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM virtual_device_operations WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                return self._virtual_operation(existing), False
            virtual_device_id = str(request.get("virtual_device_id") or "").strip()
            if virtual_device_id:
                active_rows = connection.execute(
                    "SELECT * FROM virtual_device_operations WHERE status IN ('queued','running','waiting_user') "
                    "ORDER BY created_at DESC, id DESC"
                ).fetchall()
                for active_row in active_rows:
                    active = self._virtual_operation(active_row)
                    if self._virtual_operation_device_id(active) != virtual_device_id:
                        continue
                    if (
                        active["status"] == "waiting_user"
                        and operation_type in VIRTUAL_OPERATION_WAITING_SAFE_ACTIONS
                    ):
                        if operation_type == "stop":
                            timestamp = now_iso()
                            connection.execute(
                                "UPDATE virtual_device_operations SET status='cancelled', stage='cancelled', "
                                "progress=100, message='已由停止操作结束等待', error=NULL, retryable=0, "
                                "finished_at=?, updated_at=? WHERE id=? AND status='waiting_user'",
                                (timestamp, timestamp, active["id"]),
                            )
                        continue
                    raise ValueError(
                        f"该虚拟机已有{active['operation_type']}操作正在进行"
                    )
            operation_id = uuid.uuid4().hex
            timestamp_value = datetime.now().astimezone()
            timestamp = timestamp_value.isoformat(timespec="milliseconds")
            deadline_at = (
                timestamp_value
                + timedelta(seconds=VIRTUAL_OPERATION_TIMEOUT_SECONDS.get(operation_type, 300))
            ).isoformat(timespec="milliseconds")
            connection.execute(
                "INSERT INTO virtual_device_operations "
                "(id, idempotency_key, operation_type, status, stage, progress, request_json, "
                "message, retryable, deadline_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'queued', 'queued', 0, ?, ?, 0, ?, ?, ?)",
                (
                    operation_id,
                    key,
                    operation_type,
                    json.dumps(request, ensure_ascii=False, sort_keys=True),
                    "操作已排队",
                    deadline_at,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM virtual_device_operations WHERE id=?", (operation_id,)
            ).fetchone()
        return self._virtual_operation(row), True

    def create_numbered_virtual_operation(
        self,
        operation_type: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        """Create a VM operation and reserve a never-reused MediaFlow display number."""
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("虚拟机操作缺少幂等键")
        provider = str(request.get("provider") or "mumu").strip() or "mumu"
        install_id = str(request.get("provider_install_id") or "").strip()
        if not install_id:
            raise ValueError("虚拟机操作缺少Provider安装身份")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM virtual_device_operations WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                return self._virtual_operation(existing), False
            virtual_device_id = str(request.get("virtual_device_id") or "").strip()
            if virtual_device_id:
                active_rows = connection.execute(
                    "SELECT * FROM virtual_device_operations WHERE status IN ('queued','running','waiting_user') "
                    "ORDER BY created_at DESC, id DESC"
                ).fetchall()
                for active_row in active_rows:
                    active = self._virtual_operation(active_row)
                    if str(active["request"].get("virtual_device_id") or "") == virtual_device_id:
                        raise ValueError(
                            f"该虚拟机已有{active['operation_type']}操作正在进行"
                        )
            sequence = connection.execute(
                "SELECT last_value FROM virtual_device_sequences "
                "WHERE provider=? AND provider_install_id=?",
                (provider, install_id),
            ).fetchone()
            existing_max = connection.execute(
                "SELECT COALESCE(MAX(display_index), 0) AS value FROM virtual_devices "
                "WHERE provider=? AND provider_install_id=? AND managed=1",
                (provider, install_id),
            ).fetchone()
            display_index = max(
                int(sequence["last_value"]) if sequence is not None else 0,
                int(existing_max["value"]) if existing_max is not None else 0,
            ) + 1
            timestamp_value = datetime.now().astimezone()
            timestamp = timestamp_value.isoformat(timespec="milliseconds")
            deadline_at = (
                timestamp_value
                + timedelta(seconds=VIRTUAL_OPERATION_TIMEOUT_SECONDS.get(operation_type, 300))
            ).isoformat(timespec="milliseconds")
            connection.execute(
                "INSERT INTO virtual_device_sequences "
                "(provider, provider_install_id, last_value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(provider, provider_install_id) DO UPDATE SET "
                "last_value=excluded.last_value, updated_at=excluded.updated_at",
                (provider, install_id, display_index, timestamp),
            )
            numbered_request = {
                **request,
                "provider": provider,
                "provider_install_id": install_id,
                "display_index": display_index,
                "name": f"MediaFlow虚拟机{display_index}",
            }
            operation_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO virtual_device_operations "
                "(id, idempotency_key, operation_type, status, stage, progress, request_json, "
                "message, retryable, deadline_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'queued', 'queued', 0, ?, ?, 0, ?, ?, ?)",
                (
                    operation_id,
                    key,
                    operation_type,
                    json.dumps(numbered_request, ensure_ascii=False, sort_keys=True),
                    "操作已排队",
                    deadline_at,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM virtual_device_operations WHERE id=?", (operation_id,)
            ).fetchone()
        return self._virtual_operation(row), True

    def update_virtual_operation(
        self,
        operation_id: str,
        *,
        status: str,
        stage: str,
        progress: int,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        message: str | None = None,
        retryable: bool | None = None,
    ) -> dict[str, Any]:
        if status not in VIRTUAL_OPERATION_TRANSITIONS:
            raise ValueError("虚拟机操作状态无效")
        effective_message = message or VIRTUAL_OPERATION_STAGE_MESSAGES.get(stage)
        timestamp = now_iso()
        terminal = status in VIRTUAL_OPERATION_TERMINAL_STATUSES
        with self.connection() as connection:
            # A provider thread can finish just as a timeout/restart reconciler
            # runs.  Serialize the read-and-update so a late writer cannot turn
            # an already completed operation into a failure (or vice versa).
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM virtual_device_operations WHERE id=?", (operation_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(operation_id)
            current_status = str(existing["status"])
            if current_status in VIRTUAL_OPERATION_TERMINAL_STATUSES:
                return self._virtual_operation(existing)
            if status not in VIRTUAL_OPERATION_TRANSITIONS[current_status]:
                raise ValueError(f"虚拟机操作不能从 {current_status} 变为 {status}")
            connection.execute(
                "UPDATE virtual_device_operations SET status=?, stage=?, progress=?, result_json=?, error=?, "
                "message=COALESCE(?, message), retryable=COALESCE(?, retryable), "
                "started_at=CASE WHEN ?='running' THEN COALESCE(started_at, ?) ELSE started_at END, "
                "finished_at=CASE WHEN ? THEN ? ELSE finished_at END, updated_at=? WHERE id=?",
                (
                    status,
                    stage,
                    max(0, min(100, int(progress))),
                    json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None,
                    error,
                    effective_message,
                    int(retryable) if retryable is not None else None,
                    status,
                    timestamp,
                    int(terminal),
                    timestamp,
                    timestamp,
                    operation_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM virtual_device_operations WHERE id=?", (operation_id,)
            ).fetchone()
        return self._virtual_operation(row)

    def request_template_cancellation(self, operation_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM virtual_device_operations WHERE id=?", (operation_id,)).fetchone()
            if not row or row["operation_type"] not in {"template_prepare", "template_create"}:
                raise ValueError("不是本机模板操作")
            if row["status"] not in {"queued", "running"}:
                raise ValueError("操作已收口，请查看已保留的实例")
            request = json.loads(row["request_json"])
            request["cancel_requested"] = True
            connection.execute("UPDATE virtual_device_operations SET request_json=?, message=?, updated_at=? WHERE id=?",
                               (json.dumps(request, ensure_ascii=False), "正在安全取消；当前命令结束后停止，不会删除已创建实例", now_iso(), operation_id))
        return self.get_virtual_operation(operation_id)

    def get_virtual_operation(self, operation_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_device_operations WHERE id=?", (operation_id,)
            ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return self._virtual_operation(row)

    def save_virtual_device(self, payload: dict[str, Any], *, fresh_creation: dict[str, Any] | None = None) -> dict[str, Any]:
        timestamp = now_iso()
        discovery_source = str(payload.get("discovery_source") or "mediaflow_created")
        managed = payload.get("managed")
        if managed is None:
            managed = discovery_source in {
                "mediaflow_created",
                "mediaflow_adopted",
                "provider_discovery",
                "provider_auto_discovery",
            }
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM virtual_devices WHERE provider=? AND provider_instance_id=?",
                (payload["provider"], payload["provider_instance_id"]),
            ).fetchone()
            if existing is not None and str(existing["id"]) != str(payload["virtual_device_id"]):
                if fresh_creation and str(existing["state"]) != "retired":
                    operation = connection.execute("SELECT * FROM virtual_device_operations WHERE id=?",
                                                   (fresh_creation.get("operation_id"),)).fetchone()
                    instance_id = str(payload["provider_instance_id"])
                    proven_new = (
                        operation is not None and operation["status"] == "running"
                        and operation["operation_type"] in {"create", "clone", "template_prepare", "template_create"}
                        and isinstance(fresh_creation.get("before_ids"), list)
                        and instance_id not in fresh_creation["before_ids"]
                        and fresh_creation.get("created_ids") == [instance_id]
                        and existing["presence_status"] == "missing"
                        and not existing["adb_endpoint"] and existing["state"] != "running"
                    )
                    if proven_new:
                        # Retain the old UUID, account identity and evidence. Only its
                        # no-longer-present inventory incarnation is retired.
                        connection.execute("UPDATE virtual_devices SET state='retired', presence_status='retired', updated_at=? WHERE id=?",
                                           (timestamp, existing["id"]))
                        existing = connection.execute("SELECT * FROM virtual_devices WHERE id=?", (existing["id"],)).fetchone()
                if str(existing["state"]) != "retired":
                    raise ValueError(
                        "Provider实例号已经属于另一台未退休虚拟机，需要先确认身份冲突"
                    )
                archived_instance_id = (
                    f"retired:{existing['id']}:{existing['provider_instance_id']}"
                )
                connection.execute(
                    "UPDATE virtual_devices SET provider_instance_id=?, updated_at=? WHERE id=?",
                    (archived_instance_id, timestamp, existing["id"]),
                )
            connection.execute(
                "INSERT INTO virtual_devices "
                "(id, provider, provider_instance_id, name, state, recipe_json, provider_snapshot_json, adb_endpoint, last_adb_endpoint, android_identity, "
                "discovery_source, provider_install_id, presence_status, profile_status, standard_status, standard_message, managed, display_index, last_seen_at, last_connected_at, last_error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, provider_instance_id) DO UPDATE SET "
                "name=excluded.name, state=excluded.state, recipe_json=excluded.recipe_json, "
                "provider_snapshot_json=excluded.provider_snapshot_json, adb_endpoint=excluded.adb_endpoint, "
                "last_adb_endpoint=excluded.last_adb_endpoint, android_identity=excluded.android_identity, "
                "discovery_source=excluded.discovery_source, provider_install_id=excluded.provider_install_id, "
                "presence_status=excluded.presence_status, profile_status=excluded.profile_status, standard_status=excluded.standard_status, standard_message=excluded.standard_message, managed=excluded.managed, display_index=excluded.display_index, "
                "last_seen_at=excluded.last_seen_at, last_connected_at=excluded.last_connected_at, "
                "last_error=excluded.last_error, updated_at=excluded.updated_at",
                (
                    payload["virtual_device_id"], payload["provider"], payload["provider_instance_id"],
                    payload["name"], payload.get("state", "stopped"),
                    json.dumps(payload.get("recipe", {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("provider_snapshot", {}), ensure_ascii=False, sort_keys=True),
                    payload.get("adb_endpoint"), payload.get("last_adb_endpoint"), payload.get("android_identity"),
                    discovery_source, payload.get("provider_install_id", ""),
                    payload.get("presence_status", "present"), payload.get("profile_status", "requires_verification"),
                    payload.get("standard_status", "requires_verification"), payload.get("standard_message"),
                    1 if managed else 0, payload.get("display_index"),
                    payload.get("last_seen_at"), payload.get("last_connected_at"), payload.get("last_error"),
                    timestamp, timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM virtual_devices WHERE id=?",
                (payload["virtual_device_id"],),
            ).fetchone()
        return self._virtual_device(row)

    def list_virtual_devices(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM virtual_devices ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [self._virtual_device(row) for row in rows]

    def list_managed_virtual_devices(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM virtual_devices WHERE managed=1 AND state!='retired' "
                "ORDER BY display_index, created_at, id"
            ).fetchall()
        return [item for row in rows for item in [self._virtual_device(row)]
                if not item.get("recipe", {}).get("is_template")]

    def list_unmanaged_virtual_devices(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM virtual_devices WHERE managed=0 ORDER BY created_at, id"
            ).fetchall()
        return [self._virtual_device(row) for row in rows]

    def list_active_virtual_operations(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM virtual_device_operations "
                "WHERE status IN ('queued','running','waiting_user') "
                "ORDER BY created_at, id"
            ).fetchall()
        return [self._virtual_operation(row) for row in rows]

    def get_virtual_device(self, virtual_device_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_devices WHERE id=? AND managed=1 AND state!='retired'",
                (str(virtual_device_id or "").strip(),),
            ).fetchone()
        if row is None:
            raise KeyError(virtual_device_id)
        return self._virtual_device(row)

    def get_virtual_device_for_adb(self, device_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_devices WHERE adb_endpoint=? AND managed=1 AND state!='retired' "
                "ORDER BY updated_at DESC, id DESC LIMIT 1",
                (str(device_id or "").strip(),),
            ).fetchone()
        return self._virtual_device(row) if row is not None else None

    def save_virtual_device_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        timestamp = now_iso()
        backup_id = str(payload.get("id") or uuid.uuid4().hex)
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO virtual_device_backups "
                "(id, virtual_device_id, source_name, provider, provider_instance_id, path, sha256, size_bytes, status, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, size_bytes=excluded.size_bytes, "
                "status=excluded.status, metadata_json=excluded.metadata_json, updated_at=excluded.updated_at",
                (
                    backup_id,
                    payload.get("virtual_device_id"),
                    str(payload.get("source_name") or ""),
                    str(payload.get("provider") or "mumu"),
                    str(payload.get("provider_instance_id") or ""),
                    str(payload["path"]),
                    str(payload["sha256"]),
                    int(payload["size_bytes"]),
                    str(payload.get("status") or "ready"),
                    json.dumps(payload.get("metadata", {}), ensure_ascii=False, sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM virtual_device_backups WHERE path=?", (str(payload["path"]),)
            ).fetchone()
        return self._virtual_backup(row)

    def get_virtual_device_backup(self, backup_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_device_backups WHERE id=?", (str(backup_id),)
            ).fetchone()
        if row is None:
            raise KeyError(backup_id)
        return self._virtual_backup(row)

    def list_virtual_device_backups(self, virtual_device_id: str | None = None) -> list[dict[str, Any]]:
        with self.connection() as connection:
            if virtual_device_id:
                rows = connection.execute(
                    "SELECT * FROM virtual_device_backups WHERE virtual_device_id=? ORDER BY created_at DESC, id DESC",
                    (str(virtual_device_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM virtual_device_backups ORDER BY created_at DESC, id DESC"
                ).fetchall()
        return [self._virtual_backup(row) for row in rows]

    def active_virtual_operation(
        self, virtual_device_id: str, *, exclude_operation_id: str | None = None
    ) -> dict[str, Any] | None:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM virtual_device_operations WHERE status IN ('queued','running','waiting_user') "
                "ORDER BY created_at DESC, id DESC"
            ).fetchall()
        for row in rows:
            operation = self._virtual_operation(row)
            if exclude_operation_id and operation["id"] == exclude_operation_id:
                continue
            if self._virtual_operation_device_id(operation) == str(virtual_device_id):
                return operation
        return None

    @staticmethod
    def _virtual_operation_device_id(operation: dict[str, Any]) -> str:
        request = operation.get("request") or {}
        result = operation.get("result") or {}
        return str(
            request.get("virtual_device_id")
            or result.get("virtual_device_id")
            or (result.get("device") or {}).get("virtual_device_id")
            or ""
        )

    @staticmethod
    def _device_view_session(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "device_id": row["device_id"],
            "virtual_device_id": row["virtual_device_id"],
            "mode": row["mode"],
            "stream_profile": row["stream_profile"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "expires_at": row["expires_at"],
            "lease_expires_at": row["lease_expires_at"],
            "connected": bool(row["connected"]),
            "closed_at": row["closed_at"],
            "close_reason": row["close_reason"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @staticmethod
    def _expire_device_view_sessions(connection: sqlite3.Connection) -> int:
        timestamp = now_iso()
        cursor = connection.execute(
            "UPDATE device_view_sessions SET status='expired', connected=0, "
            "closed_at=COALESCE(closed_at, ?), close_reason=COALESCE(close_reason, 'lease_expired'), "
            "updated_at=? WHERE status IN ('created','connected','disconnected') AND "
            "(expires_at<=? OR (mode='control' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?))",
            (timestamp, timestamp, timestamp, timestamp),
        )
        return cursor.rowcount

    def create_device_view_session(
        self,
        *,
        device_id: str,
        virtual_device_id: str,
        mode: str,
        stream_profile: str,
        token_hash: str,
        ttl_seconds: int = 3600,
        lease_seconds: int = 15,
    ) -> dict[str, Any]:
        clean_device_id = str(device_id or "").strip()
        clean_virtual_id = str(virtual_device_id or "").strip()
        if not clean_device_id or not clean_virtual_id:
            raise ValueError("设备画面会话缺少设备身份")
        if mode not in DEVICE_VIEW_SESSION_MODES:
            raise ValueError("画面会话模式无效")
        if stream_profile not in DEVICE_VIEW_SESSION_PROFILES:
            raise ValueError("画面档位无效")
        if not re.fullmatch(r"[0-9a-f]{64}", str(token_hash or "")):
            raise ValueError("画面会话令牌摘要无效")
        if not 30 <= int(ttl_seconds) <= 86_400:
            raise ValueError("画面会话有效期无效")
        timestamp = datetime.now().astimezone()
        created_at = timestamp.isoformat(timespec="milliseconds")
        expires_at = (timestamp + timedelta(seconds=int(ttl_seconds))).isoformat(
            timespec="milliseconds"
        )
        lease_expires_at = (
            timestamp + timedelta(seconds=int(lease_seconds))
        ).isoformat(timespec="milliseconds")
        session_id = uuid.uuid4().hex
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_device_view_sessions(connection)
            if stream_profile == "focus":
                closed_at = now_iso()
                connection.execute(
                    "UPDATE device_view_sessions SET status='closed', connected=0, "
                    "updated_at=?, closed_at=?, close_reason='focus_replaced_wall' "
                    "WHERE device_id=? AND stream_profile='wall' "
                    "AND status IN ('created','connected','disconnected')",
                    (closed_at, closed_at, clean_device_id),
                )
            else:
                focus = connection.execute(
                    "SELECT 1 FROM device_view_sessions WHERE device_id=? AND "
                    "stream_profile='focus' AND status IN ('created','connected','disconnected') LIMIT 1",
                    (clean_device_id,),
                ).fetchone()
                if focus is not None:
                    raise ValueError("该设备已打开聚焦画面，设备墙暂不重复串流")
            if mode == "control":
                running = connection.execute(
                    "SELECT 1 FROM tasks WHERE device_id=? AND status='running' LIMIT 1",
                    (clean_device_id,),
                ).fetchone()
                initializing = connection.execute(
                    "SELECT 1 FROM device_initializations WHERE device_id=? "
                    "AND status='running' LIMIT 1",
                    (clean_device_id,),
                ).fetchone()
                controlling = connection.execute(
                    "SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' "
                    "AND status IN ('created','connected','disconnected') LIMIT 1",
                    (clean_device_id,),
                ).fetchone()
                if running:
                    raise ValueError("该设备已有排队或运行任务，当前只能观看")
                if initializing:
                    raise ValueError("该设备正在初始化，当前只能观看")
                if controlling:
                    raise ValueError("该设备已有人在操作")
            connection.execute(
                "INSERT INTO device_view_sessions "
                "(id, device_id, virtual_device_id, mode, stream_profile, status, token_hash, "
                "created_at, updated_at, expires_at, lease_expires_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, '{}')",
                (
                    session_id,
                    clean_device_id,
                    clean_virtual_id,
                    mode,
                    stream_profile,
                    token_hash,
                    created_at,
                    created_at,
                    expires_at,
                    lease_expires_at if mode == "control" else None,
                ),
            )
            row = connection.execute(
                "SELECT * FROM device_view_sessions WHERE id=?", (session_id,)
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        return self._device_view_session(row)

    def get_device_view_session(self, session_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            self._expire_device_view_sessions(connection)
            row = connection.execute(
                "SELECT * FROM device_view_sessions WHERE id=?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return self._device_view_session(row)

    def validate_device_view_session(
        self, session_id: str, token_hash: str
    ) -> dict[str, Any]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_device_view_sessions(connection)
            row = connection.execute(
                "SELECT * FROM device_view_sessions WHERE id=? AND token_hash=? "
                "AND status IN ('created','connected','disconnected')",
                (session_id, token_hash),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            timestamp = datetime.now().astimezone()
            updated_at = timestamp.isoformat(timespec="milliseconds")
            lease_expires_at = (
                timestamp + timedelta(seconds=15)
            ).isoformat(timespec="milliseconds")
            connection.execute(
                "UPDATE device_view_sessions SET status='connected', connected=1, "
                "last_heartbeat_at=?, updated_at=?, lease_expires_at=? WHERE id=?",
                (
                    updated_at,
                    updated_at,
                    lease_expires_at if row["mode"] == "control" else None,
                    session_id,
                ),
            )
            refreshed = connection.execute(
                "SELECT * FROM device_view_sessions WHERE id=?", (session_id,)
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        return self._device_view_session(refreshed)

    def heartbeat_device_view_session(
        self, session_id: str, *, connected: bool, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_device_view_sessions(connection)
            row = connection.execute(
                "SELECT * FROM device_view_sessions WHERE id=? AND "
                "status IN ('created','connected','disconnected')",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            timestamp = datetime.now().astimezone()
            updated_at = timestamp.isoformat(timespec="milliseconds")
            lease_expires_at = (
                timestamp + timedelta(seconds=15)
            ).isoformat(timespec="milliseconds")
            next_status = "connected" if connected else "disconnected"
            if not connected and row["mode"] == "read_only":
                next_status = "closed"
            connection.execute(
                "UPDATE device_view_sessions SET status=?, connected=?, last_heartbeat_at=?, "
                "updated_at=?, lease_expires_at=?, metadata_json=?, closed_at=?, close_reason=? "
                "WHERE id=?",
                (
                    next_status,
                    1 if connected else 0,
                    updated_at,
                    updated_at,
                    lease_expires_at if row["mode"] == "control" else None,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                    updated_at if next_status == "closed" else None,
                    "stream_disconnected" if next_status == "closed" else None,
                    session_id,
                ),
            )
            refreshed = connection.execute(
                "SELECT * FROM device_view_sessions WHERE id=?", (session_id,)
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        return self._device_view_session(refreshed)

    def close_device_view_session(
        self, session_id: str, *, reason: str = "closed_by_client"
    ) -> dict[str, Any]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM device_view_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            if row["status"] in DEVICE_VIEW_SESSION_ACTIVE_STATUSES:
                timestamp = now_iso()
                connection.execute(
                    "UPDATE device_view_sessions SET status='closed', connected=0, "
                    "updated_at=?, closed_at=?, close_reason=? WHERE id=?",
                    (timestamp, timestamp, str(reason or "closed")[:120], session_id),
                )
            refreshed = connection.execute(
                "SELECT * FROM device_view_sessions WHERE id=?", (session_id,)
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        return self._device_view_session(refreshed)

    def has_active_control_session(self, device_id: str) -> bool:
        with self.connection() as connection:
            self._expire_device_view_sessions(connection)
            row = connection.execute(
                "SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' "
                "AND status IN ('created','connected','disconnected') LIMIT 1",
                (str(device_id or "").strip(),),
            ).fetchone()
        return row is not None

    def device_view_session_summary(self) -> dict[str, int]:
        with self.connection() as connection:
            self._expire_device_view_sessions(connection)
            rows = connection.execute(
                "SELECT mode, status, COUNT(*) AS count FROM device_view_sessions "
                "GROUP BY mode, status"
            ).fetchall()
        return {
            "active_read_only": sum(
                int(row["count"])
                for row in rows
                if row["mode"] == "read_only" and row["status"] in DEVICE_VIEW_SESSION_ACTIVE_STATUSES
            ),
            "active_control": sum(
                int(row["count"])
                for row in rows
                if row["mode"] == "control" and row["status"] in DEVICE_VIEW_SESSION_ACTIVE_STATUSES
            ),
        }

    @staticmethod
    def _virtual_operation(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "idempotency_key": row["idempotency_key"],
            "operation_type": row["operation_type"], "status": row["status"],
            "stage": row["stage"], "progress": int(row["progress"]),
            "request": json.loads(row["request_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "message": row["message"], "retryable": bool(row["retryable"]),
            "deadline_at": row["deadline_at"], "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _virtual_device(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "virtual_device_id": row["id"], "provider": row["provider"],
            "provider_instance_id": row["provider_instance_id"], "name": row["name"],
            "state": row["state"], "recipe": json.loads(row["recipe_json"]),
            "provider_snapshot": json.loads(row["provider_snapshot_json"]),
            "adb_endpoint": row["adb_endpoint"], "last_adb_endpoint": row["last_adb_endpoint"],
            "android_identity": row["android_identity"],
            "discovery_source": row["discovery_source"],
            "provider_install_id": row["provider_install_id"],
            "presence_status": row["presence_status"],
            "profile_status": row["profile_status"],
            "standard_status": row["standard_status"],
            "standard_message": row["standard_message"],
            "managed": bool(row["managed"]),
            "display_index": row["display_index"],
            "last_seen_at": row["last_seen_at"],
            "last_connected_at": row["last_connected_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    @staticmethod
    def _virtual_backup(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "virtual_device_id": row["virtual_device_id"],
            "source_name": row["source_name"],
            "provider": row["provider"],
            "provider_instance_id": row["provider_instance_id"],
            "path": row["path"],
            "sha256": row["sha256"],
            "size_bytes": int(row["size_bytes"]),
            "status": row["status"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_run_draft(self, name: str = "current") -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT revision, config_json, updated_at FROM run_drafts WHERE name=?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return {
            "name": name,
            "revision": int(row["revision"]),
            "config": json.loads(row["config_json"]),
            "updated_at": row["updated_at"],
        }

    def save_run_draft(
        self,
        config: dict[str, Any],
        *,
        name: str = "current",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        payload = json.dumps(config, ensure_ascii=False, sort_keys=True)
        updated_at = now_iso()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision, config_json, updated_at FROM run_drafts WHERE name=?",
                (name,),
            ).fetchone()
            current_revision = int(row["revision"]) if row is not None else 0
            if expected_revision is not None and expected_revision != current_revision:
                current = {
                    "name": name,
                    "revision": current_revision,
                    "config": json.loads(row["config_json"]) if row is not None else {},
                    "updated_at": row["updated_at"] if row is not None else updated_at,
                }
                raise RunDraftConflict(current)
            if row is not None and row["config_json"] == payload:
                return {
                    "name": name,
                    "revision": current_revision,
                    "config": json.loads(row["config_json"]),
                    "updated_at": row["updated_at"],
                }
            next_revision = current_revision + 1
            connection.execute(
                "INSERT INTO run_drafts(name, revision, config_json, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET revision=excluded.revision, "
                "config_json=excluded.config_json, updated_at=excluded.updated_at",
                (name, next_revision, payload, updated_at),
            )
        return {
            "name": name,
            "revision": next_revision,
            "config": json.loads(payload),
            "updated_at": updated_at,
        }

    @staticmethod
    def _content_plan_payload(plan: sqlite3.Row, revision: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": plan["id"],
            "plan_id": plan["id"],
            "name": plan["name"],
            "archived": bool(plan["archived"]),
            "current_revision_id": plan["current_revision_id"],
            "created_at": plan["created_at"],
            "updated_at": plan["updated_at"],
            "revision_id": revision["id"],
            "revision_number": int(revision["revision_number"]),
            "document": json.loads(revision["document_json"]),
            "revision_created_at": revision["created_at"],
        }

    def save_content_plan(
        self, document: dict[str, Any], *, plan_id: str | None = None
    ) -> dict[str, Any]:
        from content_plans import normalize_content_plan

        normalized = normalize_content_plan(document)
        timestamp = now_iso()
        resolved_plan_id = str(plan_id or uuid.uuid4().hex).strip()
        if not resolved_plan_id:
            raise ValueError("内容计划标识无效")
        revision_id = uuid.uuid4().hex
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM content_plans WHERE id=?", (resolved_plan_id,)
            ).fetchone()
            revision_number = 1
            if existing is not None:
                latest = connection.execute(
                    "SELECT MAX(revision_number) AS value FROM content_plan_revisions WHERE plan_id=?",
                    (resolved_plan_id,),
                ).fetchone()
                revision_number = int(latest["value"] or 0) + 1
            connection.execute(
                "INSERT INTO content_plan_revisions "
                "(id, plan_id, revision_number, document_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    revision_id,
                    resolved_plan_id,
                    revision_number,
                    json.dumps(normalized, ensure_ascii=False),
                    timestamp,
                ),
            )
            if existing is None:
                connection.execute(
                    "INSERT INTO content_plans "
                    "(id, name, current_revision_id, archived, created_at, updated_at) "
                    "VALUES (?, ?, ?, 0, ?, ?)",
                    (resolved_plan_id, normalized["name"], revision_id, timestamp, timestamp),
                )
            else:
                connection.execute(
                    "UPDATE content_plans SET name=?, current_revision_id=?, archived=0, updated_at=? WHERE id=?",
                    (normalized["name"], revision_id, timestamp, resolved_plan_id),
                )
        return self.get_content_plan_revision(revision_id)

    def get_content_plan_revision(self, revision_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            revision = connection.execute(
                "SELECT * FROM content_plan_revisions WHERE id=?", (revision_id,)
            ).fetchone()
            if revision is None:
                raise KeyError(revision_id)
            plan = connection.execute(
                "SELECT * FROM content_plans WHERE id=?", (revision["plan_id"],)
            ).fetchone()
        if plan is None:
            raise KeyError(revision_id)
        return self._content_plan_payload(plan, revision)

    def list_content_plans(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        clause = "" if include_archived else "WHERE p.archived=0"
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT p.*, r.id AS r_id, r.plan_id AS r_plan_id, "
                "r.revision_number AS r_revision_number, r.document_json AS r_document_json, "
                "r.created_at AS r_created_at FROM content_plans p "
                "JOIN content_plan_revisions r ON r.id=p.current_revision_id "
                f"{clause} ORDER BY p.updated_at DESC, p.name ASC"
            ).fetchall()
        result = []
        for row in rows:
            revision = {
                "id": row["r_id"],
                "plan_id": row["r_plan_id"],
                "revision_number": row["r_revision_number"],
                "document_json": row["r_document_json"],
                "created_at": row["r_created_at"],
            }
            result.append(self._content_plan_payload(row, revision))
        return result

    def archive_content_plan(self, plan_id: str) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE content_plans SET archived=1, updated_at=? WHERE id=? AND archived=0",
                (now_iso(), str(plan_id).strip()),
            )
        return cursor.rowcount > 0

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
        if outcome not in INCIDENT_OUTCOMES:
            raise ValueError(f"Unsupported incident outcome: {outcome}")
        incident_id = uuid.uuid4().hex
        fingerprint = self.incident_fingerprint(stage, error_type, error_message)
        with self.connection() as connection:
            if (context or {}).get("model_error_fingerprint"):
                connection.execute("BEGIN IMMEDIATE")
                model_fingerprint = str(context["model_error_fingerprint"])
                existing = connection.execute(
                    "SELECT id,context_json,created_at FROM incidents WHERE task_id=? AND device_id=? AND stage=? ORDER BY created_at",
                    (task_id, device_id, stage),
                ).fetchall()
                for row in existing:
                    previous = json.loads(row["context_json"] or "{}")
                    if previous.get("model_error_fingerprint") != model_fingerprint:
                        continue
                    previous["occurrence_count"] = int(previous.get("occurrence_count", 1)) + 1
                    previous["first_seen_at"] = previous.get("first_seen_at", row["created_at"])
                    previous["last_seen_at"] = now_iso()
                    connection.execute("UPDATE incidents SET context_json=?,outcome=? WHERE id=?",
                                       (json.dumps(previous, ensure_ascii=False), outcome, row["id"]))
                    return str(row["id"])
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
                "SELECT * FROM incidents ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
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
            TaskStore._validate_comment_policy(payload)
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
            if content_mode not in {"general", "mixed", "search", "hybrid"}:
                raise ValueError("content_mode must be general, mixed, search, or hybrid")
            topic_prompt = payload.get("topic_prompt")
            if content_mode != "general" and (
                not isinstance(topic_prompt, str) or not topic_prompt.strip()
            ):
                raise ValueError("topic_prompt is required")
            if content_mode in {"search", "hybrid"} and not str(payload.get("search_query", "")).strip():
                raise ValueError("search_query is required in search or hybrid mode")
            if content_mode == "hybrid":
                segment_values: dict[str, int] = {}
                for name, fallback in (
                    ("search_segment_min", 7),
                    ("search_segment_max", 14),
                    ("home_segment_min", 5),
                    ("home_segment_max", 10),
                ):
                    value = payload.get(name, fallback)
                    if not isinstance(value, int) or not 1 <= value <= 200:
                        raise ValueError(f"{name} must be between 1 and 200")
                    segment_values[name] = value
                if segment_values["search_segment_min"] > segment_values["search_segment_max"]:
                    raise ValueError("search segment minimum must not exceed maximum")
                if segment_values["home_segment_min"] > segment_values["home_segment_max"]:
                    raise ValueError("home segment minimum must not exceed maximum")
            search_trust_results = payload.get("search_trust_results", False)
            if not isinstance(search_trust_results, bool):
                raise ValueError("search_trust_results must be a boolean")
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
            snapshot = payload.get("content_plan_snapshot")
            if snapshot is not None:
                if not isinstance(snapshot, dict):
                    raise ValueError("content_plan_snapshot must be an object")
                theme = snapshot.get("theme")
                if not isinstance(theme, dict):
                    raise ValueError("content_plan_snapshot.theme is required")
                if str(theme.get("topic_prompt", "")).strip() != str(topic_prompt or "").strip():
                    raise ValueError("frozen topic_prompt does not match task payload")
                if content_mode in {"search", "hybrid"} and str(theme.get("search_query", "")).strip() != str(payload.get("search_query", "")).strip():
                    raise ValueError("frozen search_query does not match task payload")
                queue_index = snapshot.get("theme_queue_index")
                queue_size = snapshot.get("theme_queue_size")
                if not isinstance(queue_index, int) or not isinstance(queue_size, int) or not 1 <= queue_index <= queue_size <= 20:
                    raise ValueError("content plan queue position is invalid")
            TaskStore._validate_comment_policy(payload)
            max_gate_skips = payload.get("max_gate_skips", 3)
            if not isinstance(max_gate_skips, int) or max_gate_skips < 0:
                raise ValueError("max_gate_skips must be a non-negative integer")
        if task_type == "douyin_engagement_inspection":
            submission_id = payload.get("submission_id")
            if (
                not isinstance(submission_id, str)
                or not submission_id.strip()
                or len(submission_id) > 80
            ):
                raise ValueError("submission_id is required for engagement inspection")
            for name in (
                "inspection_index",
                "after_round_index",
                "inspection_every_rounds",
            ):
                value = payload.get(name)
                if not isinstance(value, int) or not 1 <= value <= 20:
                    raise ValueError(f"{name} must be between 1 and 20")
            max_items = payload.get("max_items_per_section", 20)
            if not isinstance(max_items, int) or not 1 <= max_items <= 100:
                raise ValueError("max_items_per_section must be between 1 and 100")
            if "round_index" in payload:
                raise ValueError("engagement inspection must not define round_index")
            workflow_version = payload.get("inspection_workflow_version", "v1")
            if workflow_version not in {"v1", "v2", "v3"}:
                raise ValueError("inspection_workflow_version must be v1, v2, or v3")
            if workflow_version in {"v2", "v3"}:
                from task_preparation import PREPARATION_VERSION, engagement_rule
                supplied = payload.get("inspection_calibration") or {}
                bundled = workflow_version == "v3" and payload.get("preparation_version") == PREPARATION_VERSION and all(
                    supplied.get(key) == value for key, value in engagement_rule().items())
                for name in ("expected_app_version", "expected_display_signature"):
                    if bundled and name == "expected_app_version":
                        continue
                    value = payload.get(name)
                    if not isinstance(value, str) or not value.strip() or len(value) > 80:
                        raise ValueError(f"{name} is required for {workflow_version} inspection")
                calibration = payload.get("inspection_calibration")
                if not isinstance(calibration, dict):
                    raise ValueError(
                        f"inspection_calibration is required for {workflow_version} inspection"
                    )
                if (
                    workflow_version == "v2"
                    and calibration.get("device_id") != payload.get("device_id")
                ):
                    raise ValueError("inspection_calibration device_id must match task device_id")
                if (
                    workflow_version == "v2"
                    and (
                        calibration.get("app_version") != payload.get("expected_app_version")
                        or calibration.get("display_signature")
                        != payload.get("expected_display_signature")
                    )
                ):
                    raise ValueError(
                        f"inspection_calibration signature must match frozen {workflow_version} fields"
                    )
                if not bundled and (
                    not isinstance(calibration.get("profile_version"), str)
                    or not calibration["profile_version"].strip()
                    or not isinstance(calibration.get("passes"), int)
                    or calibration["passes"] < 3
                    or calibration.get("later_passes_semantically_equal") is not True
                    or not isinstance(calibration.get("controls"), dict)
                    or not isinstance(calibration.get("sections"), dict)
                ):
                    raise ValueError("inspection_calibration must be a stable three-pass calibration")
                if workflow_version == "v3":
                    controls = calibration.get("controls") or {}
                    if not any(
                        any(alias in str(label) for alias in ("互动消息", "互动通知", "全部互动", "全部消息", "赞评收藏"))
                        for label in list(controls.get("aggregate") or [])
                    ):
                        raise ValueError(
                            "v3 inspection calibration must identify the unified activity entry"
                        )

    @staticmethod
    def _validate_comment_policy(payload: dict[str, Any]) -> None:
        enabled = payload.get("comment_policy_enabled", False)
        prompt = payload.get("comment_policy_prompt", "")
        if not isinstance(enabled, bool):
            raise ValueError("comment_policy_enabled must be a boolean")
        if not isinstance(prompt, str) or len(prompt) > 1000:
            raise ValueError("comment_policy_prompt must be a string up to 1000 characters")
        if enabled and not prompt.strip():
            raise ValueError("comment_policy_prompt is required when enabled")

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

    def cancel_superseded_virtual_engagement_inspections(self) -> int:
        """Cancel queued v1/v2 VM inspections once v3 becomes authoritative.

        Historical and running tasks are deliberately left untouched.  The
        SQL is idempotent and only matches endpoints registered to managed
        virtual devices, including their last known dynamic ADB address.
        """
        timestamp = now_iso()
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='cancelled', finished_at=?, "
                "error='superseded_by_engagement_inspection_v3' "
                "WHERE status='pending' "
                "AND task_type='douyin_engagement_inspection' "
                "AND COALESCE(json_extract(payload_json, '$.inspection_workflow_version'), 'v1') IN ('v1','v2') "
                "AND EXISTS (SELECT 1 FROM virtual_devices vd "
                "WHERE vd.managed=1 AND vd.state!='retired' "
                "AND (vd.adb_endpoint=tasks.device_id OR vd.last_adb_endpoint=tasks.device_id))",
                (timestamp,),
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

    def list_active_tasks(self) -> list[TaskRecord]:
        """Return only live queue records for lightweight status polling."""
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE status IN ('running','pending') "
                "ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, "
                "COALESCE(started_at, not_before, created_at), rowid"
            ).fetchall()
        return [self._record(row) for row in rows]

    def task_group_count(self) -> int:
        """Count display groups in SQL without loading task results or evidence."""
        with self.connection() as connection:
            row = connection.execute(
                "WITH shaped AS ("
                " SELECT task_type, device_id,"
                " COALESCE(json_extract(payload_json, '$.submission_id'), '') AS submission_id,"
                " COALESCE(json_extract(payload_json, '$.round_count'), 1) AS round_count,"
                " COALESCE(json_extract(payload_json, '$.round_index'), 1) AS round_index"
                " FROM tasks"
                ") SELECT "
                " COUNT(DISTINCT CASE WHEN submission_id<>'' THEN submission_id || char(31) || device_id END) +"
                " SUM(CASE WHEN submission_id='' AND (task_type<>'douyin_topic_session' OR round_count<=1 OR round_index=1) THEN 1 ELSE 0 END) AS count"
                " FROM shaped"
            ).fetchone()
        return int(row["count"] or 0)

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

    def create_initialization(
        self,
        device_id: str,
        *,
        platform_id: str = "douyin",
        options: dict[str, Any] | None = None,
    ) -> InitializationRecord:
        """Create one idempotent initialization request per device.

        Repeated clicks return the existing active record.  A completed run is
        never silently repeated because the optional write acceptance may have
        changed platform state.
        """
        clean_device_id = str(device_id or "").strip()
        if not clean_device_id:
            raise ValueError("device_id is required")
        if platform_id != "douyin":
            raise ValueError(f"Unsupported platform: {platform_id}")
        clean_options = dict(options or {})
        if not isinstance(clean_options.get("write_acceptance", False), bool):
            raise ValueError("write_acceptance must be a boolean")
        initialization_id = uuid.uuid4().hex
        timestamp = now_iso()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_device_view_sessions(connection)
            control = connection.execute(
                "SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' "
                "AND status IN ('created','connected','disconnected') LIMIT 1",
                (clean_device_id,),
            ).fetchone()
            if control is not None:
                raise ValueError("该设备正在人工接管，结束操作后才能初始化")
            row = connection.execute(
                "SELECT * FROM device_initializations WHERE device_id=? "
                "AND status IN ('queued','running','waiting_user') "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (clean_device_id,),
            ).fetchone()
            if row is not None:
                connection.commit()
                return self._initialization_record(row)
            connection.execute(
                "INSERT INTO device_initializations "
                "(id, device_id, platform_id, options_json, status, stage, "
                "progress_current, progress_total, message, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'queued', 'preflight', 0, 7, ?, ?, ?)",
                (
                    initialization_id,
                    clean_device_id,
                    platform_id,
                    json.dumps(clean_options, ensure_ascii=False),
                    "等待设备 Worker 接管",
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_initialization(initialization_id)

    def get_initialization(self, initialization_id: str) -> InitializationRecord:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM device_initializations WHERE id=?",
                (initialization_id,),
            ).fetchone()
        if row is None:
            raise KeyError(initialization_id)
        return self._initialization_record(row)

    def latest_initialization(self, device_id: str) -> InitializationRecord | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM device_initializations WHERE device_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
        return self._initialization_record(row) if row is not None else None

    def initialization_summary(self) -> dict[str, int]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM device_initializations "
                "GROUP BY status"
            ).fetchall()
        return {
            status: next(
                (int(row["count"]) for row in rows if row["status"] == status), 0
            )
            for status in INITIALIZATION_STATUSES
        }

    def has_initialization_ready(self, device_id: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM device_initializations WHERE device_id=? "
                "AND status='queued' LIMIT 1",
                (device_id,),
            ).fetchone()
        return row is not None

    def claim_initialization(
        self, device_id: str, worker_id: str
    ) -> InitializationRecord | None:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            stop_all = connection.execute("SELECT config_json FROM automation_profiles WHERE name='automation-stop'").fetchone()
            stop_device = connection.execute("SELECT value FROM system_state WHERE key=?", ("stop:" + device_id,)).fetchone()
            running = connection.execute("SELECT 1 FROM tasks WHERE device_id=? AND status='running'", (device_id,)).fetchone()
            if ((stop_all and json.loads(stop_all["config_json"]).get("stopped"))
                    or (stop_device and stop_device["value"] == "1") or running):
                connection.commit()
                return None
            self._expire_device_view_sessions(connection)
            control = connection.execute(
                "SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' "
                "AND status IN ('created','connected','disconnected') LIMIT 1",
                (device_id,),
            ).fetchone()
            if control is not None:
                connection.commit()
                return None
            row = connection.execute(
                "SELECT id FROM device_initializations WHERE device_id=? "
                "AND status='queued' ORDER BY created_at, id LIMIT 1",
                (device_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            timestamp = now_iso()
            connection.execute(
                "UPDATE device_initializations SET status='running', "
                "started_at=COALESCE(started_at, ?), updated_at=?, worker_id=?, "
                "message='初始化正在运行' WHERE id=? AND status='queued'",
                (timestamp, timestamp, worker_id, row["id"]),
            )
            connection.commit()
            initialization_id = str(row["id"])
        finally:
            connection.close()
        return self.get_initialization(initialization_id)

    def update_initialization(
        self,
        initialization_id: str,
        *,
        stage: str,
        progress_current: int,
        progress_total: int = 7,
        message: str,
        report_path: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE device_initializations SET stage=?, progress_current=?, "
                "progress_total=?, message=?, updated_at=?, "
                "report_path=COALESCE(?, report_path), "
                "result_json=COALESCE(?, result_json) "
                "WHERE id=? AND status='running'",
                (
                    stage,
                    int(progress_current),
                    int(progress_total),
                    message,
                    now_iso(),
                    report_path,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    initialization_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Initialization is no longer running")

    def finish_initialization(
        self,
        initialization_id: str,
        *,
        status: str,
        stage: str,
        message: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        report_path: str | None = None,
    ) -> None:
        if status not in {"waiting_user", "ready", "stale", "failed", "cancelled"}:
            raise ValueError(f"Unsupported initialization finish status: {status}")
        terminal = status in {"ready", "stale", "failed", "cancelled"}
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE device_initializations SET status=?, stage=?, message=?, "
                "updated_at=?, finished_at=?, report_path=COALESCE(?, report_path), "
                "result_json=?, error=? WHERE id=? AND status='running'",
                (
                    status,
                    stage,
                    message,
                    now_iso(),
                    now_iso() if terminal else None,
                    report_path,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    initialization_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Initialization is no longer running")

    def continue_initialization(self, device_id: str) -> InitializationRecord:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM device_initializations WHERE device_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            if row is None:
                raise KeyError(device_id)
            if row["status"] == "waiting_user":
                connection.execute(
                    "UPDATE device_initializations SET status='queued', "
                    "updated_at=?, message='已继续，等待设备 Worker 接管', "
                    "cancel_requested=0 WHERE id=?",
                    (now_iso(), row["id"]),
                )
            elif row["status"] not in {"queued", "running"}:
                raise ValueError("当前初始化不能继续，请重新开始初始化")
            initialization_id = str(row["id"])
            connection.commit()
        finally:
            connection.close()
        return self.get_initialization(initialization_id)

    def cancel_initialization(self, device_id: str) -> InitializationRecord:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM device_initializations WHERE device_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            if row is None:
                raise KeyError(device_id)
            timestamp = now_iso()
            if row["status"] in {"queued", "waiting_user"}:
                connection.execute(
                    "UPDATE device_initializations SET status='cancelled', "
                    "stage='cancelled', message='初始化已取消', updated_at=?, "
                    "finished_at=? WHERE id=?",
                    (timestamp, timestamp, row["id"]),
                )
            elif row["status"] == "running":
                connection.execute(
                    "UPDATE device_initializations SET cancel_requested=1, "
                    "updated_at=?, message='正在安全取消初始化' WHERE id=?",
                    (timestamp, row["id"]),
                )
            initialization_id = str(row["id"])
            connection.commit()
        finally:
            connection.close()
        return self.get_initialization(initialization_id)

    def cancel_unstarted_legacy_initializations(self, initialization_ids: list[str]) -> int:
        """Explicit upgrade targets only; never touch running or historical records."""
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cancelled = 0
            for identifier in dict.fromkeys(initialization_ids):
                row = connection.execute("SELECT * FROM device_initializations WHERE id=?", (identifier,)).fetchone()
                if not row or row["status"] != "queued" or row["started_at"] or row["worker_id"]:
                    continue
                if json.loads(row["options_json"]).get("preparation_version") == "on-demand-v1":
                    continue
                cancelled += connection.execute(
                    "UPDATE device_initializations SET status='cancelled', stage='cancelled', message=?, error=?, "
                    "updated_at=?, finished_at=? WHERE id=? AND status='queued' AND started_at IS NULL",
                    ("升级dev.11：未执行的旧式全套初始化已取消，今后按任务准备", "superseded_by_on_demand_v1",
                     now_iso(), now_iso(), identifier),
                ).rowcount
            return cancelled

    def initialization_cancel_requested(self, initialization_id: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT cancel_requested, status FROM device_initializations WHERE id=?",
                (initialization_id,),
            ).fetchone()
        return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))

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
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_device_view_sessions(connection)
            control = connection.execute(
                "SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' "
                "AND status IN ('created','connected','disconnected') LIMIT 1",
                (device_id,),
            ).fetchone()
            if control is not None:
                raise ValueError("该设备正在人工接管，不能提交或领取新任务")
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
            connection.commit()
        finally:
            connection.close()
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

    def recover_interrupted_initializations(self, device_id: str) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE device_initializations SET status='failed', stage='failed', "
                "updated_at=?, finished_at=?, "
                "message='初始化 Worker 中断，未自动重放', "
                "error='worker_interrupted; initialization was not retried automatically' "
                "WHERE device_id=? AND status='running'",
                (now_iso(), now_iso(), device_id),
            )
            return cursor.rowcount

    def reconcile_orphaned_initializations(self, worker_is_alive) -> int:
        """Only a proved-absent executor may release a running preparation lease."""
        with self.connection() as connection:
            rows = connection.execute("SELECT id, worker_id FROM device_initializations WHERE status='running'").fetchall()
        recovered = 0
        for row in rows:
            if not row["worker_id"] or worker_is_alive(row["worker_id"]):
                continue
            with self.connection() as connection:
                recovered += connection.execute(
                    "UPDATE device_initializations SET status='failed', stage='failed', updated_at=?, finished_at=?, "
                    "message='准备执行者已退出；可重新检查或人工接管', error='worker_interrupted; not replayed' "
                    "WHERE id=? AND status='running' AND worker_id=?",
                    (now_iso(), now_iso(), row["id"], row["worker_id"]),
                ).rowcount
        return recovered

    def claim_next(self, device_id: str, worker_id: str) -> TaskRecord | None:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_device_view_sessions(connection)
            control = connection.execute(
                "SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' "
                "AND status IN ('created','connected','disconnected') LIMIT 1",
                (device_id,),
            ).fetchone()
            if control is not None:
                connection.commit()
                return None
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
            self._expire_device_view_sessions(connection)
            control = connection.execute(
                "SELECT 1 FROM device_view_sessions WHERE device_id=? AND mode='control' "
                "AND status IN ('created','connected','disconnected') LIMIT 1",
                (device_id,),
            ).fetchone()
            if control is not None:
                return False
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

    def attach_run_dir(self, task_id: str, run_dir: str) -> None:
        """Attach append-only evidence to a running task for live read-only progress."""
        value = str(run_dir or "").strip()
        if not value:
            raise ValueError("run_dir is required")
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET run_dir=? WHERE id=? AND status='running'",
                (value, task_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Task is not running or does not exist: {task_id}")

    def finish(
        self,
        task_id: str,
        *,
        status: str,
        run_dir: str | None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if status not in {"completed", "degraded", "failed", "stopped"}:
            raise ValueError(
                "finish status must be completed, degraded, failed, or stopped"
            )
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

    @staticmethod
    def _task_recovery_payload(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["expected"] = json.loads(payload.pop("expected_json"))
        payload["actual"] = json.loads(payload.pop("actual_json"))
        return payload

    def create_task_recovery(
        self,
        *,
        origin_task_id: str,
        device_id: str,
        fingerprint: str,
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Create the single allowed recovery for one immutable origin task."""
        recovery_id = uuid.uuid4().hex
        timestamp = now_iso()
        created = False
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM task_recoveries WHERE origin_task_id=?",
                (origin_task_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO task_recoveries "
                    "(id, origin_task_id, device_id, fingerprint, status, "
                    "expected_json, actual_json, message, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                    (
                        recovery_id,
                        origin_task_id,
                        device_id,
                        fingerprint,
                        json.dumps(expected, ensure_ascii=False),
                        json.dumps(actual, ensure_ascii=False),
                        "等待只读语义复验",
                        timestamp,
                        timestamp,
                    ),
                )
                existing = connection.execute(
                    "SELECT * FROM task_recoveries WHERE id=?", (recovery_id,)
                ).fetchone()
                created = True
        assert existing is not None
        return self._task_recovery_payload(existing), created

    def request_task_recovery(
        self, origin_task_id: str
    ) -> tuple[dict[str, Any], bool]:
        """Queue the one allowed recovery for an existing action-free failure."""
        task = self.get(origin_task_id)
        result = task.result or {}
        if task.task_type != "douyin_engagement_inspection" or task.status != "failed":
            raise ValueError("只有失败的互动消息巡检可以复验")
        sections = result.get("sections") if isinstance(result.get("sections"), dict) else {}
        metadata = (
            result.get("inspection_metadata")
            if isinstance(result.get("inspection_metadata"), dict)
            else {}
        )
        legacy_action_free_drift = (
            result.get("failure_reason")
            in {"v2_app_version_changed", "v2_display_signature_changed"}
            and result.get("restored") is True
            and result.get("workflow_version") == "v2"
            and metadata.get("alert_created") is False
            and bool(sections)
            and all(
                isinstance(section, dict)
                and section.get("status") == "failed"
                and section.get("reason") == "not_checked"
                for section in sections.values()
            )
        )
        structured_action_free_drift = (
            result.get("failure_class") == "recoverable_precondition"
            and result.get("recovery_eligible") is True
            and result.get("navigation_started") is False
        )
        if not (structured_action_free_drift or legacy_action_free_drift):
            raise ValueError("该任务不满足只读自动复验条件")
        expected = {
            "app_version": str(
                result.get("expected_app_version")
                or task.payload.get("expected_app_version")
                or ""
            ),
            "display_signature": str(
                result.get("expected_display_signature")
                or task.payload.get("expected_display_signature")
                or ""
            ),
        }
        actual = {
            "app_version": str(result.get("actual_app_version") or ""),
            "display_signature": str(result.get("actual_display_signature") or ""),
        }
        fingerprint_source = "|".join(
            (
                expected["app_version"], actual["app_version"],
                expected["display_signature"], actual["display_signature"],
            )
        )
        fingerprint = hashlib.sha256(
            fingerprint_source.encode("utf-8")
        ).hexdigest()[:24]
        recovery, created = self.create_task_recovery(
            origin_task_id=task.id,
            device_id=task.device_id,
            fingerprint=fingerprint,
            expected=expected,
            actual=actual,
        )
        legacy_probe_never_started = (
            not created
            and recovery.get("status") == "waiting_user"
            and int(recovery.get("progress_current") or 0) == 0
            and not str((recovery.get("actual") or {}).get("app_version") or "")
            and recovery.get("error")
            in {"v2_app_version_changed", "v2_display_signature_changed"}
        )
        if legacy_probe_never_started:
            with self.connection() as connection:
                connection.execute(
                    "UPDATE task_recoveries SET status='queued', message=?, error=NULL, "
                    "evidence_dir=NULL, updated_at=? WHERE id=? AND status='waiting_user'",
                    ("等待兼容探测后执行只读语义复验", now_iso(), recovery["id"]),
                )
            recovery = self.get_task_recovery_by_id(recovery["id"])
        return recovery, created

    def update_task_recovery_context(
        self,
        recovery_id: str,
        *,
        fingerprint: str,
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> dict[str, Any]:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE task_recoveries SET fingerprint=?, expected_json=?, actual_json=?, "
                "updated_at=? WHERE id=? AND status IN ('queued', 'running')",
                (
                    fingerprint,
                    json.dumps(expected, ensure_ascii=False),
                    json.dumps(actual, ensure_ascii=False),
                    now_iso(),
                    recovery_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("复验上下文已经终结，不能覆盖")
        return self.get_task_recovery_by_id(recovery_id)

    def continue_task_recovery(self, origin_task_id: str) -> dict[str, Any]:
        """Explicitly continue a paused read-only revalidation using the same attempt."""
        timestamp = now_iso()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task_recoveries WHERE origin_task_id=?",
                (origin_task_id,),
            ).fetchone()
            if row is None:
                raise ValueError("该任务还没有可继续的复验")
            recovery = self._task_recovery_payload(row)
            if recovery.get("status") != "waiting_user":
                raise ValueError("只有等待处理的复验可以继续")
            if recovery.get("replacement_task_id"):
                raise ValueError("该任务已经生成关联补跑，不能重复继续")
            cursor = connection.execute(
                "UPDATE task_recoveries SET status='queued', progress_current=0, "
                "message=?, evidence_dir=NULL, error=NULL, updated_at=? "
                "WHERE origin_task_id=? AND status='waiting_user'",
                ("已确认页面状态，等待重新执行三遍只读语义复验", timestamp, origin_task_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("复验状态已变化，请刷新后重试")
        continued = self.get_task_recovery(origin_task_id)
        assert continued is not None
        return continued

    def has_ready_recovery(self, device_id: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM task_recoveries WHERE device_id=? AND status='queued' LIMIT 1",
                (device_id,),
            ).fetchone()
        return row is not None

    def claim_task_recovery(self, device_id: str) -> dict[str, Any] | None:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM task_recoveries WHERE device_id=? AND status='queued' "
                "ORDER BY created_at, id LIMIT 1",
                (device_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            recovery_id = str(row["id"])
            connection.execute(
                "UPDATE task_recoveries SET status='running', message=?, updated_at=? "
                "WHERE id=? AND status='queued'",
                ("正在准备只读语义复验", now_iso(), recovery_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_task_recovery_by_id(recovery_id)

    def close_interrupted_task_recoveries(self, device_id: str) -> int:
        """Never replay an interrupted semantic pass automatically."""
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE task_recoveries SET status='waiting_user', message=?, error=?, updated_at=? "
                "WHERE device_id=? AND status='running'",
                (
                    "只读复验中断，需要人工确认后重新校准",
                    "revalidation_interrupted; not replayed automatically",
                    now_iso(),
                    device_id,
                ),
            )
            return cursor.rowcount

    def update_task_recovery(
        self,
        recovery_id: str,
        *,
        status: str,
        progress_current: int,
        message: str,
        evidence_dir: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in TASK_RECOVERY_STATUSES:
            raise ValueError(f"Unsupported task recovery status: {status}")
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE task_recoveries SET status=?, progress_current=?, message=?, "
                "evidence_dir=COALESCE(?, evidence_dir), error=?, updated_at=? WHERE id=?",
                (status, progress_current, message, evidence_dir, error, now_iso(), recovery_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(recovery_id)
        return self.get_task_recovery_by_id(recovery_id)

    def finish_task_recovery(
        self,
        recovery_id: str,
        *,
        status: str,
        progress_current: int,
        progress_total: int,
        message: str,
        replacement_task_id: str | None = None,
        evidence_dir: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"ready", "waiting_user", "failed"}:
            raise ValueError(f"Unsupported terminal task recovery status: {status}")
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE task_recoveries SET status=?, progress_current=?, progress_total=?, "
                "message=?, replacement_task_id=?, evidence_dir=COALESCE(?, evidence_dir), "
                "error=?, updated_at=? WHERE id=?",
                (
                    status, progress_current, progress_total, message,
                    replacement_task_id, evidence_dir, error, now_iso(), recovery_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(recovery_id)
        return self.get_task_recovery_by_id(recovery_id)

    def get_task_recovery_by_id(self, recovery_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM task_recoveries WHERE id=?", (recovery_id,)
            ).fetchone()
        if row is None:
            raise KeyError(recovery_id)
        return self._task_recovery_payload(row)

    def get_task_recovery(self, origin_task_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM task_recoveries WHERE origin_task_id=?", (origin_task_id,)
            ).fetchone()
        return self._task_recovery_payload(row) if row is not None else None

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
                "SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._record(row) for row in rows]

    def get_visitor_baseline(self, device_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM visitor_baselines WHERE device_id=?", (device_id,)
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        try:
            payload["first_row"] = json.loads(payload.pop("first_row_json", "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload["first_row"] = {}
        return payload

    def upsert_visitor_baseline(
        self,
        *,
        device_id: str,
        app_version: str,
        display_signature: str,
        row_count: int,
        first_row_hash: str,
        visual_hash: str,
        first_row: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not device_id.strip() or row_count < 0:
            raise ValueError("invalid visitor baseline")
        timestamp = now_iso()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO visitor_baselines "
                "(device_id, app_version, display_signature, row_count, first_row_hash, visual_hash, first_row_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET "
                "app_version=excluded.app_version, display_signature=excluded.display_signature, "
                "row_count=excluded.row_count, first_row_hash=excluded.first_row_hash, "
                "visual_hash=excluded.visual_hash, first_row_json=excluded.first_row_json, "
                "updated_at=excluded.updated_at",
                (
                    device_id,
                    app_version[:80],
                    display_signature[:80],
                    int(row_count),
                    first_row_hash[:128],
                    visual_hash[:128],
                    json.dumps(first_row or {}, ensure_ascii=False, sort_keys=True),
                    timestamp,
                ),
            )
        return self.get_visitor_baseline(device_id) or {}

    @staticmethod
    def _interaction_inspection_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "device_id": row["device_id"],
            "workflow_version": row["workflow_version"],
            "status": row["status"],
            "result_kind": row["result_kind"],
            "restored": bool(row["restored"]),
            "summary": json.loads(row["summary_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "run_dir": row["run_dir"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def record_interaction_inspection(
        self,
        *,
        inspection_id: str,
        task_id: str,
        device_id: str,
        workflow_version: str,
        status: str,
        result_kind: str,
        restored: bool,
        summary: dict[str, Any],
        evidence: list[dict[str, Any]],
        run_dir: str,
        started_at: str,
        finished_at: str,
    ) -> dict[str, Any]:
        if (
            not inspection_id.strip()
            or not task_id.strip()
            or not device_id.strip()
            or workflow_version not in {"v1", "v2", "v3"}
            or status not in {"completed", "degraded", "failed"}
            or result_kind not in {"alert", "clear", "incomplete"}
            or not run_dir.strip()
        ):
            raise ValueError("invalid interaction inspection")
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO interaction_inspections "
                "(id, task_id, device_id, workflow_version, status, result_kind, restored, "
                "summary_json, evidence_json, run_dir, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, result_kind=excluded.result_kind, "
                "restored=excluded.restored, summary_json=excluded.summary_json, "
                "evidence_json=excluded.evidence_json, run_dir=excluded.run_dir, "
                "finished_at=excluded.finished_at",
                (
                    inspection_id,
                    task_id,
                    device_id,
                    workflow_version,
                    status,
                    result_kind,
                    int(restored),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    run_dir,
                    started_at,
                    finished_at,
                ),
            )
        return self.get_interaction_inspection(inspection_id)

    def get_interaction_inspection(self, inspection_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM interaction_inspections WHERE id=?",
                (inspection_id,),
            ).fetchone()
        if row is None:
            raise KeyError(inspection_id)
        return self._interaction_inspection_payload(row)

    def list_interaction_inspections(
        self, *, result_kind: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        if result_kind not in {None, "alert", "clear", "incomplete"}:
            raise ValueError("invalid interaction inspection result filter")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid pagination")
        if result_kind == "incomplete":
            # A run may discover an alert before a later section fails. It
            # belongs in both the alert view and the incomplete view.
            clause = " WHERE status!='completed'"
            params: list[Any] = []
        else:
            clause = " WHERE result_kind=?" if result_kind else ""
            params = [result_kind] if result_kind else []
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM interaction_inspections" + clause
                + " ORDER BY finished_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            total = int(connection.execute(
                "SELECT COUNT(*) AS count FROM interaction_inspections" + clause,
                params,
            ).fetchone()["count"])
        return {
            "inspections": [
                self._interaction_inspection_payload(row) for row in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def _interaction_alert_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "device_id": row["device_id"],
            "sources": json.loads(row["sources_json"]),
            "summary": json.loads(row["summary_json"]),
            "fingerprint": row["fingerprint"],
            "status": row["status"],
            "detected_at": row["detected_at"],
            "viewed_at": row["viewed_at"],
        }

    def record_interaction_alert(
        self,
        *,
        task_id: str,
        device_id: str,
        sources: list[str],
        summary: dict[str, Any],
        fingerprint: str,
    ) -> tuple[dict[str, Any], bool]:
        clean_sources = sorted(
            dict.fromkeys(
                value for value in sources if value in {
                    "private_messages", "received_likes", "comment_danmaku", "profile_visitors"
                }
            )
        )
        if not task_id.strip() or not device_id.strip() or not clean_sources or not fingerprint.strip():
            raise ValueError("invalid interaction alert")
        alert_id = uuid.uuid4().hex
        timestamp = now_iso()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO interaction_alerts "
                "(id, task_id, device_id, sources_json, summary_json, fingerprint, status, detected_at, viewed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'unread', ?, NULL)",
                (
                    alert_id,
                    task_id,
                    device_id,
                    json.dumps(clean_sources, ensure_ascii=False),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    fingerprint[:160],
                    timestamp,
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                "SELECT * FROM interaction_alerts WHERE device_id=? AND fingerprint=?",
                (device_id, fingerprint[:160]),
            ).fetchone()
        if row is None:
            raise RuntimeError("interaction alert write failed")
        return self._interaction_alert_payload(row), created

    def list_interaction_alerts(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        if status not in {None, "unread", "viewed"}:
            raise ValueError("status must be unread or viewed")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid pagination")
        clause = " WHERE status=?" if status else ""
        params: list[Any] = [status] if status else []
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM interaction_alerts" + clause
                + " ORDER BY detected_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            total = int(connection.execute(
                "SELECT COUNT(*) AS count FROM interaction_alerts" + clause,
                params,
            ).fetchone()["count"])
            unread = int(connection.execute(
                "SELECT COUNT(*) AS count FROM interaction_alerts WHERE status='unread'"
            ).fetchone()["count"])
        return {
            "alerts": [self._interaction_alert_payload(row) for row in rows],
            "total": total,
            "unread_count": unread,
            "limit": limit,
            "offset": offset,
        }

    def acknowledge_interaction_alerts(self, alert_ids: list[str]) -> dict[str, Any]:
        clean_ids = list(dict.fromkeys(
            value.strip() for value in alert_ids if isinstance(value, str) and value.strip()
        ))[:100]
        if not clean_ids:
            raise ValueError("alert_ids is required")
        timestamp = now_iso()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE interaction_alerts SET status='viewed', viewed_at=? "
                "WHERE status='unread' AND id IN (" + ",".join("?" for _ in clean_ids) + ")",
                (timestamp, *clean_ids),
            )
            acknowledged_rows = connection.execute(
                "SELECT id FROM interaction_alerts WHERE viewed_at=? "
                "AND id IN (" + ",".join("?" for _ in clean_ids) + ")",
                (timestamp, *clean_ids),
            ).fetchall()
            remaining = int(connection.execute(
                "SELECT COUNT(*) AS count FROM interaction_alerts WHERE status='unread'"
            ).fetchone()["count"])
        return {
            "acknowledged": cursor.rowcount,
            "acknowledged_ids": [row["id"] for row in acknowledged_rows],
            "remaining_unread": remaining,
        }

    def list_all(self) -> list[TaskRecord]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_incidents_for_tasks(
        self, task_ids: list[str]
    ) -> list[IncidentRecord]:
        clean_ids = list(
            dict.fromkeys(str(value).strip() for value in task_ids if str(value).strip())
        )
        if not clean_ids:
            return []
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM incidents WHERE task_id IN ("
                + ",".join("?" for _ in clean_ids)
                + ") ORDER BY created_at DESC, rowid DESC",
                clean_ids,
            ).fetchall()
        return [self._incident_record(row) for row in rows]

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

    @staticmethod
    def _initialization_record(row: sqlite3.Row) -> InitializationRecord:
        status = str(row["status"])
        if status not in INITIALIZATION_STATUSES:
            raise ValueError(f"Unknown initialization status in database: {status}")
        return InitializationRecord(
            id=str(row["id"]),
            device_id=str(row["device_id"]),
            platform_id=str(row["platform_id"]),
            options=json.loads(row["options_json"]),
            status=status,
            stage=str(row["stage"]),
            progress_current=int(row["progress_current"]),
            progress_total=int(row["progress_total"]),
            message=str(row["message"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            worker_id=row["worker_id"],
            report_path=row["report_path"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            cancel_requested=bool(row["cancel_requested"]),
        )
