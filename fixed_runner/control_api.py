from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from task_store import TaskStore
from worker import DEFAULT_ARTIFACTS, DEFAULT_DB, DEFAULT_DEVICE_ID


HOST = "127.0.0.1"
PORT = 48138
PROFILE_NAME = "default"
RUNTIME_ROOT = Path(__file__).resolve().parent / "runtime"
WORKER_PID = RUNTIME_ROOT / "control-worker.pid"
WORKER_LOG = RUNTIME_ROOT / "control-worker.log"
OPENROUTER_KEY_PATH = Path(
    r"C:\Users\jerry\Documents\Codex\Tools\Open-AutoGLM\.secrets\openrouter-api-key.dpapi"
)
OPENROUTER_KEY_STDIN_SCRIPT = Path(__file__).resolve().parent / "set-openrouter-key-from-stdin.ps1"

DEFAULT_CONFIG: dict[str, Any] = {
    "device_id": "emulator-5556",
    "device_ids": [
        "emulator-5556",
        "127.0.0.1:16448",
        "127.0.0.1:16480",
        "127.0.0.1:16512",
        "127.0.0.1:16544",
    ],
    "video_count": 20,
    "round_count": 1,
    "round_interval_minutes": 0,
    "dwell_min": 6.0,
    "dwell_max": 15.0,
    "like_probability": 0.20,
    "favorite_probability": 0.10,
    "comment_probability": 0.05,
    "topic_prompt": "不限主题",
    "topic_filter_enabled": False,
    "topic_confidence": 0.78,
    "like_only_on_match": False,
    "engagement_requires_topic": False,
    "comment_requires_topic": False,
    "preview_only": True,
    "seed": 20260821,
    "max_gate_skips": 6,
    "max_likes": 4,
    "max_favorites": 2,
    "max_comments": 1,
}


def normalized_config(raw: dict[str, Any]) -> dict[str, Any]:
    config = {**DEFAULT_CONFIG, **raw}
    raw_device_ids = raw.get("device_ids")
    if not isinstance(raw_device_ids, list):
        raw_device_ids = [raw.get("device_id", DEFAULT_DEVICE_ID)]
    device_ids = list(
        dict.fromkeys(str(value).strip() for value in raw_device_ids if str(value).strip())
    )
    if not 1 <= len(device_ids) <= 8:
        raise ValueError("请选择 1 到 8 台设备")
    config["device_ids"] = device_ids
    config["device_id"] = device_ids[0]
    config["topic_prompt"] = str(config["topic_prompt"]).strip()[:300]
    config["video_count"] = int(config["video_count"])
    config["round_count"] = int(config["round_count"])
    config["round_interval_minutes"] = int(config["round_interval_minutes"])
    config["dwell_min"] = float(config["dwell_min"])
    config["dwell_max"] = float(config["dwell_max"])
    for name in ("like_probability", "favorite_probability", "comment_probability", "topic_confidence"):
        config[name] = float(config[name])
    config["seed"] = int(config["seed"])
    config["max_gate_skips"] = int(config["max_gate_skips"])
    for name in ("max_likes", "max_favorites", "max_comments"):
        config[name] = int(config[name])
    config["preview_only"] = bool(config["preview_only"])
    legacy_topic_filter = bool(config["topic_filter_enabled"])
    engagement_requires_topic = bool(
        raw.get("engagement_requires_topic", legacy_topic_filter)
    )
    comment_requires_topic = bool(
        raw.get("comment_requires_topic", legacy_topic_filter)
    )
    config["engagement_requires_topic"] = engagement_requires_topic
    config["comment_requires_topic"] = comment_requires_topic
    # Keep the old fields in saved payloads so existing runners remain compatible.
    config["like_only_on_match"] = engagement_requires_topic
    config["topic_filter_enabled"] = (
        engagement_requires_topic or comment_requires_topic
    )
    TaskStore.validate_payload("douyin_topic_session", config)
    return config


def validate_openrouter_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key.startswith("sk-or-v1-") or not 32 <= len(key) <= 512:
        raise ValueError("请输入有效的 OpenRouter API Key")
    return key


def openrouter_key_status() -> dict[str, Any]:
    configured = OPENROUTER_KEY_PATH.is_file() and OPENROUTER_KEY_PATH.stat().st_size > 0
    return {
        "provider": "OpenRouter",
        "model": "google/gemini-3.1-flash-lite",
        "key_configured": configured,
    }


def save_openrouter_key(value: Any) -> None:
    key = validate_openrouter_key(value)
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
            str(OPENROUTER_KEY_STDIN_SCRIPT),
        ],
        input=key,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError("OpenRouter Key 加密保存失败")


def submit_scheduled_rounds(store: TaskStore, config: dict[str, Any]) -> list[str]:
    """Queue independently auditable rounds with deterministic, distinct seeds."""
    base_time = datetime.now().astimezone()
    base_seed = int(config["seed"])
    interval = int(config["round_interval_minutes"])
    task_ids: list[str] = []
    round_count = int(config["round_count"])
    for device_offset, device_id in enumerate(config["device_ids"]):
        for index in range(round_count):
            round_config = {
                **config,
                "device_id": device_id,
                "round_index": index + 1,
                "seed": base_seed + device_offset * round_count + index,
            }
            not_before = (base_time + timedelta(minutes=index * interval)).isoformat(
                timespec="milliseconds"
            )
            task_ids.append(
                store.submit(
                    "douyin_topic_session",
                    device_id,
                    round_config,
                    not_before=not_before,
                )
            )
    return task_ids


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
    pid_path = _worker_pid_path(device_id)
    if not pid_path.exists() and device_id == DEFAULT_DEVICE_ID:
        pid_path = WORKER_PID
    try:
        pid = int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return {"device_id": device_id, "running": False, "pid": None}
    running = _pid_is_running(pid)
    if running and os.name == "nt":
        running = _process_image_name(pid) in {"powershell.exe", "pwsh.exe"}
    return {"device_id": device_id, "running": running, "pid": pid}


