from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from adb_runtime import resolve_adb_executable
from task_store import InitializationRecord, RunDraftConflict, TaskStore
from worker import DEFAULT_ARTIFACTS, DEFAULT_DB
from runtime_layout import APP_ROOT, RUNTIME_ROOT, ensure_stream_secret, tool_environment
from control_config import (
    BUILTIN_PRESETS,
    DEFAULT_CONFIG,
    PRESET_FIELDS,
    delete_preset,
    list_presets,
    normalized_config,
    openrouter_key_status,
    save_openrouter_key,
    save_preset,
    submit_scheduled_rounds,
    validate_openrouter_key,
    validate_preset_name,
)
from model_connection import test_current_model, verify as verify_openrouter_key
import model_providers
from engagement_preflight import (
    acknowledge_visitor_reminder,
    visitor_reminder_status,
)
from device_profiles import enrich_device_statuses, load_device_profile_payloads, load_device_profiles
from platform_profiles import latest_device_platform_profile, profile_matches_runtime
from profile_resolution import resolve_execution_profile
from virtual_device_qualification import qualify_virtual_device
from evidence_governance import EvidenceGovernance
from runtime_control import (
    BACKGROUND_HEARTBEAT_PATH,
    PROJECT_PYTHON,
    RuntimeControl,
    worker_role,
    worker_spec,
)
from topic_review_store import TopicReviewStore
from run_planning import (
    build_preview as build_workbench_preview,
    get_or_create_draft,
    save_draft,
    submit_previewed_draft,
)
from virtual_devices import MuMuProvider, resolve_mumu_manager
from virtual_device_inventory import VirtualDeviceInventory, manager_identity
from storage_setup import schedule_data_root, storage_status, validate_data_root
from product_version import product_version
from skill_bundle import build_skill_bundle, build_skill_markdown


HOST = "127.0.0.1"
PORT = 48138
_AGENT_SERVICE_LOCK = threading.Lock()
PROFILE_NAME = "default"
DEVICE_PREFERENCES_PROFILE = "device-preferences"


def device_preferences(store: TaskStore) -> dict[str, bool]:
    saved = store.get_profile(DEVICE_PREFERENCES_PROFILE) or {}
    return {"physical_devices_enabled": bool(saved.get("physical_devices_enabled", False))}
DOUYIN_PACKAGE = "com.ss.android.ugc.aweme"
_RUNTIME_SIGNATURE_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_RUNTIME_SIGNATURE_LOCK = threading.Lock()
_RUNTIME_SIGNATURE_TTL_SECONDS = 30.0
_RUNTIME_SIGNATURE_REFRESHING: set[str] = set()
_STATUS_PAYLOAD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_STATUS_PAYLOAD_LOCK = threading.Lock()
_STATUS_PAYLOAD_TTL_SECONDS = 0.75
LEGACY_DEVELOPMENT_DEVICE_IDS = (
    "emulator-" + str(5556),
    *(f"127.0.0.1:{port}" for port in range(16448, 16545, 32)),
)


def _required_adb() -> str:
    adb = resolve_adb_executable()
    if not adb:
        raise RuntimeError("MediaFlow没有找到ADB组件，请修复安装后重试")
    return adb


def write_response_bytes(writer: Any, payload: bytes) -> bool:
    """Write a response without treating a closed browser tab as an API failure."""
    try:
        writer.write(payload)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        return False
    return True
WORKER_PID = RUNTIME_ROOT / "control-worker.pid"
WORKER_LOG = RUNTIME_ROOT / "control-worker.log"
PROJECT_ROOT = APP_ROOT
TOPIC_MANIFESTS = (
    RUNTIME_ROOT / "topic_evaluation" / "ai-search-topic-v3-1-final-results.jsonl",
    RUNTIME_ROOT / "topic_evaluation" / "core-boundary-v3-1-results.jsonl",
)
EVIDENCE_ROOTS = (
    RUNTIME_ROOT,
    Path(__file__).resolve().parent / "artifacts",
    Path(__file__).resolve().parent / "artifacts-uia2",
    Path.home() / "Pictures",
)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


def _process_image_name(pid: int) -> str | None:
    if os.name != "nt" or pid <= 0:
        return None
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(buffer))
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return None
        return Path(buffer.value).name.lower()
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def worker_id_is_running(worker_id: str) -> bool:
    """Validate the Python process encoded in worker ids such as host-12345."""
    match = re.search(r"-(\d+)$", str(worker_id))
    if not match:
        return False
    pid = int(match.group(1))
    if os.name == "nt":
        return _process_image_name(pid) in {"python.exe", "pythonw.exe"}
    return _pid_is_running(pid)


def _safe_device_name(device_id: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in device_id)


def _worker_pid_path(device_id: str) -> Path:
    return RUNTIME_ROOT / f"control-worker-{_safe_device_name(device_id)}.pid"


def worker_status(device_id: str) -> dict[str, Any]:
    status = RuntimeControl().status(worker_role(device_id))
    return {"device_id": device_id, **status}


def background_supervisor_is_fresh(
    heartbeat_path: Path = BACKGROUND_HEARTBEAT_PATH,
) -> bool:
    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        age_seconds = max(0.0, time.time() - heartbeat_path.stat().st_mtime)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return payload.get("state") == "running" and age_seconds <= 20


def background_onboarding_status(
    heartbeat_path: Path = BACKGROUND_HEARTBEAT_PATH,
) -> dict[str, Any]:
    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"enabled": False, "auto_run": False, "paused": False, "devices": []}
    summary = payload.get("emulator_onboarding")
    if not isinstance(summary, dict):
        return {"enabled": False, "auto_run": False, "paused": False, "devices": []}
    return summary


def ensure_worker(device_id: str) -> dict[str, Any]:
    status = worker_status(device_id)
    if status["running"]:
        return status
    if not PROJECT_PYTHON.is_file():
        raise RuntimeError("MediaFlow 独立 Python 环境尚未安装")
    try:
        started = RuntimeControl().start(worker_spec(device_id))
    except RuntimeError as exc:
        if (
            "OpenRouter Key 解密失败" in str(exc)
            and background_supervisor_is_fresh()
        ):
            return {
                "device_id": device_id,
                "role": worker_role(device_id),
                "running": False,
                "changed": False,
                "identity": "supervisor_pending",
                "detail": "后台守护进程正在接管启动",
            }
        raise
    return {"device_id": device_id, **started}


def stop_worker(store: TaskStore, device_id: str) -> dict[str, Any]:
    if store.running_count([device_id]):
        raise ValueError("该设备仍有任务正在执行，请先请求安全停止并等待任务结束")
    store.request_stop([device_id])
    try:
        stopped = RuntimeControl().stop(worker_role(device_id))
    except Exception:
        store.clear_stop_requests([device_id])
        raise
    return {"device_id": device_id, **stopped}


def restart_worker(store: TaskStore, device_id: str) -> dict[str, Any]:
    if store.running_count([device_id]):
        raise ValueError("该设备仍有任务正在执行，不能重启 Worker")
    RuntimeControl().stop(worker_role(device_id))
    store.clear_stop_requests([device_id])
    return ensure_worker(device_id)


def ensure_workers(device_ids: list[str]) -> list[dict[str, Any]]:
    return [ensure_worker(device_id) for device_id in device_ids]


