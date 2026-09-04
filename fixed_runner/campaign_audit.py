from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from runtime_layout import RUNTIME_ROOT


DEFAULT_TOPIC = "AI、智能制造与塑料包装"


def _read_events(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _event_time(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _iso_after(value: str, seconds: int) -> str:
    try:
        return (datetime.fromisoformat(value) + timedelta(seconds=seconds)).isoformat()
    except (TypeError, ValueError):
        return ""


def audit_run(run_dir: Path, topic: str = DEFAULT_TOPIC) -> dict[str, Any] | None:
    events = _read_events(run_dir / "events.jsonl")
    start = next((event for event in events if event.get("event") == "task_start"), None)
    if not start:
        return None
    payload = start.get("payload")
    if not isinstance(payload, dict) or payload.get("topic_prompt") != topic:
        return None
    complete = next(
        (event for event in reversed(events) if event.get("event") == "task_complete"),
        None,
    )
    device_id = str(payload.get("device_id") or run_dir.name.rsplit("-", 1)[-1])
    topic_decisions: dict[int, dict[str, Any]] = {}
    policy_decisions: dict[int, dict[str, Any]] = {}
    verified_videos: set[int] = set()
    identity_block_times: list[float] = []
    verified_write_times: list[float] = []
    reaction_before: dict[tuple[str, int], bool] = {}
    for event in events:
        name = event.get("event")
        try:
            if name == "topic_ai_decision":
                topic_decisions[int(event.get("video"))] = event
            elif name == "comment_constraint_decision":
                policy_decisions[int(event.get("video_index"))] = event
            elif name == "comment_send_verification" and event.get("verified") is True:
                verified_videos.add(int(event.get("video")))
                verified_write_times.append(_event_time(event.get("time")))
            elif (
                name in {"reaction_session_circuit_opened", "comment_session_circuit_opened"}
                and event.get("reason") == "platform_identity_safety_verification"
            ):
                identity_block_times.append(_event_time(event.get("time")))
            elif name in {"like_state_before", "favorite_state_before"}:
                reaction_before[(name.removesuffix("_state_before"), int(event.get("video")))] = bool(
                    event.get("active")
                )
            elif name in {"like_state_after", "favorite_state_after"}:
                action = name.removesuffix("_state_after")
                video = int(event.get("video"))
                if (
                    reaction_before.get((action, video)) is False
                    and event.get("active") is True
                ):
                    verified_write_times.append(_event_time(event.get("time")))
        except (TypeError, ValueError):
            continue

    comment_policy_enabled = payload.get("comment_policy_enabled") is True
    strict_comment_videos: list[int] = []
    for video in sorted(verified_videos):
        decision = topic_decisions.get(video) or {}
        policy = policy_decisions.get(video) or {}
        screenshot = run_dir / f"video-{video}-comment-sent.png"
        if (
            comment_policy_enabled
            and decision.get("matched") is True
            and decision.get("relevance") == "exact"
            and decision.get("safe") is True
            and bool(decision.get("evidence"))
            and policy.get("allowed") is True
            and screenshot.is_file()
        ):
            strict_comment_videos.append(video)

    content_mode = str(payload.get("content_mode") or "")
    expected_videos = int(payload.get("video_count") or 0)
    terminal_status = str((complete or {}).get("status") or "")
    completed = terminal_status in {"passed", "passed_with_recovery"}
    videos_seen = int((complete or {}).get("videos_seen") or 0)
    topic_matches = int((complete or {}).get("topic_matches") or 0)
    model_valid_rate = float((complete or {}).get("model_valid_response_rate") or 0.0)
    natural_eligible = bool(
        content_mode == "mixed"
        and expected_videos == 20
        and completed
        and videos_seen == 20
        and model_valid_rate >= 0.95
    )
    natural_rate = topic_matches / videos_seen if natural_eligible else None
    search_eligible = bool(
        content_mode == "search"
        and expected_videos == 20
        and completed
        and videos_seen == 20
        and model_valid_rate >= 0.95
    )
    search_rate = topic_matches / videos_seen if search_eligible else None
    return {
        "run_dir": str(run_dir),
        "task_id": str(start.get("task_id") or (complete or {}).get("task_id") or ""),
        "time": str(start.get("time") or ""),
        "time_sort": _event_time(start.get("time")),
        "terminal_time": str((complete or {}).get("time") or start.get("time") or ""),
        "terminal_sort": _event_time(
            (complete or {}).get("time") or start.get("time")
        ),
        "device_id": device_id,
        "content_mode": content_mode,
        "comment_policy_enabled": comment_policy_enabled,
        "completed": completed,
        "terminal_status": terminal_status,
        "videos_seen": videos_seen,
        "topic_matches": topic_matches,
        "model_valid_response_rate": model_valid_rate,
        "natural_eligible": natural_eligible,
        "natural_rate": natural_rate,
        "search_eligible": search_eligible,
        "search_rate": search_rate,
        "strict_comment_videos": strict_comment_videos,
        "latest_identity_block_sort": max(identity_block_times, default=0.0),
        "latest_verified_write_sort": max(verified_write_times, default=0.0),
    }


def audit_campaign(
    runs_root: Path,
    *,
    topic: str = DEFAULT_TOPIC,
    since: str | None = None,
    threshold: float = 0.30,
    required_consecutive: int = 2,
    required_comments: int = 10,
    cooldown_minutes: int = 120,
    now: str | None = None,
) -> dict[str, Any]:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if required_consecutive < 1 or required_comments < 0:
        raise ValueError("requirements must be non-negative")
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be non-negative")
    since_sort = _event_time(since) if since else None
    if since and not since_sort:
        raise ValueError("since must be an ISO-8601 timestamp")
    now_sort = _event_time(now) if now else datetime.now().timestamp()
    if now and not now_sort:
        raise ValueError("now must be an ISO-8601 timestamp")
    cooldown_seconds = cooldown_minutes * 60

    all_runs: list[dict[str, Any]] = []
    if runs_root.is_dir():
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            audited = audit_run(run_dir, topic)
            if audited is not None:
                all_runs.append(audited)
    all_runs.sort(key=lambda item: (item["time_sort"], item["run_dir"]))
    runs = [
        item
        for item in all_runs
        if since_sort is None or item["time_sort"] >= since_sort
    ]
    runs.sort(key=lambda item: (item["time_sort"], item["run_dir"]))

    devices: list[dict[str, Any]] = []
    for device_id in sorted({item["device_id"] for item in runs}):
        device_runs = [item for item in runs if item["device_id"] == device_id]
        device_all_runs = [
            item for item in all_runs if item["device_id"] == device_id
        ]
        natural_rounds = [item for item in device_runs if item["content_mode"] == "mixed"]
        natural_streak = 0
        for item in reversed(natural_rounds):
            if item["natural_eligible"] and item["natural_rate"] >= threshold:
                natural_streak += 1
            else:
                break
        search_rounds = [item for item in device_runs if item["content_mode"] == "search"]
        search_streak = 0
        for item in reversed(search_rounds):
            if item["search_eligible"] and item["search_rate"] >= threshold:
                search_streak += 1
            else:
                break
        strict_comments = sum(len(item["strict_comment_videos"]) for item in device_runs)
        evidence_runs = sum(bool(item["strict_comment_videos"]) for item in device_runs)
        topic_goal_met = search_streak >= required_consecutive
        comment_goal_met = strict_comments >= required_comments
        last_run = max(device_all_runs, key=lambda item: item["terminal_sort"])
        latest_identity_block_sort = max(
            (item["latest_identity_block_sort"] for item in device_all_runs),
            default=0.0,
        )
        latest_verified_write_sort = max(
            (item["latest_verified_write_sort"] for item in device_all_runs),
            default=0.0,
        )
        identity_verification_blocked = bool(
            latest_identity_block_sort
            and latest_identity_block_sort > latest_verified_write_sort
        )
        cooldown_ready_sort = last_run["terminal_sort"] + cooldown_seconds
        cooldown_remaining_seconds = max(
            0, math.ceil(cooldown_ready_sort - now_sort)
        )
        if topic_goal_met and comment_goal_met:
            next_campaign_stage = "complete"
        elif topic_goal_met and identity_verification_blocked:
            next_campaign_stage = "await_identity_verification"
        elif cooldown_remaining_seconds:
            next_campaign_stage = "wait"
        else:
            next_campaign_stage = "search_conditioning"
        devices.append(
            {
                "device_id": device_id,
                "verified_comments": strict_comments,
                "comment_evidence_runs": evidence_runs,
                # Kept for compatibility with historical UI/report consumers.
                "natural_qualifying_streak": natural_streak,
                "search_qualifying_streak": search_streak,
                "topic_goal_met": topic_goal_met,
                "comment_goal_met": comment_goal_met,
                "platform_identity_verification_blocked": identity_verification_blocked,
                "action_mode": (
                    "read_only" if identity_verification_blocked else "eligible"
                ),
                "last_terminal_time": last_run["terminal_time"],
                "last_content_mode": last_run["content_mode"],
                "cooldown_ready_at": _iso_after(
                    last_run["terminal_time"], cooldown_seconds
                ),
                "cooldown_remaining_seconds": cooldown_remaining_seconds,
                "next_campaign_stage": next_campaign_stage,
                "natural_rounds": [
                    {
                        "task_id": item["task_id"],
                        "time": item["time"],
                        "completed": item["completed"],
                        "terminal_status": item["terminal_status"],
                        "videos_seen": item["videos_seen"],
                        "topic_matches": item["topic_matches"],
                        "rate": item["natural_rate"],
                        "eligible": item["natural_eligible"],
                    }
                    for item in natural_rounds
                ],
                "search_rounds": [
                    {
                        "task_id": item["task_id"],
                        "time": item["time"],
                        "completed": item["completed"],
                        "terminal_status": item["terminal_status"],
                        "videos_seen": item["videos_seen"],
                        "topic_matches": item["topic_matches"],
                        "rate": item["search_rate"],
                        "eligible": item["search_eligible"],
                    }
                    for item in search_rounds
                ],
            }
        )
    return {
        "campaign_strategy": "search_only",
        "topic": topic,
        "since": since,
        "threshold": threshold,
        "required_consecutive": required_consecutive,
        "required_comments": required_comments,
        "cooldown_minutes": cooldown_minutes,
        "evaluated_at": now or datetime.now().astimezone().isoformat(),
        "devices": devices,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="只读汇总 MediaFlow 主题培养证据")
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=RUNTIME_ROOT / "artifacts" / "runs",
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--since")
    parser.add_argument("--threshold", type=float, default=0.30)
    parser.add_argument("--required-consecutive", type=int, default=2)
    parser.add_argument("--required-comments", type=int, default=10)
    parser.add_argument("--cooldown-minutes", type=int, default=120)
    parser.add_argument("--now")
    args = parser.parse_args()
    result = audit_campaign(
        args.runs_root,
        topic=args.topic,
        since=args.since,
        threshold=args.threshold,
        required_consecutive=args.required_consecutive,
        required_comments=args.required_comments,
        cooldown_minutes=args.cooldown_minutes,
        now=args.now,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
