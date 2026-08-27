from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from runtime_control import SECRET_PATH
from task_store import TaskStore
from worker import DEFAULT_DEVICE_ID

PRESET_PREFIX = "preset:"
OPENROUTER_KEY_PATH = SECRET_PATH
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
    "matched_like_probability": 0.80,
    "matched_favorite_probability": 0.70,
    "matched_comment_probability": 0.50,
    "content_mode": "general",
    "search_query": "",
    "topic_prompt": "不限主题",
    "topic_filter_enabled": False,
    "topic_confidence": 0.78,
    "like_only_on_match": False,
    "engagement_requires_topic": False,
    "comment_requires_topic": False,
    "preview_only": True,
    "seed": 20260821,
    "max_gate_skips": 6,
    "max_likes": 20,
    "max_favorites": 20,
    "max_comments": 20,
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
    "topic_prompt",
    "topic_filter_enabled",
    "topic_confidence",
    "like_only_on_match",
    "engagement_requires_topic",
    "comment_requires_topic",
    "max_gate_skips",
)

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
        "topic_prompt": "不限主题",
        "topic_filter_enabled": False,
        "topic_confidence": 0.80,
        "like_only_on_match": False,
        "engagement_requires_topic": False,
        "comment_requires_topic": False,
        "max_gate_skips": 8,
    },
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
    config["topic_prompt"] = str(config["topic_prompt"]).strip()[:800]
    config["search_query"] = str(config.get("search_query", "")).strip()[:80]
    config["video_count"] = int(config["video_count"])
    config["round_count"] = int(config["round_count"])
    config["round_interval_minutes"] = int(config["round_interval_minutes"])
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
    legacy_topic_filter = bool(config["topic_filter_enabled"])
    content_mode = str(raw.get("content_mode") or ("mixed" if legacy_topic_filter else "general"))
    if content_mode not in {"general", "mixed", "search"}:
        raise ValueError("内容模式必须是不限主题、混合主题或搜索主题")
    config["content_mode"] = content_mode
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

