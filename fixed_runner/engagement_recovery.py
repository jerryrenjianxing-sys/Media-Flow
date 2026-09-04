from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from device_profiles import PROFILE_PATH, load_device_profile_payloads, upsert_device_profile
from douyin_uia2_runner import Uia2RunRecorder
from engagement_inspection import EngagementInspector
from task_store import TaskRecord, TaskStore


def _fingerprint(result: dict[str, Any]) -> str:
    raw = "|".join(
        str(result.get(name) or "")
        for name in (
            "expected_app_version",
            "actual_app_version",
            "expected_display_signature",
            "actual_display_signature",
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _semantic_signature(result: dict[str, Any]) -> str:
    sections = result.get("sections") if isinstance(result.get("sections"), dict) else {}
    values = {
        name: {
            "status": value.get("status"),
            "reason": value.get("reason"),
            "complete": bool(value.get("complete", False)),
        }
        for name, value in sorted(sections.items())
        if isinstance(value, dict)
    }
    return json.dumps(
        {"status": result.get("status"), "restored": result.get("restored"), "sections": values},
        ensure_ascii=False,
        sort_keys=True,
    )


def recover_version_drift(
    *,
    store: TaskStore,
    device: Any,
    task: TaskRecord,
    result: dict[str, Any],
    artifacts_root: Path,
    profile_path: Path = PROFILE_PATH,
) -> dict[str, Any] | None:
    """Run one action-free three-pass semantic revalidation and enqueue one replacement."""
    if (
        task.task_type != "douyin_engagement_inspection"
        or result.get("failure_class") != "recoverable_precondition"
        or result.get("recovery_eligible") is not True
        or result.get("navigation_started") is not False
    ):
        return None
    expected = {
        "app_version": str(result.get("expected_app_version") or ""),
        "display_signature": str(result.get("expected_display_signature") or ""),
    }
    actual = {
        "app_version": str(result.get("actual_app_version") or ""),
        "display_signature": str(result.get("actual_display_signature") or ""),
    }
    recovery, created = store.create_task_recovery(
        origin_task_id=task.id,
        device_id=task.device_id,
        fingerprint=_fingerprint(result),
        expected=expected,
        actual=actual,
    )
    if not created and recovery.get("status") not in {"queued", "running"}:
        return recovery
    recovery = store.update_task_recovery_context(
        recovery["id"],
        fingerprint=_fingerprint(result),
        expected=expected,
        actual=actual,
    )

    calibration = task.payload.get("inspection_calibration")
    if not isinstance(calibration, dict) or not actual["app_version"] or not actual["display_signature"]:
        return store.finish_task_recovery(
            recovery["id"],
            status="waiting_user",
            progress_current=0,
            progress_total=3,
            message="冻结档案不完整，需要人工重新校准",
            error="revalidation_snapshot_incomplete",
        )

    provisional_calibration = {
        **calibration,
        "app_version": actual["app_version"],
        "display_signature": actual["display_signature"],
        "passes": 3,
        "later_passes_semantically_equal": True,
    }
    provisional_calibration.pop("coordinate_fallbacks", None)
    policy = {
        **task.payload,
        "expected_app_version": actual["app_version"],
        "expected_display_signature": actual["display_signature"],
        "inspection_calibration": provisional_calibration,
        "_calibration_only": True,
    }
    run_root = artifacts_root / "engagement-revalidation"
    signatures: list[str] = []
    try:
        store.update_task_recovery(
            recovery["id"], status="running", progress_current=0,
            message="正在进行第 1/3 次只读语义复验",
        )
        for pass_index in range(1, 4):
            recorder = Uia2RunRecorder(run_root, task.device_id)
            inspector = EngagementInspector(
                device,
                recorder,
                store=store,
                device_id=task.device_id,
                task_id=f"{task.id}-revalidation-{pass_index}",
            )
            pass_result = inspector.inspect(policy)
            if pass_result.get("status") == "failed" or pass_result.get("restored") is not True:
                return store.finish_task_recovery(
                    recovery["id"],
                    status="waiting_user",
                    progress_current=pass_index - 1,
                    progress_total=3,
                    message=f"第 {pass_index} 次语义复验未通过，需要人工处理",
                    evidence_dir=str(recorder.run_dir),
                    error=str(pass_result.get("failure_reason") or "semantic_revalidation_failed"),
                )
            signatures.append(_semantic_signature(pass_result))
            inspector.discard_v2_artifacts()
            store.update_task_recovery(
                recovery["id"], status="running", progress_current=pass_index,
                message=f"已完成 {pass_index}/3 次只读语义复验",
            )
        if len(set(signatures)) != 1:
            return store.finish_task_recovery(
                recovery["id"], status="waiting_user", progress_current=3,
                progress_total=3, message="三次页面语义结果不一致，需要人工处理",
                error="semantic_revalidation_inconsistent",
            )

        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        new_calibration = {
            **provisional_calibration,
            "profile_version": f"{calibration.get('profile_version', 'douyin-engagement-v2')}-auto-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "revalidated_at": timestamp,
            "revalidation_origin_task_id": task.id,
        }
        profiles = load_device_profile_payloads(profile_path)
        profile = dict(profiles.get(task.device_id) or {})
        profile.update(
            engagement_app_version=actual["app_version"],
            engagement_display_signature=actual["display_signature"],
            engagement_calibration=new_calibration,
        )
        upsert_device_profile(task.device_id, profile, profile_path)
        replacement_payload = {
            **task.payload,
            "expected_app_version": actual["app_version"],
            "expected_display_signature": actual["display_signature"],
            "inspection_calibration": new_calibration,
            "recovery_parent_task_id": task.id,
            "recovery_fingerprint": recovery["fingerprint"],
        }
        replacement_id = store.submit(task.task_type, task.device_id, replacement_payload)
        return store.finish_task_recovery(
            recovery["id"], status="ready", progress_current=3, progress_total=3,
            replacement_task_id=replacement_id,
            message="三遍语义复验一致，已创建关联替代任务",
        )
    except Exception as exc:
        return store.finish_task_recovery(
            recovery["id"], status="failed", progress_current=len(signatures),
            progress_total=3, message="自动复验出现程序错误",
            error=f"{type(exc).__name__}: {exc}",
        )
