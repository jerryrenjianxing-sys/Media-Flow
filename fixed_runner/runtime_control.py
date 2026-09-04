from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from runtime_layout import (
    APP_ROOT,
    BUNDLED_ADB,
    BUNDLED_NODE,
    BUNDLED_PYTHON,
    IS_DISTRIBUTION,
    RUNTIME_ROOT,
    SECRET_ROOT,
    STREAM_HOST_ENTRY,
    STREAM_HOST_ROOT,
    STREAM_SECRET_PATH,
    STREAM_SERVER_PATH,
    UI_STANDALONE_ROOT,
    UI_STANDALONE_SERVER,
    ensure_stream_secret,
    tool_environment,
)
from model_runtime_config import OPENROUTER_PRIMARY_MODEL, fallback_models_env
from product_version import product_version
from windows_powershell import native_windows_powershell_environment

PROJECT_ROOT = APP_ROOT
FIXED_RUNNER_ROOT = Path(__file__).resolve().parent
PROCESS_ROOT = RUNTIME_ROOT / "processes"
PROJECT_PYTHON = (
    BUNDLED_PYTHON
    if BUNDLED_PYTHON.is_file()
    else PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
)
SECRET_PATH = SECRET_ROOT / "openrouter-api-key.dpapi"
LEGACY_SECRET_PATH = Path(
    r"C:\Users\jerry\Documents\Codex\Tools\Open-AutoGLM\.secrets\openrouter-api-key.dpapi"
)
READ_SECRET_SCRIPT = FIXED_RUNNER_ROOT / "read-openrouter-key.ps1"
BACKGROUND_TASK_NAME = "MediaFlow Background"
LEGACY_BACKGROUND_TASK_NAME = "RiskFlow Background"
BACKGROUND_HEARTBEAT_PATH = RUNTIME_ROOT / "background-host.json"


def project_python_executable() -> Path:
    if BUNDLED_PYTHON.is_file():
        return BUNDLED_PYTHON.resolve()
    config = PROJECT_ROOT / ".venv" / "pyvenv.cfg"
    try:
        values = {
            key.strip().casefold(): value.strip()
            for line in config.read_text(encoding="utf-8").splitlines()
            if "=" in line
            for key, value in [line.split("=", 1)]
        }
        home = Path(values["home"])
        executable = home / ("python.exe" if os.name == "nt" else "bin/python")
        if executable.is_file():
            return executable.resolve()
    except (OSError, KeyError, ValueError):
        pass
    return PROJECT_PYTHON.resolve()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def safe_role_name(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "-" for character in value)
    return normalized.strip("-") or "process"


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    executable: str
    creation_token: str
    command_line: str | None = None


@dataclass(frozen=True)
class ProcessSpec:
    role: str
    command: tuple[str, ...]
    cwd: str
    log_path: str
    expected_executable: str
    required_markers: tuple[str, ...] = ()
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class ProcessRecord:
    role: str
    pid: int
    executable: str
    creation_token: str
    command: tuple[str, ...]
    cwd: str
    required_markers: tuple[str, ...]
    started_at: str


