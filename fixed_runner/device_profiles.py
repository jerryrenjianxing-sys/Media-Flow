from __future__ import annotations

import json
import msvcrt
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_layout import DEVICE_PROFILE_PATH
from host_identity import installation_id

PROFILE_PATH = DEVICE_PROFILE_PATH


@dataclass(frozen=True)
class DeviceProfile:
    device_id: str
    friendly_name: str
    manufacturer: str
    model: str
    width: int | None
    height: int | None
    density: int | None
    navigation_mode: str | None
    home_fallback: tuple[float, float] | None
    verified: bool
    verified_at: str | None
    control_backend: str

    def fallback_point(self, width: int, height: int) -> tuple[int, int] | None:
        if (
            not self.verified
            or self.home_fallback is None
            or self.width != width
            or self.height != height
        ):
            return None
        return (
            round(self.home_fallback[0] * width),
            round(self.home_fallback[1] * height),
        )


def _profile_from_payload(device_id: str, raw: dict[str, Any]) -> DeviceProfile:
    display = raw.get("display") if isinstance(raw.get("display"), dict) else {}
    fallback = raw.get("home_fallback")
    normalized_fallback = None
    if (
        isinstance(fallback, list)
        and len(fallback) == 2
        and all(isinstance(value, (int, float)) for value in fallback)
    ):
        normalized_fallback = (float(fallback[0]), float(fallback[1]))
    return DeviceProfile(
        device_id=device_id,
        friendly_name=str(raw.get("friendly_name") or device_id),
        manufacturer=str(raw.get("manufacturer") or ""),
        model=str(raw.get("model") or ""),
        width=int(display["width"]) if display.get("width") else None,
        height=int(display["height"]) if display.get("height") else None,
        density=int(display["density"]) if display.get("density") else None,
        navigation_mode=(
            str(display["navigation_mode"])
            if display.get("navigation_mode") is not None
            else None
        ),
        home_fallback=normalized_fallback,
        verified=bool(raw.get("verified", False)),
        verified_at=str(raw.get("verified_at")) if raw.get("verified_at") else None,
        control_backend=str(raw.get("control_backend") or "uiautomator2"),
    )


def load_device_profiles(path: Path = PROFILE_PATH) -> dict[str, DeviceProfile]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    devices = payload.get("devices") if isinstance(payload, dict) else None
    owner = str(payload.get("host_installation_id") or "") if isinstance(payload, dict) else ""
    if owner and owner != installation_id():
        return {}
    if not isinstance(devices, dict):
        return {}
    return {
        str(device_id): _profile_from_payload(str(device_id), raw)
        for device_id, raw in devices.items()
        if isinstance(raw, dict)
    }


def load_device_profile_payloads(path: Path = PROFILE_PATH) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    devices = payload.get("devices") if isinstance(payload, dict) else None
    owner = str(payload.get("host_installation_id") or "") if isinstance(payload, dict) else ""
    if owner and owner != installation_id():
        return {}
    if not isinstance(devices, dict):
        return {}
    return {
        str(device_id): dict(raw)
        for device_id, raw in devices.items()
        if isinstance(raw, dict)
    }


def get_device_profile(device_id: str, path: Path = PROFILE_PATH) -> DeviceProfile | None:
    return load_device_profiles(path).get(str(device_id))


def enrich_device_statuses(statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profiles = load_device_profiles()
    enriched: list[dict[str, Any]] = []
    for status in statuses:
        item = dict(status)
        profile = profiles.get(str(item.get("device_id", "")))
        item["friendly_name"] = profile.friendly_name if profile else item.get("device_id")
        item["profile_verified"] = bool(profile and profile.verified)
        item["model"] = profile.model if profile else ""
        enriched.append(item)
    return enriched


def merge_device_probe(
    device_id: str,
    probe: dict[str, Any],
    existing: dict[str, Any] | None = None,
    *,
    ordinal: int = 1,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Merge read-only ADB facts without silently trusting stale coordinates."""
    prior = dict(existing or {})
    display = dict(probe.get("display") or {})
    prior_display = dict(prior.get("display") or {})
    signature_changed = bool(prior_display) and any(
        prior_display.get(name) != display.get(name)
        for name in ("width", "height", "density", "navigation_mode")
    )
    manufacturer = str(probe.get("manufacturer") or prior.get("manufacturer") or "Android")
    model = str(probe.get("model") or prior.get("model") or "未知型号")
    suffix = str(device_id)[-6:].upper()
    payload = {
        **prior,
        "friendly_name": str(
            prior.get("friendly_name")
            or f"{manufacturer.title()}-{ordinal:02d}-{suffix}"
        ),
        "manufacturer": manufacturer,
        "model": model,
        "display": display,
        "control_backend": str(prior.get("control_backend") or "uiautomator2"),
        "mbh_mode": str(prior.get("mbh_mode") or "adb-only"),
        "last_observed_at": observed_at or datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if signature_changed:
        payload.update(home_fallback=None, verified=False, verified_at=None)
    else:
        payload.setdefault("home_fallback", None)
        payload.setdefault("verified", False)
        payload.setdefault("verified_at", None)
    return payload


def save_device_profile_payload(payload: dict[str, Any], path: Path = PROFILE_PATH) -> None:
    payload = {**payload, "host_installation_id": installation_id()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@contextmanager
def _profile_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def upsert_device_profile(
    device_id: str,
    profile: dict[str, Any],
    path: Path = PROFILE_PATH,
) -> dict[str, Any]:
    """Atomically merge one device without losing another worker's profile."""
    with _profile_lock(path):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {"schema_version": 2, "devices": {}}
        if not isinstance(payload, dict):
            payload = {"schema_version": 2, "devices": {}}
        payload["host_installation_id"] = installation_id()
        devices = payload.setdefault("devices", {})
        if not isinstance(devices, dict):
            devices = {}
            payload["devices"] = devices
        devices[str(device_id)] = dict(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    return dict(profile)
