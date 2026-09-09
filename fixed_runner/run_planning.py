from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, Mapping

from control_config import (
    build_scheduled_plan,
    inspection_profiles_for_store,
    normalized_config,
    submit_scheduled_rounds,
)
from engagement_preflight import visitor_reminder_status
from task_store import TaskStore


DRAFT_NAME = "current"


def get_or_create_draft(
    store: TaskStore, fallback_config: Mapping[str, Any]
) -> dict[str, Any]:
    draft = store.get_run_draft(DRAFT_NAME)
    if draft is not None:
        return draft
    config = normalized_config(dict(fallback_config))
    return store.save_run_draft(config, name=DRAFT_NAME, expected_revision=0)


def save_draft(
    store: TaskStore,
    raw_config: Mapping[str, Any],
    *,
    expected_revision: int,
) -> dict[str, Any]:
    config = normalized_config(dict(raw_config))
    return store.save_run_draft(
        config,
        name=DRAFT_NAME,
        expected_revision=int(expected_revision),
    )


def _content_plan_revision(
    store: TaskStore, config: Mapping[str, Any]
) -> dict[str, Any] | None:
    revision_id = config.get("content_plan_revision_id")
    if config.get("content_mode") == "general" or not revision_id:
        return None
    try:
        revision = store.get_content_plan_revision(str(revision_id))
    except KeyError as exc:
        raise ValueError("所选内容计划版本不存在") from exc
    if revision["plan_id"] != config.get("content_plan_id"):
        raise ValueError("内容计划与版本不匹配")
    return revision


def _action_probabilities(config: Mapping[str, Any]) -> dict[str, float]:
    mode = str(config.get("content_mode") or "general")
    if mode == "search":
        return {
            "like": float(config.get("matched_like_probability") or 0),
            "favorite": float(config.get("matched_favorite_probability") or 0),
            "comment": float(config.get("matched_comment_probability") or 0),
        }
    if mode in {"mixed", "hybrid"}:
        return {
            "like": max(
                float(config.get("like_probability") or 0),
                float(config.get("matched_like_probability") or 0),
            ),
            "favorite": max(
                float(config.get("favorite_probability") or 0),
                float(config.get("matched_favorite_probability") or 0),
            ),
            "comment": max(
                float(config.get("comment_probability") or 0),
                float(config.get("matched_comment_probability") or 0),
            ),
        }
    return {
        "like": float(config.get("like_probability") or 0),
        "favorite": float(config.get("favorite_probability") or 0),
        "comment": float(config.get("comment_probability") or 0),
    }