class SystemProcessInspector:
    """Read-only process identity adapter used by RuntimeControl."""

    def snapshot(self, pid: int) -> ProcessSnapshot | None:
        if pid <= 0:
            return None
        if os.name == "nt":
            return self._windows_snapshot(pid)
        return self._proc_snapshot(pid)

    def child_pids(self, parent_pid: int) -> list[int]:
        if os.name == "nt":
            return self._windows_child_pids(parent_pid)
        children: list[int] = []
        for item in Path("/proc").iterdir():
            if not item.name.isdigit():
                continue
            try:
                stat = (item / "stat").read_text(encoding="utf-8").split()
                if int(stat[3]) == parent_pid:
                    children.append(int(item.name))
            except (OSError, ValueError, IndexError):
                continue
        return children

    @staticmethod
    def _windows_child_pids(parent_pid: int) -> list[int]:
        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
            return []
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        result: list[int] = []
        try:
            success = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while success:
                if int(entry.th32ParentProcessID) == parent_pid:
                    result.append(int(entry.th32ProcessID))
                success = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return result

    @staticmethod
    def _windows_snapshot(pid: int) -> ProcessSnapshot | None:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return None
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel_time = ctypes.c_ulonglong()
            user_time = ctypes.c_ulonglong()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            return ProcessSnapshot(
                pid=pid,
                executable=str(Path(buffer.value).resolve()),
                creation_token=str(creation.value),
                command_line=None,
            )
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def _windows_command_line(pid: int) -> str | None:
        powershell = (
            Path(os.environ.get("WINDIR", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        command = (
            "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = "
            + str(pid)
            + "\" -ErrorAction Stop; [Console]::Out.Write($p.CommandLine)"
        )
        try:
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    @staticmethod
    def _proc_snapshot(pid: int) -> ProcessSnapshot | None:
        root = Path("/proc") / str(pid)
        try:
            executable = str((root / "exe").resolve())
            stat = (root / "stat").read_text(encoding="utf-8").split()
            command = (root / "cmdline").read_bytes().replace(b"\0", b" ").decode()
            return ProcessSnapshot(pid, executable, stat[21], command)
        except (OSError, IndexError, UnicodeDecodeError):
            return None


def terminate_process(pid: int, timeout_seconds: float = 8.0) -> None:
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
        if not handle:
            raise RuntimeError(f"无法打开进程 {pid}")
        try:
            if not kernel32.TerminateProcess(handle, 0):
                raise RuntimeError(f"无法停止进程 {pid}")
            wait_ms = max(1, int(timeout_seconds * 1000))
            if kernel32.WaitForSingleObject(handle, wait_ms) == 0x00000102:
                raise RuntimeError(f"进程 {pid} 未在限定时间内退出")
        finally:
            kernel32.CloseHandle(handle)
        return
    os.kill(pid, 15)


class RuntimeControl:
    """Deep module for verified project process lifecycle."""

    def __init__(
        self,
        registry_root: Path = PROCESS_ROOT,
        *,
        inspector: SystemProcessInspector | Any | None = None,
        launcher: Callable[[ProcessSpec], int] | None = None,
        terminator: Callable[[int], None] | None = None,
    ) -> None:
        self.registry_root = registry_root
        self.inspector = inspector or SystemProcessInspector()
        self.launcher = launcher or self._launch
        self.terminator = terminator or terminate_process

    def _record_path(self, role: str) -> Path:
        return self.registry_root / f"{safe_role_name(role)}.json"

    def _read_record(self, role: str) -> ProcessRecord | None:
        try:
            raw = json.loads(self._record_path(role).read_text(encoding="utf-8"))
            return ProcessRecord(
                role=str(raw["role"]),
                pid=int(raw["pid"]),
                executable=str(raw["executable"]),
                creation_token=str(raw["creation_token"]),
                command=tuple(str(value) for value in raw["command"]),
                cwd=str(raw["cwd"]),
                required_markers=tuple(str(value) for value in raw["required_markers"]),
                started_at=str(raw["started_at"]),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def _write_record(self, record: ProcessRecord) -> None:
        self.registry_root.mkdir(parents=True, exist_ok=True)
        path = self._record_path(record.role)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(record), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _remove_record(self, role: str) -> None:
        try:
            self._record_path(role).unlink()
        except FileNotFoundError:
            pass

    def roles(self) -> list[str]:
        if not self.registry_root.is_dir():
            return []
        roles: list[str] = []
        for path in sorted(self.registry_root.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                role = str(raw["role"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            if role not in roles:
                roles.append(role)
        return roles

    @staticmethod
    def _identity_matches(record: ProcessRecord, snapshot: ProcessSnapshot) -> tuple[bool, str]:
        expected = os.path.normcase(os.path.abspath(record.executable))
        observed = os.path.normcase(os.path.abspath(snapshot.executable))
        if expected != observed:
            return False, "executable_mismatch"
        if record.creation_token != snapshot.creation_token:
            return False, "creation_mismatch"
        if snapshot.command_line:
            command_line = snapshot.command_line.casefold()
            missing = [
                marker for marker in record.required_markers
                if marker.casefold() not in command_line
            ]
            if missing:
                return False, "command_mismatch"
        return True, "verified"

    def status(self, role: str) -> dict[str, Any]:
        record = self._read_record(role)
        if record is None:
            return {"role": role, "running": False, "pid": None, "identity": "missing"}
        snapshot = self.inspector.snapshot(record.pid)
        if snapshot is None:
            self._remove_record(role)
            return {"role": role, "running": False, "pid": None, "identity": "exited"}
        matches, reason = self._identity_matches(record, snapshot)
        return {
            "role": role,
            "running": matches,
            "pid": record.pid,
            "identity": reason,
            "started_at": record.started_at,
        }

    def start(self, spec: ProcessSpec) -> dict[str, Any]:
        current = self.status(spec.role)
        if current["running"]:
            return {**current, "changed": False}
        if current.get("identity") not in {"missing", "exited"}:
            raise RuntimeError(
                f"{spec.role} 的 PID 记录与当前进程不匹配，已拒绝覆盖"
            )
        pid = self.launcher(spec)
        snapshot = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            snapshot = self.inspector.snapshot(pid)
            if snapshot is not None:
                break
            time.sleep(0.05)
        if snapshot is None:
            raise RuntimeError(f"{spec.role} 启动后无法核验进程身份")
        expected = os.path.normcase(os.path.abspath(spec.expected_executable))
        observed = os.path.normcase(os.path.abspath(snapshot.executable))
        if expected != observed:
            self.terminator(pid)
            raise RuntimeError(f"{spec.role} 启动了意外的可执行程序")
        record = ProcessRecord(
            role=spec.role,
            pid=pid,
            executable=snapshot.executable,
            creation_token=snapshot.creation_token,
            command=spec.command,
            cwd=spec.cwd,
            required_markers=spec.required_markers,
            started_at=now_iso(),
        )
        self._write_record(record)
        return {**self.status(spec.role), "changed": True}

    def stop(self, role: str) -> dict[str, Any]:
        current = self.status(role)
        if not current["running"]:
            if current.get("identity") not in {"missing", "exited"}:
                raise RuntimeError(f"{role} 进程身份核验失败，已拒绝停止")
            return {**current, "changed": False}
        self.terminator(int(current["pid"]))
        self._remove_record(role)
        return {"role": role, "running": False, "pid": None, "identity": "stopped", "changed": True}

    def restart(self, spec: ProcessSpec) -> dict[str, Any]:
        self.stop(spec.role)
        return self.start(spec)

    def _launch(self, spec: ProcessSpec) -> int:
        log_path = Path(spec.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab", buffering=0)
        try:
            process = subprocess.Popen(
                list(spec.command),
                cwd=spec.cwd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=spec.env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            log.close()
        expected = os.path.normcase(os.path.abspath(spec.expected_executable))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            # Prefer the process launched directly by Popen. Some Node CLIs
            # briefly create another node.exe child; tracking that helper
            # leaves the real port listener orphaned when the role is stopped.
            parent = self.inspector.snapshot(process.pid)
            if parent and os.path.normcase(os.path.abspath(parent.executable)) == expected:
                return process.pid
            child_pids = getattr(self.inspector, "child_pids", lambda _pid: [])(process.pid)
            for child_pid in child_pids:
                child = self.inspector.snapshot(child_pid)
                if child and os.path.normcase(os.path.abspath(child.executable)) == expected:
                    return child_pid
            if process.poll() is not None:
                break
            time.sleep(0.05)
        return process.pid


def migrate_secret() -> dict[str, Any]:
    if SECRET_PATH.is_file() and SECRET_PATH.stat().st_size:
        return {"configured": True, "migrated": False, "path": str(SECRET_PATH)}
    # A distributed build must never reach back into a developer workstation.
    # New installations receive their key through the local control console.
    if IS_DISTRIBUTION:
        return {"configured": False, "migrated": False, "path": str(SECRET_PATH)}
    if not LEGACY_SECRET_PATH.is_file() or not LEGACY_SECRET_PATH.stat().st_size:
        return {"configured": False, "migrated": False, "path": str(SECRET_PATH)}
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LEGACY_SECRET_PATH, SECRET_PATH)
    return {"configured": True, "migrated": True, "path": str(SECRET_PATH)}


def _decrypt_openrouter_key(path: Path) -> str:
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
            str(READ_SECRET_SCRIPT),
            "-Path",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=native_windows_powershell_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    value = result.stdout.strip()
    if result.returncode != 0:
        detail = " ".join(result.stderr.strip().split())[:240] or "no stderr"
        raise RuntimeError(
            f"OpenRouter Key 解密失败 (PowerShell exit {result.returncode}: {detail})"
        )
    if not value.startswith("sk-or-v1-"):
        raise RuntimeError(
            f"OpenRouter Key 解密失败 (unexpected output length {len(value)})"
        )
    return value


def load_openrouter_key() -> str:
    state = migrate_secret()
    if not state["configured"]:
        raise RuntimeError("尚未配置 OpenRouter Key")

    candidates = [SECRET_PATH]
    # A developer checkout may retain a working legacy DPAPI blob after the
    # copied project-local blob becomes unreadable (for example, after a
    # startup-context change). A distributed build must remain self-contained.
    if (
        not IS_DISTRIBUTION
        and LEGACY_SECRET_PATH != SECRET_PATH
        and LEGACY_SECRET_PATH.is_file()
        and LEGACY_SECRET_PATH.stat().st_size
    ):
        candidates.append(LEGACY_SECRET_PATH)

    errors: list[str] = []
    for candidate in candidates:
        try:
            return _decrypt_openrouter_key(candidate)
        except RuntimeError as exc:
            errors.append(str(exc))
    raise RuntimeError(errors[0])


def api_spec() -> ProcessSpec:
    return ProcessSpec(
        role="control-api",
        command=(str(PROJECT_PYTHON), str(FIXED_RUNNER_ROOT / "control_api.py")),
        cwd=str(FIXED_RUNNER_ROOT),
        log_path=str(RUNTIME_ROOT / "control-api.log"),
        expected_executable=str(project_python_executable()),
        required_markers=("control_api.py",),
        env=tool_environment(),
    )


def ui_spec() -> ProcessSpec:
    node = str(BUNDLED_NODE) if BUNDLED_NODE.is_file() else (
        shutil.which("node.exe") or shutil.which("node")
    )
    if not node:
        raise RuntimeError("未找到 Node.js")
    environment = tool_environment()
    # Vinext beta does not reliably infer production mode from the `start`
    # subcommand. Without this flag it serves Vite HMR modules, whose console
    # forwarding transport can fail before its websocket is connected.
    environment["NODE_ENV"] = "production"
    if UI_STANDALONE_SERVER.is_file():
        command = (str(node), str(UI_STANDALONE_SERVER))
        cwd = UI_STANDALONE_ROOT
        markers = ("server.js",)
        environment.update({"HOST": "127.0.0.1", "PORT": "3000"})
    else:
        cli = PROJECT_ROOT / "control_console" / "node_modules" / "vinext" / "dist" / "cli.js"
        command = (str(node), str(cli), "start", "--host", "127.0.0.1", "--port", "3000")
        cwd = PROJECT_ROOT / "control_console"
        markers = ("vinext", "3000")
    return ProcessSpec(
        role="control-ui",
        command=command,
        cwd=str(cwd),
        log_path=str(RUNTIME_ROOT / "control-ui.log"),
        expected_executable=str(Path(node).resolve()),
        required_markers=markers,
        env=environment,
    )


def stream_spec() -> ProcessSpec:
    node = str(BUNDLED_NODE) if BUNDLED_NODE.is_file() else (
        shutil.which("node.exe") or shutil.which("node")
    )
    if not node:
        raise RuntimeError("未找到 Node.js")
    if not STREAM_HOST_ENTRY.is_file():
        raise RuntimeError("实时画面模块尚未安装")
    if not STREAM_SERVER_PATH.is_file():
        raise RuntimeError("缺少匹配的 scrcpy server 3.3.3")
    server_hash = hashlib.sha256(STREAM_SERVER_PATH.read_bytes()).hexdigest()
    if server_hash != "7e70323ba7f259649dd4acce97ac4fefbae8102b2c6d91e2e7be613fd5354be0":
        raise RuntimeError("scrcpy server 校验失败，已阻止不匹配版本启动")
    ensure_stream_secret()
    environment = tool_environment()
    environment.update(
        {
            "MEDIAFLOW_STREAM_HOST": "127.0.0.1",
            "MEDIAFLOW_STREAM_PORT": "48139",
            "MEDIAFLOW_STREAM_API": "http://127.0.0.1:48138",
            "MEDIAFLOW_STREAM_SECRET_PATH": str(STREAM_SECRET_PATH),
            "MEDIAFLOW_SCRCPY_SERVER": str(STREAM_SERVER_PATH),
            "RISKFLOW_STREAM_HOST": "127.0.0.1",
            "RISKFLOW_STREAM_PORT": "48139",
            "RISKFLOW_STREAM_API": "http://127.0.0.1:48138",
            "RISKFLOW_STREAM_SECRET_PATH": str(STREAM_SECRET_PATH),
            "RISKFLOW_SCRCPY_SERVER": str(STREAM_SERVER_PATH),
        }
    )
    return ProcessSpec(
        role="device-stream-host",
        command=(str(node), str(STREAM_HOST_ENTRY)),
        cwd=str(STREAM_HOST_ROOT),
        log_path=str(RUNTIME_ROOT / "device-stream-host.log"),
        expected_executable=str(Path(node).resolve()),
        required_markers=("server.mjs", "48139"),
        env=environment,
    )


def analyzer_spec() -> ProcessSpec:
    analyzer_env = tool_environment()
    analyzer_env.update(
        {
            "PHONE_AGENT_API_KEY": load_openrouter_key(),
            "PHONE_AGENT_BASE_URL": "https://openrouter.ai/api/v1",
            "PHONE_AGENT_COMMENT_MODEL": OPENROUTER_PRIMARY_MODEL,
            "PHONE_AGENT_COMMENT_FALLBACK_MODELS": fallback_models_env(),
        }
    )
    return ProcessSpec(
        role="incident-analyzer",
        command=(
            str(PROJECT_PYTHON),
            str(FIXED_RUNNER_ROOT / "incident_analyzer.py"),
        ),
        cwd=str(FIXED_RUNNER_ROOT),
        log_path=str(RUNTIME_ROOT / "incident-analyzer.log"),
        expected_executable=str(project_python_executable()),
        required_markers=("incident_analyzer.py",),
        env=analyzer_env,
    )


def optional_analyzer_spec() -> ProcessSpec | None:
    """The advisory analyzer is optional until a model key is available."""
    try:
        return analyzer_spec()
    except RuntimeError as exc:
        if "OpenRouter Key" in str(exc):
            return None
        raise


def worker_role(device_id: str) -> str:
    return f"worker-{safe_role_name(device_id)}"


def worker_spec(device_id: str) -> ProcessSpec:
    worker_env = tool_environment()
    worker_env.update(
        {
            "PHONE_AGENT_BASE_URL": "https://openrouter.ai/api/v1",
            "PHONE_AGENT_COMMENT_MODEL": OPENROUTER_PRIMARY_MODEL,
            "PHONE_AGENT_COMMENT_FALLBACK_MODELS": fallback_models_env(),
        }
    )
    try:
        worker_env["PHONE_AGENT_API_KEY"] = load_openrouter_key()
    except RuntimeError as exc:
        if "OpenRouter Key" not in str(exc):
            raise
    return ProcessSpec(
        role=worker_role(device_id),
        command=(
            str(PROJECT_PYTHON),
            str(FIXED_RUNNER_ROOT / "worker.py"),
            "worker",
            "--device-id",
            device_id,
        ),
        cwd=str(FIXED_RUNNER_ROOT),
        log_path=str(RUNTIME_ROOT / f"control-{worker_role(device_id)}.log"),
        expected_executable=str(project_python_executable()),
        required_markers=("worker.py", device_id),
        env=worker_env,
    )


def background_task_check() -> tuple[bool, str]:
    """Require the scheduled host only for a formal installed release.

    Source checkouts and development-stage packages are intentionally runnable
    without registering a Windows task. Their live heartbeat is checked
    separately below, so treating the missing task as a fatal diagnostic makes
    an otherwise healthy development runtime look broken.
    """
    if os.name != "nt":
        return True, "not_required_on_this_platform"
    if str(product_version().get("channel") or "development") != "release":
        return True, "not_required_in_development"
    schtasks = shutil.which("schtasks.exe") or str(
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "schtasks.exe"
    )
    task = subprocess.run(
        [schtasks, "/Query", "/TN", BACKGROUND_TASK_NAME],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return task.returncode == 0, "registered" if task.returncode == 0 else "not_registered"


def doctor() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("project_python", PROJECT_PYTHON.is_file(), str(PROJECT_PYTHON))
    for module in ("PIL", "requests", "uiautomator2"):
        try:
            __import__(module)
            add(f"python:{module}", True, "available")
        except Exception as exc:
            add(f"python:{module}", False, type(exc).__name__)
    node = str(BUNDLED_NODE) if BUNDLED_NODE.is_file() else (
        shutil.which("node.exe") or shutil.which("node")
    )
    add("node", bool(node), str(node or "missing"))
    frontend_dependencies = (
        (UI_STANDALONE_ROOT / "node_modules").is_dir()
        if UI_STANDALONE_SERVER.is_file()
        else (PROJECT_ROOT / "control_console" / "node_modules").is_dir()
    )
    frontend_build = UI_STANDALONE_SERVER.is_file() or (
        PROJECT_ROOT / "control_console" / "dist"
    ).is_dir()
    add("frontend_dependencies", frontend_dependencies, "standalone" if UI_STANDALONE_SERVER.is_file() else "node_modules")
    add("frontend_build", frontend_build, "standalone" if UI_STANDALONE_SERVER.is_file() else "dist")
    stream_manifest = STREAM_HOST_ROOT / "runtime-manifest.json"
    stream_modules = STREAM_HOST_ROOT / "node_modules"
    stream_ok = STREAM_HOST_ENTRY.is_file() and stream_manifest.is_file() and stream_modules.is_dir()
    add("device_stream_host", stream_ok, str(STREAM_HOST_ROOT))
    expected_server_hash = "7e70323ba7f259649dd4acce97ac4fefbae8102b2c6d91e2e7be613fd5354be0"
    actual_server_hash = "missing"
    if STREAM_SERVER_PATH.is_file():
        actual_server_hash = hashlib.sha256(STREAM_SERVER_PATH.read_bytes()).hexdigest()
    add("scrcpy_server_3_3_3", actual_server_hash == expected_server_hash, actual_server_hash)
    adb = str(BUNDLED_ADB) if BUNDLED_ADB.is_file() else (
        shutil.which("adb.exe") or shutil.which("adb")
    )
    add("adb", bool(adb), str(adb or "missing"))
    secret = migrate_secret()
    add("openrouter_key", bool(secret["configured"]), "configured" if secret["configured"] else "missing")
    task_ok, task_detail = background_task_check()
    add("background_task", task_ok, task_detail)
    try:
        heartbeat = json.loads(BACKGROUND_HEARTBEAT_PATH.read_text(encoding="utf-8"))
        age = max(0.0, time.time() - BACKGROUND_HEARTBEAT_PATH.stat().st_mtime)
        fresh = heartbeat.get("state") == "running" and age <= 20
        detail = f"{heartbeat.get('state', 'unknown')}; age={age:.1f}s"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        fresh = False
        detail = "missing"
    add("background_heartbeat", fresh, detail)
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="MediaFlow verified runtime control")
    parser.add_argument(
        "action",
        choices=("doctor", "status", "start", "stop", "restart", "refresh-ui"),
    )
    args = parser.parse_args()
    if args.action == "doctor":
        result = doctor()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    control = RuntimeControl()
    if args.action == "status":
        # Status is deliberately read-only.  Building the launch specs would
        # decrypt the OpenRouter key even though no process is being started,
        # and can make a healthy runtime look broken under another session.
        roles = list(
            dict.fromkeys(
                ["control-api", "incident-analyzer", "control-ui", "device-stream-host", *control.roles()]
            )
        )
        result = [control.status(role) for role in roles]
        print(json.dumps({"ok": True, "processes": result}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "refresh-ui":
        current = control.status("control-ui")
        result = control.restart(ui_spec()) if current.get("running") else current
        print(json.dumps({"ok": True, "processes": [result]}, ensure_ascii=False, indent=2))
        return 0

    optional_analyzer = optional_analyzer_spec()
    specs = tuple(
        spec
        for spec in (api_spec(), optional_analyzer, ui_spec(), stream_spec())
        if spec is not None
    )
    if args.action == "start":
        result = [control.start(spec) for spec in specs]
    elif args.action == "stop":
        from task_store import TaskStore
        from worker import DEFAULT_DB

        if TaskStore(DEFAULT_DB).running_count():
            raise RuntimeError("仍有任务正在执行，请先安全停止任务")
        roles = [role for role in control.roles() if role.startswith("worker-")]
        roles.extend([spec.role for spec in reversed(specs)])
        result = [control.stop(role) for role in dict.fromkeys(roles)]
    else:
        from task_store import TaskStore
        from worker import DEFAULT_DB

        if TaskStore(DEFAULT_DB).running_count():
            raise RuntimeError("仍有任务正在执行，请先安全停止任务")
        roles = [role for role in control.roles() if role.startswith("worker-")]
        roles.extend([spec.role for spec in reversed(specs)])
        stopped = [control.stop(role) for role in dict.fromkeys(roles)]
        result = [*stopped, *(control.start(spec) for spec in specs)]
    print(json.dumps({"ok": True, "processes": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
