from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from task_store import TaskStore
from worker import DEFAULT_ARTIFACTS, DEFAULT_DB
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
from device_profiles import enrich_device_statuses
from runtime_control import (
    PROJECT_PYTHON,
    RuntimeControl,
    worker_role,
    worker_spec,
)


HOST = "127.0.0.1"
PORT = 48138
PROFILE_NAME = "default"
RUNTIME_ROOT = Path(__file__).resolve().parent / "runtime"
WORKER_PID = RUNTIME_ROOT / "control-worker.pid"
WORKER_LOG = RUNTIME_ROOT / "control-worker.log"


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


def ensure_worker(device_id: str) -> dict[str, Any]:
    status = worker_status(device_id)
    if status["running"]:
        return status
    if not PROJECT_PYTHON.is_file():
        raise RuntimeError("RiskFlow 独立 Python 环境尚未安装")
    started = RuntimeControl().start(worker_spec(device_id))
    return {"device_id": device_id, **started}


def stop_worker(store: TaskStore, device_id: str) -> dict[str, Any]:
    if store.running_count([device_id]):
        raise ValueError("该设备仍有任务正在执行，请先请求安全停止并等待任务结束")
    stopped = RuntimeControl().stop(worker_role(device_id))
    return {"device_id": device_id, **stopped}


def restart_worker(store: TaskStore, device_id: str) -> dict[str, Any]:
    if store.running_count([device_id]):
        raise ValueError("该设备仍有任务正在执行，不能重启 Worker")
    RuntimeControl().stop(worker_role(device_id))
    store.clear_stop_requests([device_id])
    return ensure_worker(device_id)


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
        return enrich_device_statuses([
            {"device_id": device_id, "state": states.get(device_id, "offline")}
            for device_id in ordered_ids
        ])
    except (OSError, subprocess.SubprocessError) as exc:
        return enrich_device_statuses([
            {"device_id": device_id, "state": "unknown", "detail": str(exc)}
            for device_id in configured_ids
        ])


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
        "stop_requested_device_ids": [
            device_id for device_id in config["device_ids"]
            if store.is_stop_requested(device_id)
        ],
        "reconciled_tasks": reconciled_tasks,
        "task_summary": store.task_status_counts(),
        "tasks": [asdict(task) for task in store.list(5)],
        "incidents": [asdict(incident) for incident in store.list_incidents(5)],
        "incident_summary": incident_summary,
    }


def paged_records_payload(
    store: TaskStore, record_type: str, limit: int, offset: int
) -> dict[str, Any]:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1-100 and offset must be non-negative")
    if record_type == "tasks":
        items = [asdict(task) for task in store.list(limit, offset)]
        total = sum(store.task_status_counts().values())
    elif record_type == "incidents":
        items = [asdict(incident) for incident in store.list_incidents(limit, offset)]
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
        if path == "/api/presets":
            self._json({"presets": list_presets(self.store)})
            return
        if path == "/api/model":
            self._json(openrouter_key_status())
            return
        if path == "/api/status":
            config = normalized_config(self.store.get_profile(PROFILE_NAME) or {})
            self._json(build_status_payload(self.store, config))
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
                cleared = self.store.clear_stop_requests()
                self._json({"ok": True, "paused": False, "stop_requests_cleared": cleared})
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
