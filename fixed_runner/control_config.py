from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from model_connection import (
    save_candidate as save_openrouter_key,
    status as openrouter_key_status,
    validate_openrouter_key,
)
from content_plans import round_snapshot
from device_profiles import load_device_profile_payloads
from task_store import TaskStore

PRESET_PREFIX = "preset:"
DEFAULT_CONFIG: dict[str, Any] = {
    "device_id": "",
    "device_ids": [],
    "video_count": 20,
    "round_count": 1,
    "round_interval_minutes": 0,
    "engagement_inspection_enabled": False,
    "inspection_every_rounds": 5,
    "dwell_min": 6.0,
    "dwell_max": 15.0,
    "like_probability": 0.20,
    "favorite_probability": 0.10,
    "comment_probability": 0.05,
    "matched_like_probability": 0.80,
    "matched_favorite_probability": 0.70,
    "matched_comment_probability": 0.50,
    "content_mode": "general",
    "search_query": "",
    "search_trust_results": False,
    "search_segment_min": 7,
    "search_segment_max": 14,
    "home_segment_min": 5,
    "home_segment_max": 10,
    "topic_prompt": "不限主题",
    "content_plan_id": None,
    "content_plan_revision_id": None,
    "topic_filter_enabled": False,
    "topic_confidence": 0.78,
    "like_only_on_match": False,
    "engagement_requires_topic": False,
    "comment_requires_topic": False,
    "comment_policy_enabled": False,
    "comment_policy_prompt": "不对日常生活相关内容发表评论",
    "preview_only": True,
    "seed": 20260821,
    "max_gate_skips": 6,
    "max_likes": 20,
    "max_favorites": 20,
    "max_comments": 20,
    "auto_onboard_root_emulators": False,
    "auto_run_after_onboarding": False,
    "emulator_identity_registry": {},
}

PRESET_FIELDS = (
    "video_count",
    "round_count",
    "round_interval_minutes",
    "dwell_min",
    "dwell_max",
    "like_probability",
    "favorite_probability",
    "comment_probability",
    "matched_like_probability",
    "matched_favorite_probability",
    "matched_comment_probability",
    "content_mode",
    "search_query",
    "search_trust_results",
    "search_segment_min",
    "search_segment_max",
    "home_segment_min",
    "home_segment_max",
    "topic_prompt",
    "content_plan_id",
    "content_plan_revision_id",
    "topic_filter_enabled",
    "topic_confidence",
    "like_only_on_match",
    "engagement_requires_topic",
    "comment_requires_topic",
    "comment_policy_enabled",
    "comment_policy_prompt",
    "max_gate_skips",
)


def inspection_profiles_for_store(store: TaskStore) -> dict[str, dict[str, Any]]:
    """Bind local inspection profiles to the authoritative VM inventory.

    ADB endpoints are runtime addresses.  The inventory is the source of truth
    for whether a selected endpoint belongs to a managed standard VM, so task
    planning cannot silently treat a standard VM as a legacy v1 device.
    """
    profiles = load_device_profile_payloads()
    for virtual_device in store.list_managed_virtual_devices():
        endpoint = str(virtual_device.get("adb_endpoint") or "").strip()
        if not endpoint:
            continue
        current = dict(profiles.get(endpoint) or {})
        current.update(
            device_kind="virtual",
            managed_standard=(virtual_device.get("standard_status") == "standard"),
            virtual_device_id=str(virtual_device.get("virtual_device_id") or ""),
            android_identity=str(virtual_device.get("android_identity") or ""),
        )
        profiles[endpoint] = current
    return profiles

BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    "保守预演": {
        "video_count": 10,
        "round_count": 1,
        "round_interval_minutes": 0,
        "dwell_min": 8,
        "dwell_max": 18,
        "like_probability": 0.10,
        "favorite_probability": 0.05,
        "comment_probability": 0,
        "matched_like_probability": 0.20,
        "matched_favorite_probability": 0.10,
        "matched_comment_probability": 0,
        "content_mode": "general",
        "search_query": "",
        "search_trust_results": False,
        "topic_prompt": "不限主题",
        "topic_filter_enabled": False,
        "topic_confidence": 0.78,
        "like_only_on_match": False,
        "engagement_requires_topic": False,
        "comment_requires_topic": False,
        "max_gate_skips": 6,
    },
    "均衡测试": {
        "video_count": 20,
        "round_count": 1,
        "round_interval_minutes": 0,
        "dwell_min": 6,
        "dwell_max": 15,
        "like_probability": 0.20,
        "favorite_probability": 0.10,
        "comment_probability": 0.05,
        "matched_like_probability": 0.80,
        "matched_favorite_probability": 0.60,
        "matched_comment_probability": 0.35,
        "content_mode": "general",
        "search_query": "",
        "search_trust_results": False,
        "topic_prompt": "不限主题",
        "topic_filter_enabled": False,
        "topic_confidence": 0.78,
        "like_only_on_match": False,
        "engagement_requires_topic": False,
        "comment_requires_topic": False,
        "max_gate_skips": 6,
    },
    "长时稳定性": {
        "video_count": 100,
        "round_count": 3,
        "round_interval_minutes": 15,
        "dwell_min": 8,
        "dwell_max": 25,
        "like_probability": 0.15,
        "favorite_probability": 0.05,
        "comment_probability": 0.05,
        "matched_like_probability": 0.60,
        "matched_favorite_probability": 0.40,
        "matched_comment_probability": 0.20,
        "content_mode": "general",
        "search_query": "",
        "search_trust_results": False,
        "topic_prompt": "不限主题",
        "topic_filter_enabled": False,
        "topic_confidence": 0.80,
        "like_only_on_match": False,
        "engagement_requires_topic": False,
        "comment_requires_topic": False,
        "max_gate_skips": 8,
    },
    "主题搜索测试": {
        "video_count": 20,
        "round_count": 1,
        "round_interval_minutes": 0,
        "dwell_min": 6,
        "dwell_max": 15,
        "like_probability": 0.05,
        "favorite_probability": 0.05,
        "comment_probability": 0,
        "matched_like_probability": 0.30,
        "matched_favorite_probability": 0.20,
        "matched_comment_probability": 0.10,
        "content_mode": "search",
        "search_query": "人工智能 智能制造 塑料包装",
        "search_trust_results": True,
        "topic_prompt": "AI、智能制造与塑料包装",
        "topic_filter_enabled": True,
        "topic_confidence": 1.0,
        "like_only_on_match": False,
        "engagement_requires_topic": False,
        "comment_requires_topic": True,
        "max_gate_skips": 6,
    },
    "搜索＋主页交替测试": {
        "video_count": 20,
        "round_count": 1,
        "round_interval_minutes": 0,
        "dwell_min": 6,
        "dwell_max": 15,
        "like_probability": 0.20,
        "favorite_probability": 0.10,
        "comment_probability": 0.05,
        "matched_like_probability": 0.30,
        "matched_favorite_probability": 0.20,
        "matched_comment_probability": 0.10,
        "content_mode": "hybrid",
        "search_query": "人工智能 智能制造 塑料包装",
        "search_trust_results": True,
        "search_segment_min": 7,
        "search_segment_max": 14,
        "home_segment_min": 5,
        "home_segment_max": 10,
        "topic_prompt": "AI、智能制造与塑料包装",
        "topic_filter_enabled": True,
        "topic_confidence": 1.0,
        "like_only_on_match": False,
        "engagement_requires_topic": False,
        "comment_requires_topic": True,
        "max_gate_skips": 6,
    },
}


@dataclass(frozen=True)
class PlannedTask:
    task_type: str
    device_id: str
    payload: dict[str, Any]
    not_before: str


@dataclass(frozen=True)
class SubmissionPlan:
    tasks: tuple[PlannedTask, ...]
    video_task_count: int
    inspection_task_count: int
    device_plans: tuple[dict[str, Any], ...]


class SubmissionResult(list[str]):
    """List-compatible submission receipt for old callers and new summaries."""

    def __init__(
        self,
        task_ids: Iterable[str],
        *,
        video_task_count: int,
        inspection_task_count: int,
        device_plans: Iterable[dict[str, Any]],
    ) -> None:
        super().__init__(task_ids)
        self.video_task_count = int(video_task_count)
        self.inspection_task_count = int(inspection_task_count)
        self.device_plans = tuple(dict(item) for item in device_plans)