def _device_preview(
    store: TaskStore,
    selected_ids: Iterable[str],
    devices: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {str(item.get("device_id") or ""): item for item in devices}
    rows: list[dict[str, Any]] = []
    eligible: list[str] = []
    for device_id in selected_ids:
        raw = by_id.get(device_id)
        reason = ""
        if raw is None or raw.get("state") != "device":
            reason = "设备离线或未授权"
        elif store.running_count([device_id]):
            reason = "设备已有任务在执行"
        elif store.has_active_control_session(device_id):
            reason = "设备正在人工接管"
        else:
            initialization = str(raw.get("initialization_status") or "")
            profile_verified = bool(raw.get("profile_verified"))
            on_demand = raw.get("device_type") == "virtual" and raw.get("environment_status") == "standard"
            if not on_demand and initialization not in {"ready", "legacy"} and not profile_verified:
                reason = "设备尚未初始化或需要复验"
        capabilities = (raw or {}).get("capabilities")
        if not reason and isinstance(capabilities, Mapping):
            required = ["browse_home"]
            mode = str(config.get("content_mode") or "general")
            probabilities = _action_probabilities(config)
            if mode in {"search", "mixed", "hybrid"}:
                required.append("search_input")
            if config.get("engagement_inspection_enabled") and config.get("inspection_mode") != "home_badge":
                required.append("engagement_v3")
            if mode != "general" or bool(config.get("topic_filter_enabled")):
                required.append("topic_analysis")
            if probabilities["like"] > 0 or probabilities["favorite"] > 0:
                required.append("like_favorite")
            if probabilities["comment"] > 0:
                required.append(
                    "comment_preview" if bool(config.get("preview_only", True)) else "comment_send"
                )
            missing = [
                name
                for name in dict.fromkeys(required)
                if str((capabilities.get(name) or {}).get("status") or "") not in {"ready", "preparable"}
            ]
            if missing:
                first = capabilities.get(missing[0]) or {}
                reason = str(first.get("reason") or f"缺少任务能力：{missing[0]}")
        available = not reason
        if available:
            eligible.append(device_id)
        rows.append(
            {
                "device_id": device_id,
                "name": str((raw or {}).get("friendly_name") or device_id),
                "available": available,
                "reason": reason,
                "preparation_status": "blocked" if reason else "preparable" if any(
                    value.get("status") == "preparable" for value in ((raw or {}).get("capabilities") or {}).values()
                ) else "ready",
                "initialization_status": str(
                    (raw or {}).get("initialization_status") or "unknown"
                ),
            }
        )
    return rows, eligible


def _estimate_seconds(config: Mapping[str, Any]) -> int:
    average_dwell = (
        float(config.get("dwell_min") or 0) + float(config.get("dwell_max") or 0)
    ) / 2
    per_video = average_dwell + 4
    per_round = int(config.get("video_count") or 0) * per_video
    intervals = max(0, int(config.get("round_count") or 0) - 1) * int(
        config.get("round_interval_minutes") or 0
    ) * 60
    return max(0, round(per_round * int(config.get("round_count") or 0) + intervals))


def build_preview(
    store: TaskStore,
    draft: Mapping[str, Any],
    *,
    devices: Iterable[Mapping[str, Any]],
    paused: bool,
    model_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Compatibility callers must preview the same frozen mode they execute.
    config = normalized_config(dict(draft.get("config") or {}))
    device_rows, eligible_ids = _device_preview(
        store, config["device_ids"], devices, config
    )
    blockers: list[str] = []
    warnings: list[str] = []
    unavailable = [item for item in device_rows if not item["available"]]
    if unavailable:
        warnings.append(
            f"{len(unavailable)} 台已选设备当前不可执行，提交时不会为其创建任务"
        )
    if not eligible_ids:
        blockers.append("没有可执行设备，请检查连接、显示环境和占用状态")
    if paused:
        warnings.append("任务领取当前已暂停；提交后任务会保持排队")
    probabilities = _action_probabilities(config)
    model_required = bool(
        config.get("content_mode") != "general"
        or config.get("topic_filter_enabled")
        or probabilities["like"] > 0
        or probabilities["favorite"] > 0
        or probabilities["comment"] > 0
    )
    if model_required and model_status is not None and not bool(model_status.get("model_ready")):
        blockers.append("当前内容模式需要视觉模型，请在模型设置中测试并启用当前服务商")
    inspection_profiles = inspection_profiles_for_store(store)
    if config.get("engagement_inspection_enabled") and config.get("inspection_mode") != "home_badge" and eligible_ids:
        unsupported = [
            device_id
            for device_id in eligible_ids
            if not (
                inspection_profiles.get(device_id, {}).get("device_kind") == "virtual"
                and inspection_profiles.get(device_id, {}).get("managed_standard") is True
            )
        ]
        if unsupported:
            blockers.append("互动巡检 v3 仅支持已复验的900×1600标准虚拟机")
        else:
            reminder = visitor_reminder_status(store, eligible_ids)
            if reminder.get("reminder_pending"):
                warnings.append(reminder["message"])

    video_task_count = 0
    inspection_task_count = 0
    total_task_count = 0
    content_revision = _content_plan_revision(store, config)
    if eligible_ids:
        executable = {**config, "device_ids": eligible_ids, "device_id": eligible_ids[0]}
        try:
            plan = build_scheduled_plan(
                executable,
                plan_revision=content_revision,
                inspection_profiles=inspection_profiles,
            )
        except ValueError as exc:
            blockers.append(str(exc))
        else:
            video_task_count = plan.video_task_count
            inspection_task_count = plan.inspection_task_count
            total_task_count = len(plan.tasks)

    write_actions: list[str] = []
    if probabilities["like"] > 0:
        write_actions.append("点赞")
    if probabilities["favorite"] > 0:
        write_actions.append("收藏")
    if probabilities["comment"] > 0 and not bool(config.get("preview_only", True)):
        write_actions.append("发送评论")
    comment_mode = (
        "关闭"
        if probabilities["comment"] <= 0
        else "仅生成预览"
        if bool(config.get("preview_only", True))
        else "允许真实发送"
    )
    basis = {
        "draft_revision": int(draft.get("revision") or 0),
        "config": config,
        "eligible_device_ids": eligible_ids,
        "content_plan_revision_id": (
            content_revision.get("revision_id") if content_revision else None
        ),
    }
    plan_hash = hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "ready": not blockers,
        "draft_revision": int(draft.get("revision") or 0),
        "plan_hash": plan_hash,
        "mode": config["content_mode"],
        "topic": config.get("topic_prompt") or "不限主题",
        "search_query": config.get("search_query") or "",
        "segments": (
            {
                "search": {
                    "min": int(config.get("search_segment_min", 7)),
                    "max": int(config.get("search_segment_max", 14)),
                },
                "home": {
                    "min": int(config.get("home_segment_min", 5)),
                    "max": int(config.get("home_segment_max", 10)),
                },
            }
            if config.get("content_mode") == "hybrid"
            else None
        ),
        "devices": device_rows,
        "eligible_device_ids": eligible_ids,
        "video_task_count": video_task_count,
        "inspection_task_count": inspection_task_count,
        "total_task_count": total_task_count,
        "estimated_seconds": _estimate_seconds(config),
        "probabilities": probabilities,
        "comment_mode": comment_mode,
        "write_actions": write_actions,
        "requires_confirmation": bool(write_actions),
        "model_required": model_required,
        "model_ready": bool((model_status or {}).get("model_ready")) if model_required else True,
        "warnings": warnings,
        "blockers": blockers,
    }


def build_workbench_preview(
    store: TaskStore,
    draft: Mapping[str, Any],
    *,
    devices: Iterable[Mapping[str, Any]],
    paused: bool,
    model_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Only the task UI selects homepage inspection. Do not rewrite saved drafts.
    workbench_draft = {**draft, "config": {**dict(draft.get("config") or {}), "inspection_mode": "home_badge"}}
    return build_preview(store, workbench_draft, devices=devices, paused=paused, model_status=model_status)


def submit_previewed_draft(
    store: TaskStore,
    draft: Mapping[str, Any],
    preview: Mapping[str, Any],
    *,
    expected_plan_hash: str,
    confirm_writes: bool,
) -> Any:
    if expected_plan_hash != preview.get("plan_hash"):
        raise ValueError("任务计划已经变化，请重新核对后提交")
    if not preview.get("ready"):
        raise ValueError("当前任务仍有阻断项，不能提交")
    if preview.get("requires_confirmation") and not confirm_writes:
        actions = "、".join(preview.get("write_actions") or [])
        raise ValueError(f"本任务可能执行{actions}，请确认后再开始")
    config = normalized_config({**dict(draft.get("config") or {}), "inspection_mode": "home_badge"})
    eligible_ids = list(preview.get("eligible_device_ids") or [])
    controlled_ids = [
        device_id
        for device_id in eligible_ids
        if store.has_active_control_session(device_id)
    ]
    if controlled_ids:
        raise ValueError("设备正在人工接管，请结束操作后重新预览")
    config.update(
        {
            "device_ids": eligible_ids,
            "device_id": eligible_ids[0],
            "run_draft_snapshot": {
                "revision": int(draft.get("revision") or 0),
                "plan_hash": str(preview["plan_hash"]),
                "submitted_at": datetime.now().astimezone().isoformat(
                    timespec="milliseconds"
                ),
                "write_actions": list(preview.get("write_actions") or []),
                "comment_mode": preview.get("comment_mode"),
            },
        }
    )
    return submit_scheduled_rounds(store, config)
