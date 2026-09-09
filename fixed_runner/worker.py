from __future__ import annotations

import argparse
import json
import os
import socket
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from douyin_fixed_runner import RUNTIME_ROOT, DeviceLock
from douyin_uia2_runner import Uia2RunRecorder
from control_vision import VisionCandidateLocator
from device_initialization import execute_initialization
from engagement_recovery import recover_version_drift
from engagement_inspection import EngagementInspector
from incident_evidence import record_incident_evidence
from execution_tasks import (
    ConsecutiveAnomalyLimitError,
    DeviceFatalError,
    ModelChannelError,
    _comment_is_visible,
    _comment_screenshot_evidence,
    _comment_task_type,
    _normalized_visible_text,
    comment_preview,
    execute_task,
    healthcheck,
    process_current_comment,
    routed_action_plan,
    routed_action_probabilities,
    send_comment,
    topic_session,
    two_video_demo,
)
from task_store import TaskStore
from task_resilience import TaskWaiting
from model_recovery import model_configuration_key, service_model_wait
from worker_runtime import (
    connect_with_retry,
    device_preflight,
    parse_worker_dwell,
    print_json,
    wait_while_paused,
    wake_and_unlock,
    write_report,
)


DEFAULT_DB = RUNTIME_ROOT / "tasks.db"
DEFAULT_ARTIFACTS = RUNTIME_ROOT / "artifacts"
DEFAULT_REPORT = RUNTIME_ROOT / "latest-report.md"


