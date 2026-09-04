from __future__ import annotations

import json
import msvcrt
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from runtime_layout import PLATFORM_PROFILE_PATH


ADAPTER_VERSION = "douyin-adapter-v1"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def display_signature(display: dict[str, Any]) -> str:
    return "x".join(
        str(display.get(name) if display.get(name) is not None else "unknown")
        for name in ("width", "height", "density", "orientation", "navigation_mode")
    )


def profile_key(
    device_id: str,
    package_name: str,
    app_version: str,
    display: dict[str, Any],
    adapter_version: str = ADAPTER_VERSION,
) -> str:
    return "|".join(
        (device_id, package_name, app_version, display_signature(display), adapter_version)
    )


def normalize_bounds(
    bounds: tuple[int, int, int, int], width: int, height: int
) -> list[float]:
    if width <= 0 or height <= 0:
        raise ValueError("Display size must be positive")
    left, top, right, bottom = bounds
    return [
        round(left / width, 6),
        round(top / height, 6),
        round(right / width, 6),
        round(bottom / height, 6),
    ]


@dataclass(frozen=True)
class PlatformProfile:
    key: str
    device_id: str
    platform_id: str
    package_name: str
    app_version: str
    display_signature: str
    adapter_version: str
    status: str
    controls: dict[str, dict[str, Any]]
    capabilities: dict[str, bool]
    verified_at: str
    initialization_id: str


@contextmanager
def _profile_lock(path: Path) -> Iterator[None]:
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


def _read_payload(path: Path = PLATFORM_PROFILE_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"schema_version": 1, "profiles": {}}
    if not isinstance(value, dict) or not isinstance(value.get("profiles"), dict):
        return {"schema_version": 1, "profiles": {}}
    return value


def save_platform_profile(profile: dict[str, Any], path: Path = PLATFORM_PROFILE_PATH) -> None:
    key = str(profile.get("key") or "").strip()
    if not key:
        raise ValueError("Platform profile key is required")
    with _profile_lock(path):
        payload = _read_payload(path)
        payload["profiles"][key] = dict(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def load_platform_profiles(path: Path = PLATFORM_PROFILE_PATH) -> dict[str, dict[str, Any]]:
    return {
        str(key): dict(value)
        for key, value in _read_payload(path)["profiles"].items()
        if isinstance(value, dict)
    }


def latest_device_platform_profile(
    device_id: str,
    platform_id: str = "douyin",
    path: Path = PLATFORM_PROFILE_PATH,
) -> dict[str, Any] | None:
    candidates = [
        value
        for value in load_platform_profiles(path).values()
        if value.get("device_id") == device_id
        and value.get("platform_id") == platform_id
    ]
    return max(candidates, key=lambda item: str(item.get("verified_at") or ""), default=None)


def profile_matches_runtime(
    profile: dict[str, Any] | None,
    *,
    app_version: str,
    display: dict[str, Any],
    adapter_version: str = ADAPTER_VERSION,
    portable_virtual: bool = False,
) -> bool:
    if portable_virtual:
        expected_display = profile.get("display") if profile else None
        if not isinstance(expected_display, dict):
            expected_signature = str((profile or {}).get("display_signature") or "")
            parts = expected_signature.split("x")
            expected_display = {
                "width": int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else None,
                "height": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
                "density": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
            }
        return bool(
            profile
            and profile.get("status") == "ready"
            and profile.get("adapter_version") == adapter_version
            and all(
                int(display.get(key) or 0) == wanted
                for key, wanted in (("width", 900), ("height", 1600), ("density", 320))
            )
            and all(
                expected_display.get(key) in {None, wanted}
                for key, wanted in (("width", 900), ("height", 1600), ("density", 320))
            )
        )
    return bool(
        profile
        and profile.get("status") == "ready"
        and profile.get("app_version") == app_version
        and profile.get("display_signature") == display_signature(display)
        and profile.get("adapter_version") == adapter_version
    )
