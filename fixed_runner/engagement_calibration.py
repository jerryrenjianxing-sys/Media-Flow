from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from douyin_uia2_runner import Uia2RunRecorder
from engagement_inspection import EngagementInspector
from task_store import TaskStore


def _semantic_signature(result: dict[str, Any]) -> str:
    unified = result.get("unified_activity")
    sections = result.get("sections")
    return json.dumps(
        {
            "status": result.get("status"),
            "restored": result.get("restored"),
            "unified": {
                "complete": bool(unified.get("complete")),
                "read_boundary": unified.get("read_boundary"),
                "reason_code": unified.get("reason_code"),
            }
            if isinstance(unified, dict)
            else None,
            "sections": {
                name: {
                    "status": value.get("status"),
                    "reason": value.get("reason"),
                    "complete": bool(value.get("complete")),
                }
                for name, value in sorted(
                    sections.items() if isinstance(sections, dict) else []
                )
                if isinstance(value, dict)
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def calibrate_engagement_v3(
    *,
    device: Any,
    store: TaskStore,
    device_id: str,
    app_version: str,
    display_signature: str,
    artifacts_root: Path,
    origin_id: str,
    inspector_factory: Callable[..., EngagementInspector] = EngagementInspector,
) -> dict[str, Any]:
    """Prove the fixed v3 semantic route three times while the worker owns the lock."""
    provisional = {
        "profile_version": "mediaflow-engagement-v3-r1",
        "device_id": device_id,
        "app_version": app_version,
        "display_signature": display_signature,
        "passes": 3,
        "later_passes_semantically_equal": True,
        "controls": {"aggregate": ["互动消息"]},
    }
    policy = {
        "inspection_workflow_version": "v3",
        "expected_app_version": app_version,
        "expected_display_signature": display_signature,
        "inspection_calibration": provisional,
        "_calibration_only": True,
    }
    signatures: list[str] = []
    for pass_index in range(1, 4):
        recorder = Uia2RunRecorder(
            artifacts_root / "engagement-v3-calibration" / f"pass-{pass_index}",
            device_id,
        )
        inspector = inspector_factory(
            device,
            recorder,
            store=store,
            device_id=device_id,
            task_id=f"{origin_id}-engagement-v3-{pass_index}",
            incident_sink=lambda incident: store.record_incident(
                task_id=origin_id, device_id=device_id, **incident
            ),
        )
        result = inspector.inspect(policy)
        unified = result.get("unified_activity") or {}
        if (
            result.get("status") != "completed"
            or result.get("restored") is not True
            or unified.get("complete") is not True
        ):
            raise RuntimeError(
                str(result.get("failure_reason") or "v3_semantic_calibration_failed")
            )
        signatures.append(_semantic_signature(result))
        # Successful calibration is evidence, not a throwaway probe. Retain the
        # paired frames so promotion of a shared rule can be audited later.
    if len(set(signatures)) != 1:
        raise RuntimeError("v3_semantic_calibration_inconsistent")
    return {
        **provisional,
        "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
