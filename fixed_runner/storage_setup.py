from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime
from contextlib import closing
from pathlib import Path
from typing import Any


PRODUCT_NAME = "MediaFlow"
LEGACY_PRODUCT_NAME = "RiskFlow"


def local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def bootstrap_path() -> Path:
    return local_app_data() / PRODUCT_NAME / "bootstrap.json"


def pending_migration_path() -> Path:
    return local_app_data() / PRODUCT_NAME / "pending-storage-migration.json"


def migration_result_path() -> Path:
    return local_app_data() / PRODUCT_NAME / "storage-migration-result.json"


def default_data_root() -> Path:
    return local_app_data() / PRODUCT_NAME / "data"


def legacy_data_root() -> Path:
    return local_app_data() / LEGACY_PRODUCT_NAME / "data"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _drive_type(path: Path) -> int | None:
    if os.name != "nt":
        return None
    anchor = path.anchor or str(path)
    return int(ctypes.windll.kernel32.GetDriveTypeW(str(anchor)))


def validate_data_root(value: str | Path, *, app_root: str | Path | None = None) -> dict[str, Any]:
    raw = str(value or "").strip().strip('"')
    if not raw:
        return {"valid": False, "message": "请选择MediaFlow数据目录"}
    candidate = Path(raw).expanduser().resolve()
    if candidate == Path(candidate.anchor):
        return {"valid": False, "path": str(candidate), "message": "不能把磁盘根目录作为数据目录"}
    if app_root:
        application = Path(app_root).resolve()
        if candidate == application or _is_relative_to(candidate, application):
            return {"valid": False, "path": str(candidate), "message": "数据目录不能放在程序安装目录内"}
    drive_type = _drive_type(candidate)
    if drive_type is not None and drive_type != 3:  # DRIVE_FIXED
        return {"valid": False, "path": str(candidate), "message": "首版只支持本地固定磁盘"}
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".mediaflow-write-", dir=candidate, delete=True):
            pass
        free_bytes = shutil.disk_usage(candidate).free
    except OSError as exc:
        return {"valid": False, "path": str(candidate), "message": f"目录不可写：{exc}"}
    return {
        "valid": True,
        "path": str(candidate),
        "free_bytes": int(free_bytes),
        "message": "目录可用",
    }


def _sqlite_quick_check(root: Path) -> None:
    for database in root.rglob("*.db"):
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"数据库校验失败：{database.name}")


def _tree_summary(root: Path) -> tuple[int, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)


