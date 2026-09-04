from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from platform_profiles import ADAPTER_VERSION, display_signature, profile_matches_runtime
from runtime_layout import APP_ROOT


SHARED_VIRTUAL_PROFILE_ROOT = APP_ROOT / "assets" / "profiles" / "virtual"


def load_shared_virtual_recipes(root: Path = SHARED_VIRTUAL_PROFILE_ROOT) -> list[dict[str, Any]]:
    recipes: list[dict[str, Any]] = []
    if not root.is_dir():
        return recipes
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        forbidden = {"device_id", "adb_endpoint", "android_identity", "account", "coordinates"}
        if forbidden.intersection(payload):
            continue
        recipes.append(dict(payload))
    return recipes


def resolve_execution_profile(
    *,
    device_id: str,
    is_virtual: bool,
    runtime: Mapping[str, Any] | None,
    local_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if runtime is None:
        return {"status": "unavailable", "source": "none", "reason": "无法读取设备运行签名"}
    app_version = str(runtime.get("app_version") or "")
    display = dict(runtime.get("display") or {})
    if profile_matches_runtime(
        dict(local_profile) if local_profile else None,
        app_version=app_version,
        display=display,
    ):
        return {
            "status": "ready",
            "source": "local_instance_profile" if is_virtual else "local_physical_profile",
            "signature": display_signature(display),
            "reason": "本机档案与当前设备一致",
        }
    if not is_virtual:
        return {
            "status": "requires_verification",
            "source": "local_physical_profile" if local_profile else "none",
            "signature": display_signature(display),
            "reason": "真机需要在当前电脑连接并重新复验",
        }
    for recipe in load_shared_virtual_recipes():
        signature = recipe.get("signature") if isinstance(recipe.get("signature"), dict) else {}
        display_expected = signature.get("display") if isinstance(signature.get("display"), dict) else {}
        if any(display.get(name) != value for name, value in display_expected.items()):
            continue
        if str(signature.get("adapter_version") or ADAPTER_VERSION) != ADAPTER_VERSION:
            continue
        compatible_prefixes = signature.get("douyin_version_prefixes") or []
        if compatible_prefixes and not any(app_version.startswith(str(prefix)) for prefix in compatible_prefixes):
            continue
        return {
            "status": "requires_verification",
            "source": "shared_virtual_recipe",
            "recipe_id": recipe.get("id"),
            "signature": display_signature(display),
            "reason": "共享虚拟机配方匹配；实例仍需快速语义复验和零写入自检",
        }
    return {
        "status": "requires_verification",
        "source": "none",
        "signature": display_signature(display),
        "reason": "当前虚拟机签名没有可复用配方，需要初始化",
    }