def normalized_config(raw: dict[str, Any]) -> dict[str, Any]:
    config = {**DEFAULT_CONFIG, **raw}
    raw_device_ids = raw.get("device_ids")
    if not isinstance(raw_device_ids, list):
        raw_device_id = str(raw.get("device_id") or "").strip()
        raw_device_ids = [raw_device_id] if raw_device_id else []
    device_ids = list(
        dict.fromkeys(str(value).strip() for value in raw_device_ids if str(value).strip())
    )
    if len(device_ids) > 8:
        raise ValueError("最多选择 8 台设备")
    config["device_ids"] = device_ids
    config["device_id"] = device_ids[0] if device_ids else ""
    config["topic_prompt"] = str(config["topic_prompt"]).strip()[:800]
    for name in ("content_plan_id", "content_plan_revision_id"):
        value = config.get(name)
        config[name] = str(value).strip()[:80] if value else None
    if bool(config["content_plan_id"]) != bool(config["content_plan_revision_id"]):
        raise ValueError("内容计划和版本必须同时选择")
    config["comment_policy_prompt"] = " ".join(
        str(config.get("comment_policy_prompt", "")).split()
    )[:1000]
    config["comment_policy_enabled"] = bool(config.get("comment_policy_enabled", False))
    if config["comment_policy_enabled"] and not config["comment_policy_prompt"]:
        raise ValueError("启用评论约束后请输入约束内容")
    config["search_query"] = str(config.get("search_query", "")).strip()[:80]
    config["video_count"] = int(config["video_count"])
    config["round_count"] = int(config["round_count"])
    config["round_interval_minutes"] = int(config["round_interval_minutes"])
    config["engagement_inspection_enabled"] = bool(
        raw.get("engagement_inspection_enabled", False)
    )
    config["inspection_every_rounds"] = int(
        raw.get("inspection_every_rounds", 5)
    )
    if not 1 <= config["inspection_every_rounds"] <= 20:
        raise ValueError("每几轮检查互动必须是 1 到 20")
    config["dwell_min"] = float(config["dwell_min"])
    config["dwell_max"] = float(config["dwell_max"])
    for name in (
        "like_probability",
        "favorite_probability",
        "comment_probability",
        "matched_like_probability",
        "matched_favorite_probability",
        "matched_comment_probability",
    ):
        config[name] = float(config[name])
    config["topic_confidence"] = 1.0
    config["seed"] = int(config["seed"])
    # Migrate old saved values such as 0 or 99 into the new bounded
    # consecutive-anomaly threshold without making the control API unavailable.
    config["max_gate_skips"] = max(1, min(50, int(config["max_gate_skips"])))
    # Compatibility fields remain in task payloads for older workers, but no
    # longer impose a second ceiling on probability-driven actions.
    for name in ("max_likes", "max_favorites", "max_comments"):
        config[name] = config["video_count"]
    config["preview_only"] = bool(config["preview_only"])
    config["auto_onboard_root_emulators"] = bool(
        config.get("auto_onboard_root_emulators", False)
    )
    # Kept in serialized profiles for compatibility, but onboarding now stops
    # after the audited three-video zero-write validation. Formal tasks always
    # require an explicit submission from the workbench.
    config["auto_run_after_onboarding"] = False
    raw_registry = config.get("emulator_identity_registry", {})
    if not isinstance(raw_registry, dict):
        raw_registry = {}
    config["emulator_identity_registry"] = {
        str(identity).strip()[:128]: str(device_id).strip()[:160]
        for identity, device_id in raw_registry.items()
        if str(identity).strip() and str(device_id).strip()
    }
    legacy_topic_filter = bool(config["topic_filter_enabled"])
    content_mode = str(raw.get("content_mode") or ("mixed" if legacy_topic_filter else "general"))
    if content_mode not in {"general", "mixed", "search", "hybrid"}:
        raise ValueError("内容模式必须是不限主题、混合主题、搜索主题或搜索主页交替")
    config["content_mode"] = content_mode
    for name, fallback in (
        ("search_segment_min", 7),
        ("search_segment_max", 14),
        ("home_segment_min", 5),
        ("home_segment_max", 10),
    ):
        # These settings are meaningful only in hybrid mode. Old tasks in the
        # other modes may carry stale values, so ignore rather than reject them.
        config[name] = int(config.get(name, fallback)) if content_mode == "hybrid" else fallback
    # The field is deliberately read from the original payload. A saved preset
    # or queued task created before this feature must not silently inherit the
    # new search-source trust behavior from DEFAULT_CONFIG.
    config["search_trust_results"] = bool(
        raw.get("search_trust_results", False)
    )
    config["engagement_requires_topic"] = False
    config["comment_requires_topic"] = False
    # Keep the old fields in saved payloads so existing runners remain compatible.
    config["like_only_on_match"] = False
    config["topic_filter_enabled"] = content_mode != "general"
    TaskStore.validate_payload("douyin_topic_session", config)
    return config