def ensure_worker(device_id: str) -> dict[str, Any]:
    status = worker_status(device_id)
    if status["running"]:
        return status
    script = Path(__file__).resolve().parent / "run-worker-openrouter-secure.ps1"
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = RUNTIME_ROOT / f"control-worker-{_safe_device_name(device_id)}.log"
    log = log_path.open("ab", buffering=0)
    worker_env = os.environ.copy()
    windows_modules = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "Modules")
    current_module_path = worker_env.get("PSModulePath", "")
    if windows_modules.lower() not in current_module_path.lower():
        worker_env["PSModulePath"] = windows_modules + os.pathsep + current_module_path
    powershell = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    process = subprocess.Popen(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "worker",
            "--device-id",
            device_id,
        ],
        cwd=str(script.parent), stdin=subprocess.DEVNULL, stdout=log,
        stderr=subprocess.STDOUT, env=worker_env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _worker_pid_path(device_id).write_text(str(process.pid), encoding="ascii")
    return {"device_id": device_id, "running": True, "pid": process.pid}


def ensure_workers(device_ids: list[str]) -> list[dict[str, Any]]:
    return [ensure_worker(device_id) for device_id in device_ids]


def device_statuses(configured_ids: list[str]) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["adb.exe", "devices"], capture_output=True, text=True, timeout=6,
            check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        states: dict[str, str] = {}
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                states[parts[0]] = parts[1]
        ordered_ids = list(dict.fromkeys([*configured_ids, *states.keys()]))
        return [
            {"device_id": device_id, "state": states.get(device_id, "offline")}
            for device_id in ordered_ids
        ]
    except (OSError, subprocess.SubprocessError) as exc:
        return [
            {"device_id": device_id, "state": "unknown", "detail": str(exc)}
            for device_id in configured_ids
        ]


def build_status_payload(store: TaskStore, config: dict[str, Any]) -> dict[str, Any]:
    reconciled_tasks = store.reconcile_orphaned_running(worker_id_is_running)
    devices = device_statuses(config["device_ids"])
    workers = [worker_status(device_id) for device_id in config["device_ids"]]
    incident_summary = {
        "total": 0,
        "queued": 0,
        "recovered": 0,
        "skipped": 0,
        "device_fatal": 0,
        **store.incident_statistics(),
    }
    return {
        "device": devices[0],
        "devices": devices,
        "worker": workers[0],
        "workers": workers,
        "paused": store.is_paused(),
        "reconciled_tasks": reconciled_tasks,
        "task_summary": store.task_status_counts(),
        "tasks": [asdict(task) for task in store.list(12)],
        "incidents": [asdict(incident) for incident in store.list_incidents(8)],
        "incident_summary": incident_summary,
    }


class Handler(BaseHTTPRequestHandler):
    store = TaskStore(DEFAULT_DB)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stdout.write((format % args) + "\n")

    def _headers(self, status: int = 200, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:3000")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _json(self, payload: Any, status: int = 200) -> None:
        self._headers(status)
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100_000:
            raise ValueError("Request body is too large")
        value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._headers(204)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/config":
            self._json(normalized_config(self.store.get_profile(PROFILE_NAME) or {}))
            return
        if path == "/api/model":
            self._json(openrouter_key_status())
            return
        if path == "/api/status":
            config = normalized_config(self.store.get_profile(PROFILE_NAME) or {})
            self._json(build_status_payload(self.store, config))
            return
        if path == "/api/latest-image":
            images = sorted(DEFAULT_ARTIFACTS.rglob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True)
            if not images:
                self._json({"error": "No screenshot available"}, 404)
                return
            self._headers(200, "image/png")
            self.wfile.write(images[0].read_bytes())
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
            self.wfile.write(image_path.read_bytes())
            return
        self._json({"error": "Not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            path = urlparse(self.path).path
            if path == "/api/config":
                config = normalized_config(body)
                self.store.save_profile(PROFILE_NAME, config)
                self._json({"ok": True, "config": config})
                return
            if path == "/api/model-key":
                save_openrouter_key(body.get("api_key"))
                self._json({"ok": True, **openrouter_key_status()})
                return
            if path == "/api/pause":
                self.store.set_paused(True)
                self._json({"ok": True, "paused": True})
                return
            if path == "/api/resume":
                self.store.set_paused(False)
                self._json({"ok": True, "paused": False})
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
                self.store.save_profile(PROFILE_NAME, config)
                task_ids = submit_scheduled_rounds(self.store, config)
                workers = ensure_workers(config["device_ids"])
                self._json(
                    {
                        "ok": True,
                        "task_id": task_ids[0],
                        "task_ids": task_ids,
                        "count": len(task_ids),
                        "worker": workers[0],
                        "workers": workers,
                    },
                    202,
                )
                return
            self._json({"error": "Not found"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(json.dumps({"event": "control_api_ready", "url": f"http://{HOST}:{PORT}"}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
