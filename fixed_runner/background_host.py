from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from control_config import normalized_config
from runtime_control import (
    RUNTIME_ROOT,
    RuntimeControl,
    analyzer_spec,
    api_spec,
    optional_analyzer_spec,
    stream_spec,
    ui_spec,
    worker_role,
    worker_spec,
)
from task_store import TaskStore
from worker import DEFAULT_DB
from runtime_layout import APP_ROOT, BUNDLED_ADB, SECRET_ROOT, tool_environment
from windows_powershell import native_windows_powershell_environment


PROFILE_NAME = "default"
HEARTBEAT_PATH = RUNTIME_ROOT / "background-host.json"
STOP_REQUEST_PATH = RUNTIME_ROOT / "background-stop-request.json"
LOG_PATH = RUNTIME_ROOT / "background-host.log"
MUTEX_NAME = r"Local\RiskFlow.BackgroundHost"
PROJECT_ROOT = APP_ROOT
SECRET_TRANSFER_PATH = SECRET_ROOT / "openrouter-transfer.dpapi-machine"
IMPORT_SECRET_SCRIPT = Path(__file__).resolve().parent / "import-background-secret.ps1"


def online_adb_device_ids() -> set[str]:
    """Return only currently authorized ADB transports."""
    adb = str(BUNDLED_ADB) if BUNDLED_ADB.is_file() else (
        shutil.which("adb.exe") or shutil.which("adb")
    )
    if not adb:
        return set()
    try:
        completed = subprocess.run(
            [adb, "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            env=tool_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if completed.returncode:
        return set()
    result: set[str] = set()
    for line in completed.stdout.splitlines()[1:]:
        columns = line.strip().split()
        if len(columns) >= 2 and columns[1] == "device":
            result.add(columns[0])
    return result


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def append_log(event: str, **details: Any) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"time": now_iso(), "event": event, **details}
    with LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def default_role_health_probe(role: str) -> bool:
    if role not in {"control-api", "control-ui", "device-stream-host"}:
        return True
    url = (
        "http://127.0.0.1:48138/api/config"
        if role == "control-api"
        else "http://127.0.0.1:48139/health"
        if role == "device-stream-host"
        else "http://127.0.0.1:3001/"
    )
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:
            if response.status != 200:
                return False
            if role in {"control-api", "device-stream-host"}:
                return True
            body = response.read(128 * 1024).decode("utf-8", errors="replace")
            asset_prefixes = ("/_next/static/", "/_native/assets/")
            if (
                not any(prefix in body for prefix in asset_prefixes)
                or "/@vite/" in body
                or "/@id/" in body
            ):
                return False
            # A live Vinext process keeps its build manifest in memory. Building
            # into dist while that process is running can replace hashed files
            # on disk while the HTML still points at the old names. The page
            # then looks complete but never hydrates, so every button is inert.
            asset_paths = {
                match
                for match in re.findall(
                    r'(?:src|href)=["\']([^"\']+)["\']', body, flags=re.IGNORECASE
                )
                if match.startswith(asset_prefixes)
                and match.split("?", 1)[0].lower().endswith((".js", ".css"))
            }
            if not asset_paths:
                return False
            for asset_path in sorted(asset_paths):
                asset_url = urllib.parse.urljoin(url, asset_path)
                with urllib.request.urlopen(asset_url, timeout=2.0) as asset_response:
                    if asset_response.status != 200:
                        return False
                    asset_response.read(1)
            return True
    except (OSError, TimeoutError):
        return False


def import_pending_secret() -> bool:
    if not SECRET_TRANSFER_PATH.is_file():
        return False
    powershell = (
        Path(os.environ.get("WINDIR", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(IMPORT_SECRET_SCRIPT),
            "-TransferPath",
            str(SECRET_TRANSFER_PATH),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env=native_windows_powershell_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = " ".join(result.stderr.strip().split())[:240] or "no stderr"
        raise RuntimeError(
            f"后台模型密钥迁移失败 (PowerShell exit {result.returncode}: {detail})"
        )
    append_log("background_secret_imported")
    return True


class SingleInstance:
    def __init__(self) -> None:
        self.handle: int | None = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            raise OSError("无法创建 MediaFlow 后台互斥体")
        if kernel32.GetLastError() == 183:
            kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def close(self) -> None:
        if self.handle and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


@dataclass
class RetryState:
    failures: int = 0
    retry_at: float = 0.0
    last_error: str | None = None


class RuntimeSupervisor:
    """Maintains MediaFlow process availability without controlling devices."""

    def __init__(
        self,
        *,
        control: RuntimeControl | None = None,
        store: TaskStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        heartbeat_path: Path = HEARTBEAT_PATH,
        stop_request_path: Path = STOP_REQUEST_PATH,
        health_probe: Callable[[str], bool] = default_role_health_probe,
        log_event: Callable[..., None] = append_log,
        onboarding: Any | None = None,
        onboarding_interval_seconds: float = 15.0,
        device_discovery: Callable[[], set[str]] = online_adb_device_ids,
    ) -> None:
        self.control = control or RuntimeControl()
        self.store = store or TaskStore(DEFAULT_DB)
        self.clock = clock
        self.heartbeat_path = heartbeat_path
        self.stop_request_path = stop_request_path
        self.health_probe = health_probe
        self.log_event = log_event
        # Kept only as a compatibility injection point for older callers.  The
        # unmanaged root-emulator path is intentionally never executed.
        self.onboarding = onboarding
        self.onboarding_interval_seconds = onboarding_interval_seconds
        self.device_discovery = device_discovery
        self.next_onboarding_at = 0.0
        self.onboarding_summary: dict[str, Any] = {
            "enabled": False,
            "auto_run": False,
            "paused": False,
            "devices": [],
        }
        self.retries: dict[str, RetryState] = {}
        self.health_failures: dict[str, int] = {}
        self.started_at = now_iso()
        self.stop_signal_received = False

    def configured_device_ids(self) -> list[str]:
        saved = self.store.get_profile(PROFILE_NAME)
        if not saved:
            return []
        return normalized_config(saved)["device_ids"]

    def eligible_device_ids(self) -> list[str]:
        """Resolve a saved selection against real hardware and managed VM inventory."""
        requested = self.configured_device_ids()
        online = self.device_discovery()
        managed_ready = {
            str(item.get("adb_endpoint") or "")
            for item in self.store.list_managed_virtual_devices()
            if item.get("managed")
            and item.get("profile_status") == "ready"
            and item.get("state") == "ready"
            and item.get("adb_endpoint")
        }
        eligible: list[str] = []
        for device_id in requested:
            if device_id not in online:
                continue
            is_loopback = device_id.startswith("127.0.0.1:") or device_id.startswith("localhost:")
            if is_loopback and device_id not in managed_ready:
                continue
            eligible.append(device_id)
        return eligible

    def desired_factories(self) -> list[tuple[str, Callable[[], Any]]]:
        factories: list[tuple[str, Callable[[], Any]]] = [
            ("control-api", api_spec),
            ("device-stream-host", stream_spec),
            ("control-ui", ui_spec),
        ]
        if optional_analyzer_spec() is not None:
            factories.append(("incident-analyzer", analyzer_spec))
        for device_id in self.eligible_device_ids():
            if not self.store.is_stop_requested(device_id):
                role = worker_role(device_id)
                factories.append((role, lambda value=device_id: worker_spec(value)))
        return factories

    def stop_undesired_workers(self, desired_roles: set[str]) -> list[dict[str, Any]]:
        """Retire idle workers that no longer belong to the executable inventory."""
        if self.store.running_count():
            return []
        stopped: list[dict[str, Any]] = []
        for role in self.control.roles():
            if not role.startswith("worker-") or role in desired_roles:
                continue
            result = self.control.stop(role)
            stopped.append(result)
            self.log_event("undesired_worker_stopped", role=role)
        return stopped

    @staticmethod
    def retry_delay(failures: int) -> float:
        return min(60.0, float(2 ** min(max(failures, 1), 6)))

    def ensure_role(self, spec: Any) -> dict[str, Any]:
        retry = self.retries.setdefault(spec.role, RetryState())
        if retry.retry_at > self.clock():
            return {
                "role": spec.role,
                "running": False,
                "identity": "backoff",
                "retry_in_seconds": round(retry.retry_at - self.clock(), 1),
                "last_error": retry.last_error,
            }
        try:
            result = self.control.start(spec)
        except Exception as exc:
            retry.failures += 1
            retry.retry_at = self.clock() + self.retry_delay(retry.failures)
            retry.last_error = f"{type(exc).__name__}: {exc}"
            self.log_event(
                "role_start_failed",
                role=spec.role,
                failures=retry.failures,
                retry_in_seconds=self.retry_delay(retry.failures),
                error=retry.last_error,
            )
            return {
                "role": spec.role,
                "running": False,
                "identity": "start_failed",
                "last_error": retry.last_error,
            }
        if result.get("running") and not result.get("changed"):
            try:
                healthy = self.health_probe(spec.role)
            except Exception:
                healthy = False
            if not healthy:
                failures = self.health_failures.get(spec.role, 0) + 1
                self.health_failures[spec.role] = failures
                if failures < 3:
                    return {
                        **result,
                        "identity": "health_pending",
                        "health_failures": failures,
                    }
                try:
                    result = self.control.restart(spec)
                except Exception as exc:
                    retry.failures += 1
                    retry.retry_at = self.clock() + self.retry_delay(retry.failures)
                    retry.last_error = f"{type(exc).__name__}: {exc}"
                    self.log_event(
                        "role_health_restart_failed",
                        role=spec.role,
                        failures=retry.failures,
                        error=retry.last_error,
                    )
                    return {
                        "role": spec.role,
                        "running": False,
                        "identity": "health_restart_failed",
                        "last_error": retry.last_error,
                    }
                self.log_event(
                    "role_health_restarted",
                    role=spec.role,
                    pid=result.get("pid"),
                    failed_probes=failures,
                )
            self.health_failures[spec.role] = 0
        else:
            self.health_failures[spec.role] = 0
        if retry.failures or result.get("changed"):
            self.log_event(
                "role_available",
                role=spec.role,
                pid=result.get("pid"),
                restarted=bool(retry.failures),
            )
        self.retries[spec.role] = RetryState()
        return result

    def ensure_factory(
        self, role: str, factory: Callable[[], Any]
    ) -> dict[str, Any]:
        retry = self.retries.setdefault(role, RetryState())
        if retry.retry_at > self.clock():
            return {
                "role": role,
                "running": False,
                "identity": "backoff",
                "retry_in_seconds": round(retry.retry_at - self.clock(), 1),
                "last_error": retry.last_error,
            }
        try:
            spec = factory()
        except Exception as exc:
            retry.failures += 1
            retry.retry_at = self.clock() + self.retry_delay(retry.failures)
            retry.last_error = f"{type(exc).__name__}: {exc}"
            self.log_event(
                "role_config_failed",
                role=role,
                failures=retry.failures,
                retry_in_seconds=self.retry_delay(retry.failures),
                error=retry.last_error,
            )
            return {
                "role": role,
                "running": False,
                "identity": "config_failed",
                "last_error": retry.last_error,
            }
        return self.ensure_role(spec)

    def tick(self) -> dict[str, Any]:
        if SECRET_TRANSFER_PATH.is_file():
            try:
                import_pending_secret()
            except Exception as exc:
                self.log_event(
                    "background_secret_import_failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
        saved = self.store.get_profile(PROFILE_NAME)
        self.onboarding_summary = {
            "enabled": False,
            "auto_run": False,
            "paused": self.store.is_paused(),
            "devices": [],
            "retired": True,
        }
        try:
            factories = self.desired_factories()
            config_error = None
        except Exception as exc:
            factories = [
                ("control-api", api_spec),
                ("device-stream-host", stream_spec),
                ("control-ui", ui_spec),
            ]
            if optional_analyzer_spec() is not None:
                factories.append(("incident-analyzer", analyzer_spec))
            config_error = f"{type(exc).__name__}: {exc}"
            self.log_event("device_config_failed", error=config_error)
        desired_roles = {role for role, _factory in factories}
        self.stop_undesired_workers(desired_roles)
        processes = [
            self.ensure_factory(role, factory) for role, factory in factories
        ]
        try:
            configured_ids = self.eligible_device_ids()
        except Exception:
            configured_ids = []
        payload = {
            "state": "running",
            "pid": os.getpid(),
            "started_at": self.started_at,
            "checked_at": now_iso(),
            "configured_device_ids": configured_ids,
            "processes": processes,
            "emulator_onboarding": self.onboarding_summary,
        }
        if config_error:
            payload["config_error"] = config_error
        atomic_json_write(self.heartbeat_path, payload)
        return payload

    def request_shutdown(self) -> dict[str, Any]:
        if self.store.running_count():
            try:
                self.stop_request_path.unlink()
            except FileNotFoundError:
                pass
            payload = {
                "state": "stop_refused",
                "pid": os.getpid(),
                "started_at": self.started_at,
                "checked_at": now_iso(),
                "error": "仍有设备任务正在执行",
            }
            atomic_json_write(self.heartbeat_path, payload)
            self.log_event("shutdown_refused", reason="running_tasks")
            return payload

        roles = [role for role in self.control.roles() if role.startswith("worker-")]
        roles.extend(["incident-analyzer", "control-ui", "device-stream-host", "control-api"])
        stopped: list[dict[str, Any]] = []
        for role in dict.fromkeys(roles):
            try:
                stopped.append(self.control.stop(role))
            except Exception as exc:
                stopped.append(
                    {
                        "role": role,
                        "running": True,
                        "identity": "stop_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        payload = {
            "state": "stopped",
            "pid": os.getpid(),
            "started_at": self.started_at,
            "checked_at": now_iso(),
            "processes": stopped,
        }
        atomic_json_write(self.heartbeat_path, payload)
        try:
            self.stop_request_path.unlink()
        except FileNotFoundError:
            pass
        self.log_event("background_host_stopped")
        return payload

    def run(self, interval_seconds: float = 5.0) -> int:
        instance = SingleInstance()
        if not instance.acquire():
            self.log_event("duplicate_background_host_ignored", pid=os.getpid())
            return 0

        def receive_stop(_signum, _frame) -> None:
            self.stop_signal_received = True

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, receive_stop)
        self.log_event("background_host_started", pid=os.getpid())
        try:
            while True:
                if self.stop_request_path.is_file():
                    outcome = self.request_shutdown()
                    if outcome["state"] == "stopped":
                        return 0
                if self.stop_signal_received:
                    self.log_event("background_host_signal_exit", pid=os.getpid())
                    return 0
                try:
                    self.tick()
                except Exception as exc:
                    self.log_event(
                        "supervisor_tick_failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                time.sleep(max(0.2, interval_seconds))
        finally:
            instance.close()


def read_heartbeat(path: Path = HEARTBEAT_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        modified_seconds_ago = max(0.0, time.time() - path.stat().st_mtime)
        payload["heartbeat_age_seconds"] = round(modified_seconds_ago, 1)
        payload["heartbeat_fresh"] = (
            payload.get("state") == "running" and modified_seconds_ago <= 20
        )
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"state": "missing", "heartbeat_fresh": False}


def request_start() -> dict[str, Any]:
    try:
        STOP_REQUEST_PATH.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "requested": "start"}


def request_stop() -> dict[str, Any]:
    store = TaskStore(DEFAULT_DB)
    running = store.running_count()
    if running:
        raise RuntimeError(f"仍有 {running} 个设备任务正在执行，请先安全停止任务")
    atomic_json_write(
        STOP_REQUEST_PATH,
        {"requested_at": now_iso(), "requested_by_pid": os.getpid()},
    )
    return {"ok": True, "requested": "stop"}


def main() -> int:
    parser = argparse.ArgumentParser(description="MediaFlow independent background host")
    parser.add_argument(
        "action", choices=("run", "tick", "status", "request-start", "request-stop")
    )
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    args = parser.parse_args()
    try:
        if args.action == "run":
            return RuntimeSupervisor().run(args.interval_seconds)
        if args.action == "tick":
            result = RuntimeSupervisor().tick()
        elif args.action == "status":
            result = read_heartbeat()
        elif args.action == "request-start":
            result = request_start()
        else:
            result = request_stop()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