def validate_preset_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("请输入预设名称")
    if len(name) > 40:
        raise ValueError("预设名称最多 40 个字符")
    if name in BUILTIN_PRESETS:
        raise ValueError("内置预设不能覆盖")
    return name


def preset_values(raw: dict[str, Any]) -> dict[str, Any]:
    config = normalized_config({**DEFAULT_CONFIG, **raw})
    return {name: config[name] for name in PRESET_FIELDS}


def list_presets(store: TaskStore) -> list[dict[str, Any]]:
    presets = [
        {"name": name, "builtin": True, "config": preset_values(config)}
        for name, config in BUILTIN_PRESETS.items()
    ]
    presets.extend(
        {
            "name": item["name"][len(PRESET_PREFIX) :],
            "builtin": False,
            "config": preset_values(item["config"]),
        }
        for item in store.list_profiles(PRESET_PREFIX)
    )
    return presets


def save_preset(store: TaskStore, name: Any, raw: Any) -> dict[str, Any]:
    preset_name = validate_preset_name(name)
    if not isinstance(raw, dict):
        raise ValueError("预设参数格式无效")
    values = preset_values(raw)
    store.save_profile(f"{PRESET_PREFIX}{preset_name}", values)
    return {"name": preset_name, "builtin": False, "config": values}


def delete_preset(store: TaskStore, name: Any) -> bool:
    preset_name = validate_preset_name(name)
    return store.delete_profile(f"{PRESET_PREFIX}{preset_name}")