def adb_device_states() -> dict[str, str]:
    adb = resolve_adb_executable()
    if not adb:
        return {}
    result = subprocess.run(
        [adb, "devices"], capture_output=True, text=True, timeout=6,
        check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=tool_environment(),
    )
    states: dict[str, str] = {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            states[parts[0]] = parts[1]
    return states


def _adb_shell_text(device_id: str, command: list[str], timeout: int = 4) -> str:
    adb = _required_adb()
    completed = subprocess.run(
        [adb, "-s", device_id, "shell", *command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=tool_environment(),
    )
    if completed.returncode:
        raise RuntimeError("ADB runtime signature query failed")
    return completed.stdout.strip()


def adb_runtime_signature(device_id: str) -> dict[str, Any] | None:
    now = time.monotonic()
    with _RUNTIME_SIGNATURE_LOCK:
        cached = _RUNTIME_SIGNATURE_CACHE.get(device_id)
        if cached and now - cached[0] < _RUNTIME_SIGNATURE_TTL_SECONDS:
            return cached[1]
    try:
        size_matches = re.findall(
            r"(\d+)x(\d+)", _adb_shell_text(device_id, ["wm", "size"])
        )
        density_matches = re.findall(
            r"\d+", _adb_shell_text(device_id, ["wm", "density"])
        )
        navigation_raw = _adb_shell_text(
            device_id, ["settings", "get", "secure", "navigation_mode"]
        )
        rotation_raw = _adb_shell_text(
            device_id, ["settings", "get", "system", "user_rotation"]
        )
        package_dump = _adb_shell_text(
            device_id, ["dumpsys", "package", DOUYIN_PACKAGE], timeout=6
        )
        version_match = re.search(r"versionName=([^\s]+)", package_dump)
        if not size_matches or not density_matches or not version_match:
            runtime = None
        else:
            width, height = (int(value) for value in size_matches[-1])
            runtime = {
                "app_version": version_match.group(1),
                "display": {
                    "width": width,
                    "height": height,
                    "density": int(density_matches[-1]),
                    "orientation": int(rotation_raw) if rotation_raw.isdigit() else 0,
                    "navigation_mode": {
                        "0": "three_button",
                        "1": "two_button",
                        "2": "gesture",
                    }.get(navigation_raw, navigation_raw or "unknown"),
                },
            }
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError):
        runtime = None
    with _RUNTIME_SIGNATURE_LOCK:
        _RUNTIME_SIGNATURE_CACHE[device_id] = (now, runtime)
    return runtime


def status_runtime_signature(device_id: str) -> dict[str, Any] | None:
    """Return cached device facts immediately and refresh them off the request path."""
    now = time.monotonic()
    with _RUNTIME_SIGNATURE_LOCK:
        cached = _RUNTIME_SIGNATURE_CACHE.get(device_id)
        if cached and now - cached[0] < _RUNTIME_SIGNATURE_TTL_SECONDS:
            return cached[1]
        fallback = cached[1] if cached else None
        if device_id in _RUNTIME_SIGNATURE_REFRESHING:
            return fallback
        _RUNTIME_SIGNATURE_REFRESHING.add(device_id)

    def refresh() -> None:
        try:
            adb_runtime_signature(device_id)
        finally:
            with _RUNTIME_SIGNATURE_LOCK:
                _RUNTIME_SIGNATURE_REFRESHING.discard(device_id)

    threading.Thread(
        target=refresh,
        daemon=True,
        name=f"status-signature-{_safe_device_name(device_id)[:32]}",
    ).start()
    return fallback


def initialization_runtime_status(
    stored_status: str,
    profile: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
    *,
    is_virtual: bool = False,
) -> str:
    if stored_status != "ready" or runtime is None:
        return stored_status
    if profile_matches_runtime(
        profile,
        app_version=str(runtime.get("app_version") or ""),
        display=dict(runtime.get("display") or {}),
        portable_virtual=is_virtual,
    ):
        return "ready"
    return "stale"


def device_screenshot_png(device_id: str) -> bytes:
    device_id = str(device_id or "").strip()
    if not device_id or adb_device_states().get(device_id) != "device":
        raise KeyError("Device is not online and authorized")
    adb = _required_adb()
    result = subprocess.run(
        [adb, "-s", device_id, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=8,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=tool_environment(),
    )
    payload = bytes(result.stdout or b"")
    if result.returncode or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Device screenshot is unavailable")
    return payload


def device_statuses(_configured_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return only devices that ADB actually reports on this computer."""
    try:
        states = adb_device_states()
        return enrich_device_statuses([
            {"device_id": device_id, "state": state}
            for device_id, state in states.items()
        ])
    except (OSError, subprocess.SubprocessError):
        return []


def _clear_legacy_development_selection(
    store: TaskStore,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Remove the exact historical five-device seed without touching history."""
    selected = tuple(config.get("device_ids") or [])
    if selected != LEGACY_DEVELOPMENT_DEVICE_IDS:
        return config
    try:
        actual = set(adb_device_states())
    except (OSError, subprocess.SubprocessError):
        actual = set()
    bound = {
        str(item.get("adb_endpoint") or "")
        for item in store.list_virtual_devices()
        if item.get("state") != "retired"
    }
    if actual.intersection(selected) or bound.intersection(selected):
        return config
    cleaned = normalized_config({**config, "device_ids": [], "device_id": ""})
    store.save_profile(PROFILE_NAME, cleaned)
    draft = store.get_run_draft()
    if draft is not None and tuple(draft["config"].get("device_ids") or []) == selected:
        store.save_run_draft(
            {**draft["config"], "device_ids": [], "device_id": ""},
            expected_revision=draft["revision"],
        )
    return cleaned


def _is_virtual_adb_id(device_id: str) -> bool:
    return device_id.startswith("127.0.0.1:") or device_id.startswith("localhost:") or device_id.startswith("emulator-")


def build_device_onboarding_payload(
    store: TaskStore,
    config: dict[str, Any],
    *,
    custom_path: str | None = None,
) -> dict[str, Any]:
    devices = device_statuses(config["device_ids"])
    for device in devices:
        device["device_type"] = "virtual" if _is_virtual_adb_id(str(device["device_id"])) else "physical"
        latest = store.latest_initialization(str(device["device_id"]))
        device["initialization"] = public_initialization(latest) if latest else None
    inventory = VirtualDeviceInventory(store)
    inventory_result = inventory.reconcile(
        custom_path, online_adb_ids={str(item["device_id"]) for item in devices if item.get("state") == "device"}
    )
    virtual = {
        "provider": inventory_result["provider"],
        "instances": inventory_result["instances"],
        "recipe": inventory_result["recipe"],
    }
    stored_virtual_devices = [
        item
        for item in inventory_result["devices"]
        if item.get("state") != "retired"
        or item.get("presence_status") == "identity_conflict"
    ]
    for virtual_device in stored_virtual_devices:
        adb_endpoint = str(virtual_device.get("adb_endpoint") or "")
        latest = store.latest_initialization(adb_endpoint) if adb_endpoint else None
        virtual_device["initialization"] = public_initialization(latest) if latest else None
        if latest and latest.status == "ready":
            virtual_device["state"] = "ready"
    preferences = device_preferences(store)
    return {
        "status": "ready",
        "physical_devices": (
            [item for item in devices if item["device_type"] == "physical"]
            if preferences["physical_devices_enabled"]
            else []
        ),
        "device_preferences": preferences,
        "virtual_devices": stored_virtual_devices,
        "mumu": virtual,
        "agent_guide_url": "/devices/guide",
        "agent_document": (
            "MediaFlow 真机初始化：先确认设备名称与ADB序列号映射；检查在线、已授权、解锁、"
            "抖音已安装登录且设备无任务占用；再从设备页启动只读初始化。厂商安装确认、登录或"
            "安全验证由用户在对应手机处理，完成后点击继续。最终须完成主页、搜索、搜索沉浸流、"
            "评论面板和中文输入复验，并通过3条点赞/收藏/评论概率均为0的自检。"
        ),
    }


def _douyin_is_installed(adb_endpoint: str) -> bool:
    adb = _required_adb()
    package = subprocess.run(
        [adb, "-s", adb_endpoint, "shell", "pm", "path", DOUYIN_PACKAGE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=12,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=tool_environment(),
    )
    return package.returncode == 0 and "package:" in package.stdout


def _install_approved_douyin_if_configured(adb_endpoint: str) -> bool:
    approved_apk = str(os.environ.get("MEDIAFLOW_APPROVED_DOUYIN_APK") or os.environ.get("RISKFLOW_APPROVED_DOUYIN_APK") or "").strip()
    approved_url = str(os.environ.get("MEDIAFLOW_APPROVED_DOUYIN_APK_URL") or os.environ.get("RISKFLOW_APPROVED_DOUYIN_APK_URL") or "").strip()
    approved_sha256 = str(os.environ.get("MEDIAFLOW_APPROVED_DOUYIN_APK_SHA256") or os.environ.get("RISKFLOW_APPROVED_DOUYIN_APK_SHA256") or "").strip().lower()
    if approved_apk:
        apk_path = Path(approved_apk).expanduser().resolve(strict=True)
    elif approved_url:
        if urlparse(approved_url).scheme.lower() != "https":
            raise RuntimeError("公司批准的抖音下载地址必须使用HTTPS")
        if not re.fullmatch(r"[0-9a-f]{64}", approved_sha256):
            raise RuntimeError("使用公司批准下载地址时必须配置APK SHA-256")
        download_root = RUNTIME_ROOT / "approved-downloads"
        download_root.mkdir(parents=True, exist_ok=True)
        apk_path = download_root / f"douyin-{approved_sha256[:16]}.apk"
        if not apk_path.exists():
            temporary = apk_path.with_suffix(".part")
            digest = hashlib.sha256()
            total = 0
            try:
                with urllib.request.urlopen(approved_url, timeout=60) as response, temporary.open("wb") as writer:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 500 * 1024 * 1024:
                            raise RuntimeError("公司批准的抖音APK超过500MB限制")
                        digest.update(chunk)
                        writer.write(chunk)
                if digest.hexdigest().lower() != approved_sha256:
                    raise RuntimeError("公司批准的抖音APK校验值不一致")
                os.replace(temporary, apk_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        elif hashlib.sha256(apk_path.read_bytes()).hexdigest().lower() != approved_sha256:
            apk_path.unlink()
            raise RuntimeError("本地缓存的抖音APK校验值不一致，请重新继续")
    else:
        return False
    if not apk_path.is_file() or apk_path.suffix.lower() != ".apk":
        raise RuntimeError("公司批准的抖音APK路径无效")
    adb = _required_adb()
    installed = subprocess.run(
        [adb, "-s", adb_endpoint, "install", "-r", str(apk_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=tool_environment(),
    )
    if installed.returncode or "success" not in installed.stdout.lower():
        detail = installed.stderr.strip() or installed.stdout.strip() or "安装命令失败"
        raise RuntimeError(f"公司批准的抖音APK安装失败：{detail[:300]}")
    return True


def _queue_virtual_initialization(
    store: TaskStore, operation_id: str, created: dict[str, Any]
) -> None:
    adb_endpoint = str(created.get("adb_endpoint") or "")
    if not adb_endpoint:
        raise RuntimeError("虚拟机ADB地址缺失")
    installed = _douyin_is_installed(adb_endpoint)
    if not installed:
        installed = _install_approved_douyin_if_configured(adb_endpoint)
    if not installed or not _douyin_is_installed(adb_endpoint):
        created["state"] = "waiting_app"
        store.save_virtual_device(created)
        store.update_virtual_operation(
            operation_id,
            status="waiting_user",
            stage="waiting_app_install",
            progress=70,
            result=created,
            error="请在虚拟机画面安装公司批准来源的抖音，完成后点击继续",
        )
        return
    record = store.create_initialization(
        adb_endpoint,
        platform_id="douyin",
        options={"preparation_version": "on-demand-v1", "requirements": ["connection", "display", "application"], "write_acceptance": False},
    )
    ensure_worker(adb_endpoint)
    created["state"] = "initializing"
    store.save_virtual_device(created)
    store.update_virtual_operation(
        operation_id,
        status="completed",
        stage="initialization_queued",
        progress=100,
        result={**created, "initialization_id": record.id},
        error=None,
    )


def _run_virtual_device_create(
    store: TaskStore,
    operation_id: str,
    *,
    name: str,
    custom_path: str | None,
) -> None:
    try:
        request = store.get_virtual_operation(operation_id)["request"]
        if request.get("from_template"):
            from local_vm_template import LocalVmTemplate
            created = LocalVmTemplate(store, custom_path).create(
                operation_id, name, int(request.get("display_index") or 0),
                rebuild=request.get("rebuild") is True, prepare_only=request.get("prepare_only") is True,
                private_manifest=request.get("private_manifest"), resume_instance_id=request.get("resume_instance_id"), new_attempt=request.get("new_attempt") is True)
            store.update_virtual_operation(operation_id, status="completed", stage="completed", progress=100,
                                           result=created, message="已预装抖音；请打开模拟器登录，然后选择任务")
            return
        store.update_virtual_operation(operation_id, status="running", stage="creating", progress=10)
        manager = resolve_mumu_manager(custom_path)
        provider = MuMuProvider(manager)
        probe = provider.probe(custom_path)
        if not probe.get("compatible"):
            raise RuntimeError(str(probe.get("message") or "MuMu不可用"))
        created = provider.create_from_recipe(name)
        created.update(
            state="stopped",
            discovery_source="mediaflow_created",
            managed=True,
            display_index=int(
                store.get_virtual_operation(operation_id)["request"]["display_index"]
            ),
            provider_install_id=store.get_virtual_operation(operation_id)["request"][
                "provider_install_id"
            ],
            presence_status="present",
            profile_status="requires_verification",
            standard_status="standard",
            standard_message=None,
        )
        store.save_virtual_device(created)
        store.update_virtual_operation(
            operation_id,
            status="running",
            stage="starting_mumu",
            progress=45,
            result=created,
        )
        created = VirtualDeviceInventory(store).start(
            str(created["virtual_device_id"]),
            operation_id,
            custom_path=custom_path,
        )
        if created.get("state") == "degraded":
            return
        _queue_virtual_initialization(store, operation_id, created)
    except Exception as exc:
        from local_vm_template import TemplateCancelled
        store.update_virtual_operation(
            operation_id,
            status="cancelled" if isinstance(exc, TemplateCancelled) else "failed",
            stage="cancelled" if isinstance(exc, TemplateCancelled) else "failed",
            progress=100,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_virtual_device_adopt(
    store: TaskStore,
    operation_id: str,
    *,
    provider_instance_id: str,
    custom_path: str | None,
) -> None:
    try:
        operation = store.get_virtual_operation(operation_id)
        request = operation["request"]
        store.update_virtual_operation(
            operation_id, status="running", stage="renaming", progress=35
        )
        adopted = VirtualDeviceInventory(store).adopt(
            provider_instance_id,
            name=str(request["name"]),
            display_index=int(request["display_index"]),
            custom_path=custom_path,
        )
        store.update_virtual_operation(
            operation_id,
            status="completed",
            stage="adopted_requires_verification",
            progress=100,
            result=adopted,
            error=None,
        )
    except Exception as exc:
        store.update_virtual_operation(
            operation_id,
            status="failed",
            stage="failed",
            progress=100,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_virtual_device_continue(store: TaskStore, operation_id: str) -> None:
    try:
        operation = store.get_virtual_operation(operation_id)
        if operation["status"] != "waiting_user" or operation["stage"] != "waiting_app_install":
            raise RuntimeError("当前虚拟机不在等待安装抖音阶段")
        created = dict(operation.get("result") or {})
        store.update_virtual_operation(
            operation_id,
            status="running",
            stage="checking_app_install",
            progress=75,
            result=created,
            error=None,
        )
        _queue_virtual_initialization(store, operation_id, created)
    except Exception as exc:
        store.update_virtual_operation(
            operation_id,
            status="failed",
            stage="failed",
            progress=100,
            error=f"{type(exc).__name__}: {exc}",
        )


def submit_vm_command(store, virtual_device_id, body):
    from virtual_commands import submit_virtual_command
    target = _run_virtual_device_lifecycle if body.get('action') in {'start', 'stop', 'restart'} else _run_virtual_device_extended_operation
    return submit_virtual_command(store, virtual_device_id, body, executor=target)


def _run_virtual_device_lifecycle(
    store: TaskStore,
    operation_id: str,
    *,
    virtual_device_id: str,
    action: str,
    custom_path: str | None,
) -> None:
    inventory = VirtualDeviceInventory(store)
    try:
        if action in {"stop", "restart"}:
            inventory.stop(
                virtual_device_id,
                operation_id,
                custom_path=custom_path,
                finalize_operation=action == "stop",
            )
            if action == "stop":
                return
            store.update_virtual_operation(
                operation_id,
                status="running",
                stage="restarting",
                progress=20,
            )
        connected = inventory.start(
            virtual_device_id, operation_id, custom_path=custom_path
        )
        if connected.get("state") == "degraded":
            return
        endpoint = str(connected.get("adb_endpoint") or "")
        latest = store.latest_initialization(endpoint) if endpoint else None
        effective_status = None
        if latest and latest.status == "ready":
            effective_status = initialization_runtime_status(
                latest.status,
                latest_device_platform_profile(endpoint),
                adb_runtime_signature(endpoint),
            )
        if effective_status == "ready":
            ready = store.save_virtual_device(
                {
                    **connected,
                    "state": "ready",
                    "profile_status": "ready",
                    "last_error": None,
                }
            )
            store.update_virtual_operation(
                operation_id,
                status="completed",
                stage="ready",
                progress=100,
                result=ready,
                error=None,
            )
            return
        connected["profile_status"] = "requires_verification"
        store.save_virtual_device(connected)
        _queue_virtual_initialization(store, operation_id, connected)
    except Exception as exc:
        try:
            online_ids = {
                device_id
                for device_id, state in adb_device_states().items()
                if state == "device"
            }
            inventory_result = inventory.reconcile(
                custom_path, online_adb_ids=online_ids
            )
            virtual_device = next(
                (
                    item
                    for item in inventory_result["devices"]
                    if item["virtual_device_id"] == virtual_device_id
                ),
                store.get_virtual_device(virtual_device_id),
            )
            store.save_virtual_device(
                {
                    **virtual_device,
                    "state": (
                        "running"
                        if virtual_device.get("state") in {"running", "starting", "adb_ready"}
                        else "failed"
                    ),
                    "last_error": f"{type(exc).__name__}: {exc}",
                }
            )
        except KeyError:
            pass
        store.update_virtual_operation(
            operation_id,
            status="failed",
            stage="failed",
            progress=100,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_virtual_device_extended_operation(
    store: TaskStore,
    operation_id: str,
    *,
    action: str,
    virtual_device_id: str | None = None,
    custom_path: str | None = None,
) -> None:
    inventory = VirtualDeviceInventory(store)
    try:
        operation = store.get_virtual_operation(operation_id)
        request = operation.get("request") or {}
        if action == "settings":
            result = inventory.apply_settings(
                str(virtual_device_id), operation_id, dict(request.get("settings") or {}),
                custom_path=custom_path,
            )
        elif action == "repair_standard":
            result = inventory.repair_standard(
                str(virtual_device_id), operation_id, custom_path=custom_path
            )
        elif action == "clone":
            result = inventory.clone(
                str(virtual_device_id), operation_id,
                name=str(request["name"]),
                display_index=int(request["display_index"]),
                custom_path=custom_path,
            )
        elif action == "backup":
            result = inventory.backup(
                str(virtual_device_id), operation_id, custom_path=custom_path
            )
        elif action == "restore":
            result = inventory.restore(
                str(request["backup_id"]), operation_id,
                name=str(request["name"]),
                display_index=int(request["display_index"]),
                custom_path=custom_path,
            )
        elif action == "delete":
            backup = None
            if bool(request.get("backup", True)):
                backup = inventory.backup(
                    str(virtual_device_id), operation_id, custom_path=custom_path
                )
                store.update_virtual_operation(
                    operation_id,
                    status="running",
                    stage="backup_verified",
                    progress=55,
                    result={"backup": backup},
                )
            retired = inventory.delete(
                str(virtual_device_id), operation_id,
                confirmation_name=str(request.get("confirmation_name") or ""),
                custom_path=custom_path,
            )
            result = {"device": retired, "backup": backup}
        else:
            raise ValueError("虚拟机扩展操作无效")
        store.update_virtual_operation(
            operation_id,
            status="completed",
            stage="completed",
            progress=100,
            result=result,
            error=None,
        )
    except Exception as exc:
        store.update_virtual_operation(
            operation_id,
            status="failed",
            stage="failed",
            progress=100,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_virtual_device_pool(
    store: TaskStore,
    operation_id: str,
    *,
    custom_path: str | None = None,
) -> None:
    """Apply a confirmed pool plan without replaying ambiguous destructive steps."""
    inventory = VirtualDeviceInventory(store)
    try:
        operation = store.get_virtual_operation(operation_id)
        request = operation.get("request") or {}
        mode = str(request.get("mode") or "")
        target_count = int(request.get("target_count") or 0)
        store.update_virtual_operation(
            operation_id, status="running", stage="planning_pool", progress=5
        )
        plan = inventory.pool_plan(mode, target_count, custom_path=custom_path)
        other_operations = [
            item
            for item in store.list_active_virtual_operations()
            if item["id"] != operation_id
        ]
        if other_operations:
            raise ValueError("还有虚拟机生命周期操作未结束，暂时不能配置标准池")
        if mode == "reset":
            if store.list_active_tasks():
                raise ValueError("还有运行或排队任务，不能重建标准池")
            if store.device_view_session_summary().get("active_control", 0):
                raise ValueError("还有人工控制会话，不能重建标准池")
            if str(request.get("confirmation") or "") != plan["confirmation_phrase"]:
                raise ValueError("重建确认文字不匹配")
            manager = resolve_mumu_manager(custom_path)
            if manager is None:
                raise ValueError("MuMu管理命令不可用")
            provider = MuMuProvider(manager)
            deletion_items = list(plan["deletion_items"])
            managed_by_instance = {
                str(item["provider_instance_id"]): item
                for item in store.list_managed_virtual_devices()
            }
            for index, item in enumerate(deletion_items, start=1):
                instance_id = str(item["provider_instance_id"])
                store.update_virtual_operation(
                    operation_id,
                    status="running",
                    stage="deleting_pool",
                    progress=5 + int(40 * index / max(1, len(deletion_items))),
                    result={"plan": plan, "deleted": index - 1},
                )
                if item.get("state") != "stopped":
                    provider.stop(instance_id)
                    refreshed = None
                    stop_deadline = time.monotonic() + 45
                    while time.monotonic() < stop_deadline:
                        refreshed = next(
                            (
                                candidate
                                for candidate in provider.list_instances()
                                if str(candidate.get("provider_instance_id")) == instance_id
                            ),
                            None,
                        )
                        if refreshed is not None and str(refreshed.get("state") or "") == "stopped":
                            break
                        time.sleep(1)
                    if refreshed is None or str(refreshed.get("state") or "") != "stopped":
                        raise RuntimeError(
                            f"{item['name']}停止结果无法确认；没有继续删除或创建"
                        )
                provider.delete(instance_id)
                managed = managed_by_instance.get(instance_id)
                if managed:
                    store.save_virtual_device(
                        {
                            **managed,
                            "state": "retired",
                            "presence_status": "retired",
                            "adb_endpoint": None,
                            "last_error": None,
                        }
                    )
            store.update_virtual_operation(
                operation_id,
                status="running",
                stage="verifying_pool_empty",
                progress=48,
                result={"plan": plan, "deleted": len(deletion_items)},
            )
            remaining = provider.list_instances()
            if remaining:
                raise RuntimeError("MuMu仍返回未删除实例；没有开始创建标准虚拟机")

        create_count = int(plan["create_count"])
        child_operations: list[dict[str, Any]] = []
        manager = resolve_mumu_manager(custom_path)
        if manager is None:
            raise ValueError("MuMu管理命令不可用")
        install_id = manager_identity(manager)
        for index in range(create_count):
            progress = 50 + int(45 * index / max(1, create_count))
            store.update_virtual_operation(
                operation_id,
                status="running",
                stage="creating_pool",
                progress=progress,
                result={"plan": plan, "children": child_operations},
            )
            child, created = store.create_numbered_virtual_operation(
                "template_create",
                {
                    "provider": "mumu",
                    "provider_install_id": install_id,
                    "mumu_path": custom_path,
                    "pool_operation_id": operation_id,
                    "from_template": True,
                    "virtual_device_id": "local-template-creation",
                },
                idempotency_key=f"pool:{operation_id}:create:{index + 1}",
            )
            if created:
                _run_virtual_device_create(
                    store,
                    child["id"],
                    name=str(child["request"]["name"]),
                    custom_path=custom_path,
                )
                child = store.get_virtual_operation(child["id"])
            child_operations.append(child)
            if child["status"] == "failed":
                raise RuntimeError(
                    f"{child['request'].get('name', '虚拟机')}创建失败：{child.get('error') or '未知错误'}"
                )
        store.update_virtual_operation(
            operation_id,
            status="completed",
            stage="pool_waiting_onboarding" if child_operations else "completed",
            progress=100,
            result={"plan": plan, "children": child_operations},
            error=None,
            message=(
                "标准虚拟机已创建；请按卡片提示完成登录、复验和零写入自检"
                if child_operations
                else "标准池数量已满足，没有创建或删除虚拟机"
            ),
        )
    except Exception as exc:
        store.update_virtual_operation(
            operation_id,
            status="failed",
            stage="failed",
            progress=100,
            error=f"{type(exc).__name__}: {exc}",
            message=str(exc),
            retryable=False,
        )


def _run_unmanaged_virtual_operation(
    store: TaskStore,
    operation_id: str,
    *,
    provider_instance_id: str,
    action: str,
    custom_path: str | None = None,
) -> None:
    try:
        manager = resolve_mumu_manager(custom_path)
        if manager is None:
            raise ValueError("MuMu管理命令不可用")
        provider = MuMuProvider(manager)
        candidates = {
            str(item.get("provider_instance_id")): item
            for item in VirtualDeviceInventory(store).unmanaged_candidates(custom_path)["instances"]
        }
        candidate = candidates.get(str(provider_instance_id))
        if candidate is None:
            raise ValueError("该实例已被接管或不再存在")
        store.update_virtual_operation(
            operation_id, status="running", stage=f"unmanaged_{action}", progress=30
        )
        if action == "start":
            provider.launch(provider_instance_id)
        elif action == "stop":
            provider.stop(provider_instance_id)
        elif action == "delete":
            expected = str((store.get_virtual_operation(operation_id).get("request") or {}).get("confirmation_name") or "")
            candidate_name = str(
                candidate.get("name")
                or candidate.get("player_name")
                or f"MuMu 虚拟机 {provider_instance_id}"
            ).strip()
            if expected != candidate_name:
                raise ValueError("请输入完整虚拟机名称以确认删除")
            if str(candidate.get("state") or "") != "stopped":
                raise ValueError("删除前必须先停止虚拟机")
            provider.delete(provider_instance_id)
        else:
            raise ValueError("未托管虚拟机操作无效")
        store.update_virtual_operation(
            operation_id,
            status="completed",
            stage="completed",
            progress=100,
            result={"provider_instance_id": provider_instance_id, "action": action},
            error=None,
        )
    except Exception as exc:
        store.update_virtual_operation(
            operation_id,
            status="failed",
            stage="failed",
            progress=100,
            error=f"{type(exc).__name__}: {exc}",
            message=str(exc),
        )
def active_tasks_for_display(
    tasks: list[Any], configured_device_ids: list[str]
) -> list[Any]:
    """Return at most one live card per device, preferring running work."""
    selected: dict[str, Any] = {}
    for task in tasks:
        if task.status not in {"running", "pending", "waiting_model", "waiting_device", "waiting_user"}:
            continue
        current = selected.get(task.device_id)
        candidate_key = (
            0 if task.status == "running" else 2 if task.status == "pending" else 1,
            task.started_at or task.not_before,
            task.created_at,
            task.id,
        )
        if current is None:
            selected[task.device_id] = task
            continue
        current_key = (
            0 if current.status == "running" else 2 if current.status == "pending" else 1,
            current.started_at or current.not_before,
            current.created_at,
            current.id,
        )
        if candidate_key < current_key:
            selected[task.device_id] = task

    ordered_device_ids = list(dict.fromkeys(configured_device_ids))
    ordered_device_ids.extend(
        sorted(device_id for device_id in selected if device_id not in ordered_device_ids)
    )
    return [selected[device_id] for device_id in ordered_device_ids if device_id in selected]


def _live_task_progress(task: Any) -> dict[str, Any]:
    """Build read-only progress from append-only events without changing task state."""
    progress: dict[str, Any] = {
        "videos_seen": 0,
        "feed_items_seen": 0,
        "likes": 0,
        "favorites": 0,
        "comments_sent": 0,
        "successful_recoveries": 0,
        "page_drifts": 0,
        "non_video_feed_items": 0,
        "feed_phase_reentries": 0,
    }
    if not task.run_dir:
        return progress
    try:
        run_dir = Path(task.run_dir).resolve()
        if DEFAULT_ARTIFACTS.resolve() not in run_dir.parents:
            return progress
        events_path = run_dir / "events.jsonl"
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, RuntimeError):
        return progress
    liked: set[int] = set()
    favorited: set[int] = set()
    commented: set[int] = set()
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        if name == "page_observation":
            labels = {"waiting_page": "等待页面加载（最多8秒）", "visual_recognition": "视觉识别（最多20秒）",
                      "visual_reading": "视觉复核互动列表", "action_verification": "导航动作后复核",
                      "fixed_recognition": "固定规则识别", "waiting_model": "等待配置视觉模型"}
            phase = str(event.get("phase") or "")
            if phase in labels:
                progress["navigation_phase"] = phase
                progress["navigation_message"] = labels[phase]
        elif name in {"valid_video_processed", "feed_phase_started", "engagement_inspection_finished"}:
            progress.pop("navigation_phase", None)
            progress.pop("navigation_message", None)
        if name == "feed_phase_started":
            phase = str(event.get("phase") or "")
            if phase in {"search", "home"}:
                progress["current_feed_phase"] = phase
                progress["phase_target"] = int(event.get("target") or 0)
                progress["phase_processed"] = int(event.get("processed") or 0)
        elif name == "valid_video_processed":
            progress["videos_seen"] = max(
                int(progress["videos_seen"]), int(event.get("videos_seen") or 0)
            )
            progress["feed_items_seen"] = max(
                int(progress["feed_items_seen"]), int(event.get("video") or 0)
            )
            phase = str(event.get("feed_phase") or "")
            if phase in {"search", "home"}:
                progress["current_feed_phase"] = phase
            progress["phase_target"] = int(event.get("phase_target") or 0)
            progress["phase_processed"] = int(event.get("phase_processed") or 0)
        elif name == "non_video_feed_item":
            progress["non_video_feed_items"] = (
                int(progress["non_video_feed_items"]) + 1
            )
            progress["feed_items_seen"] = max(
                int(progress["feed_items_seen"]), int(event.get("video") or 0)
            )
            phase = str(event.get("feed_phase") or "")
            if phase in {"search", "home"}:
                progress["current_feed_phase"] = phase
        elif name == "feed_phase_reentered":
            progress["feed_phase_reentries"] = (
                int(progress["feed_phase_reentries"]) + 1
            )
            phase = str(event.get("feed_phase") or "")
            if phase in {"search", "home"}:
                progress["current_feed_phase"] = phase
        elif name in {"like_state_after", "favorite_state_after"} and event.get("active") is True:
            try:
                index = int(event.get("video"))
            except (TypeError, ValueError):
                continue
            (liked if name == "like_state_after" else favorited).add(index)
        elif name == "comment_send_verification" and event.get("verified") is True:
            try:
                commented.add(int(event.get("video")))
            except (TypeError, ValueError):
                pass
        elif name == "video_incident":
            if event.get("outcome") == "recovered":
                progress["successful_recoveries"] = int(progress["successful_recoveries"]) + 1
            if event.get("error_type") == "FeedContextDriftError":
                progress["page_drifts"] = int(progress["page_drifts"]) + 1
    progress["likes"] = len(liked)
    progress["favorites"] = len(favorited)
    progress["comments_sent"] = len(commented)
    return progress


def _active_task_payload(task: Any) -> dict[str, Any]:
    payload = asdict(task)
    if task.status == "running" and not task.result:
        payload["result"] = _live_task_progress(task)
    return payload


def _compact_task_payload(task: Any, *, include_live_result: bool = False) -> dict[str, Any]:
    payload = task.payload or {}
    result = _live_task_progress(task) if include_live_result and task.status == "running" and not task.result else task.result
    return {
        "id": task.id,
        "task_type": task.task_type,
        "device_id": task.device_id,
        "status": task.status,
        "created_at": task.created_at,
        "not_before": task.not_before,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "error": task.error,
        "payload": {
            key: payload.get(key)
            for key in ("topic_prompt", "video_count", "round_index", "round_count", "submission_id")
            if key in payload
        },
        "result": result,
    }


def _compact_initialization_payload(record: InitializationRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "status": record.status,
        "stage": record.stage,
        "progress_current": record.progress_current,
        "progress_total": record.progress_total,
        "message": record.message,
        "updated_at": record.updated_at,
        "error": record.error,
    }


def _compact_group_payload(group: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in group.items()
        if key not in {"tasks", "inspections", "results", "action_routing"}
    }


def _readiness_step(
    step_id: str, label: str, status: str, message: str
) -> dict[str, str]:
    return {
        "id": step_id,
        "label": label,
        "status": status,
        "message": message,
    }


def _virtual_device_guidance(
    virtual_device: dict[str, Any],
    *,
    connected: bool,
    active_operation: dict[str, Any] | None,
    initialization: InitializationRecord | None,
    model_status: dict[str, Any],
) -> dict[str, Any]:
    """One backend-owned explanation for every non-ready virtual-device state."""
    presence = str(virtual_device.get("presence_status") or "present")
    state = str(virtual_device.get("state") or "unknown")
    profile_ready = str(virtual_device.get("profile_status") or "") == "ready"
    operation_stage = str((active_operation or {}).get("stage") or "")
    initialization_status = str(initialization.status if initialization else "")
    initialization_message = str(initialization.message if initialization else "")
    model_ready = bool(model_status.get("model_ready"))
    waiting_app = state == "waiting_app" or operation_stage == "waiting_app_install"
    waiting_login = initialization_status == "waiting_user" and any(
        marker in initialization_message for marker in ("登录", "验证", "安全确认")
    )
    initialization_running = initialization_status == "running"
    standard = str(virtual_device.get("standard_status") or "requires_verification")
    capabilities = dict(virtual_device.get("capabilities") or {})

    def capability_ready(name: str) -> bool:
        return str((capabilities.get(name) or {}).get("status") or "") == "ready"

    def capability_reason(name: str) -> str:
        return str((capabilities.get(name) or {}).get("reason") or "尚未复验")

    if presence == "identity_conflict":
        reason_code = "android_identity_changed"
        issue_status = "needs_confirmation"
        blocking_scope = "identity"
        user_message = "设备身份与原记录不一致，已停止继承旧档案"
        suggested_action = "确认虚拟机身份后重新初始化"
        available_actions = ["review_identity"]
    elif presence in {"missing", "engine_unavailable"}:
        reason_code = "mumu_instance_unavailable"
        issue_status = "program_error" if presence == "engine_unavailable" else "needs_confirmation"
        blocking_scope = "connection"
        user_message = "MediaFlow暂时无法找到这台MuMu虚拟机"
        suggested_action = "检查MuMu安装后重新检索设备"
        available_actions = ["reconcile"]
    elif standard != "standard":
        reason_code = "virtual_device_nonstandard"
        issue_status = "partially_available"
        blocking_scope = "configuration"
        user_message = str(
            virtual_device.get("standard_message")
            or "虚拟机配置不符合MediaFlow标准"
        )
        suggested_action = "停止虚拟机后恢复900×1600、320 DPI"
        available_actions = (
            ["stop", "repair_standard"]
            if state != "stopped"
            else ["repair_standard"]
        )
    elif state == "stopped":
        reason_code = "virtual_device_stopped"
        issue_status = "waiting_user"
        blocking_scope = "connection"
        user_message = "虚拟机当前已停止"
        suggested_action = "点击启动，MediaFlow会自动连接ADB"
        available_actions = ["start"]
    elif not connected:
        reason_code = "adb_unavailable"
        issue_status = "retryable"
        blocking_scope = "connection"
        user_message = "MuMu已经运行，但ADB尚未连接到MediaFlow"
        suggested_action = "点击重试连接；不会重复启动虚拟机"
        available_actions = ["retry_connection", "reconcile"]
    elif waiting_app:
        reason_code = "douyin_not_installed"
        issue_status = "waiting_user"
        blocking_scope = "onboarding"
        user_message = "设备已经连接，正在等待安装抖音"
        suggested_action = "打开画面安装公司批准来源的抖音，完成后继续检查"
        available_actions = ["open_screen", "manual_control", "continue_onboarding"]
    elif waiting_login:
        reason_code = "douyin_login_required"
        issue_status = "waiting_user"
        blocking_scope = "onboarding"
        user_message = "抖音已经安装，账号需要你登录或完成验证"
        suggested_action = "打开画面处理登录，退出人工接管后继续复验"
        available_actions = ["open_screen", "manual_control", "continue_initialization"]
    elif initialization_status in {"queued", "running", "waiting_user"}:
        from task_preparation import preparation_presentation
        presentation = preparation_presentation(initialization_status, initialization_message)
        reason_code = "initialization_" + initialization_status
        issue_status = "normal"
        blocking_scope = "onboarding"
        user_message = presentation["message"]
        suggested_action = "可安全取消设备准备；执行者停止后即可人工接管" if initialization_running else "可以先人工操作；选择任务时自动准备所需能力"
        available_actions = presentation["actions"]
    elif not capability_ready("browse_home") and not virtual_device.get("task_eligibility", {}).get("browse"):
        reason_code = "profile_verification_required"
        issue_status = "partially_available"
        blocking_scope = "onboarding"
        user_message = "ADB和看屏可用，首页浏览还需要一次只读复验"
        suggested_action = capability_reason("browse_home")
        available_actions = ["open_screen", "continue_initialization"]
    elif not all(
        capability_ready(name)
        for name in ("search_input", "engagement_v3", "topic_analysis", "like_favorite", "comment_preview", "comment_send")
    ):
        reason_code = "optional_capabilities_pending"
        issue_status = "normal"
        blocking_scope = "capability"
        missing_count = sum(
            not capability_ready(name)
            for name in ("search_input", "engagement_v3", "topic_analysis", "like_favorite", "comment_preview", "comment_send")
        )
        user_message = "设备可选任务；首次使用时自动准备所需能力"
        suggested_action = "请在模拟器中自行登录；遇到实际登录页时才暂停相关任务"
        available_actions = ["open_screen", "manual_control", "add_to_draft", "continue_initialization"]
        if not model_ready:
            available_actions.append("configure_model")
    else:
        reason_code = "ready"
        issue_status = "normal"
        blocking_scope = "none"
        user_message = "设备已完成连接和复验"
        suggested_action = "可以打开画面或加入任务"
        available_actions = ["open_screen", "manual_control", "add_to_draft"]

    app_ready = connected and not waiting_app
    readiness_steps = [
        _readiness_step("mumu_engine", "MuMu引擎", "ready" if presence == "present" else "blocked", "已找到MuMu管理能力" if presence == "present" else "MuMu引擎或实例不可用"),
        _readiness_step("instance", "虚拟机实例", "ready" if state != "stopped" and presence == "present" else "waiting", "实例已启动" if state != "stopped" else "等待启动"),
        _readiness_step("android", "Android系统", "ready" if connected else "waiting", "Android已响应" if connected else "等待Android和ADB响应"),
        _readiness_step("adb", "ADB连接", "ready" if connected else "blocked", "ADB已连接" if connected else "ADB尚未连接"),
        _readiness_step("identity", "设备身份", "ready" if connected and virtual_device.get("android_identity") else "waiting", "Android身份已核对" if connected and virtual_device.get("android_identity") else "等待核对Android身份"),
        _readiness_step("standard", "显示环境", "ready" if standard == "standard" else "blocked", "900×1600、320 DPI已确认" if standard == "standard" else str(virtual_device.get("standard_message") or "显示环境不符合MediaFlow标准")),
        _readiness_step("douyin", "抖音安装", "ready" if app_ready else "waiting", "抖音安装检查已通过" if app_ready else "等待安装抖音"),
        _readiness_step("login", "抖音登录", "optional", "用户自行登录；仅在任务遇到登录或验证页时提示处理"),
        _readiness_step("browse", "首页浏览", "ready" if capability_ready("browse_home") else "pending", "首页只读流程已验证" if capability_ready("browse_home") else capability_reason("browse_home")),
        _readiness_step("input", "搜索与中文输入", "ready" if capability_ready("search_input") else "optional", "搜索和中文输入已验证" if capability_ready("search_input") else capability_reason("search_input")),
        _readiness_step("engagement", "互动消息巡检", "ready" if capability_ready("engagement_v3") else "optional", "v3共享规则已快速复验" if capability_ready("engagement_v3") else capability_reason("engagement_v3")),
        _readiness_step("model", "视觉模型", "ready" if model_ready else "optional", "当前模型已鉴权并测试" if model_ready else "未配置或尚未测试；不影响ADB、看屏、首页浏览和互动巡检"),
        _readiness_step("writes", "写入能力", "ready" if capability_ready("like_favorite") and capability_ready("comment_send") else "optional", "点赞、收藏和评论发送已验证" if capability_ready("like_favorite") and capability_ready("comment_send") else "只在需要真实互动时补齐，不影响只读任务"),
        _readiness_step("task", "加入任务", "ready" if connected and standard == "standard" else "blocked", "显示环境达标即可选择任务，运行时准备所需能力" if connected and standard == "standard" else "请连接设备并修复显示环境"),
    ]
    diagnostic_seed = ":".join(
        (
            str(virtual_device.get("virtual_device_id") or "unknown"),
            reason_code,
            str((active_operation or {}).get("id") or "none"),
            str(virtual_device.get("last_error") or ""),
        )
    )
    return {
        "onboarding_status": operation_stage or initialization_status or ("ready" if profile_ready else "requires_verification"),
        "issue_status": issue_status,
        "blocking_scope": blocking_scope,
        "reason_code": reason_code,
        "user_message": user_message,
        "suggested_action": suggested_action,
        "available_actions": available_actions,
        "retryable": issue_status == "retryable",
        "diagnostic_id": hashlib.sha256(diagnostic_seed.encode("utf-8")).hexdigest()[:12],
        "readiness_steps": readiness_steps,
    }


def build_status_payload(store: TaskStore, config: dict[str, Any]) -> dict[str, Any]:
    store.reconcile_orphaned_initializations(worker_id_is_running)
    config = _clear_legacy_development_selection(store, config)
    reconciled_tasks = store.reconcile_orphaned_running(worker_id_is_running)
    managed_virtual_devices = [
        item
        for item in store.list_managed_virtual_devices()
        if item.get("state") != "retired"
        or item.get("presence_status") == "identity_conflict"
    ]
    managed_virtual_adb_ids = {
        str(item.get("adb_endpoint") or "")
        for item in managed_virtual_devices
        if item.get("adb_endpoint")
    }
    preferences = device_preferences(store)
    discovered_devices = device_statuses()
    devices = [
        item
        for item in discovered_devices
        if (
            str(item.get("device_id") or "") in managed_virtual_adb_ids
            or (
                preferences["physical_devices_enabled"]
                and not _is_virtual_adb_id(str(item.get("device_id") or ""))
            )
        )
    ]
    for device in devices:
        device["device_type"] = (
            "virtual"
            if str(device.get("device_id") or "") in managed_virtual_adb_ids
            else "physical"
        )
    virtual_adb_ids = {
        str(item.get("adb_endpoint") or "")
        for item in managed_virtual_devices
        if item.get("state") != "retired" and item.get("adb_endpoint")
    }
    runtime_signatures: dict[str, dict[str, Any] | None] = {}
    for device in devices:
        device_id = str(device["device_id"])
        latest = store.latest_initialization(device_id)
        effective_status = latest.status if latest is not None else None
        local_platform_profile = latest_device_platform_profile(device_id)
        runtime_signature = (
            status_runtime_signature(device_id) if device.get("state") == "device" else None
        )
        runtime_signatures[device_id] = runtime_signature
        device["profile_resolution"] = resolve_execution_profile(
            device_id=device_id,
            is_virtual=device_id in virtual_adb_ids or _is_virtual_adb_id(device_id),
            runtime=runtime_signature,
            local_profile=local_platform_profile,
        )
        if latest is not None and latest.status == "ready" and device.get("state") == "device":
            effective_status = initialization_runtime_status(
                latest.status,
                local_platform_profile,
                runtime_signature,
                is_virtual=device_id in virtual_adb_ids or _is_virtual_adb_id(device_id),
            )
        initialization_payload = _compact_initialization_payload(latest)
        if initialization_payload is not None and effective_status == "stale":
            initialization_payload.update(
                status="stale",
                message="显示参数、抖音版本或适配器版本已变化，需要快速复验",
            )
        device["initialization"] = initialization_payload
        device["initialization_status"] = (
            effective_status
            if latest is not None
            else "legacy"
            if device.get("profile_verified")
            else "uninitialized"
        )
    workers = [worker_status(str(device["device_id"])) for device in devices]
    incident_summary = {
        "total": 0,
        "queued": 0,
        "recovered": 0,
        "skipped": 0,
        "device_fatal": 0,
        **store.incident_statistics(),
    }
    recent_tasks = store.list(100)
    active_tasks = store.list_active_tasks()
    task_groups = group_tasks_for_display(recent_tasks)[:5]
    _enrich_group_progress(store, task_groups)
    initialization_summary = store.initialization_summary()
    runtime_stale_count = sum(
        1
        for device in devices
        if device.get("initialization_status") == "stale"
        and (device.get("initialization") or {}).get("id")
    )
    if runtime_stale_count:
        initialization_summary["ready"] = max(
            0, initialization_summary.get("ready", 0) - runtime_stale_count
        )
        initialization_summary["stale"] = (
            initialization_summary.get("stale", 0) + runtime_stale_count
        )
    virtual_devices = managed_virtual_devices
    online_device_ids = {
        str(item["device_id"])
        for item in discovered_devices
        if item.get("state") == "device"
    }
    discovered_by_id = {
        str(item.get("device_id") or ""): item
        for item in discovered_devices
        if item.get("device_id")
    }
    model_status = openrouter_key_status()
    device_profile_payloads = load_device_profile_payloads()
    for virtual_device in virtual_devices:
        active_operation = store.active_virtual_operation(
            virtual_device["virtual_device_id"]
        )
        adb_endpoint = str(virtual_device.get("adb_endpoint") or "")
        latest = store.latest_initialization(adb_endpoint) if adb_endpoint else None
        if (latest and latest.status == "ready" and adb_endpoint in online_device_ids
                and latest.options.get("preparation_version") != "on-demand-v1"):
            virtual_device["state"] = "ready"
            virtual_device["profile_status"] = "ready"
        runtime_display = dict((runtime_signatures.get(adb_endpoint) or {}).get("display") or {})
        qualification_source = dict(virtual_device)
        if runtime_display:
            snapshot = dict(qualification_source.get("provider_snapshot") or {})
            settings = dict(snapshot.get("settings") or {})
            settings.update(
                {
                    "resolution_width.custom": runtime_display.get("width"),
                    "resolution_height.custom": runtime_display.get("height"),
                    "resolution_dpi.custom": runtime_display.get("density"),
                }
            )
            qualification_source["provider_snapshot"] = {**snapshot, "settings": settings}
        qualification = qualify_virtual_device(
            qualification_source,
            connected=adb_endpoint in online_device_ids,
            initialization_status=str(latest.status if latest else ""),
            model_ready=bool(model_status.get("model_ready")),
            verified_capabilities=(
                device_profile_payloads.get(adb_endpoint, {}).get("capabilities", {})
                if adb_endpoint
                else {}
            ),
        )
        virtual_device.update(qualification)
        virtual_device["standard_status"] = (
            "standard"
            if qualification["environment_status"] == "standard"
            else "nonstandard"
            if qualification["environment_status"] == "needs_display_fix"
            else "requires_verification"
        )
        virtual_device["standard_message"] = qualification["message"]
        virtual_device["task_ready"] = bool(
            qualification["task_eligibility"]["browse"]
        )
        virtual_device["management_status"] = (
            "identity_conflict"
            if virtual_device.get("presence_status") == "identity_conflict"
            else "managed_standard"
            if virtual_device.get("standard_status") == "standard"
            else "managed_nonstandard"
        )
        virtual_device["active_operation"] = active_operation
        virtual_device["connection_status"] = (
            "connected"
            if adb_endpoint in online_device_ids
            else "stopped"
            if virtual_device.get("state") == "stopped"
            else "operation_active"
            if active_operation
            else "adb_unavailable"
            if virtual_device.get("state") in {"running", "starting", "adb_ready"}
            else str(virtual_device.get("state") or "unavailable")
        )
        connected_device = discovered_by_id.get(adb_endpoint)
        if connected_device is not None and connected_device.get("state") == "device":
            connected_device.update(
                environment_status=virtual_device.get("environment_status"),
                environment_mismatches=virtual_device.get("environment_mismatches", []),
                capabilities=virtual_device.get("capabilities", {}),
                task_eligibility=virtual_device.get("task_eligibility", {}),
                profile_bundle_id=virtual_device.get("profile_bundle_id"),
                ui_compatibility_id=virtual_device.get("ui_compatibility_id"),
            )
            connected_payload = dict(connected_device)
            connected_payload.update(
                device_type="virtual",
                friendly_name=virtual_device.get("name") or adb_endpoint,
                environment_status=virtual_device.get("environment_status"),
                environment_mismatches=virtual_device.get("environment_mismatches", []),
                capabilities=virtual_device.get("capabilities", {}),
                task_eligibility=virtual_device.get("task_eligibility", {}),
                profile_bundle_id=virtual_device.get("profile_bundle_id"),
                ui_compatibility_id=virtual_device.get("ui_compatibility_id"),
                initialization=_compact_initialization_payload(latest),
                initialization_status=(
                    latest.status
                    if latest is not None
                    else "legacy"
                    if connected_device.get("profile_verified")
                    else "uninitialized"
                ),
            )
            virtual_device["connected_device"] = connected_payload
        else:
            virtual_device["connected_device"] = None
        virtual_device.update(
            _virtual_device_guidance(
                virtual_device,
                connected=adb_endpoint in online_device_ids,
                active_operation=active_operation,
                initialization=latest,
                model_status=model_status,
            )
        )
        from task_preparation import inspection_suspension_key
        if (store.get_profile(inspection_suspension_key(adb_endpoint, "home_badge")) or {}).get("suspended"):
            virtual_device.update(reason_code="home_badge_suspended", issue_status="partially_available",
                                  blocking_scope="home_badge", user_message="消息提醒检查已暂停；首页安全时视频任务不受影响",
                                  suggested_action="查看现场后，重新检查消息提醒")
            virtual_device["available_actions"] = list(dict.fromkeys(virtual_device["available_actions"] + ["recheck_home_badge"]))
        if (store.get_profile(inspection_suspension_key(adb_endpoint)) or {}).get("suspended"):
            virtual_device.update(reason_code="inspection_suspended", issue_status="partially_available",
                                  blocking_scope="engagement_v3", user_message="互动巡检已暂停；安全恢复首页后视频任务可继续",
                                  suggested_action="查看已有现场，点击重新检查并恢复巡检")
            virtual_device["available_actions"] = list(dict.fromkeys(virtual_device["available_actions"] + ["recheck_inspection"]))
        preparation_issue = store.get_profile("preparation-issue:" + adb_endpoint) or {}
        if store.is_stop_requested(adb_endpoint) and preparation_issue:
            virtual_device.update(reason_code="preparation_waiting_user", issue_status="waiting_user",
                                  blocking_scope="business", user_message=preparation_issue.get("message", "当前业务已暂停"),
                                  suggested_action="打开画面处理，完成后点击继续检查；原失败任务不会自动重放")
            virtual_device["available_actions"] = ["open_screen", "manual_control", "continue_onboarding"]
        if latest and latest.status in {"queued", "running", "waiting_user"}:
            # Maintenance presentation wins over older business issues.
            from task_preparation import preparation_presentation
            presentation = preparation_presentation(latest.status, latest.message)
            virtual_device.update(user_message=presentation["message"], available_actions=presentation["actions"])
        virtual_device["can_start"] = bool(
            not active_operation
            and virtual_device.get("presence_status") == "present"
            and not virtual_device.get("task_ready")
        )
    return {
        "product_version": product_version(),
        "device": devices[0] if devices else None,
        "devices": devices,
        "worker": workers[0] if workers else None,
        "workers": workers,
        "incident_analyzer": RuntimeControl().status("incident-analyzer"),
        "device_stream_host": RuntimeControl().status("device-stream-host"),
        "device_view_sessions": store.device_view_session_summary(),
        "paused": store.is_paused(),
        "all_automation_stopped": bool((store.get_profile("automation-stop") or {}).get("stopped")),
        "device_preferences": preferences,
        "stop_requested_device_ids": [
            device_id for device_id in config["device_ids"]
            if store.is_stop_requested(device_id)
        ],
        "reconciled_tasks": reconciled_tasks,
        "task_summary": store.task_status_counts(),
        "tasks": [{**_compact_task_payload(task), "progress": store.task_progress(task.id)} for task in store.list(5)],
        "active_tasks": [
            {**_compact_task_payload(task, include_live_result=True), "progress": store.task_progress(task.id)}
            for task in active_tasks_for_display(active_tasks, config["device_ids"])
        ],
        "task_groups": [_compact_group_payload(group) for group in task_groups],
        "task_group_total": store.task_group_count(),
        "incidents": [
            _public_incident(incident, include_analysis=False)
            for incident in store.list_incidents(5)
        ],
        "incident_summary": incident_summary,
        "initialization_summary": initialization_summary,
        "virtualization": {
            "provider": "mumu",
            "device_count": len(virtual_devices),
            "ready_count": sum(1 for item in virtual_devices if item.get("task_ready")),
            "stopped_count": sum(1 for item in virtual_devices if item.get("state") == "stopped"),
            "starting_count": sum(
                1
                for item in virtual_devices
                if (item.get("active_operation") or {}).get("status")
                in {"queued", "running"}
            ),
            "verification_count": sum(1 for item in virtual_devices if item.get("profile_status") == "requires_verification"),
            "unavailable_count": sum(1 for item in virtual_devices if item.get("presence_status") in {"missing", "engine_unavailable", "identity_conflict"}),
            "issues": [
                {
                    key: item.get(key)
                    for key in (
                        "virtual_device_id",
                        "name",
                        "issue_status",
                        "blocking_scope",
                        "reason_code",
                        "user_message",
                        "suggested_action",
                        "available_actions",
                        "retryable",
                        "diagnostic_id",
                        "updated_at",
                    )
                }
                for item in virtual_devices
                if item.get("reason_code") != "ready"
                and item.get("issue_status") != "normal"
            ],
            "devices": virtual_devices,
        },
        "emulator_onboarding": background_onboarding_status(),
    }


def cached_status_payload(store: TaskStore, config: dict[str, Any]) -> dict[str, Any]:
    """Coalesce simultaneous page polling into one short-lived local snapshot."""
    cache_key = str(store.path.resolve())
    now = time.monotonic()
    with _STATUS_PAYLOAD_LOCK:
        cached = _STATUS_PAYLOAD_CACHE.get(cache_key)
        if cached and now - cached[0] < _STATUS_PAYLOAD_TTL_SECONDS:
            return cached[1]
        payload = build_status_payload(store, config)
        _STATUS_PAYLOAD_CACHE[cache_key] = (time.monotonic(), payload)
        return payload


def public_initialization(record: InitializationRecord) -> dict[str, Any]:
    payload = asdict(record)
    payload["write_acceptance"] = bool(record.options.get("write_acceptance", False))
    if record.status == "waiting_user":
        payload["error"] = None
    return payload


def initialization_options_from_body(body: dict[str, Any]) -> dict[str, Any]:
    write_acceptance = body.get("write_acceptance", False)
    if not isinstance(write_acceptance, bool):
        raise ValueError("写入验收开关格式无效")
    if write_acceptance and body.get("confirmation") != "ENABLE_WRITE_ACCEPTANCE":
        raise ValueError("完整写入验收会发送一条真实评论，请明确确认")
    search_query = str(body.get("search_query") or "人工智能").strip()
    if not search_query:
        search_query = "人工智能"
    return {
        "write_acceptance": write_acceptance,
        "search_query": search_query[:80],
    }


def initialization_report_path(record: InitializationRecord) -> Path:
    if not record.report_path:
        raise KeyError("Initialization report is unavailable")
    path = Path(record.report_path).resolve()
    artifacts_root = DEFAULT_ARTIFACTS.resolve()
    if artifacts_root not in path.parents or not path.is_file():
        raise KeyError("Initialization report is unavailable")
    return path


def _public_task(task, recovery: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_result = dict(task.result or {})
    result = (
        _public_inspection_result(raw_result)
        if task.task_type == "douyin_engagement_inspection"
        else raw_result
    )
    snapshot = task.payload.get("content_plan_snapshot")
    content_plan = None
    if isinstance(snapshot, dict) and isinstance(snapshot.get("theme"), dict):
        theme = snapshot["theme"]
        content_plan = {
            "plan_id": snapshot.get("content_plan_id"),
            "revision_id": snapshot.get("content_plan_revision_id"),
            "revision_number": snapshot.get("content_plan_revision_number"),
            "plan_name": snapshot.get("content_plan_name"),
            "theme_name": theme.get("name"),
            "theme_queue_index": snapshot.get("theme_queue_index"),
            "theme_queue_size": snapshot.get("theme_queue_size"),
            "search_query": theme.get("search_query"),
        }
    is_video_round = task.task_type == "douyin_topic_session"
    return {
        "id": task.id,
        "device_id": task.device_id,
        "task_type": task.task_type,
        "status": task.status,
        "created_at": task.created_at,
        "not_before": task.not_before,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "round_index": (
            int(task.payload.get("round_index", 1)) if is_video_round else None
        ),
        "round_count": (
            int(task.payload.get("round_count", 1)) if is_video_round else None
        ),
        "inspection_index": (
            int(task.payload.get("inspection_index", 0))
            if task.task_type == "douyin_engagement_inspection"
            else None
        ),
        "after_round_index": (
            int(task.payload.get("after_round_index", 0))
            if task.task_type == "douyin_engagement_inspection"
            else None
        ),
        "inspection_every_rounds": (
            int(task.payload.get("inspection_every_rounds", 0))
            if task.task_type == "douyin_engagement_inspection"
            else None
        ),
        "inspection_mode": task.payload.get("inspection_mode"),
        "inspection_workflow_version": task.payload.get("inspection_workflow_version"),
        "content_plan": content_plan,
        "parent_task_id": task.payload.get("recovery_parent_task_id"),
        "recovery": (
            {
                key: recovery.get(key)
                for key in (
                    "status", "progress_current", "progress_total", "message",
                    "replacement_task_id", "expected", "actual", "error",
                )
            }
            if recovery
            else None
        ),
        "result": result,
        "error": task.error,
    }


def _bounded_public_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if re.match(r"^(?:[A-Za-z]:[\\/]|/)", text):
        return ""
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _public_home_badge(raw: dict[str, Any]) -> dict[str, Any]:
    state = raw.get("state") if raw.get("state") in {"present", "absent", "unknown"} else "unknown"
    text = str(raw.get("badge_text") or "")
    text = text if state == "present" and re.fullmatch(r"[1-9]\d{0,5}\+?", text) else None
    bounds = raw.get("target_bounds")
    return {
        "state": state, "badge_text": text,
        "message_count": int(text) if text and not text.endswith("+") else None,
        "count_is_lower_bound": bool(text and text.endswith("+")),
        "message": _bounded_public_text(raw.get("message")),
        "reason_code": _bounded_public_text(raw.get("reason_code"), 100),
        "source": "vision" if raw.get("source") == "vision" else "local",
        "target_bounds": bounds if isinstance(bounds, list) and len(bounds)==4 and all(type(n) is int and 0<=n<=10000 for n in bounds) else None,
        "evidence_missing": [v for v in raw.get("evidence_missing", []) if v in {"screenshot", "ui_tree"}],
    }


def _public_inspection_result(raw: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    raw_sections = raw.get("sections")
    if not isinstance(raw_sections, dict):
        raw_sections = {}
    for name in (
        "private_messages", "received_likes", "comment_danmaku", "profile_visitors"
    ):
        source = raw_sections.get(name)
        if not isinstance(source, dict):
            continue
        entries = source.get("entries")
        if not isinstance(entries, list):
            entries = []
        public_entries: list[dict[str, Any]] = []
        for entry in entries[:20]:
            if not isinstance(entry, dict):
                continue
            public_entry: dict[str, Any] = {}
            for key in ("display_name", "time", "preview", "summary", "content"):
                if key in entry:
                    public_entry[key] = _bounded_public_text(entry.get(key))
            if entry.get("category") in {
                "received_likes", "comment_danmaku", "profile_visitors"
            }:
                public_entry["category"] = entry["category"]
            if isinstance(entry.get("unread_count"), int):
                public_entry["unread_count"] = max(0, int(entry["unread_count"]))
            public_entries.append(public_entry)
        section = {
            "status": str(source.get("status") or "failed")
            if source.get("status") in {"available", "unavailable", "failed"}
            else "failed",
            "count": source.get("count") if isinstance(source.get("count"), int) else None,
            "unread_count": (
                source.get("unread_count")
                if isinstance(source.get("unread_count"), int)
                else None
            ),
            "entries": public_entries,
            "truncated": bool(source.get("truncated") or len(entries) > 20),
            "reason": _bounded_public_text(source.get("reason")),
        }
        badge = source.get("entry_badge")
        if isinstance(badge, dict):
            section["entry_badge"] = {
                "has_unread": bool(badge.get("has_unread")),
                "unread_count": (
                    badge.get("unread_count")
                    if isinstance(badge.get("unread_count"), int)
                    else None
                ),
                "indicator": (
                    badge.get("indicator")
                    if badge.get("indicator") in {"number", "dot", "none"}
                    else "none"
                ),
            }
        if isinstance(source.get("scroll_count"), int):
            section["scroll_count"] = max(0, int(source["scroll_count"]))
        if "complete" in source:
            section["complete"] = bool(source.get("complete"))
        if source.get("baseline_status") in {
            "baseline_created", "unchanged", "changed", "pending_review", "unavailable"
        }:
            section["baseline_status"] = source["baseline_status"]
        sections[name] = section
    evidence = raw.get("evidence")
    public = {
        "status": (
            raw.get("status")
            if raw.get("status") in {"completed", "degraded", "failed"}
            else "failed"
        ),
        "task_type": "douyin_engagement_inspection",
        "restored": bool(raw.get("restored")),
        "failure_reason": _bounded_public_text(raw.get("failure_reason")),
        "side_effect_notice": _bounded_public_text(raw.get("side_effect_notice")),
        "sections": sections,
        "evidence": [
            _bounded_public_text(item, 100)
            for item in evidence[:6]
            if isinstance(item, str)
            and (item.startswith("section:") or item.startswith("unified_activity:"))
        ]
        if isinstance(evidence, list)
        else [],
    }
    if raw.get("workflow_version") in {"v1", "v2", "v3", "home_badge"}:
        public["workflow_version"] = raw["workflow_version"]
    if raw.get("workflow_version") == "home_badge" and isinstance(raw.get("home_badge"), dict):
        public["home_badge"] = _public_home_badge(raw["home_badge"])
    unified = raw.get("unified_activity")
    if raw.get("workflow_version") == "v3" and isinstance(unified, dict):
        public["unified_activity"] = {
            "status": unified.get("status")
            if unified.get("status") in {"available", "failed"}
            else "failed",
            "complete": bool(unified.get("complete")),
            "read_boundary": unified.get("read_boundary")
            if unified.get("read_boundary")
            in {"first_screen", "after_scroll", "explicit_empty", "end_of_list"}
            else None,
            "scroll_count": max(0, int(unified.get("scroll_count") or 0)),
            "unread_item_count": unified.get("unread_item_count")
            if isinstance(unified.get("unread_item_count"), int)
            else None,
            "categories": [
                value
                for value in list(unified.get("categories") or [])
                if value in {"received_likes", "comment_danmaku", "profile_visitors"}
            ][:3],
            "reason_code": _bounded_public_text(unified.get("reason_code"), 80),
        }
    if raw.get("failure_class") == "recoverable_precondition":
        public.update(
            failure_class="recoverable_precondition",
            recovery_eligible=bool(raw.get("recovery_eligible")),
            navigation_started=bool(raw.get("navigation_started")),
            expected_app_version=_bounded_public_text(raw.get("expected_app_version"), 80),
            actual_app_version=_bounded_public_text(raw.get("actual_app_version"), 80),
            expected_display_signature=_bounded_public_text(raw.get("expected_display_signature"), 80),
            actual_display_signature=_bounded_public_text(raw.get("actual_display_signature"), 80),
        )
    metadata = raw.get("inspection_metadata")
    if isinstance(metadata, dict):
        public["inspection_metadata"] = {
            "workflow_version": metadata.get("workflow_version")
            if metadata.get("workflow_version") in {"v1", "v2", "v3", "home_badge"}
            else "v1",
            "alert_sources": [
                value for value in metadata.get("alert_sources", [])
                if value in {"private_messages", "received_likes", "comment_danmaku", "profile_visitors", "home_badge"}
            ][:4],
            "alert_created": bool(metadata.get("alert_created")),
            "visitor_comparison": metadata.get("visitor_comparison")
            if metadata.get("visitor_comparison") in {
                "not_checked", "baseline_created", "unchanged", "changed", "pending_review", "unavailable"
            }
            else "not_checked",
        }
        if raw.get("workflow_version") == "home_badge":
            public["inspection_metadata"].update(workflow_version="home_badge",
                inspection_id=_bounded_public_text(metadata.get("inspection_id"), 100),
                result_kind=metadata.get("result_kind") if metadata.get("result_kind") in {"alert", "clear", "incomplete"} else "incomplete",
                evidence_count=max(0, int(metadata.get("evidence_count") or 0)),
                conclusion=_bounded_public_text(metadata.get("conclusion")))
    return public


def _public_interaction_alert(alert: dict[str, Any]) -> dict[str, Any]:
    profiles = load_device_profiles()
    profile = profiles.get(str(alert.get("device_id") or ""))
    sources = [
        value for value in alert.get("sources", [])
        if value in {"private_messages", "received_likes", "comment_danmaku", "profile_visitors", "home_badge"}
    ]
    summary = alert.get("summary") if isinstance(alert.get("summary"), dict) else {}
    public_sources: dict[str, Any] = {}
    raw_sources = summary.get("sources") if isinstance(summary.get("sources"), dict) else {}
    for name in sources:
        value = raw_sources.get(name) if isinstance(raw_sources.get(name), dict) else {}
        public_value: dict[str, Any] = {
            "indicator": value.get("indicator") if value.get("indicator") in {"number", "dot", "none"} else "none",
            "complete": bool(value.get("complete", True)),
        }
        if isinstance(value.get("unread_count"), int) and value.get("unread_count") > 0:
            public_value["unread_count"] = int(value["unread_count"])
        items = [item for item in value.get("items", []) if isinstance(item, dict) and item]
        if items:
            public_value["items"] = items
        public_sources[name] = public_value
    public: dict[str, Any] = {
        "id": str(alert.get("id") or ""),
        "device_id": str(alert.get("device_id") or ""),
        "device_name": profile.friendly_name if profile else str(alert.get("device_id") or ""),
        "sources": sources,
        "summary": {
            "source_count": len(sources),
            "sources": public_sources,
            "conclusion": str(summary.get("conclusion") or "检测到新互动"),
            "evidence_count": int(summary.get("evidence_count") or 0),
        },
        "status": alert.get("status") if alert.get("status") in {"unread", "viewed"} else "viewed",
        "detected_at": str(alert.get("detected_at") or ""),
    }
    if summary.get("inspection_id"):
        public["inspection_id"] = str(summary["inspection_id"])
    if "home_badge" in sources:
        public["summary"].update(home_badge=_public_home_badge(summary.get("home_badge") or {}),
            confirmed=summary.get("confirmed") is True,
            last_checked_at=_bounded_public_text(summary.get("last_checked_at"), 60),
            last_check_message=_bounded_public_text(summary.get("last_check_message")))
    if alert.get("viewed_at"):
        public["viewed_at"] = str(alert["viewed_at"])
    return public


def _public_interaction_inspection(
    inspection: dict[str, Any], *, detail: bool
) -> dict[str, Any]:
    profiles = load_device_profiles()
    device_id = str(inspection.get("device_id") or "")
    profile = profiles.get(device_id)
    summary = inspection.get("summary") if isinstance(inspection.get("summary"), dict) else {}
    public: dict[str, Any] = {
        "id": str(inspection.get("id") or ""),
        "device_id": device_id,
        "device_name": profile.friendly_name if profile else device_id,
        "workflow_version": str(inspection.get("workflow_version") or "v1"),
        "status": str(inspection.get("status") or "failed"),
        "result_kind": str(inspection.get("result_kind") or "incomplete"),
        "restored": bool(inspection.get("restored")),
        "summary": summary,
        "started_at": str(inspection.get("started_at") or ""),
        "finished_at": str(inspection.get("finished_at") or ""),
    }
    evidence = [
        item for item in inspection.get("evidence", [])
        if isinstance(item, dict) and item.get("id")
    ]
    public["evidence_count"] = len(evidence)
    if detail and evidence:
        public["evidence"] = [
            {
                "id": str(item["id"]),
                "label": str(item.get("label") or item.get("name") or "巡检证据"),
                "section": str(item.get("section") or "navigation"),
                "captured_at": str(item.get("captured_at") or ""),
                **({"image_url": f"/api/interaction-evidence?inspection_id={quote(str(inspection['id']))}&evidence_id={quote(str(item['id']))}&kind=image"} if item.get("image_name") else {}),
                **({"ui_tree_url": f"/api/interaction-evidence?inspection_id={quote(str(inspection['id']))}&evidence_id={quote(str(item['id']))}&kind=ui_tree"} if item.get("ui_tree_name") else {}),
            }
            for item in evidence
        ]
    return public


def interaction_evidence_path(
    store: TaskStore, inspection_id: str, evidence_id: str, kind: str
) -> Path:
    inspection = store.get_interaction_inspection(inspection_id)
    run_dir = Path(str(inspection.get("run_dir") or "")).resolve()
    if DEFAULT_ARTIFACTS.resolve() not in run_dir.parents:
        raise KeyError("Interaction evidence is unavailable")
    evidence = next(
        (
            item for item in inspection.get("evidence", [])
            if isinstance(item, dict) and str(item.get("id") or "") == evidence_id
        ),
        None,
    )
    if evidence is None or kind not in {"image", "ui_tree"}:
        raise KeyError("Interaction evidence is unavailable")
    name = evidence.get("image_name" if kind == "image" else "ui_tree_name")
    if not isinstance(name, str) or Path(name).name != name:
        raise KeyError("Interaction evidence is unavailable")
    path = (run_dir / name).resolve()
    allowed_suffix = ".png" if kind == "image" else ".gz"
    if path.parent != run_dir or path.suffix.lower() != allowed_suffix or not path.is_file():
        raise KeyError("Interaction evidence is unavailable")
    return path


def _legacy_group_signature(task) -> tuple[str, str, str, int]:
    ignored = {"device_id", "device_ids", "round_index", "seed", "submission_id"}
    stable_payload = {
        key: value for key, value in task.payload.items() if key not in ignored
    }
    round_index = int(task.payload.get("round_index", 1))
    seed = int(task.payload.get("seed", 0))
    return (
        task.device_id,
        task.task_type,
        json.dumps(stable_payload, ensure_ascii=False, sort_keys=True),
        seed - round_index + 1,
    )


def _group_status(tasks: list) -> str:
    statuses = [task.status for task in tasks]
    if "running" in statuses:
        return "running"
    for waiting_status in ("waiting_user", "waiting_model", "waiting_device"):
        if waiting_status in statuses:
            return waiting_status
    if "pending" in statuses:
        return "pending"
    failed = statuses.count("failed")
    if failed and failed < len(statuses):
        return "partial_failed"
    if failed:
        return "failed"
    degraded = statuses.count("degraded")
    if degraded and degraded < len(statuses):
        return "partial_degraded"
    if degraded:
        return "degraded"
    if statuses and all(status == "completed" for status in statuses):
        return "completed"
    if "stopped" in statuses:
        return "stopped"
    if statuses and all(status == "cancelled" for status in statuses):
        return "cancelled"
    return statuses[0] if statuses else "pending"


def _summarize_task_group(group_id: str, tasks: list) -> dict[str, Any]:
    video_tasks = [task for task in tasks if task.task_type == "douyin_topic_session"]
    inspection_tasks = [
        task for task in tasks if task.task_type == "douyin_engagement_inspection"
    ]
    other_tasks = [
        task
        for task in tasks
        if task.task_type
        not in {"douyin_topic_session", "douyin_engagement_inspection"}
    ]
    ordered_videos = sorted(
        video_tasks or other_tasks,
        key=lambda task: (
            int(task.payload.get("round_index", 1)),
            task.created_at,
            task.id,
        ),
    )
    ordered_inspections = sorted(
        inspection_tasks,
        key=lambda task: (
            int(task.payload.get("inspection_index", 0)),
            task.created_at,
            task.id,
        ),
    )
    replacements = {
        str(task.payload.get("recovery_parent_task_id")): task
        for task in ordered_inspections
        if task.payload.get("recovery_parent_task_id")
    }
    recovered_origins = set(replacements)
    logical_inspections = [
        task for task in ordered_inspections if task.id not in recovered_origins
    ]
    inspection_sections = [
        section
        for task in logical_inspections
        for section in (
            ((task.result or {}).get("sections") or {}).values()
            if isinstance((task.result or {}).get("sections"), dict)
            else ()
        )
        if isinstance(section, dict)
    ]
    control_flow_success = sum(
        task.status in {"completed", "degraded"} and bool((task.result or {}).get("restored"))
        for task in logical_inspections
    )
    ordered_all = sorted(tasks, key=lambda task: (task.created_at, task.id))
    profiles = load_device_profiles()
    device_id = ordered_all[0].device_id
    profile = profiles.get(device_id)
    results = [task.result or {} for task in ordered_videos]
    model_attempts = sum(
        int(result.get("model_attempts", 0) or 0) for result in results
    )
    model_valid_decisions = sum(
        int(result.get("model_valid_decisions", 0) or 0) for result in results
    )
    return {
        "id": group_id,
        "device_id": device_id,
        "device_name": profile.friendly_name if profile else f"设备 {device_id[-6:]}",
        "task_type": (
            "douyin_topic_session"
            if video_tasks
            else ordered_all[0].task_type
        ),
        "status": _group_status(ordered_videos or logical_inspections),
        "inspection_status": (
            _group_status(logical_inspections) if logical_inspections else None
        ),
        "created_at": min(task.created_at for task in ordered_all),
        "started_at": min(
            (task.started_at for task in ordered_all if task.started_at), default=None
        ),
        "finished_at": max(
            (task.finished_at for task in ordered_all if task.finished_at), default=None
        ),
        "rounds_total": len(ordered_videos),
        "completed_rounds": sum(task.status == "completed" for task in ordered_videos),
        "degraded_rounds": sum(task.status == "degraded" for task in ordered_videos),
        "failed_rounds": sum(task.status == "failed" for task in ordered_videos),
        "running_rounds": sum(task.status == "running" for task in ordered_videos),
        "pending_rounds": sum(task.status == "pending" for task in ordered_videos),
        "stopped_rounds": sum(task.status == "stopped" for task in ordered_videos),
        "cancelled_rounds": sum(task.status == "cancelled" for task in ordered_videos),
        "inspection_total": len(logical_inspections),
        "recovered_preconditions": len(recovered_origins),
        "control_flow_success": control_flow_success,
        "control_flow_total": len(logical_inspections),
        "control_flow_success_rate": round(
            control_flow_success / len(logical_inspections) if logical_inspections else 1.0, 4
        ),
        "data_sections_available": sum(
            section.get("status") == "available" for section in inspection_sections
        ),
        "data_sections_total": len(inspection_sections),
        "data_completeness_rate": round(
            sum(section.get("status") == "available" for section in inspection_sections)
            / len(inspection_sections) if inspection_sections else 1.0,
            4,
        ),
        "completed_inspections": sum(
            task.status == "completed" for task in logical_inspections
        ),
        "degraded_inspections": sum(
            task.status == "degraded" for task in logical_inspections
        ),
        "failed_inspections": sum(
            task.status == "failed" for task in logical_inspections
        ),
        "pending_inspections": sum(
            task.status == "pending" for task in logical_inspections
        ),
        "videos_seen": sum(int(result.get("videos_seen", 0) or 0) for result in results),
        "non_video_feed_items": sum(
            int(result.get("non_video_feed_items", 0) or 0) for result in results
        ),
        "feed_phase_reentries": sum(
            int(result.get("feed_phase_reentries", 0) or 0) for result in results
        ),
        "likes": sum(int(result.get("likes", 0) or 0) for result in results),
        "favorites": sum(int(result.get("favorites", 0) or 0) for result in results),
        "comments_sent": sum(
            int(result.get("comments_sent", 0) or 0) for result in results
        ),
        "video_errors": sum(
            int(result.get("video_errors", 0) or 0) for result in results
        ),
        "model_attempts": model_attempts,
        "model_valid_decisions": model_valid_decisions,
        "model_errors": sum(
            int(result.get("model_errors", 0) or 0) for result in results
        ),
        "model_valid_response_rate": round(
            model_valid_decisions / model_attempts if model_attempts else 1.0, 4
        ),
        "tasks": [_public_task(task) for task in ordered_videos],
        "inspections": [_public_task(task) for task in ordered_inspections],
    }


def group_tasks_for_display(tasks: list) -> list[dict[str, Any]]:
    """Derive presentation groups without changing per-round queue records."""
    explicit: dict[tuple[str, str], list] = {}
    legacy_groups: list[tuple[str, list]] = []
    active_legacy: dict[tuple[str, str, str, int], tuple[str, list, set[int]]] = {}
    for task in sorted(tasks, key=lambda item: (item.created_at, item.id)):
        submission_id = str(task.payload.get("submission_id") or "").strip()
        if submission_id:
            explicit.setdefault((submission_id, task.device_id), []).append(task)
            continue
        round_count = int(task.payload.get("round_count", 1) or 1)
        round_index = int(task.payload.get("round_index", 1) or 1)
        if task.task_type != "douyin_topic_session" or round_count <= 1:
            legacy_groups.append((f"task-{task.id}", [task]))
            continue
        signature = _legacy_group_signature(task)
        current = active_legacy.get(signature)
        if (
            current is None
            or round_index == 1
            or round_index in current[2]
            or len(current[1]) >= round_count
        ):
            current = (f"legacy-{task.id}", [], set())
            active_legacy[signature] = current
            legacy_groups.append((current[0], current[1]))
        current[1].append(task)
        current[2].add(round_index)

    groups = [
        _summarize_task_group(f"submission-{submission_id}-{device_id}", grouped)
        for (submission_id, device_id), grouped in explicit.items()
    ]
    groups.extend(
        _summarize_task_group(group_id, grouped)
        for group_id, grouped in legacy_groups
    )
    return sorted(groups, key=lambda group: (group["created_at"], group["id"]), reverse=True)


def _enrich_group_progress(store, groups):
    for group in groups:
        progress = []
        for task in group['tasks'] + group['inspections']:
            task['progress'] = store.task_progress(task['id'])
            progress.append(task['progress'])
        group['progress'] = {key: sum(item.get(key, 0) for item in progress) for key in
            ('processed_slots', 'successful_slots', 'failed_slots', 'unavailable_slots', 'unknown_actions', 'skipped_slots')}


def paged_task_groups_payload(
    store: TaskStore, limit: int, offset: int, task_id: str | None = None
) -> dict[str, Any]:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1-100 and offset must be non-negative")
    groups = group_tasks_for_display([store.get(task_id)] if task_id else store.list_all())
    _enrich_group_progress(store, groups[offset : offset + limit])
    return {
        "items": groups[offset : offset + limit],
        "total": len(groups),
        "limit": limit,
        "offset": offset,
    }


def run_submission_payload(task_ids, workers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "task_id": task_ids[0],
        "task_ids": task_ids,
        "count": len(task_ids),
        "video_task_count": int(task_ids.video_task_count),
        "inspection_task_count": int(task_ids.inspection_task_count),
        "device_plans": list(task_ids.device_plans),
        "worker": workers[0],
        "workers": workers,
    }


def _task_image_label(name: str) -> str:
    if name == "topic-session-initial.png":
        return "启动页面"
    match = re.search(r"video-(\d+)-comment-sent", name)
    if match:
        return f"第 {match.group(1)} 条评论成功"
    match = re.search(r"video-(\d+)-incident", name)
    if match:
        return f"第 {match.group(1)} 条异常现场"
    match = re.search(r"video-(\d+)-topic-analysis-before", name)
    if match:
        return f"第 {match.group(1)} 条识别画面"
    return "运行截图"


def task_image_path(store: TaskStore, task_id: str, image_name: str) -> Path:
    task = store.get(task_id)
    if not task.run_dir or Path(image_name).name != image_name or not image_name.lower().endswith(".png"):
        raise KeyError("Task image is unavailable")
    run_dir = Path(task.run_dir).resolve()
    artifacts_root = DEFAULT_ARTIFACTS.resolve()
    if artifacts_root not in run_dir.parents:
        raise KeyError("Task image is unavailable")
    image_path = (run_dir / image_name).resolve()
    if image_path.parent != run_dir or not image_path.is_file():
        raise KeyError("Task image is unavailable")
    return image_path


def _representative_task_images(store: TaskStore, task) -> list[dict[str, str]]:
    if not task.run_dir:
        return []
    try:
        run_dir = Path(task.run_dir).resolve()
        if DEFAULT_ARTIFACTS.resolve() not in run_dir.parents:
            return []
        files = [path for path in run_dir.glob("*.png") if path.is_file()]
    except OSError:
        return []
    selected: list[Path] = []
    initial = run_dir / "topic-session-initial.png"
    if initial.is_file():
        selected.append(initial)
    comment_evidence = (task.result or {}).get("comment_screenshots", [])
    if isinstance(comment_evidence, list):
        for item in comment_evidence[-3:]:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("screenshot_path") or "").strip()
            try:
                image_path = Path(raw_path).resolve()
            except (OSError, RuntimeError):
                continue
            if (
                image_path.parent == run_dir
                and image_path.is_file()
                and image_path.suffix.lower() == ".png"
            ):
                selected.append(image_path)
    for incident in store.list_incidents_for_tasks([task.id])[-3:]:
        raw_path = str(incident.screenshot_path or "").strip()
        try:
            image_path = Path(raw_path).resolve()
        except (OSError, RuntimeError):
            continue
        if (
            image_path.parent == run_dir
            and image_path.is_file()
            and image_path.suffix.lower() == ".png"
        ):
            selected.append(image_path)
    selected.extend(sorted(run_dir.glob("video-*-topic-analysis-before.png"))[-2:])
    if not selected and files:
        safe_fallbacks = [
            path
            for path in files
            if "comment-sent" not in path.name and "incident-" not in path.name
        ]
        selected.extend(sorted(safe_fallbacks, key=lambda path: path.stat().st_mtime)[-2:])
    unique: list[Path] = []
    for path in selected:
        if path not in unique:
            unique.append(path)
    return [
        {"name": path.name, "label": _task_image_label(path.name)}
        for path in unique[:8]
    ]


def _task_action_evidence(store: TaskStore, task) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {
        "video": [],
        "like": [],
        "favorite": [],
        "comment": [],
        "correction": [],
    }
    if not task.run_dir:
        return groups
    try:
        run_dir = Path(task.run_dir).resolve()
        if DEFAULT_ARTIFACTS.resolve() not in run_dir.parents:
            return groups
    except (OSError, RuntimeError):
        return groups

    seen: dict[str, set[str]] = {key: set() for key in groups}
    phase_by_video: dict[int, str] = {}
    events_path = run_dir / "events.jsonl"
    event_lines: list[str] = []
    if events_path.is_file():
        try:
            event_lines = events_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            event_lines = []
        for line in event_lines:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            try:
                index = int(event.get("video"))
            except (TypeError, ValueError):
                continue
            phase = str(event.get("feed_phase") or "").strip()
            if index > 0 and phase in {"search", "home"}:
                phase_by_video[index] = phase

    def append_image(
        kind: str,
        image_path: Path,
        video_index: int,
        label: str,
        feed_phase: str | None = None,
    ) -> None:
        try:
            resolved = image_path.resolve()
        except (OSError, RuntimeError):
            return
        if (
            resolved.parent != run_dir
            or not resolved.is_file()
            or resolved.suffix.lower() != ".png"
            or resolved.name in seen[kind]
        ):
            return
        seen[kind].add(resolved.name)
        item = {"name": resolved.name, "label": label, "video_index": video_index}
        phase = feed_phase or phase_by_video.get(video_index)
        if phase in {"search", "home"}:
            item["feed_phase"] = phase
        groups[kind].append(item)

    def video_number(path: Path, pattern: str) -> int | None:
        match = re.fullmatch(pattern, path.name)
        return int(match.group(1)) if match else None

    topic_images: list[tuple[int, Path]] = []
    for image_path in run_dir.glob("video-*-topic-analysis-before.png"):
        index = video_number(image_path, r"video-(\d+)-topic-analysis-before\.png")
        if index is not None:
            topic_images.append((index, image_path))
    for index, image_path in sorted(topic_images):
        append_image("video", image_path, index, f"第 {index} 条 · 主题判断画面")

    if event_lines:
        for line in event_lines:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            event_name = event.get("event") if isinstance(event, dict) else None
            if event_name not in {"like_state_after", "favorite_state_after"}:
                continue
            if event.get("active") is not True:
                continue
            try:
                index = int(event.get("video"))
            except (TypeError, ValueError):
                continue
            if index < 1:
                continue
            kind = "like" if event_name == "like_state_after" else "favorite"
            action = "点赞" if kind == "like" else "收藏"
            append_image(
                kind,
                run_dir / f"video-{index}-{kind}-after.png",
                index,
                f"第 {index} 条 · {action}确认",
            )

    comment_evidence = (task.result or {}).get("comment_screenshots", [])
    if isinstance(comment_evidence, list):
        for item in comment_evidence:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("video_index"))
            except (TypeError, ValueError):
                continue
            raw_path = str(item.get("screenshot_path") or "").strip()
            if raw_path:
                append_image(
                    "comment",
                    Path(raw_path),
                    index,
                    f"第 {index} 条 · 评论发送确认",
                )

    for incident in store.list_incidents_for_tasks([task.id]):
        raw_path = str(incident.screenshot_path or "").strip()
        if not raw_path:
            continue
        index = int(incident.video_index or 0)
        append_image(
            "correction",
            Path(raw_path),
            index,
            f"第 {index or '-'} 条 · 纠错现场",
            str((incident.context or {}).get("feed_phase") or ""),
        )

    for evidence in groups.values():
        evidence.sort(key=lambda item: (item["video_index"], item["name"]))
    return groups


def _task_action_routing(task) -> dict[str, list[dict[str, Any]]]:
    """Summarize only explicitly recorded per-action routes for new tasks."""
    routes: dict[str, dict[tuple[str, str], int]] = {
        "like": {},
        "favorite": {},
        "comment": {},
    }
    if not task.run_dir:
        return {key: [] for key in routes}
    try:
        run_dir = Path(task.run_dir).resolve()
        if DEFAULT_ARTIFACTS.resolve() not in run_dir.parents:
            return {key: [] for key in routes}
    except (OSError, RuntimeError):
        return {key: [] for key in routes}
    events_path = run_dir / "events.jsonl"
    if not events_path.is_file():
        return {key: [] for key in routes}
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {key: [] for key in routes}
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict) or event.get("event") != "topic_ai_decision":
            continue
        action_routes = event.get("action_routes")
        if not isinstance(action_routes, dict):
            continue
        phase = str(event.get("feed_phase") or "").strip()
        if phase not in {"search", "home"}:
            phase = ""
        for action in routes:
            route = str(action_routes.get(action) or "").strip()
            if route:
                key = (phase, route)
                routes[action][key] = routes[action].get(key, 0) + 1
    return {
        action: [
            {
                **({"feed_phase": phase} if phase else {}),
                "route": route,
                "count": count,
            }
            for (phase, route), count in sorted(counts.items())
        ]
        for action, counts in routes.items()
    }


def task_detail_payload(store: TaskStore, task_ids: list[str]) -> dict[str, Any]:
    clean_ids = list(
        dict.fromkeys(str(value).strip() for value in task_ids if str(value).strip())
    )
    if not clean_ids or len(clean_ids) > 20:
        raise ValueError("task ids must contain 1-20 items")
    tasks = [store.get(task_id) for task_id in clean_ids]
    if len({task.device_id for task in tasks}) != 1:
        raise ValueError("task ids must belong to one device")
    incidents_by_task: dict[str, list[dict[str, Any]]] = {
        task_id: [] for task_id in clean_ids
    }
    for incident in store.list_incidents_for_tasks(clean_ids):
        item = _public_incident(incident)
        task = next((value for value in tasks if value.id == incident.task_id), None)
        if task is not None and task.task_type == "douyin_engagement_inspection":
            item = {
                key: item.get(key)
                for key in (
                    "id",
                    "task_id",
                    "device_id",
                    "video_index",
                    "stage",
                    "error_type",
                    "fingerprint",
                    "outcome",
                    "recovery_action",
                    "has_screenshot",
                    "has_ui_tree",
                    "analysis_status",
                    "analysis",
                    "created_at",
                )
            }
            item["error_message"] = _bounded_public_text(
                incident.error_message, 160
            )
        incidents_by_task.setdefault(incident.task_id, []).append(item)
    return {
        "tasks": [
            {
                **_public_task(task, store.get_task_recovery(task.id)),
                "progress": store.task_progress(task.id),
                "images": (
                    []
                    if task.task_type == "douyin_engagement_inspection"
                    else _representative_task_images(store, task)
                ),
                "evidence_groups": (
                    {}
                    if task.task_type == "douyin_engagement_inspection"
                    else _task_action_evidence(store, task)
                ),
                "action_routing": (
                    {"like": [], "favorite": [], "comment": []}
                    if task.task_type == "douyin_engagement_inspection"
                    else _task_action_routing(task)
                ),
                "incidents": incidents_by_task.get(task.id, []),
                "incident_evidence_status": (
                    "available"
                    if incidents_by_task.get(task.id)
                    else "not_captured_historical"
                    if task.task_type == "douyin_engagement_inspection"
                    and task.status in {"failed", "degraded"}
                    else "not_required"
                ),
            }
            for task in sorted(
                tasks,
                key=lambda item: (
                    0 if item.task_type == "douyin_topic_session" else 1,
                    int(
                        item.payload.get("round_index")
                        or item.payload.get("inspection_index")
                        or 0
                    ),
                ),
            )
        ]
    }


def _public_incident(incident, *, include_analysis: bool = True) -> dict[str, Any]:
    """Return incident metadata without exposing local evidence paths or raw UI."""
    item = asdict(incident)
    item["has_screenshot"] = bool(incident.screenshot_path)
    item["has_ui_tree"] = bool(incident.ui_tree_path)
    item.pop("screenshot_path", None)
    item.pop("ui_tree_path", None)
    item.pop("context", None)
    if not include_analysis:
        item.pop("analysis", None)
    return item


def paged_records_payload(
    store: TaskStore, record_type: str, limit: int, offset: int
) -> dict[str, Any]:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1-100 and offset must be non-negative")
    if record_type == "tasks":
        items = [asdict(task) for task in store.list(limit, offset)]
        total = sum(store.task_status_counts().values())
    elif record_type == "incidents":
        items = [
            _public_incident(incident)
            for incident in store.list_incidents(limit, offset)
        ]
        total = store.incident_statistics()["total"]
    else:
        raise ValueError("Unsupported record type")
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def comment_screenshot_path(
    store: TaskStore, task_id: str, video_index: int
) -> Path:
    task = store.get(task_id)
    result = task.result or {}
    evidence = result.get("comment_screenshots", [])
    if not isinstance(evidence, list):
        raise KeyError("Comment screenshot is unavailable")
    for item in evidence:
        if not isinstance(item, dict) or item.get("video_index") != video_index:
            continue
        raw_path = item.get("screenshot_path")
        if not isinstance(raw_path, str) or not raw_path:
            break
        image_path = Path(raw_path).resolve()
        artifacts_root = DEFAULT_ARTIFACTS.resolve()
        if artifacts_root not in image_path.parents or not image_path.is_file():
            break
        return image_path
    raise KeyError("Comment screenshot is unavailable")


class Handler(BaseHTTPRequestHandler):
    def automation_context(self):
        from automation import PlatformContext
        with _AGENT_SERVICE_LOCK:
            service = getattr(self.server, '_mediaflow_automation', None)
            if service is None:
                store = self.store
                service = PlatformContext(lambda: build_status_payload(store, normalized_config(store.get_profile(PROFILE_NAME) or {})),
                    store=store, model_status_reader=openrouter_key_status, worker_launcher=ensure_workers,
                    virtual_dispatch=lambda device_id, body: submit_vm_command(store, device_id, body), evidence_root=DEFAULT_ARTIFACTS)
                self.server._mediaflow_automation = service
            return service

    def retired_chat(self):
        self._json({'reason_code': 'external_skill_home',
            'user_message': '内置聊天已停用，请到平台首页下载MediaFlow Skill，由外部Agent调用平台。历史数据仍保留。',
            'agent_url': 'http://127.0.0.1:3001/'}, 410)

    def agent_service(self):
        # Lazy: an unused Agent must not add processes or block the legacy UI.
        from agent_service import AgentService
        with _AGENT_SERVICE_LOCK:
            service = getattr(self.server, '_mediaflow_agent', None)
            if service is None:
                store = self.store
                service = AgentService(lambda: build_status_payload(store, normalized_config(store.get_profile(PROFILE_NAME) or {})),
                                       store=store, model_status_reader=openrouter_key_status, worker_launcher=ensure_workers,
                                       virtual_dispatch=lambda device_id, body: submit_vm_command(store, device_id, body), evidence_root=DEFAULT_ARTIFACTS,
                                       native_frontend=(PROJECT_ROOT/'native_console/dist/index.html').is_file())
                self.server._mediaflow_agent = service
            return service

    store = TaskStore(DEFAULT_DB)
    review_store = TopicReviewStore(DEFAULT_DB, PROJECT_ROOT)
    review_store.seed_manifests(TOPIC_MANIFESTS)
    governance = EvidenceGovernance(
        DEFAULT_DB, EVIDENCE_ROOTS, RUNTIME_ROOT / "backups"
    )

    def log_message(self, format: str, *args: Any) -> None:
        sys.stdout.write((format % args) + "\n")

    def _headers(self, status: int = 200, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        origin = self.headers.get("Origin", "")
        if origin in {"http://127.0.0.1:3000", "http://127.0.0.1:3001"}:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _json(self, payload: Any, status: int = 200) -> None:
        self._headers(status)
        write_response_bytes(
            self.wfile, json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 500_000:
            raise ValueError("Request body is too large")
        value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _stream_internal_authorized(self) -> bool:
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            return False
        try:
            expected = ensure_stream_secret().read_text(encoding="utf-8").strip()
        except OSError:
            return False
        supplied = self.headers.get("X-MediaFlow-Stream-Secret", "") or self.headers.get("X-RiskFlow-Stream-Secret", "")
        return bool(expected) and secrets.compare_digest(expected, supplied)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._headers(204)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/platform-skill":
            as_markdown = parse_qs(parsed.query).get('format') == ['markdown']
            try:
                if as_markdown:
                    payload = build_skill_markdown().encode('utf-8')
                else:
                    with tempfile.TemporaryDirectory(prefix="mediaflow-skill-") as directory:
                        archive = build_skill_bundle(Path(directory) / "MediaFlow-Skill.zip")
                        payload = archive.read_bytes()
            except Exception:
                self._json(
                    {"error": "MediaFlow Skill 下载包暂不可用，请检查安装文件后重试。"},
                    503,
                )
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8" if as_markdown else "application/zip")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="MediaFlow-Skill.md"' if as_markdown else 'attachment; filename="MediaFlow-Skill.zip"',
            )
            self.send_header("Content-Length", str(len(payload)))
            origin = self.headers.get("Origin", "")
            if origin in {"http://127.0.0.1:3000", "http://127.0.0.1:3001"}:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            write_response_bytes(self.wfile, payload)
            return
        if path == '/api/automation':
            from automation import handle_automation_http
            handle_automation_http(self, 'GET', path)
            return
        if path.startswith('/api/agent/'):
            self.retired_chat()
            return
        initialization_match = re.fullmatch(
            r"/api/devices/([^/]+)/initialization(?:/(report))?", path
        )
        if initialization_match:
            device_id = unquote(initialization_match.group(1))
            record = self.store.latest_initialization(device_id)
            if record is None:
                self._json({"error": "该设备尚未运行初始化"}, 404)
                return
            if initialization_match.group(2) == "report":
                try:
                    report_path = initialization_report_path(record)
                except KeyError:
                    self._json({"error": "初始化报告尚不可用"}, 404)
                    return
                self._headers(200, "text/markdown; charset=utf-8")
                write_response_bytes(self.wfile, report_path.read_bytes())
                return
            self._json({"initialization": public_initialization(record)})
            return
        if path == "/api/config":
            self._json(normalized_config(self.store.get_profile(PROFILE_NAME) or {}))
            return
        if path == "/api/device-preferences":
            self._json(device_preferences(self.store))
            return
        if path == "/api/engagement-preflight":
            query = parse_qs(parsed.query)
            device_ids = [
                device_id.strip()
                for value in query.get("device_id", []) + query.get("device_ids", [])
                for device_id in value.split(",")
                if device_id.strip()
            ]
            self._json(visitor_reminder_status(self.store, device_ids))
            return
        if path == "/api/workbench/draft":
            fallback = normalized_config(self.store.get_profile(PROFILE_NAME) or {})
            fallback = _clear_legacy_development_selection(self.store, fallback)
            self._json({"draft": get_or_create_draft(self.store, fallback)})
            return
        if path == "/api/presets":
            self._json({"presets": list_presets(self.store)})
            return
        if path == "/api/content-plans":
            query = parse_qs(parsed.query)
            include_archived = query.get("include_archived", ["false"])[0].lower() == "true"
            self._json(
                {"content_plans": self.store.list_content_plans(include_archived=include_archived)}
            )
            return
        if path == "/api/model":
            provider = parse_qs(parsed.query).get("provider", [None])[0]
            self._json(model_providers.status(provider))
            return
        if path == "/api/status":
            config = normalized_config(self.store.get_profile(PROFILE_NAME) or {})
            self._json(cached_status_payload(self.store, config))
            return
        if path == "/api/system/storage":
            self._json(storage_status())
            return
        if path == "/api/device-onboarding":
            config = normalized_config(self.store.get_profile(PROFILE_NAME) or {})
            custom_path = parse_qs(parsed.query).get("mumu_path", [None])[0]
            self._json(build_device_onboarding_payload(self.store, config, custom_path=custom_path))
            return
        if path == "/api/virtual-devices":
            devices = [
                item
                for item in self.store.list_managed_virtual_devices()
                if item.get("state") != "retired"
                or item.get("presence_status") == "identity_conflict"
            ]
            for device in devices:
                operation = self.store.active_virtual_operation(
                    device["virtual_device_id"]
                )
                device["active_operation"] = operation
                device["can_start"] = bool(
                    not operation
                    and device.get("presence_status") == "present"
                    and not device.get("adb_endpoint")
                )
            self._json(
                {
                    "devices": devices,
                    "counts": {
                        "total": len(devices),
                        "stopped": sum(1 for item in devices if item.get("state") == "stopped"),
                        "ready": sum(1 for item in devices if item.get("state") == "ready"),
                        "unavailable": sum(
                            1
                            for item in devices
                            if item.get("presence_status")
                            in {"missing", "engine_unavailable", "identity_conflict"}
                        ),
                    },
                }
            )
            return
        if path == "/api/virtual-device-template":
            from local_vm_template import public_status
            self._json(public_status(self.store))
            return
        if path == "/api/virtual-devices/unmanaged":
            custom_path = parse_qs(parsed.query).get("mumu_path", [None])[0]
            self._json(VirtualDeviceInventory(self.store).unmanaged_candidates(custom_path))
            return
        if path == "/api/virtual-device-backups":
            query = parse_qs(parsed.query)
            virtual_device_id = str(query.get("virtual_device_id", [""])[0]).strip() or None
            self._json(
                {"backups": self.store.list_virtual_device_backups(virtual_device_id)}
            )
            return
        operation_match = re.fullmatch(r"/api/virtual-device-operations/([^/]+)", path)
        if operation_match:
            try:
                self._json({"operation": self.store.get_virtual_operation(unquote(operation_match.group(1)))})
            except KeyError:
                self._json({"error": "虚拟机操作不存在"}, 404)
            return
        view_session_match = re.fullmatch(r"/api/device-view-sessions/([^/]+)", path)
        if view_session_match:
            try:
                session = self.store.get_device_view_session(
                    unquote(view_session_match.group(1))
                )
                self._json({"session": session})
            except KeyError:
                self._json({"error": "画面会话不存在"}, 404)
            return
        if path == "/api/topic-reviews":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["100"])[0])
                offset = int(query.get("offset", ["0"])[0])
                self._json(self.review_store.payload(limit, offset))
            except (TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/topic-review-image":
            sample_id = parse_qs(parsed.query).get("id", [""])[0]
            try:
                image_path = self.review_store.image_path(sample_id, EVIDENCE_ROOTS)
            except KeyError:
                self._json({"error": "Review image is unavailable"}, 404)
                return
            content_type = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            self._headers(200, content_type)
            write_response_bytes(self.wfile, image_path.read_bytes())
            return
        if path == "/api/evidence/status":
            self._json(
                {
                    "inventory": self.governance.inventory(),
                    "backups": self.governance.backups(),
                }
            )
            return
        if path == "/api/evidence/manifest":
            self._json(self.governance.inventory())
            return
        if path == "/api/records/task-groups":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
                self._json(paged_task_groups_payload(self.store, limit, offset, query.get('task_id', [None])[0]))
            except (KeyError, TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/interaction-alerts":
            query = parse_qs(parsed.query)
            try:
                status_value = query.get("status", [""])[0]
                status = status_value if status_value in {"unread", "viewed"} else None
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
                payload = self.store.list_interaction_alerts(
                    status=status, limit=limit, offset=offset
                )
                payload["alerts"] = [
                    _public_interaction_alert(alert) for alert in payload["alerts"]
                ]
                self._json(payload)
            except (TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            return
        inspection_match = re.fullmatch(r"/api/interaction-inspections/([^/]+)", path)
        if inspection_match:
            inspection_id = unquote(inspection_match.group(1))
            try:
                self._json(
                    {
                        "inspection": _public_interaction_inspection(
                            self.store.get_interaction_inspection(inspection_id),
                            detail=True,
                        )
                    }
                )
            except KeyError:
                self._json({"error": "Interaction inspection not found"}, 404)
            return
        if path == "/api/interaction-inspections":
            query = parse_qs(parsed.query)
            try:
                result_value = query.get("result", [""])[0]
                result_kind = (
                    result_value
                    if result_value in {"alert", "clear", "incomplete"}
                    else None
                )
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
                payload = self.store.list_interaction_inspections(
                    result_kind=result_kind, limit=limit, offset=offset
                )
                payload["inspections"] = [
                    _public_interaction_inspection(item, detail=False)
                    for item in payload["inspections"]
                ]
                self._json(payload)
            except (TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/interaction-evidence":
            query = parse_qs(parsed.query)
            try:
                kind = query.get("kind", ["image"])[0]
                evidence_path = interaction_evidence_path(
                    self.store,
                    query.get("inspection_id", [""])[0],
                    query.get("evidence_id", [""])[0],
                    kind,
                )
            except KeyError:
                self._json({"error": "Interaction evidence is unavailable"}, 404)
                return
            content_type = "image/png" if kind == "image" else "application/gzip"
            self._headers(200, content_type)
            write_response_bytes(self.wfile, evidence_path.read_bytes())
            return
        if path == "/api/records/task-detail":
            query = parse_qs(parsed.query)
            task_ids = [
                value
                for raw in query.get("ids", [])
                for value in raw.split(",")
                if value
            ]
            try:
                self._json(task_detail_payload(self.store, task_ids))
            except (KeyError, TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path in {"/api/records/tasks", "/api/records/incidents"}:
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
                self._json(
                    paged_records_payload(
                        self.store, path.rsplit("/", 1)[-1], limit, offset
                    )
                )
            except (TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/task-image":
            query = parse_qs(parsed.query)
            task_id = query.get("task_id", [""])[0]
            image_name = query.get("name", [""])[0]
            try:
                image_path = task_image_path(self.store, task_id, image_name)
            except KeyError:
                self._json({"error": "Task image is unavailable"}, 404)
                return
            self._headers(200, "image/png")
            write_response_bytes(self.wfile, image_path.read_bytes())
            return
        if path == "/api/latest-image":
            images = sorted(DEFAULT_ARTIFACTS.rglob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True)
            if not images:
                self._json({"error": "No screenshot available"}, 404)
                return
            self._headers(200, "image/png")
            write_response_bytes(self.wfile, images[0].read_bytes())
            return
        if path == "/api/device-image":
            device_id = parse_qs(parsed.query).get("device_id", [""])[0]
            try:
                payload = device_screenshot_png(device_id)
            except KeyError:
                self._json({"error": "Device is not online and authorized"}, 404)
                return
            except (OSError, subprocess.SubprocessError, RuntimeError):
                self._json({"error": "Device screenshot is unavailable"}, 503)
                return
            self._headers(200, "image/png")
            write_response_bytes(self.wfile, payload)
            return
        if path == "/api/incident-image":
            incident_id = parse_qs(parsed.query).get("id", [""])[0]
            try:
                incident = self.store.get_incident(incident_id)
            except KeyError:
                self._json({"error": "Incident not found"}, 404)
                return
            if not incident.screenshot_path:
                self._json({"error": "Incident screenshot is unavailable"}, 404)
                return
            image_path = Path(incident.screenshot_path).resolve()
            artifacts_root = DEFAULT_ARTIFACTS.resolve()
            if artifacts_root not in image_path.parents or not image_path.is_file():
                self._json({"error": "Incident screenshot is unavailable"}, 404)
                return
            self._headers(200, "image/png")
            write_response_bytes(self.wfile, image_path.read_bytes())
            return
        if path == "/api/comment-image":
            query = parse_qs(parsed.query)
            task_id = query.get("task_id", [""])[0]
            try:
                video_index = int(query.get("video", [""])[0])
                image_path = comment_screenshot_path(
                    self.store, task_id, video_index
                )
            except (KeyError, TypeError, ValueError):
                self._json({"error": "Comment screenshot is unavailable"}, 404)
                return
            self._headers(200, "image/png")
            write_response_bytes(self.wfile, image_path.read_bytes())
            return
        self._json({"error": "Not found"}, 404)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            body = self._body()
            path = urlparse(self.path).path
            if path == "/api/automation-stop":
                stopped = body.get("stopped")
                if not isinstance(stopped, bool):
                    raise ValueError("请明确停止或恢复自动操作")
                self.store.save_profile("automation-stop", {"stopped": stopped})
                self.store.set_paused(True)
                if stopped:
                    for item in self.store.list_managed_virtual_devices():
                        if item.get("adb_endpoint"):
                            self.store.request_stop([item["adb_endpoint"]])
                            latest = self.store.latest_initialization(item["adb_endpoint"])
                            if latest and latest.status == "running":
                                self.store.cancel_initialization(item["adb_endpoint"])
                else:
                    self.store.clear_stop_requests()
                self._json({"ok": True, "stopped": stopped, "paused": True})
                return
            if path != "/api/workbench/draft":
                self._json({"error": "Not found"}, 404)
                return
            raw_config = body.get("config")
            if not isinstance(raw_config, dict):
                raise ValueError("任务草稿格式无效")
            revision = body.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise ValueError("任务草稿版本无效")
            draft = save_draft(
                self.store,
                raw_config,
                expected_revision=revision,
            )
            self.store.save_profile(PROFILE_NAME, draft["config"])
            self._json({"ok": True, "draft": draft})
        except RunDraftConflict as exc:
            self._json({"error": str(exc), "draft": exc.current}, 409)
        except KeyError:
            self._json({"error": "请求的本地记录不存在"}, 404)
        except model_providers.ProviderError as exc:
            self._json({"error": model_providers.ERRORS.get(exc.code, "模型配置需要处理"), "reason_code": exc.code, "retryable": False}, 400)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_PATCH(self) -> None:  # noqa: N802
        try:
            body = self._body()
            path = urlparse(self.path).path
            match = re.fullmatch(r"/api/virtual-devices/([^/]+)/settings", path)
            if match is None:
                self._json({"error": "Not found"}, 404)
                return
            virtual_device_id = unquote(match.group(1))
            virtual_device = self.store.get_virtual_device(virtual_device_id)
            VirtualDeviceInventory(self.store).assert_idle(virtual_device)
            settings = body.get("settings")
            if not isinstance(settings, dict) or not settings:
                raise ValueError("没有要修改的虚拟机配置")
            key = str(body.get("idempotency_key") or "").strip()
            if not key:
                raise ValueError("虚拟机操作缺少幂等键")
            custom_path = str(body.get("mumu_path") or "").strip() or None
            operation, created = self.store.create_virtual_operation(
                "settings",
                {
                    "virtual_device_id": virtual_device_id,
                    "settings": settings,
                    "mumu_path": custom_path,
                },
                idempotency_key=key,
            )
            if created:
                threading.Thread(
                    target=_run_virtual_device_extended_operation,
                    kwargs={
                        "store": self.store,
                        "operation_id": operation["id"],
                        "action": "settings",
                        "virtual_device_id": virtual_device_id,
                        "custom_path": custom_path,
                    },
                    daemon=True,
                    name=f"virtual-settings-{operation['id'][:8]}",
                ).start()
            self._json(
                {"ok": True, "created": created, "operation": operation},
                202 if created else 200,
            )
        except KeyError:
            self._json({"error": "虚拟机不存在"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/device-view-sessions/([^/]+)", path)
        virtual_match = re.fullmatch(r"/api/virtual-devices/([^/]+)", path)
        try:
            if match is not None:
                session = self.store.close_device_view_session(
                    unquote(match.group(1)), reason="closed_by_client"
                )
                self._json({"ok": True, "session": session})
                return
            if virtual_match is None:
                self._json({"error": "Not found"}, 404)
                return
            body = self._body()
            virtual_device_id = unquote(virtual_match.group(1))
            virtual_device = self.store.get_virtual_device(virtual_device_id)
            VirtualDeviceInventory(self.store).assert_idle(virtual_device)
            confirmation_name = str(body.get("confirmation_name") or "")
            if confirmation_name != virtual_device["name"]:
                raise ValueError("请输入完整虚拟机名称以确认删除")
            key = str(body.get("idempotency_key") or "").strip()
            if not key:
                raise ValueError("虚拟机操作缺少幂等键")
            custom_path = str(body.get("mumu_path") or "").strip() or None
            operation, created = self.store.create_virtual_operation(
                "delete",
                {
                    "virtual_device_id": virtual_device_id,
                    "confirmation_name": confirmation_name,
                    "backup": body.get("backup", True) is not False,
                    "mumu_path": custom_path,
                },
                idempotency_key=key,
            )
            if created:
                threading.Thread(
                    target=_run_virtual_device_extended_operation,
                    kwargs={
                        "store": self.store,
                        "operation_id": operation["id"],
                        "action": "delete",
                        "virtual_device_id": virtual_device_id,
                        "custom_path": custom_path,
                    },
                    daemon=True,
                    name=f"virtual-delete-{operation['id'][:8]}",
                ).start()
            self._json(
                {"ok": True, "created": created, "operation": operation},
                202 if created else 200,
            )
        except KeyError:
            self._json({"error": "本地记录不存在"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            path = urlparse(self.path).path
            if path == '/api/automation':
                from automation import handle_automation_http
                handle_automation_http(self, 'POST', path, body)
                return
            if path.startswith('/api/agent/'):
                self.retired_chat()
                return
            if path == "/api/virtual-device-template/cancel":
                operation = self.store.request_template_cancellation(str(body.get("operation_id") or ""))
                self._json({"ok": True, "operation": operation}, 202)
                return
            if path == "/api/virtual-device-template":
                if (self.store.get_profile("automation-stop") or {}).get("stopped"):
                    raise ValueError("所有自动操作已停止，请先恢复设备维护")
                private_manifest = None
                if body.get("private_manifest"):
                    if body.get("confirmation") != "导入私人快照，保留旧模板和实例":
                        raise ValueError("请确认私人快照可能有缓存与账号标识；旧模板和实例保留")
                    private_manifest = str(Path(str(body["private_manifest"])).resolve(strict=True))
                    if body.get('new_attempt') and body.get('new_attempt_confirmation') != '保留失败副本并新建一次':
                        raise ValueError('新建模板必须明确确认保留失败副本，不会自动重复导入')
                    counts = self.store.task_status_counts()
                    if not self.store.is_paused() or counts.get('running') or counts.get('pending'):
                        raise ValueError('请先暂停业务队列并等待任务池为空')
                    if self.store.list_active_virtual_operations():
                        raise ValueError('已有虚拟机操作未结束，请在平台查看进度')
                if body.get("rebuild") and body.get("confirmation") != "新建干净模板，保留旧实例":
                    raise ValueError("请确认新建干净模板；已有实例不会删除")
                import_directory = None
                if body.get("import_directory"):
                    directory = Path(str(body["import_directory"])).resolve(strict=True)
                    if not directory.is_dir() or not list(directory.glob("*.apk")):
                        raise ValueError("该目录没有APK，请导入完整安装包目录")
                    import_directory = str(directory)
                operation, created = self.store.create_virtual_operation(
                    "template_prepare", {"virtual_device_id": "local-template-creation", "from_template": True,
                                         "prepare_only": True, "rebuild": body.get("rebuild") is True,
                                         "import_directory": import_directory, "private_manifest": private_manifest, "resume_instance_id": body.get("resume_instance_id"), "new_attempt": body.get('new_attempt') is True},
                    idempotency_key=str(body.get("idempotency_key") or ""))
                if created:
                    threading.Thread(target=_run_virtual_device_create,
                                     kwargs={"store": self.store, "operation_id": operation["id"], "name": "本机模板", "custom_path": body.get('mumu_path')},
                                     daemon=True).start()
                self._json({"ok": True, "operation": operation}, 202)
                return
            create_initialization_match = re.fullmatch(
                r"/api/devices/([^/]+)/initializations", path
            )
            initialization_action_match = re.fullmatch(
                r"/api/devices/([^/]+)/initialization/(continue|cancel)", path
            )
            virtual_operation_continue_match = re.fullmatch(
                r"/api/virtual-device-operations/([^/]+)/continue", path
            )
            virtual_lifecycle_match = re.fullmatch(
                r"/api/virtual-devices/([^/]+)/operations", path
            )
            virtual_adopt_match = re.fullmatch(
                r"/api/virtual-devices/unmanaged/([^/]+)/adopt", path
            )
            unmanaged_operation_match = re.fullmatch(
                r"/api/virtual-devices/unmanaged/([^/]+)/operations", path
            )
            create_view_session_match = re.fullmatch(
                r"/api/devices/([^/]+)/view-sessions", path
            )
            virtual_window_match = re.fullmatch(
                r"/api/virtual-devices/([^/]+)/window", path
            )
            virtual_restore_match = re.fullmatch(
                r"/api/virtual-device-backups/([^/]+)/restore", path
            )
            internal_view_session_match = re.fullmatch(
                r"/api/internal/device-view-sessions/([^/]+)/(validate|heartbeat|disconnect)",
                path,
            )
            if internal_view_session_match:
                if not self._stream_internal_authorized():
                    self._json({"error": "未授权的画面网关"}, 403)
                    return
                session_id = unquote(internal_view_session_match.group(1))
                action = internal_view_session_match.group(2)
                if action == "validate":
                    raw_token = str(body.get("token") or "")
                    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                    try:
                        session = self.store.validate_device_view_session(
                            session_id, token_hash
                        )
                    except KeyError:
                        self._json({"error": "画面会话或令牌无效"}, 403)
                        return
                    virtual_device = self.store.get_virtual_device_for_adb(
                        session["device_id"]
                    )
                    if virtual_device is None:
                        self.store.close_device_view_session(
                            session_id, reason="virtual_device_unavailable"
                        )
                        raise ValueError("虚拟设备映射已失效")
                    self._json(
                        {
                            "session": session,
                            "adb_endpoint": session["device_id"],
                            "provider_instance_id": virtual_device["provider_instance_id"],
                        }
                    )
                    return
                metadata = body.get("metadata")
                if metadata is not None and not isinstance(metadata, dict):
                    raise ValueError("画面状态格式无效")
                try:
                    session = self.store.heartbeat_device_view_session(
                        session_id,
                        connected=action == "heartbeat",
                        metadata=metadata,
                    )
                except KeyError:
                    self._json({"error": "画面会话已失效"}, 410)
                    return
                self._json({"session": session})
                return
            if create_view_session_match:
                device_id = unquote(create_view_session_match.group(1))
                mode = str(body.get("mode") or "read_only")
                profile = str(body.get("profile") or "focus")
                if profile == "wall" and mode != "read_only":
                    raise ValueError("设备墙只允许只读观看")
                if adb_device_states().get(device_id) != "device":
                    raise ValueError("虚拟设备不在线或ADB未就绪")
                virtual_device = self.store.get_virtual_device_for_adb(device_id)
                if virtual_device is None or virtual_device.get("provider") != "mumu":
                    raise ValueError("首版实时画面只支持已接入的MuMu虚拟机")
                if (virtual_device.get("recipe") or {}).get("is_template"):
                    raise ValueError("本机模板已封存，不允许打开控制会话；请创建业务实例后操作")
                raw_token = secrets.token_urlsafe(36)
                token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                session = self.store.create_device_view_session(
                    device_id=device_id,
                    virtual_device_id=virtual_device["virtual_device_id"],
                    mode=mode,
                    stream_profile=profile,
                    token_hash=token_hash,
                )
                websocket_url = (
                    f"ws://127.0.0.1:48139/v1/sessions/{session['id']}"
                    f"?token={quote(raw_token)}"
                )
                self._json(
                    {
                        "session": session,
                        "token": raw_token,
                        "websocket_url": websocket_url,
                    },
                    201,
                )
                return
            if virtual_window_match:
                virtual_device = self.store.get_virtual_device(
                    unquote(virtual_window_match.group(1))
                )
                visible = body.get("visible", True)
                if not isinstance(visible, bool):
                    raise ValueError("窗口显示状态格式无效")
                if virtual_device.get("provider") != "mumu":
                    raise ValueError("当前虚拟设备不支持单机窗口")
                manager = resolve_mumu_manager()
                if manager is None:
                    raise ValueError("MuMu管理命令不可用")
                operation, created = self.store.create_virtual_operation(
                    "show_window" if visible else "hide_window",
                    {
                        "virtual_device_id": virtual_device["virtual_device_id"],
                        "visible": visible,
                    },
                    idempotency_key=str(
                        body.get("idempotency_key")
                        or f"window:{virtual_device['virtual_device_id']}:{visible}:{int(time.time() // 10)}"
                    ),
                )
                if created:
                    try:
                        self.store.update_virtual_operation(
                            operation["id"], status="running", stage="window_command", progress=50
                        )
                        MuMuProvider(manager).set_window_visible(
                            virtual_device["provider_instance_id"], visible
                        )
                        operation = self.store.update_virtual_operation(
                            operation["id"],
                            status="completed",
                            stage="completed",
                            progress=100,
                            result={"visible": visible},
                        )
                    except Exception as exc:
                        operation = self.store.update_virtual_operation(
                            operation["id"],
                            status="failed",
                            stage="failed",
                            progress=100,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        raise ValueError(operation["error"])
                self._json({"ok": True, "operation": operation})
                return
            if create_initialization_match:
                if (self.store.get_profile("automation-stop") or {}).get("stopped"):
                    raise ValueError("所有自动操作已停止，请先恢复设备维护；业务队列仍会保持暂停")
                device_id = unquote(create_initialization_match.group(1))
                managed_virtual = next(
                    (
                        item
                        for item in self.store.list_managed_virtual_devices()
                        if str(item.get("adb_endpoint") or "") == device_id
                        and item.get("state") != "retired"
                    ),
                    None,
                )
                if _is_virtual_adb_id(device_id):
                    if managed_virtual is None:
                        raise ValueError("该虚拟机未由MediaFlow托管，不能开始初始化")
                    if managed_virtual.get("standard_status") != "standard":
                        raise ValueError("请先恢复900×1600标准配置，再开始初始化")
                elif not device_preferences(self.store)["physical_devices_enabled"]:
                    raise ValueError("真机支持尚未启用，请先在设备设置中开启")
                states = adb_device_states()
                if states.get(device_id) != "device":
                    raise ValueError("设备不在线、未授权或当前不可用")
                if self.store.running_count([device_id]):
                    raise ValueError("该设备仍有普通任务在执行，请等待任务结束后初始化")
                options = initialization_options_from_body(body)
                if managed_virtual is not None:
                    from task_preparation import PREPARATION_VERSION
                    options.update(preparation_version=PREPARATION_VERSION,
                                   requirements=["connection", "display", "application"])
                    if body.get("inspection_recheck") is True:
                        options["inspection_recheck"] = True
                        options["inspection_mode"] = "home_badge" if body.get("inspection_mode") == "home_badge" else "legacy"
                        options["requirements"].append("home_badge" if options["inspection_mode"] == "home_badge" else "engagement_v3")
                record = self.store.create_initialization(
                    device_id,
                    platform_id="douyin",
                    options=options,
                )
                self.store.clear_stop_requests([device_id])
                worker = ensure_worker(device_id)
                self._json(
                    {
                        "ok": True,
                        "initialization": public_initialization(record),
                        "worker": worker,
                    },
                    202,
                )
                return
            if initialization_action_match:
                device_id = unquote(initialization_action_match.group(1))
                action = initialization_action_match.group(2)
                if action == "continue":
                    if (self.store.get_profile("automation-stop") or {}).get("stopped"):
                        raise ValueError("所有自动操作已停止，请先恢复设备维护")
                    record = self.store.continue_initialization(device_id)
                    self.store.clear_stop_requests([device_id])
                    worker = ensure_worker(device_id)
                    self._json(
                        {
                            "ok": True,
                            "initialization": public_initialization(record),
                            "worker": worker,
                        },
                        202,
                    )
                else:
                    record = self.store.cancel_initialization(device_id)
                    self._json(
                        {"ok": True, "initialization": public_initialization(record)},
                        202 if record.status == "running" else 200,
                    )
                return
            if virtual_operation_continue_match:
                operation_id = unquote(virtual_operation_continue_match.group(1))
                operation = self.store.get_virtual_operation(operation_id)
                if operation["status"] != "waiting_user" or operation["stage"] != "waiting_app_install":
                    raise ValueError("当前虚拟机不需要继续安装流程")
                threading.Thread(
                    target=_run_virtual_device_continue,
                    kwargs={"store": self.store, "operation_id": operation_id},
                    daemon=True,
                    name=f"virtual-continue-{operation_id[:8]}",
                ).start()
                self._json({"ok": True, "operation": operation}, 202)
                return
            if path == "/api/virtual-devices/reconcile":
                custom_path = str(body.get("mumu_path") or "").strip() or None
                try:
                    online_ids = {
                        device_id
                        for device_id, state in adb_device_states().items()
                        if state == "device"
                    }
                except (OSError, subprocess.SubprocessError):
                    online_ids = set()
                result = VirtualDeviceInventory(self.store).reconcile(
                    custom_path, online_adb_ids=online_ids
                )
                self._json({"ok": True, **result})
                return
            if path == "/api/device-preferences":
                enabled = body.get("physical_devices_enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("真机支持开关格式无效")
                preferences = {"physical_devices_enabled": enabled}
                self.store.save_profile(DEVICE_PREFERENCES_PROFILE, preferences)
                with _STATUS_PAYLOAD_LOCK:
                    _STATUS_PAYLOAD_CACHE.clear()
                self._json({"ok": True, **preferences})
                return
            if path == "/api/virtual-device-pools/preview":
                target_count = int(body.get("target_count") or 0)
                mode = str(body.get("mode") or "supplement")
                custom_path = str(body.get("mumu_path") or "").strip() or None
                plan = VirtualDeviceInventory(self.store).pool_plan(
                    mode, target_count, custom_path=custom_path
                )
                self._json({"plan": plan})
                return
            if path == "/api/virtual-device-pools":
                target_count = int(body.get("target_count") or 0)
                mode = str(body.get("mode") or "supplement").strip().lower()
                custom_path = str(body.get("mumu_path") or "").strip() or None
                plan = VirtualDeviceInventory(self.store).pool_plan(
                    mode, target_count, custom_path=custom_path
                )
                confirmation = str(body.get("confirmation") or "")
                if mode == "reset" and confirmation != plan["confirmation_phrase"]:
                    raise ValueError("请输入完整确认文字后再重建标准池")
                key = str(body.get("idempotency_key") or "").strip()
                if not key:
                    raise ValueError("标准池操作缺少幂等键")
                operation, created = self.store.create_virtual_operation(
                    "configure_pool",
                    {
                        "mode": mode,
                        "target_count": target_count,
                        "confirmation": confirmation,
                        "mumu_path": custom_path,
                        "plan_snapshot": plan,
                    },
                    idempotency_key=key,
                )
                if created:
                    threading.Thread(
                        target=_run_virtual_device_pool,
                        kwargs={
                            "store": self.store,
                            "operation_id": operation["id"],
                            "custom_path": custom_path,
                        },
                        daemon=True,
                        name=f"virtual-pool-{operation['id'][:8]}",
                    ).start()
                self._json(
                    {"ok": True, "created": created, "operation": operation},
                    202 if created else 200,
                )
                return
            if unmanaged_operation_match:
                provider_instance_id = unquote(unmanaged_operation_match.group(1))
                action = str(body.get("action") or "").strip().lower()
                if action not in {"start", "stop", "delete"}:
                    raise ValueError("未托管虚拟机只支持启动、停止或删除")
                key = str(body.get("idempotency_key") or "").strip()
                if not key:
                    raise ValueError("虚拟机操作缺少幂等键")
                custom_path = str(body.get("mumu_path") or "").strip() or None
                request = {
                    "provider_instance_id": provider_instance_id,
                    "action": action,
                    "confirmation_name": str(body.get("confirmation_name") or ""),
                    "mumu_path": custom_path,
                }
                if any(
                    str((item.get("request") or {}).get("provider_instance_id") or "")
                    == provider_instance_id
                    for item in self.store.list_active_virtual_operations()
                ):
                    raise ValueError("该未托管虚拟机已有操作正在执行")
                operation, created = self.store.create_virtual_operation(
                    f"unmanaged_{action}", request, idempotency_key=key
                )
                if created:
                    threading.Thread(
                        target=_run_unmanaged_virtual_operation,
                        kwargs={
                            "store": self.store,
                            "operation_id": operation["id"],
                            "provider_instance_id": provider_instance_id,
                            "action": action,
                            "custom_path": custom_path,
                        },
                        daemon=True,
                        name=f"unmanaged-{action}-{operation['id'][:8]}",
                    ).start()
                self._json(
                    {"ok": True, "created": created, "operation": operation},
                    202 if created else 200,
                )
                return
            if virtual_restore_match:
                backup_id = unquote(virtual_restore_match.group(1))
                self.store.get_virtual_device_backup(backup_id)
                custom_path = str(body.get("mumu_path") or "").strip() or None
                manager = resolve_mumu_manager(custom_path)
                if manager is None:
                    raise ValueError("MuMu管理命令不可用")
                key = str(body.get("idempotency_key") or "").strip()
                operation, created = self.store.create_numbered_virtual_operation(
                    "restore",
                    {
                        "provider": "mumu",
                        "provider_install_id": manager_identity(manager),
                        "backup_id": backup_id,
                        "mumu_path": custom_path,
                    },
                    idempotency_key=key,
                )
                if created:
                    threading.Thread(
                        target=_run_virtual_device_extended_operation,
                        kwargs={
                            "store": self.store,
                            "operation_id": operation["id"],
                            "action": "restore",
                            "custom_path": custom_path,
                        },
                        daemon=True,
                        name=f"virtual-restore-{operation['id'][:8]}",
                    ).start()
                self._json(
                    {"ok": True, "created": created, "operation": operation},
                    202 if created else 200,
                )
                return
            if path == "/api/virtual-devices/batch":
                action = str(body.get("action") or "").strip().lower()
                if action not in {"start", "stop"}:
                    raise ValueError("批量操作仅支持启动或停止")
                raw_ids = body.get("virtual_device_ids")
                if not isinstance(raw_ids, list) or not raw_ids:
                    raise ValueError("请选择至少一台虚拟机")
                ids = list(dict.fromkeys(str(item).strip() for item in raw_ids if str(item).strip()))
                if len(ids) > 50:
                    raise ValueError("单次批量操作最多50台虚拟机")
                batch_key = str(body.get("idempotency_key") or "").strip()
                if not batch_key:
                    raise ValueError("批量操作缺少幂等键")
                custom_path = str(body.get("mumu_path") or "").strip() or None
                results: list[dict[str, Any]] = []
                for virtual_device_id in ids:
                    try:
                        virtual_device = self.store.get_virtual_device(virtual_device_id)
                        VirtualDeviceInventory(self.store).assert_idle(virtual_device)
                        operation, created = self.store.create_virtual_operation(
                            action,
                            {
                                "virtual_device_id": virtual_device_id,
                                "action": action,
                                "mumu_path": custom_path,
                                "batch_key": batch_key,
                            },
                            idempotency_key=f"{batch_key}:{virtual_device_id}:{action}",
                        )
                        if created:
                            threading.Thread(
                                target=_run_virtual_device_lifecycle,
                                kwargs={
                                    "store": self.store,
                                    "operation_id": operation["id"],
                                    "virtual_device_id": virtual_device_id,
                                    "action": action,
                                    "custom_path": custom_path,
                                },
                                daemon=True,
                                name=f"virtual-batch-{action}-{operation['id'][:8]}",
                            ).start()
                        results.append({"virtual_device_id": virtual_device_id, "created": created, "operation": operation})
                    except Exception as exc:
                        results.append({"virtual_device_id": virtual_device_id, "error": str(exc)})
                self._json({"ok": True, "operations": results}, 202)
                return
            if virtual_adopt_match:
                provider_instance_id = unquote(virtual_adopt_match.group(1))
                custom_path = str(body.get("mumu_path") or "").strip() or None
                manager = resolve_mumu_manager(custom_path)
                if manager is None:
                    raise ValueError("MuMu管理命令不可用")
                key = str(body.get("idempotency_key") or "").strip()
                operation, created = self.store.create_numbered_virtual_operation(
                    "adopt",
                    {
                        "provider": "mumu",
                        "provider_install_id": manager_identity(manager),
                        "provider_instance_id": provider_instance_id,
                        "mumu_path": custom_path,
                    },
                    idempotency_key=key,
                )
                if created:
                    threading.Thread(
                        target=_run_virtual_device_adopt,
                        kwargs={
                            "store": self.store,
                            "operation_id": operation["id"],
                            "provider_instance_id": provider_instance_id,
                            "custom_path": custom_path,
                        },
                        daemon=True,
                        name=f"virtual-adopt-{operation['id'][:8]}",
                    ).start()
                self._json(
                    {"ok": True, "created": created, "operation": operation},
                    202 if created else 200,
                )
                return
            if virtual_lifecycle_match:
                virtual_device_id = unquote(virtual_lifecycle_match.group(1))
                action = str(body.get("action") or "").strip().lower()
                if action not in {"start", "stop", "restart", "clone", "backup", "repair_standard"}:
                    raise ValueError("虚拟机操作不受支持")
                result = submit_vm_command(self.store, virtual_device_id, {**body, 'action': action})
                self._json(result, 202 if result['created'] else 200)
                return
            if path == "/api/device-onboarding/scan":
                config = normalized_config(self.store.get_profile(PROFILE_NAME) or {})
                custom_path = str(body.get("mumu_path") or "").strip() or None
                self._json(build_device_onboarding_payload(self.store, config, custom_path=custom_path))
                return
            if path == "/api/system/storage/validate":
                self._json(
                    validate_data_root(
                        str(body.get("data_root") or ""),
                        app_root=APP_ROOT,
                    )
                )
                return
            if path == "/api/system/storage/schedule":
                counts = self.store.task_status_counts()
                active_count = int(counts.get("running", 0)) + int(counts.get("pending", 0))
                if active_count:
                    raise ValueError(
                        f"还有 {active_count} 个运行或排队任务；请先安全停止并清空队列"
                    )
                self._json(
                    schedule_data_root(
                        str(body.get("data_root") or ""),
                        app_root=APP_ROOT,
                    )
                )
                return
            if path == "/api/virtual-devices":
                if (self.store.get_profile("automation-stop") or {}).get("stopped"):
                    raise ValueError("所有自动操作已停止，请先恢复设备维护")
                key = str(body.get("idempotency_key") or "").strip()
                custom_path = str(body.get("mumu_path") or "").strip() or None
                manager = resolve_mumu_manager(custom_path)
                if manager is None:
                    raise ValueError("MuMu管理命令不可用")
                request = {
                    "provider": "mumu",
                    "provider_install_id": manager_identity(manager),
                    "mumu_path": custom_path,
                    "from_template": True,
                    "virtual_device_id": "local-template-creation",
                }
                operation, created = self.store.create_numbered_virtual_operation(
                    "template_create", request, idempotency_key=key
                )
                if created:
                    threading.Thread(
                        target=_run_virtual_device_create,
                        kwargs={
                            "store": self.store,
                            "operation_id": operation["id"],
                            "name": str(operation["request"]["name"]),
                            "custom_path": request["mumu_path"],
                        },
                        daemon=True,
                        name=f"virtual-create-{operation['id'][:8]}",
                    ).start()
                self._json({"ok": True, "created": created, "operation": operation}, 202 if created else 200)
                return
            if path == "/api/config":
                config = normalized_config(body)
                self.store.save_profile(PROFILE_NAME, config)
                self.store.save_run_draft(config)
                self._json({"ok": True, "config": config})
                return
            if path in {"/api/workbench/preview", "/api/workbench/submit"}:
                fallback = self.store.get_profile(PROFILE_NAME) or DEFAULT_CONFIG
                draft = get_or_create_draft(self.store, fallback)
                expected_revision = body.get("revision")
                if (
                    isinstance(expected_revision, bool)
                    or not isinstance(expected_revision, int)
                    or expected_revision != draft["revision"]
                ):
                    self._json(
                        {
                            "error": "任务草稿已经变化，请重新核对",
                            "draft": draft,
                        },
                        409,
                    )
                    return
                status = build_status_payload(self.store, draft["config"])
                preview = build_workbench_preview(
                    self.store,
                    draft,
                    devices=status["devices"],
                    paused=bool(status["paused"]),
                    model_status=openrouter_key_status(),
                )
                if path.endswith("/preview"):
                    self._json({"preview": preview})
                    return
                task_ids = submit_previewed_draft(
                    self.store,
                    draft,
                    preview,
                    expected_plan_hash=str(body.get("plan_hash") or ""),
                    confirm_writes=bool(body.get("confirm_writes", False)),
                )
                self.store.save_profile(PROFILE_NAME, draft["config"])
                workers = ensure_workers(preview["eligible_device_ids"])
                self._json(
                    {
                        **run_submission_payload(task_ids, workers),
                        "submission": {
                            "draft_revision": draft["revision"],
                            "plan_hash": preview["plan_hash"],
                            "write_actions": preview["write_actions"],
                        },
                    },
                    202,
                )
                return
            if path == "/api/presets":
                preset = save_preset(
                    self.store, body.get("name"), body.get("config")
                )
                self._json(
                    {"ok": True, "preset": preset, "presets": list_presets(self.store)}
                )
                return
            if path == "/api/presets/delete":
                deleted = delete_preset(self.store, body.get("name"))
                self._json(
                    {"ok": True, "deleted": deleted, "presets": list_presets(self.store)}
                )
                return
            if path == "/api/incidents/retry-analysis":
                queued = self.store.retry_failed_incident_analyses()
                self._json(
                    {
                        "ok": True,
                        "queued": queued,
                        "message": (
                            f"已将 {queued} 条失败记录重新加入只读分析队列"
                            if queued
                            else "没有需要重试的纠错分析"
                        ),
                    }
                )
                return
            if path == "/api/model-key":
                result = model_providers.save_candidate(body.get("api_key"), body.get("provider", "openrouter"))
                self._json({"ok": bool(result.get("accepted")), **result})
                return
            if path == "/api/model/verify":
                if body.get("provider") == model_providers.QWEN:
                    self._json({"ok": True, **model_providers.status(model_providers.QWEN),
                                "message": "千问没有免费鉴权流程；请确认说明后点击图片测试"})
                else:
                    self._json({"ok": True, **model_providers.verify_openrouter()})
                return
            if path == "/api/model/test":
                if body.get("provider") == model_providers.QWEN:
                    result = model_providers.test_candidate(consent=body.get("upload_consent") is True)
                else:
                    test_current_model()
                    result = model_providers.status("openrouter")
                self._json({"ok": result.get("model_test_status") == "passed", **result})
                return
            if path == "/api/model":
                self._json(model_providers.activate(body.get("provider"), task_db=self.store.path))
                return
            if path == "/api/interaction-alerts/acknowledge":
                alert_ids = body.get("alert_ids")
                if not isinstance(alert_ids, list):
                    raise ValueError("alert_ids 必须是列表")
                result = self.store.acknowledge_interaction_alerts(alert_ids)
                self._json({"ok": True, **result})
                return
            if path == "/api/engagement-preflight/visitor-acknowledgement":
                device_ids = body.get("device_ids")
                if not isinstance(device_ids, list):
                    raise ValueError("device_ids 必须是列表")
                result = acknowledge_visitor_reminder(self.store, device_ids)
                self._json({"ok": True, **result})
                return
            if path == "/api/content-plans":
                revision = self.store.save_content_plan(
                    body.get("document"), plan_id=body.get("plan_id")
                )
                self._json({"ok": True, "content_plan": revision}, 201)
                return
            if path == "/api/content-plans/archive":
                plan_id = str(body.get("plan_id") or "").strip()
                if not plan_id:
                    raise ValueError("请选择需要归档的内容计划")
                archived = self.store.archive_content_plan(plan_id)
                if not archived:
                    raise ValueError("内容计划不存在或已经归档")
                self._json({"ok": True, "archived": True, "plan_id": plan_id})
                return
            if path == "/api/topic-reviews/confirm":
                sample_id = str(body.get("sample_id") or "").strip()
                if not sample_id:
                    raise ValueError("请选择需要复核的样本")
                review = self.review_store.confirm(
                    sample_id,
                    str(body.get("relevance") or ""),
                    str(body.get("note") or ""),
                )
                self._json(
                    {
                        "ok": True,
                        "review": review,
                        "evaluation": self.review_store.evaluation(),
                    }
                )
                return
            if path == "/api/evidence/policy":
                raw_days = body.get("retention_days")
                if isinstance(raw_days, bool):
                    raise ValueError("证据保留天数格式无效")
                try:
                    days = int(raw_days)
                except (TypeError, ValueError) as exc:
                    raise ValueError("证据保留天数格式无效") from exc
                self._json({"ok": True, "policy": self.governance.save_policy(days)})
                return
            if path == "/api/evidence/backup":
                self._json({"ok": True, "backup": self.governance.create_backup()}, 201)
                return
            if path == "/api/pause":
                self.store.set_paused(True)
                self._json({"ok": True, "paused": True})
                return
            if path == "/api/resume":
                self.store.set_paused(False)
                cleared = self.store.clear_stop_requests()
                self._json({"ok": True, "paused": False, "stop_requests_cleared": cleared})
                return
            task_control = re.fullmatch(r"/api/tasks/([^/]+)/(resume|stop)", path)
            if task_control:
                from automation import AutomationService
                reply = AutomationService(self.automation_context()).call({
                    'action': task_control.group(2) + '_task',
                    'arguments': {'task_id': task_control.group(1)},
                    'request_id': body.get('request_id'), 'session_id': body.get('session_id')})
                self._json(reply, 200 if reply.get('ok') else 409)
                return
            if path == "/api/tasks/stop":
                device_ids = body.get("device_ids")
                if not isinstance(device_ids, list) or not device_ids:
                    raise ValueError("请选择需要安全停止的设备")
                changed = self.store.request_stop(device_ids)
                self._json(
                    {
                        "ok": True,
                        "requested": changed,
                        "device_ids": list(dict.fromkeys(str(value) for value in device_ids)),
                        "running": self.store.running_count(device_ids),
                    },
                    202,
                )
                return
            if path == "/api/tasks/cancel-pending":
                if body.get("confirmation") != "CANCEL_PENDING_TASKS":
                    raise ValueError("请确认取消全部等待任务")
                task_ids = body.get("task_ids")
                device_ids = body.get("device_ids")
                if task_ids is not None and not isinstance(task_ids, list):
                    raise ValueError("task_ids 格式无效")
                if device_ids is not None and not isinstance(device_ids, list):
                    raise ValueError("device_ids 格式无效")
                cancelled = self.store.cancel_pending(task_ids, device_ids=device_ids)
                self._json({"ok": True, "cancelled": cancelled})
                return
            recovery_continue_match = re.fullmatch(
                r"/api/tasks/([^/]+)/recover/continue", path
            )
            if recovery_continue_match:
                task_id = unquote(recovery_continue_match.group(1))
                recovery = self.store.continue_task_recovery(task_id)
                worker = ensure_worker(recovery["device_id"])
                self._json(
                    {"ok": True, "recovery": recovery, "worker": worker},
                    202,
                )
                return
            recovery_match = re.fullmatch(r"/api/tasks/([^/]+)/recover", path)
            if recovery_match:
                task_id = unquote(recovery_match.group(1))
                recovery, created = self.store.request_task_recovery(task_id)
                worker = ensure_worker(recovery["device_id"])
                self._json(
                    {
                        "ok": True,
                        "created": created,
                        "recovery": recovery,
                        "worker": worker,
                    },
                    202 if created else 200,
                )
                return
            if path in {"/api/workers/stop", "/api/workers/restart"}:
                device_ids = body.get("device_ids")
                if not isinstance(device_ids, list) or not device_ids:
                    raise ValueError("请选择设备")
                operation = stop_worker if path.endswith("/stop") else restart_worker
                results = [operation(self.store, str(device_id)) for device_id in device_ids]
                self._json({"ok": True, "workers": results})
                return
            if path == "/api/tasks/clear":
                if body.get("confirmation") != "CLEAR_ALL_TASKS":
                    raise ValueError("请确认清空全部任务记录")
                self.store.reconcile_orphaned_running(worker_id_is_running)
                deleted = self.store.clear_all_tasks()
                self._json({"ok": True, **deleted})
                return
            if path == "/api/run":
                # An empty run request means "run the saved form", not
                # "replace it with one default device".
                config = normalized_config(
                    body if body else (self.store.get_profile(PROFILE_NAME) or {})
                )
                current_status = build_status_payload(self.store, config)
                eligible_device_ids = {
                    str(item.get("device_id") or "")
                    for item in current_status.get("devices", [])
                    if item.get("state") == "device"
                }
                ineligible = [
                    device_id
                    for device_id in config["device_ids"]
                    if device_id not in eligible_device_ids
                ]
                if ineligible:
                    raise ValueError(
                        "以下设备当前不能执行任务："
                        + "、".join(ineligible)
                        + "。请使用已连接并达标的MediaFlow虚拟机；如需真机，请先在设备设置中启用真机支持。"
                    )
                controlled = [
                    device_id
                    for device_id in config["device_ids"]
                    if self.store.has_active_control_session(device_id)
                ]
                if controlled:
                    raise ValueError(
                        "以下设备正在人工接管，结束操作后才能提交："
                        + "、".join(controlled)
                    )
                self.store.save_profile(PROFILE_NAME, config)
                task_ids = submit_scheduled_rounds(self.store, config)
                workers = ensure_workers(config["device_ids"])
                self._json(run_submission_payload(task_ids, workers), 202)
                return
            self._json({"error": "Not found"}, 404)
        except KeyError:
            self._json({"error": "请求的本地记录不存在"}, 404)
        except model_providers.ProviderError as exc:
            self._json({"error": model_providers.ERRORS.get(exc.code, "模型配置需要处理"), "reason_code": exc.code, "retryable": False}, 400)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(json.dumps({"event": "control_api_ready", "url": f"http://{HOST}:{PORT}"}))
    def reconcile_virtual_devices_on_startup() -> None:
        try:
            online_ids = {
                device_id
                for device_id, state in adb_device_states().items()
                if state == "device"
            }
            inventory = VirtualDeviceInventory(Handler.store)
            inventory.reconcile_incomplete_operations(
                online_adb_ids=online_ids
            )
            inventory.reconcile(online_adb_ids=online_ids)
            cancelled = Handler.store.cancel_superseded_virtual_engagement_inspections()
            if cancelled:
                print(
                    json.dumps(
                        {
                            "event": "engagement_inspections_superseded",
                            "count": cancelled,
                            "replacement": "v3",
                        },
                        ensure_ascii=False,
                    )
                )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "virtual_inventory_reconcile_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
    threading.Thread(
        target=reconcile_virtual_devices_on_startup,
        daemon=True,
        name="virtual-inventory-startup",
    ).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
