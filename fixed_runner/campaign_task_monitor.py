from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "stopped", "degraded"}


def read_status(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.load(response)


def poll_status(
    url: str, *, reader=read_status
) -> tuple[dict | None, str | None]:
    try:
        return reader(url), None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _ready_in_seconds(value: object, now: datetime) -> int | None:
    try:
        scheduled = datetime.fromisoformat(str(value))
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=now.tzinfo)
    except (TypeError, ValueError):
        return None
    return max(0, round((scheduled - now).total_seconds()))


def task_snapshot(task: dict, task_id: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    result = task.get("result") or {}
    snapshot = {
        "id": task_id[:8],
        "device": task.get("device_id"),
        "status": task.get("status", "missing"),
        "videos": result.get("videos_seen"),
        "matches": result.get("topic_matches"),
        "model_errors": result.get("model_errors"),
        "video_errors": result.get("video_errors"),
        "unknown": result.get("unknown_blocked_pages"),
    }
    if snapshot["status"] == "pending":
        snapshot["not_before"] = task.get("not_before")
        snapshot["ready_in_seconds"] = _ready_in_seconds(
            task.get("not_before"), now
        )
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor exact MediaFlow task IDs.")
    parser.add_argument("task_ids", nargs="+")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument(
        "--url", default="http://127.0.0.1:48138/api/status"
    )
    args = parser.parse_args()

    while True:
        status, monitor_error = poll_status(args.url)
        if status is None:
            print(
                datetime.now().astimezone().isoformat(timespec="seconds"),
                json.dumps(
                    {
                        "monitor_error": monitor_error,
                        "retry_in_seconds": max(1.0, args.interval),
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
            time.sleep(max(1.0, args.interval))
            continue
        tasks = {
            task.get("id"): task
            for task in status.get("tasks", [])
            if task.get("id") in args.task_ids
        }
        snapshots = [
            task_snapshot(tasks.get(task_id, {}), task_id)
            for task_id in args.task_ids
        ]
        print(
            datetime.now().astimezone().isoformat(timespec="seconds"),
            json.dumps(snapshots, ensure_ascii=True),
            flush=True,
        )
        if all(
            tasks.get(task_id, {}).get("status") in TERMINAL_STATUSES
            for task_id in args.task_ids
        ):
            return 0
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