def build_scheduled_plan(
    config: dict[str, Any],
    *,
    base_time: datetime | None = None,
    submission_id: str | None = None,
    plan_revision: dict[str, Any] | None = None,
    inspection_profiles: dict[str, dict[str, Any]] | None = None,
) -> SubmissionPlan:
    """Build a deterministic per-device queue without mutating the task store."""
    if not config.get("device_ids"):
        raise ValueError("请选择至少一台可执行设备")
    base_time = base_time or datetime.now().astimezone()
    base_seed = int(config["seed"])
    interval = int(config["round_interval_minutes"])
    round_count = int(config["round_count"])
    submission_id = submission_id or uuid.uuid4().hex
    inspection_enabled = bool(config.get("engagement_inspection_enabled", False))
    inspection_every = int(config.get("inspection_every_rounds", 5))
    planned: list[PlannedTask] = []
    device_plans: list[dict[str, Any]] = []
    inspection_profiles = inspection_profiles or {}
    for device_offset, device_id in enumerate(config["device_ids"]):
        queue_position = 0
        device_video_count = 0
        device_inspection_count = 0
        for index in range(round_count):
            round_config = {
                **config,
                "device_id": device_id,
                "round_index": index + 1,
                "seed": base_seed + device_offset * round_count + index,
                "submission_id": submission_id,
            }
            if plan_revision is not None:
                snapshot = round_snapshot(plan_revision, index + 1)
                theme = snapshot["theme"]
                round_config.update(
                    {
                        "content_plan_snapshot": snapshot,
                        "topic_prompt": theme["topic_prompt"],
                        "search_query": theme["search_query"],
                    }
                )
            not_before = (
                base_time
                + timedelta(minutes=index * interval, microseconds=queue_position + 1)
            ).isoformat(
                timespec="microseconds"
            )
            planned.append(
                PlannedTask(
                    "douyin_topic_session",
                    device_id,
                    round_config,
                    not_before=not_before,
                )
            )
            queue_position += 1
            device_video_count += 1
            round_index = index + 1
            if inspection_enabled and round_index % inspection_every == 0:
                inspection_config = {
                    **config,
                    "device_id": device_id,
                    "submission_id": submission_id,
                    "inspection_index": device_inspection_count + 1,
                    "after_round_index": round_index,
                    "inspection_every_rounds": inspection_every,
                    "max_items_per_section": 20,
                }
                inspection_profile = inspection_profiles.get(device_id, {})
                standard_virtual = (
                    inspection_profile.get("device_kind") == "virtual"
                    and inspection_profile.get("managed_standard") is True
                )
                workflow = str(
                    inspection_profile.get("engagement_inspection_version")
                    or ("v3" if standard_virtual else "v1")
                )
                inspection_config["inspection_workflow_version"] = (
                    workflow if workflow in {"v1", "v2", "v3"} else "v1"
                )
                if standard_virtual and inspection_config["inspection_workflow_version"] != "v3":
                    raise ValueError(
                        f"device {device_id} requires a stable v3 calibration"
                    )
                inspection_config["max_items_per_section"] = (
                    100
                    if inspection_config["inspection_workflow_version"] in {"v2", "v3"}
                    else 20
                )
                if inspection_config["inspection_workflow_version"] in {"v2", "v3"}:
                    workflow_version = inspection_config["inspection_workflow_version"]
                    calibration = inspection_profile.get("engagement_calibration")
                    if (
                        not isinstance(calibration, Mapping)
                        or int(calibration.get("passes") or 0) < 3
                        or calibration.get("later_passes_semantically_equal") is not True
                        or (
                            workflow_version == "v3"
                            and "互动消息"
                            not in list(
                                (calibration.get("controls") or {}).get("aggregate")
                                if isinstance(calibration.get("controls"), Mapping)
                                else []
                            )
                        )
                    ):
                        raise ValueError(
                            f"device {device_id} requires a stable v3 calibration"
                            if workflow_version == "v3"
                            else f"device {device_id} requires a stable three-pass calibration for v2"
                        )
                    inspection_config["expected_app_version"] = str(
                        inspection_profile.get("engagement_app_version") or ""
                    )
                    inspection_config["expected_display_signature"] = str(
                        inspection_profile.get("engagement_display_signature") or ""
                    )
                    calibration_snapshot = json.loads(
                        json.dumps(calibration, ensure_ascii=False)
                    )
                    calibration_snapshot.setdefault(
                        "sections",
                        (
                            {
                                "received_likes": True,
                                "comment_danmaku": True,
                                "profile_visitors": True,
                            }
                            if workflow_version == "v3"
                            else {
                                "private_messages": True,
                                "received_likes": True,
                                "received_comments": True,
                                "received_danmaku": True,
                                "profile_visitors": True,
                            }
                        ),
                    )
                    inspection_config["inspection_calibration"] = calibration_snapshot
                inspection_config.pop("round_index", None)
                inspection_not_before = (
                    base_time
                    + timedelta(
                        minutes=index * interval,
                        microseconds=queue_position + 1,
                    )
                ).isoformat(timespec="microseconds")
                planned.append(
                    PlannedTask(
                        "douyin_engagement_inspection",
                        device_id,
                        inspection_config,
                        not_before=inspection_not_before,
                    )
                )
                queue_position += 1
                device_inspection_count += 1
        device_plans.append(
            {
                "device_id": device_id,
                "video_task_count": device_video_count,
                "inspection_task_count": device_inspection_count,
                "total_task_count": device_video_count + device_inspection_count,
            }
        )
    return SubmissionPlan(
        tasks=tuple(planned),
        video_task_count=sum(item["video_task_count"] for item in device_plans),
        inspection_task_count=sum(
            item["inspection_task_count"] for item in device_plans
        ),
        device_plans=tuple(device_plans),
    )


def submit_scheduled_rounds(store: TaskStore, config: dict[str, Any]) -> SubmissionResult:
    """Queue independently auditable rounds and interleaved inspections."""
    plan_revision: dict[str, Any] | None = None
    if config.get("content_mode") != "general" and config.get("content_plan_revision_id"):
        try:
            plan_revision = store.get_content_plan_revision(
                str(config["content_plan_revision_id"])
            )
        except KeyError as exc:
            raise ValueError("所选内容计划版本不存在") from exc
        if plan_revision["plan_id"] != config.get("content_plan_id"):
            raise ValueError("内容计划与版本不匹配")
    plan = build_scheduled_plan(
        config,
        plan_revision=plan_revision,
        inspection_profiles=inspection_profiles_for_store(store),
    )
    task_ids = [
        store.submit(
            item.task_type,
            item.device_id,
            item.payload,
            not_before=item.not_before,
        )
        for item in plan.tasks
    ]
    return SubmissionResult(
        task_ids,
        video_task_count=plan.video_task_count,
        inspection_task_count=plan.inspection_task_count,
        device_plans=plan.device_plans,
    )

