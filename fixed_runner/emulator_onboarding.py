from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from control_config import normalized_config, submit_scheduled_rounds
from task_store import InitializationRecord, TaskRecord, TaskStore


PROFILE_NAME = "default"
AUTO_INITIALIZE_FIELD = "auto_onboard_root_emulators"
AUTO_RUN_FIELD = "auto_run_after_onboarding"
ONBOARDING_MARKER = "auto_onboarding_initialization_id"
ONBOARDING_STAGE = "auto_onboarding_stage"
VALIDATION_STAGE = "validation"
RUN_STAGE = "run"
VALIDATION_VIDEO_COUNT = 3


@dataclass(frozen=True)
class EmulatorCandidate:
    device_id: str
    android_identity: str
    name: str
    source: str
    alias_of: str | None = None


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _adb_path() -> str:
    return shutil.which("adb.exe") or shutil.which("adb") or "adb.exe"


def _run_text(
    args: list[str], *, timeout: int = 8, runner: Callable[..., Any] = subprocess.run
) -> subprocess.CompletedProcess[str]:
    return runner(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=_creation_flags(),
    )


def _mumu_manager_path() -> Path | None:
    candidates = [
        Path(os.environ.get("MUMU_MANAGER_PATH", "")),
        Path(r"D:\MuMuPlayer\nx_main\MuMuManager.exe"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Netease"
        / "MuMuPlayerGlobal-12.0"
        / "shell"
        / "MuMuManager.exe",
    ]
    for candidate in candidates:
        if str(candidate) and candidate.is_file():
            return candidate
    discovered = shutil.which("MuMuManager.exe")
    return Path(discovered) if discovered else None


def _adb_states(
    *, adb: str | None = None, runner: Callable[..., Any] = subprocess.run
) -> dict[str, str]:
    result = _run_text([adb or _adb_path(), "devices"], runner=runner)
    states: dict[str, str] = {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            states[parts[0]] = parts[1]
    return states


def _adb_shell(
    device_id: str,
    command: list[str],
    *,
    adb: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    result = _run_text(
        [adb or _adb_path(), "-s", device_id, "shell", *command],
        runner=runner,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _android_identity(
    device_id: str,
    *,
    adb: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    value = _adb_shell(
        device_id,
        ["settings", "get", "secure", "android_id"],
        adb=adb,
        runner=runner,
    )
    if value and value.lower() not in {"null", "unknown"}:
        return value
    return _adb_shell(
        device_id, ["getprop", "ro.boot.serialno"], adb=adb, runner=runner
    )


def _is_root(
    device_id: str,
    *,
    adb: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> bool:
    return "uid=0(root)" in _adb_shell(
        device_id, ["id"], adb=adb, runner=runner
    )


def _mumu_instances(
    *,
    manager_path: Path | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> list[dict[str, Any]]:
    manager = manager_path or _mumu_manager_path()
    if manager is None:
        return []
    result = _run_text(
        [str(manager), "info", "-v", "all"], timeout=10, runner=runner
    )
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    instances: list[dict[str, Any]] = []
    for value in payload.values() if isinstance(payload, dict) else []:
        if not isinstance(value, dict) or not value.get("is_android_started"):
            continue
        host = str(value.get("adb_host_ip") or "").strip()
        port = value.get("adb_port")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            continue
        try:
            port_number = int(port)
        except (TypeError, ValueError):
            continue
        if not 1 <= port_number <= 65535:
            continue
        instances.append(
            {
                "device_id": f"127.0.0.1:{port_number}",
                "name": str(value.get("name") or f"MuMu-{value.get('index', port_number)}"),
                "source": "mumu_manager",
            }
        )
    return instances


def discover_root_emulators(
    configured_ids: list[str],
    known_identities: dict[str, str] | None = None,
    *,
    manager_path: Path | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> list[EmulatorCandidate]:
    """Discover only locally proven emulator endpoints and deduplicate aliases."""
    adb = _adb_path()
    states_before = _adb_states(adb=adb, runner=runner)
    configured_identities = dict(known_identities or {})
    for device_id in configured_ids:
        if states_before.get(device_id) != "device":
            continue
        identity = _android_identity(device_id, adb=adb, runner=runner)
        if identity:
            configured_identities.setdefault(identity, device_id)
    raw = _mumu_instances(manager_path=manager_path, runner=runner)
    raw.extend(
        {
            "device_id": device_id,
            "name": device_id,
            "source": "standard_emulator_serial",
        }
        for device_id, state in states_before.items()
        if state == "device" and device_id.startswith("emulator-")
    )
    candidates: list[EmulatorCandidate] = []
    seen_ids: set[str] = set()
    seen_identities: dict[str, str] = dict(configured_identities)
    for item in raw:
        device_id = str(item["device_id"])
        if device_id in seen_ids:
            continue
        seen_ids.add(device_id)
        preexisting = states_before.get(device_id) == "device"
        if not preexisting:
            _run_text([adb, "connect", device_id], runner=runner)
        states = _adb_states(adb=adb, runner=runner)
        if states.get(device_id) != "device" or not _is_root(
            device_id, adb=adb, runner=runner
        ):
            if not preexisting:
                _run_text([adb, "disconnect", device_id], runner=runner)
            continue
        identity = _android_identity(device_id, adb=adb, runner=runner)
        if not identity:
            if not preexisting:
                _run_text([adb, "disconnect", device_id], runner=runner)
            continue
        alias_of = seen_identities.get(identity)
        if alias_of == device_id:
            alias_of = None
        candidates.append(
            EmulatorCandidate(
                device_id=device_id,
                android_identity=identity,
                name=str(item.get("name") or device_id),
                source=str(item.get("source") or "unknown"),
                alias_of=alias_of,
            )
        )
        if alias_of:
            if not preexisting:
                _run_text([adb, "disconnect", device_id], runner=runner)
        else:
            seen_identities[identity] = device_id
    return candidates


def _tasks_for_initialization(
    tasks: list[TaskRecord], initialization_id: str, stage: str
) -> list[TaskRecord]:
    return [
        task
        for task in tasks
        if task.payload.get(ONBOARDING_MARKER) == initialization_id
        and task.payload.get(ONBOARDING_STAGE) == stage
    ]


def _validation_passed(task: TaskRecord) -> bool:
    result = dict(task.result or {})
    return (
        task.status == "completed"
        and result.get("status") == "passed"
        and int(result.get("videos_seen") or 0) == VALIDATION_VIDEO_COUNT
        and int(result.get("model_valid_decisions") or 0) == VALIDATION_VIDEO_COUNT
        and int(result.get("model_errors") or 0) == 0
        and int(result.get("video_errors") or 0) == 0
        and int(result.get("likes") or 0) == 0
        and int(result.get("favorites") or 0) == 0
        and int(result.get("comments_sent") or 0) == 0
    )


class EmulatorOnboardingCoordinator:
    def __init__(
        self,
        store: TaskStore,
        *,
        discover: Callable[
            [list[str], dict[str, str]], list[EmulatorCandidate]
        ] = discover_root_emulators,
        submitter: Callable[[TaskStore, dict[str, Any]], list[str]] = submit_scheduled_rounds,
    ) -> None:
        self.store = store
        self.discover = discover
        self.submitter = submitter

    def _queue_validation(
        self, config: dict[str, Any], device_id: str, initialization_id: str
    ) -> list[str]:
        payload = normalized_config(
            {
                **config,
                "device_id": device_id,
                "device_ids": [device_id],
                "video_count": VALIDATION_VIDEO_COUNT,
                "round_count": 1,
                "round_interval_minutes": 0,
                "dwell_min": 3.0,
                "dwell_max": 5.0,
                "like_probability": 0.0,
                "favorite_probability": 0.0,
                "comment_probability": 0.0,
                "matched_like_probability": 0.0,
                "matched_favorite_probability": 0.0,
                "matched_comment_probability": 0.0,
                "content_mode": "general",
                "search_trust_results": False,
                "topic_filter_enabled": False,
                "preview_only": True,
                ONBOARDING_MARKER: initialization_id,
                ONBOARDING_STAGE: VALIDATION_STAGE,
            }
        )
        return self.submitter(self.store, payload)

    def _queue_current_plan(
        self, config: dict[str, Any], device_id: str, initialization_id: str
    ) -> list[str]:
        payload = normalized_config(
            {
                **config,
                "device_id": device_id,
                "device_ids": [device_id],
                ONBOARDING_MARKER: initialization_id,
                ONBOARDING_STAGE: RUN_STAGE,
            }
        )
        return self.submitter(self.store, payload)

    def _process_ready(
        self,
        config: dict[str, Any],
        candidate: EmulatorCandidate,
        initialization: InitializationRecord,
    ) -> dict[str, Any]:
        tasks = self.store.list_all()
        validation = _tasks_for_initialization(
            tasks, initialization.id, VALIDATION_STAGE
        )
        if not validation:
            task_ids = self._queue_validation(
                config, candidate.device_id, initialization.id
            )
            return {
                "device_id": candidate.device_id,
                "name": candidate.name,
                "stage": "validation_queued",
                "task_ids": task_ids,
            }
        validation_task = validation[0]
        if validation_task.status in {"pending", "running"}:
            return {
                "device_id": candidate.device_id,
                "name": candidate.name,
                "stage": "validation_running",
                "task_id": validation_task.id,
                "task_status": validation_task.status,
            }
        if not _validation_passed(validation_task):
            return {
                "device_id": candidate.device_id,
                "name": candidate.name,
                "stage": "validation_blocked",
                "task_id": validation_task.id,
                "task_status": validation_task.status,
                "error": validation_task.error or "三条零写入自检未完整通过",
            }
        return {
            "device_id": candidate.device_id,
            "name": candidate.name,
            "stage": "ready_waiting",
            "task_id": validation_task.id,
            "formal_task_requires_workbench": True,
        }

    def tick(self, config: dict[str, Any]) -> dict[str, Any]:
        enabled = bool(config.get(AUTO_INITIALIZE_FIELD, False))
        summary: dict[str, Any] = {
            "enabled": enabled,
            "auto_run": False,
            "paused": False,
            "devices": [],
        }
        if not enabled:
            return summary
        summary["paused"] = self.store.is_paused()
        if summary["paused"]:
            return summary
        current_config = normalized_config(config)
        registry = dict(current_config.get("emulator_identity_registry") or {})
        candidates = self.discover(list(current_config["device_ids"]), registry)
        registry_changed = False
        for candidate in candidates:
            if candidate.alias_of:
                continue
            if candidate.android_identity not in registry:
                registry[candidate.android_identity] = candidate.device_id
                registry_changed = True
        if registry_changed:
            current_config = normalized_config(
                {**current_config, "emulator_identity_registry": registry}
            )
            self.store.save_profile(PROFILE_NAME, current_config)
        for candidate in candidates:
            if candidate.alias_of:
                summary["devices"].append(
                    {
                        "device_id": candidate.device_id,
                        "name": candidate.name,
                        "stage": "alias_existing",
                        "alias_of": candidate.alias_of,
                    }
                )
                continue
            if candidate.device_id not in current_config["device_ids"]:
                if len(current_config["device_ids"]) >= 8:
                    summary["devices"].append(
                        {
                            "device_id": candidate.device_id,
                            "name": candidate.name,
                            "stage": "device_pool_full",
                            "error": "设备池已达到 8 台上限",
                        }
                    )
                    continue
                current_config = normalized_config(
                    {
                        **current_config,
                        "device_ids": [
                            *current_config["device_ids"],
                            candidate.device_id,
                        ],
                    }
                )
                self.store.save_profile(PROFILE_NAME, current_config)
            initialization = self.store.latest_initialization(candidate.device_id)
            if initialization is None:
                initialization = self.store.create_initialization(
                    candidate.device_id,
                    options={
                        "write_acceptance": False,
                        "search_query": str(
                            current_config.get("search_query")
                            or current_config.get("topic_prompt")
                            or "人工智能"
                        )[:80],
                        "auto_onboarding": True,
                        "emulator_identity": candidate.android_identity,
                        "emulator_name": candidate.name,
                    },
                )
                summary["devices"].append(
                    {
                        "device_id": candidate.device_id,
                        "name": candidate.name,
                        "stage": "initialization_queued",
                        "initialization_id": initialization.id,
                    }
                )
                continue
            if not initialization.options.get("auto_onboarding"):
                summary["devices"].append(
                    {
                        "device_id": candidate.device_id,
                        "name": candidate.name,
                        "stage": "manual_managed",
                        "initialization_status": initialization.status,
                    }
                )
                continue
            if initialization.status == "ready":
                summary["devices"].append(
                    self._process_ready(current_config, candidate, initialization)
                )
                continue
            stage = (
                "initialization_running"
                if initialization.status in {"queued", "running"}
                else "initialization_blocked"
            )
            summary["devices"].append(
                {
                    "device_id": candidate.device_id,
                    "name": candidate.name,
                    "stage": stage,
                    "initialization_id": initialization.id,
                    "initialization_status": initialization.status,
                    "error": initialization.error,
                }
            )
        return summary