def run_worker(
    *,
    store: TaskStore,
    device_id: str,
    artifacts_root: Path,
    poll_seconds: float,
    max_tasks: int,
    connect_attempts: int,
    reconnect_delay_seconds: float,
    offline_wait_seconds: float,
) -> int:
    hostname = "".join(
        character if character.isascii() and character.isalnum() else "-"
        for character in socket.gethostname()
    ).strip("-") or "host"
    worker_id = f"{hostname}-{os.getpid()}"
    completed = 0
    with DeviceLock(RUNTIME_ROOT, device_id):
        # The process lock elects one Worker. SQLite claims below are the action
        # leases shared with platform manual control; idle workers do not own them.
        recovered = store.recover_interrupted(device_id)
        recovered_initializations = store.recover_interrupted_initializations(device_id)
        interrupted_recoveries = store.close_interrupted_task_recoveries(device_id)
        print_json({"event": "worker_start", "worker_id": worker_id, "device_id": device_id,
                    "recovered_interrupted_tasks": recovered,
                    "recovered_interrupted_initializations": recovered_initializations,
                    "interrupted_task_recoveries_waiting_user": interrupted_recoveries})
        device = None
        needs_reconnect = False
        while max_tasks == 0 or completed < max_tasks:
            if store.is_stop_requested(device_id):
                print_json(
                    {
                        "event": "worker_stop_requested",
                        "device_id": device_id,
                        "processed_tasks": completed,
                    }
                )
                break
            from task_preparation import maintenance_may_run
            if (store.get_profile("automation-stop") or {}).get("stopped"):
                time.sleep(poll_seconds)
                continue
            waiting = store.waiting_task(device_id)
            if waiting is not None and not maintenance_may_run(store, device_id):
                if waiting['reason_code'] == 'worker_interrupted':
                    # Reclaim the same task, then check identity/page under its
                    # action lease. No old write or finished slot is replayed.
                    store.resume_waiting_task(waiting['id'])
                else:
                    service_model_wait(store, waiting)
                time.sleep(poll_seconds)
                continue
            if store.is_paused() and not maintenance_may_run(store, device_id) and not store.has_ready(device_id):
                time.sleep(poll_seconds)
                continue
            if store.has_active_control_session(device_id):
                time.sleep(poll_seconds)
                continue
            if store.is_stop_requested(device_id):
                print_json(
                    {
                        "event": "worker_stop_requested",
                        "device_id": device_id,
                        "processed_tasks": completed,
                    }
                )
                break
            if device is None or needs_reconnect:
                if needs_reconnect:
                    print_json({"event": "device_reconnect_before_next_task"})
                try:
                    device = connect_with_retry(
                        device_id, connect_attempts, reconnect_delay_seconds
                    )
                    needs_reconnect = False
                except RuntimeError as exc:
                    print_json(
                        {
                            "event": "device_unavailable_waiting",
                            "wait_seconds": offline_wait_seconds,
                            "error": str(exc),
                        }
                    )
                    device = None
                    time.sleep(offline_wait_seconds)
                    continue
            if not (
                store.has_initialization_ready(device_id)
                or store.has_ready_recovery(device_id)
                or store.has_ready(device_id)
            ):
                time.sleep(poll_seconds)
                continue
            initialization = store.claim_initialization(device_id, worker_id)
            if initialization is not None:
                result = execute_initialization(
                    device,
                    initialization,
                    store,
                    artifacts_root=artifacts_root / "initializations",
                    vision_locator=VisionCandidateLocator(),
                )
                print_json(
                    {
                        "event": "initialization_finished",
                        "initialization_id": initialization.id,
                        "device_id": device_id,
                        "status": result.get("status"),
                        "run_dir": result.get("run_dir"),
                    }
                )
                continue
            if store.is_paused() and not store.has_ready(device_id):
                time.sleep(poll_seconds)
                continue
            recovery_request = None if store.is_paused() else store.claim_task_recovery(device_id)
            if recovery_request is not None:
                origin_task = store.get(recovery_request["origin_task_id"])
                recovery_result = origin_task.result or {}
                if recovery_result.get("failure_class") != "recoverable_precondition":
                    probe_recorder = Uia2RunRecorder(
                        artifacts_root / "engagement-revalidation", origin_task.device_id
                    )
                    probe = EngagementInspector(
                        device,
                        probe_recorder,
                        store=store,
                        device_id=origin_task.device_id,
                        task_id=f"{origin_task.id}-revalidation-probe",
                    )
                    recovery_result = probe.inspect(
                        {**origin_task.payload, "_calibration_only": True}
                    )
                    probe.discard_v2_artifacts()
                    if (
                        recovery_result.get("failure_class")
                        != "recoverable_precondition"
                        or recovery_result.get("recovery_eligible") is not True
                        or recovery_result.get("navigation_started") is not False
                    ):
                        recovery = store.finish_task_recovery(
                            recovery_request["id"],
                            status="waiting_user",
                            progress_current=0,
                            progress_total=3,
                            message="当前页面或版本状态与原失败记录不一致，需要人工处理",
                            evidence_dir=str(probe_recorder.run_dir),
                            error=str(
                                recovery_result.get("failure_reason")
                                or "legacy_revalidation_probe_inconclusive"
                            ),
                        )
                        print_json(
                            {
                                "event": "task_recovery_updated",
                                "task_id": origin_task.id,
                                "recovery_status": recovery.get("status"),
                                "replacement_task_id": None,
                            }
                        )
                        completed += 1
                        continue
                recovery = recover_version_drift(
                    store=store,
                    device=device,
                    task=origin_task,
                    result=recovery_result,
                    artifacts_root=artifacts_root,
                )
                print_json(
                    {
                        "event": "task_recovery_updated",
                        "task_id": origin_task.id,
                        "recovery_status": recovery.get("status") if recovery else None,
                        "replacement_task_id": (
                            recovery.get("replacement_task_id") if recovery else None
                        ),
                    }
                )
                completed += 1
                continue
            task = store.claim_next(device_id, worker_id)
            if task is None:
                continue
            recorder = Uia2RunRecorder(artifacts_root, task.device_id)
            run_dir = str(recorder.run_dir)
            store.attach_run_dir(task.id, run_dir)
            try:
                # Device actions begin only after claiming the persistent lease.
                if not device_preflight(device):
                    device = connect_with_retry(device_id, connect_attempts, reconnect_delay_seconds)
                    if not device_preflight(device):
                        raise RuntimeError("设备连接检查失败，请重试连接")
                if task.payload.get('resilience_version') == 'v1':
                    from session_checkpoint import verify_task_identity
                    verify_task_identity(store, task, device)
                def save_video_incident(incident: dict[str, Any]) -> None:
                    store.record_incident(
                        task_id=task.id,
                        device_id=task.device_id,
                        **incident,
                    )

                result = execute_task(
                    device,
                    task,
                    recorder,
                    pause_waiter=lambda: wait_while_paused(
                        store, poll_seconds, recorder, device_id, task_id=task.id
                    ),
                    incident_sink=save_video_incident,
                    stop_checker=lambda: store.is_stop_requested(device_id) or store.agent_task_stop_requested(task.id),
                    task_store=store,
                )
                from task_preparation import record_inspection_outcome
                if task.payload.get('resilience_version') == 'v1':
                    record_inspection_outcome(store, task, result)
                task_status = (
                    "stopped"
                    if result.get("stopped_by_user")
                    else "failed"
                    if result.get("status") == "failed"
                    else "degraded"
                    if result.get("status") == "degraded"
                    else "completed"
                )
                store.finish(
                    task.id,
                    status=task_status,
                    run_dir=run_dir,
                    result=result,
                    error=(
                        "stopped_by_user"
                        if task_status == "stopped"
                        else str(result.get("failure_reason") or "failed")
                        if task_status == "failed"
                        else str(
                            (result.get("degraded_reason") or {}).get(
                                "code", "degraded"
                            )
                        )
                        if task_status == "degraded"
                        else None
                    ),
                )
                if task.payload.get('resilience_version') != 'v1':
                    record_inspection_outcome(store, task, result)
                if task_status == "failed" and task.payload.get("preparation_version") != "on-demand-v1":
                    try:
                        recovery = recover_version_drift(
                            store=store,
                            device=device,
                            task=task,
                            result=result,
                            artifacts_root=artifacts_root,
                        )
                        if recovery is not None:
                            print_json(
                                {
                                    "event": "task_recovery_updated",
                                    "task_id": task.id,
                                    "recovery_status": recovery.get("status"),
                                    "replacement_task_id": recovery.get("replacement_task_id"),
                                }
                            )
                    except Exception as recovery_error:
                        print_json(
                            {
                                "event": "task_recovery_failed",
                                "task_id": task.id,
                                "error_type": type(recovery_error).__name__,
                                "error": str(recovery_error),
                            }
                        )
                print_json(
                    {
                        "event": (
                            "task_stopped"
                            if task_status == "stopped"
                            else "task_failed"
                            if task_status == "failed"
                            else "task_degraded"
                            if task_status == "degraded"
                            else "task_completed"
                        ),
                        "task_id": task.id,
                        **result,
                    }
                )
                if task_status == "stopped":
                    completed += 1
                    break
            except TaskWaiting as exc:
                key = exc.model_key
                if exc.status == 'waiting_model' or exc.reason in {'authentication', 'balance', 'permanent_rejection', 'invalid_request'}:
                    key = key or model_configuration_key()
                waiting = store.wait_task(task.id, exc.status, exc.reason, model_key=key, result=exc.result)
                current = store.get(task.id)
                print_json({'event': 'task_waiting' if waiting else 'task_' + current.status,
                            'task_id': task.id, 'status': current.status,
                            'reason_code': exc.reason if waiting else current.error, 'run_dir': run_dir})
                continue
            except Exception as exc:
                if task.payload.get('resilience_version') == 'v1':
                    record_incident_evidence(
                        device=device, recorder=recorder,
                        incident_sink=lambda incident: store.record_incident(task_id=task.id, device_id=task.device_id, **incident),
                        video_index=None, stage='task', error_type=type(exc).__name__,
                        error_message=str(exc), outcome='device_fatal', recovery_action='waiting_device',
                        context={'task_type': task.task_type, 'progress_preserved': True})
                    waiting = store.wait_task(task.id, 'waiting_device', 'device_or_page_unavailable',
                                              result=getattr(exc, 'result', None))
                    needs_reconnect = True
                    current = store.get(task.id)
                    print_json({'event': 'task_waiting' if waiting else 'task_' + current.status,
                                'task_id': task.id, 'status': current.status,
                                'reason_code': 'device_or_page_unavailable' if waiting else current.error, 'run_dir': run_dir})
                    continue
                if task.payload.get("preparation_version") == "on-demand-v1":
                    # Unclassified preparation failures have no safe-home proof.
                    store.request_stop([task.device_id])
                    store.save_profile("preparation-issue:" + task.device_id,
                                       {"task_id": task.id, "message": "任务已暂停，请检查当前页面或连接：" + str(exc)[:350]})
                device_healthy = device_preflight(device)
                needs_reconnect = (
                    isinstance(exc, DeviceFatalError)
                    and not isinstance(exc, ModelChannelError)
                ) or not device_healthy
                if not isinstance(exc, (DeviceFatalError, ModelChannelError)):
                    record_incident_evidence(
                        device=device,
                        recorder=recorder,
                        incident_sink=lambda incident: store.record_incident(
                            task_id=task.id,
                            device_id=task.device_id,
                            **incident,
                        ),
                        video_index=None,
                        stage="task",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        outcome="device_fatal" if needs_reconnect else "skipped",
                        recovery_action=(
                            "reconnect" if needs_reconnect else "device_still_healthy"
                        ),
                        context={"task_type": task.task_type},
                    )
                store.finish(
                    task.id,
                    status="failed",
                    run_dir=run_dir,
                    result=getattr(exc, "result", None),
                    error=f"{type(exc).__name__}: {exc}",
                )
                print_json(
                    {
                        "event": "task_failed",
                        "task_id": task.id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
            completed += 1
    print_json({"event": "worker_stop", "processed_tasks": completed})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fixed Android task worker")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit")
    submit.add_argument(
        "task_type",
        choices=(
            "healthcheck",
            "douyin_benchmark",
            "douyin_comment_preview",
            "douyin_comment",
            "douyin_two_video_demo",
        ),
    )
    submit.add_argument("--device-id", required=True)
    submit.add_argument(
        "--dwell", type=parse_worker_dwell, default=parse_worker_dwell("4,5,4,5")
    )
    submit.add_argument("--max-gate-skips", type=int, default=3)
    submit.add_argument("--comment-dwell", type=float, default=8.0)
    submit.add_argument("--count", type=int, default=1)
    submit.add_argument("--interval-seconds", type=float, default=0.0)

    worker = subparsers.add_parser("worker")
    worker.add_argument("--device-id", required=True)
    worker.add_argument("--artifacts-root", type=Path, default=DEFAULT_ARTIFACTS)
    worker.add_argument("--poll-seconds", type=float, default=0.5)
    worker.add_argument("--max-tasks", type=int, default=0)
    worker.add_argument("--connect-attempts", type=int, default=3)
    worker.add_argument("--reconnect-delay-seconds", type=float, default=2.0)
    worker.add_argument("--offline-wait-seconds", type=float, default=15.0)

    status = subparsers.add_parser("status")
    status.add_argument("--limit", type=int, default=20)
    status.add_argument("--json", action="store_true")
    report = subparsers.add_parser("report")
    report.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    store = TaskStore(args.db)
    if args.command == "submit":
        payload: dict[str, Any] = {}
        if args.task_type == "douyin_benchmark":
            if len(args.dwell) != 4:
                parser.error("benchmark requires --dwell with exactly four values")
            payload = {
                "dwell": args.dwell,
                "max_gate_skips": args.max_gate_skips,
            }
        elif args.task_type in {"douyin_comment_preview", "douyin_comment"}:
            payload = {
                "dwell_seconds": args.comment_dwell,
                "max_gate_skips": args.max_gate_skips,
            }
        elif args.task_type == "douyin_two_video_demo":
            if len(args.dwell) != 2:
                parser.error("two-video demo requires --dwell with exactly two values")
            payload = {
                "dwell": args.dwell,
                "max_gate_skips": args.max_gate_skips,
            }
        if args.count < 1 or args.count > 100:
            parser.error("--count must be between 1 and 100")
        if args.interval_seconds < 0:
            parser.error("--interval-seconds must be non-negative")
        base_time = datetime.now().astimezone()
        task_ids = []
        for index in range(args.count):
            not_before = (
                base_time + timedelta(seconds=index * args.interval_seconds)
            ).isoformat(timespec="milliseconds")
            task_ids.append(
                store.submit(
                    args.task_type,
                    args.device_id,
                    payload,
                    not_before=not_before,
                )
            )
        print_json(
            {
                "event": "tasks_submitted",
                "task_ids": task_ids,
                "count": len(task_ids),
                "interval_seconds": args.interval_seconds,
            }
        )
        return 0
    if args.command == "worker":
        if args.poll_seconds <= 0:
            parser.error("--poll-seconds must be positive")
        if args.max_tasks < 0:
            parser.error("--max-tasks must be non-negative")
        if args.connect_attempts < 1:
            parser.error("--connect-attempts must be positive")
        if args.reconnect_delay_seconds < 0:
            parser.error("--reconnect-delay-seconds must be non-negative")
        if args.offline_wait_seconds <= 0:
            parser.error("--offline-wait-seconds must be positive")
        return run_worker(
            store=store,
            device_id=args.device_id,
            artifacts_root=args.artifacts_root,
            poll_seconds=args.poll_seconds,
            max_tasks=args.max_tasks,
            connect_attempts=args.connect_attempts,
            reconnect_delay_seconds=args.reconnect_delay_seconds,
            offline_wait_seconds=args.offline_wait_seconds,
        )
    if args.command == "report":
        statistics = write_report(store, args.output)
        print_json(
            {
                "event": "report_written",
                "path": str(args.output.resolve()),
                "by_status": statistics["by_status"],
            }
        )
        return 0
    tasks = store.list(args.limit)
    if args.json:
        print(json.dumps([asdict(task) for task in tasks], ensure_ascii=True, indent=2))
    else:
        if not tasks:
            print("No tasks.")
        for task in tasks:
            schedule_marker = (
                f"  scheduled={task.not_before}"
                if task.status == "pending" and task.not_before > datetime.now().astimezone().isoformat(timespec="milliseconds")
                else ""
            )
            print(
                f"{task.id[:8]}  {task.status:<9}  {task.task_type:<18}  "
                f"{task.device_id}  {task.created_at}{schedule_marker}"
            )
            if task.error:
                print(f"  error: {task.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