def _copy_verified(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise RuntimeError("当前数据目录不存在，不能迁移")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and any(target.iterdir()):
        raise RuntimeError("目标数据目录已有文件；为避免覆盖，请选择空目录")
    before_count, before_size = _tree_summary(source)
    staging = target.parent / f".mediaflow-migration-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, staging, copy_function=shutil.copy2)
        _sqlite_quick_check(staging)
        after_count, after_size = _tree_summary(staging)
        if after_count != before_count or after_size != before_size:
            raise RuntimeError("数据复制校验未通过，仍保留原目录")
        if target.exists():
            target.rmdir()
        os.replace(staging, target)
    except Exception:
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def write_bootstrap(data_root: Path, *, migrated_from: Path | None = None) -> Path:
    target = bootstrap_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    payload = {
        "schema_version": 1,
        "product": PRODUCT_NAME,
        "data_root": str(data_root.resolve()),
        "legacy_data_root": str(migrated_from.resolve()) if migrated_from else None,
        "legacy_preserved": bool(migrated_from),
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def prepare_data_root(
    target_value: str | Path,
    *,
    app_root: str | Path | None = None,
    migrate_legacy: bool = True,
) -> dict[str, Any]:
    validation = validate_data_root(target_value, app_root=app_root)
    if not validation["valid"]:
        raise ValueError(validation["message"])
    target = Path(validation["path"])
    legacy = legacy_data_root()
    migrated = False
    if migrate_legacy and legacy.is_dir() and legacy.resolve() != target.resolve():
        _copy_verified(legacy, target)
        migrated = True
    target.mkdir(parents=True, exist_ok=True)
    config = write_bootstrap(target, migrated_from=legacy if migrated else None)
    return {
        **validation,
        "migrated": migrated,
        "legacy_preserved": migrated,
        "bootstrap_path": str(config),
    }


def schedule_data_root(
    target_value: str | Path,
    *,
    app_root: str | Path | None = None,
) -> dict[str, Any]:
    validation = validate_data_root(target_value, app_root=app_root)
    if not validation["valid"]:
        raise ValueError(validation["message"])
    status = storage_status()
    source = Path(status["data_root"]).resolve()
    target = Path(validation["path"]).resolve()
    if source == target:
        return {**validation, "scheduled": False, "message": "当前已经在使用这个目录"}
    if target.exists() and any(target.iterdir()):
        raise RuntimeError("目标数据目录已有文件；为避免覆盖，请选择空目录")
    marker = pending_migration_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "source": str(source),
        "target": str(target),
        "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(marker)
    return {
        **validation,
        "scheduled": True,
        "source": str(source),
        "message": "已安排迁移；安全停止后台后重新打开MediaFlow即可执行",
    }


def apply_pending_migration(*, app_root: str | Path | None = None) -> dict[str, Any]:
    marker = pending_migration_path()
    if not marker.is_file():
        return {"applied": False, "message": "没有待处理的数据迁移"}
    try:
        payload = json.loads(marker.read_text(encoding="utf-8-sig"))
        source = Path(str(payload["source"])).resolve()
        target_value = str(payload["target"])
        validation = validate_data_root(target_value, app_root=app_root)
        if not validation["valid"]:
            raise ValueError(validation["message"])
        target = Path(validation["path"]).resolve()
        _copy_verified(source, target)
        config = write_bootstrap(target, migrated_from=source)
        result = {
            "applied": True,
            "source": str(source),
            "target": str(target),
            "source_preserved": True,
            "bootstrap_path": str(config),
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "message": "数据已复制校验并切换；原目录保留用于回滚",
        }
    except Exception as exc:
        result = {
            "applied": False,
            "error": str(exc),
            "source_preserved": True,
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "message": "迁移失败，仍使用原数据目录",
        }
    outcome = migration_result_path()
    outcome.parent.mkdir(parents=True, exist_ok=True)
    outcome.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    marker.unlink(missing_ok=True)
    return result


def storage_status() -> dict[str, Any]:
    config = bootstrap_path()
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(config.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    chosen = Path(str(payload.get("data_root") or default_data_root()))
    pending: dict[str, Any] | None = None
    last_result: dict[str, Any] | None = None
    try:
        pending = json.loads(pending_migration_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    try:
        last_result = json.loads(migration_result_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {
        "product": PRODUCT_NAME,
        "data_root": str(chosen),
        "configured": config.is_file(),
        "bootstrap_path": str(config),
        "legacy_available": legacy_data_root().is_dir(),
        "legacy_data_root": str(legacy_data_root()),
        "pending_migration": pending,
        "last_migration": last_result,
        "categories": ["数据库与配置", "任务截图和证据", "日志", "设备档案", "虚拟机备份及临时文件"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="MediaFlow first-run storage setup")
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--target", required=True)
    prepare.add_argument("--app-root")
    prepare.add_argument("--no-migrate", action="store_true")
    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--target", required=True)
    schedule.add_argument("--app-root")
    apply_pending = subparsers.add_parser("apply-pending")
    apply_pending.add_argument("--app-root")
    subparsers.add_parser("status")
    arguments = parser.parse_args()
    if arguments.action == "prepare":
        result = prepare_data_root(
            arguments.target,
            app_root=arguments.app_root,
            migrate_legacy=not arguments.no_migrate,
        )
    elif arguments.action == "schedule":
        result = schedule_data_root(arguments.target, app_root=arguments.app_root)
    elif arguments.action == "apply-pending":
        result = apply_pending_migration(app_root=arguments.app_root)
    else:
        result = storage_status()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
