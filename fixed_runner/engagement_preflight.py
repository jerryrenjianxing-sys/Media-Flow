from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from task_store import TaskStore


PROFILE_PREFIX = "engagement-v3-visitor-reminder:"


def _profile_name(virtual_device_id: str) -> str:
    return f"{PROFILE_PREFIX}{virtual_device_id}"


def _app_data_generation(
    store: TaskStore, device_id: str, virtual_device: dict[str, Any]
) -> str:
    """Return a stable generation that changes when Douyin data is re-created."""
    snapshot = virtual_device.get("provider_snapshot")
    if isinstance(snapshot, dict):
        explicit = str(
            snapshot.get("douyin_app_data_identity")
            or snapshot.get("app_data_identity")
            or ""
        ).strip()
        if explicit:
            return explicit
    latest = store.latest_initialization(device_id)
    return latest.id if latest is not None and latest.status == "ready" else ""


def visitor_reminder_status(
    store: TaskStore, device_ids: Iterable[str]
) -> dict[str, Any]:
    """Return the per-VM visitor reminder state for selected ADB endpoints."""
    devices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_device_id in device_ids:
        device_id = str(raw_device_id or "").strip()
        if not device_id or device_id in seen:
            continue
        seen.add(device_id)
        virtual_device = store.get_virtual_device_for_adb(device_id)
        if virtual_device is None:
            continue
        virtual_device_id = str(virtual_device["virtual_device_id"])
        android_identity = str(virtual_device.get("android_identity") or "").strip()
        app_data_generation = _app_data_generation(store, device_id, virtual_device)
        saved = store.get_profile(_profile_name(virtual_device_id)) or {}
        acknowledged = bool(
            android_identity
            and saved.get("android_identity") == android_identity
            and saved.get("app_data_generation", "") == app_data_generation
            and saved.get("acknowledged") is True
        )
        devices.append(
            {
                "device_id": device_id,
                "virtual_device_id": virtual_device_id,
                "name": str(virtual_device.get("name") or device_id),
                "android_identity_available": bool(android_identity),
                "app_data_generation": app_data_generation,
                "acknowledged": acknowledged,
                "acknowledged_at": saved.get("acknowledged_at") if acknowledged else None,
            }
        )
    pending = [item for item in devices if not item["acknowledged"]]
    return {
        "required": bool(pending),
        "message": (
            "请先在抖音隐私设置中打开访客记录，否则访客类互动可能不完整。"
            if pending
            else "访客记录提醒已确认"
        ),
        "devices": devices,
        "pending_device_ids": [item["device_id"] for item in pending],
    }


def acknowledge_visitor_reminder(
    store: TaskStore, device_ids: Iterable[str]
) -> dict[str, Any]:
    status = visitor_reminder_status(store, device_ids)
    if not status["devices"]:
        raise ValueError("请先选择一台已连接的标准虚拟机")
    unavailable = [
        item["name"]
        for item in status["devices"]
        if not item["android_identity_available"]
    ]
    if unavailable:
        raise ValueError(
            f"{unavailable[0]} 尚未完成设备身份核对，请先在设备管理中完成连接"
        )
    acknowledged_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
    for item in status["devices"]:
        virtual_device = store.get_virtual_device(item["virtual_device_id"])
        store.save_profile(
            _profile_name(item["virtual_device_id"]),
            {
                "acknowledged": True,
                "android_identity": str(virtual_device.get("android_identity") or ""),
                "app_data_generation": _app_data_generation(
                    store, item["device_id"], virtual_device
                ),
                "acknowledged_at": acknowledged_at,
            },
        )
    return visitor_reminder_status(store, device_ids)
