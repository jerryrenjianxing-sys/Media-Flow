from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import uiautomator2 as u2

from douyin_uia2_runner import Uia2RunRecorder
from task_store import TaskStore


def parse_worker_dwell(value: str) -> list[float]:
    try:
        values = [float(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use two or four comma-separated non-negative seconds"
        ) from exc
    if len(values) not in {2, 4} or any(item < 0 for item in values):
        raise argparse.ArgumentTypeError(
            "Use two or four comma-separated non-negative seconds"
        )
    return values


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True), flush=True)


def connect_with_retry(device_id: str, attempts: int, delay_seconds: float):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            device = u2.connect(device_id)
            device.app_current()
            print_json(
                {
                    "event": "device_connected",
                    "attempt": attempt,
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
            )
            return device
        except Exception as exc:
            last_error = exc
            print_json(
                {
                    "event": "device_connect_failed",
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            if attempt < attempts:
                time.sleep(delay_seconds)
    assert last_error is not None
    raise RuntimeError(
        f"Could not connect to device after {attempts} attempts: {last_error}"
    ) from last_error


def device_preflight(device) -> bool:
    try:
        if not wake_and_unlock(device):
            return False
        device.app_current()
        return True
    except Exception as exc:
        print_json(
            {
                "event": "device_preflight_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return False


def wake_and_unlock(device) -> bool:
    """Wake and dismiss a non-secure Android keyguard before a queued task."""
    if not hasattr(device, "screen_on") or not hasattr(device, "unlock"):
        return True
    try:
        before = bool(device.info.get("screenOn", True))
        device.screen_on()
        device.unlock()
        time.sleep(0.6)
        after = bool(device.info.get("screenOn", False))
        print_json(
            {
                "event": "device_wake_unlock",
                "screen_was_on": before,
                "screen_is_on": after,
            }
        )
        return after
    except Exception as exc:
        print_json(
            {
                "event": "device_wake_unlock_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return False


def wait_while_paused(
    store: TaskStore,
    poll_seconds: float,
    recorder: Uia2RunRecorder | None = None,
    device_id: str | None = None,
) -> None:
    pause_seen = False
    while store.is_paused():
        if device_id and store.is_stop_requested(device_id):
            print_json({"event": "pause_interrupted_by_stop", "device_id": device_id})
            if recorder is not None:
                recorder.emit("pause_interrupted_by_stop")
            return
        if not pause_seen:
            pause_seen = True
            print_json({"event": "worker_paused"})
            if recorder is not None:
                recorder.emit("task_paused")
        time.sleep(max(0.2, poll_seconds))
    if pause_seen:
        print_json({"event": "worker_resumed"})
        if recorder is not None:
            recorder.emit("task_resumed")


def write_report(store: TaskStore, output: Path) -> dict[str, Any]:
    statistics = store.statistics()
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 固定程序运行汇总",
        "",
        f"生成时间：{statistics['generated_at']}",
        "",
        "## 任务状态",
        "",
        "| 状态 | 数量 |",
        "|---|---:|",
    ]
    for status in ("pending", "running", "completed", "failed", "stopped", "cancelled"):
        lines.append(f"| {status} | {statistics['by_status'].get(status, 0)} |")
    lines.extend(
        [
            "",
            "## 各类任务",
            "",
            "| 任务类型 | 状态 | 数量 |",
            "|---|---|---:|",
        ]
    )
    for row in statistics["by_type_and_status"]:
        lines.append(f"| {row['task_type']} | {row['status']} | {row['count']} |")
    timing = statistics["completed_wall_s"]
    lines.extend(
        [
            "",
            "## 完整流程耗时",
            "",
            f"有耗时记录的任务：{timing['count']}；平均 {timing['average']} 秒；"
            f"最短 {timing['minimum']} 秒；最长 {timing['maximum']} 秒。",
            "",
            "## 最近失败",
            "",
        ]
    )
    if statistics["recent_failures"]:
        for failure in statistics["recent_failures"]:
            lines.append(
                f"- `{failure['id'][:8]}` {failure['task_type']}：{failure['error']}"
            )
    else:
        lines.append("无失败任务。")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return statistics
