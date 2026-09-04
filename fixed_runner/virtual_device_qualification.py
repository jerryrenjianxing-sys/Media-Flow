from __future__ import annotations

from typing import Any, Mapping


STANDARD_WIDTH = 900
STANDARD_HEIGHT = 1600
STANDARD_DPI = 320
PROFILE_BUNDLE_ID = "mediaflow-mumu-900x1600-320-v1"
UI_COMPATIBILITY_ID = "douyin-semantic-v3"


def _number(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def assess_environment(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """Assess only the portable display contract; all other facts are capabilities."""
    values = dict(settings or {})
    actual = {
        "width": _number(values.get("resolution_width.custom", values.get("width"))),
        "height": _number(values.get("resolution_height.custom", values.get("height"))),
        "dpi": _number(values.get("resolution_dpi.custom", values.get("density", values.get("dpi")))),
    }
    expected = {"width": STANDARD_WIDTH, "height": STANDARD_HEIGHT, "dpi": STANDARD_DPI}
    mismatches = [
        {"field": key, "actual": actual[key], "expected": expected[key]}
        for key in expected
        if actual[key] != expected[key]
    ]
    if any(value is None for value in actual.values()):
        status = "unverified"
        message = "暂时无法确认实际分辨率和DPI；连接后可自动复核"
    elif mismatches:
        status = "needs_display_fix"
        message = "显示环境需要调整为900×1600、320 DPI"
    else:
        status = "standard"
        message = "显示环境符合900×1600、320 DPI标准"
    return {
        "environment_status": status,
        "environment_mismatches": mismatches,
        "environment": actual,
        "message": message,
    }


def qualify_virtual_device(
    virtual_device: Mapping[str, Any],
    *,
    connected: bool,
    initialization_status: str = "",
    model_ready: bool = False,
    verified_capabilities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one backend-owned capability matrix for UI and task preflight."""
    snapshot = virtual_device.get("provider_snapshot")
    snapshot = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    settings = snapshot.get("settings")
    environment = assess_environment(settings if isinstance(settings, Mapping) else {})
    if (
        environment["environment_status"] == "unverified"
        and str(virtual_device.get("standard_status") or "") == "standard"
    ):
        environment.update(
            environment_status="standard",
            environment_mismatches=[],
            message="最近一次回读符合900×1600、320 DPI；连接后将再次复核",
        )
    env_ready = environment["environment_status"] == "standard"
    presence_ready = str(virtual_device.get("presence_status") or "present") == "present"
    profile_ready = str(virtual_device.get("profile_status") or "") == "ready"
    init_ready = initialization_status == "ready" or profile_ready
    state = str(virtual_device.get("state") or "")
    waiting_app = state == "waiting_app"
    waiting_login = state == "waiting_login"
    verified = dict(verified_capabilities or {})

    def was_verified(name: str, fallback: bool = False) -> bool:
        value = verified.get(name)
        return bool(value) if value is not None else fallback

    def capability(ready: bool, reason: str, remediation: str | None = None) -> dict[str, Any]:
        return {
            "status": "ready" if ready else "unavailable",
            "reason": "可用" if ready else reason,
            "remediation": remediation,
        }

    base_ready = presence_ready and connected
    browse_ready = base_ready and env_ready and was_verified("main_feed", init_ready)
    input_ready = was_verified("chinese_input")
    search_ready = browse_ready and was_verified("search") and input_ready
    engagement_ready = browse_ready and was_verified("engagement_v3")
    like_ready = browse_ready and was_verified("like") and model_ready
    favorite_ready = browse_ready and was_verified("favorite") and model_ready
    comment_panel_ready = browse_ready and was_verified("comment")
    capabilities = {
        "adb_view": capability(base_ready, "ADB尚未连接", "connect_adb"),
        "browse_home": capability(
            browse_ready,
            "需要连接、标准显示环境和一次首页只读复验",
            "verify_browsing" if base_ready and env_ready else "normalize_display" if base_ready else "connect_adb",
        ),
        "search_input": capability(
            search_ready and not waiting_app and not waiting_login,
            "需要抖音、登录和中文输入组件",
            "install_input_component",
        ),
        "engagement_v3": capability(
            engagement_ready,
            "需要互动消息v3共享规则的一次只读复验",
            "verify_engagement_v3",
        ),
        "topic_analysis": capability(browse_ready and model_ready, "视觉模型尚未验证", "configure_model"),
        "like_favorite": capability(like_ready and favorite_ready, "需要首页控件和已验证模型", "configure_model"),
        "comment_preview": capability(
            comment_panel_ready and model_ready and input_ready and not waiting_app and not waiting_login,
            "需要模型、抖音登录和中文输入组件",
            "verify_search_input",
        ),
        "comment_send": capability(
            comment_panel_ready and model_ready and input_ready and not waiting_app and not waiting_login,
            "需要评论预览、安全检查和写入确认",
            "verify_comment_safety",
        ),
    }
    remediations = []
    for action in (
        "connect_adb" if not connected else None,
        "normalize_display" if connected and not env_ready else None,
        "install_douyin" if waiting_app else None,
        "verify_browsing" if base_ready and env_ready and not init_ready else None,
    ):
        if action and action not in remediations:
            remediations.append(action)
    return {
        **environment,
        "auto_managed": bool(virtual_device.get("managed", True)),
        "capabilities": capabilities,
        "task_eligibility": {
            "screen": capabilities["adb_view"]["status"] == "ready",
            "browse": capabilities["browse_home"]["status"] == "ready",
            "search": capabilities["search_input"]["status"] == "ready",
            "engagement_inspection": capabilities["engagement_v3"]["status"] == "ready",
            "writes": capabilities["like_favorite"]["status"] == "ready",
        },
        "profile_bundle_id": PROFILE_BUNDLE_ID if env_ready else None,
        "ui_compatibility_id": UI_COMPATIBILITY_ID if profile_ready else None,
        "remediations": remediations,
    }
