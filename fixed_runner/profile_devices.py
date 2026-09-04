from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from device_profiles import PROFILE_PATH, merge_device_probe, save_device_profile_payload


ADB = os.environ.get("ADB_PATH", "adb")


def adb(*args: str) -> str:
    try:
        result = subprocess.run(
            [ADB, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ADB command timed out after 10 seconds") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ADB command failed")
    return result.stdout.strip()


def shell(device_id: str, *args: str) -> str:
    return adb("-s", device_id, "shell", *args)


def parse_pair(value: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)x(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def parse_integer(value: str) -> int | None:
    match = re.search(r"(\d+)", value)
    return int(match.group(1)) if match else None


def online_device_ids() -> list[str]:
    rows = adb("devices").splitlines()[1:]
    return [row.split()[0] for row in rows if len(row.split()) >= 2 and row.split()[1] == "device"]


def probe(device_id: str) -> dict[str, Any]:
    width, height = parse_pair(shell(device_id, "wm", "size"))
    density = parse_integer(shell(device_id, "wm", "density"))
    try:
        navigation_mode = shell(device_id, "settings", "get", "secure", "navigation_mode")
        navigation_mode = None if navigation_mode in {"", "null"} else navigation_mode
    except RuntimeError:
        navigation_mode = None
    return {
        "manufacturer": shell(device_id, "getprop", "ro.product.manufacturer"),
        "model": shell(device_id, "getprop", "ro.product.model"),
        "display": {
            "width": width,
            "height": height,
            "density": density,
            "navigation_mode": navigation_mode,
        },
    }


def main() -> None:
    try:
        payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {"schema_version": 1, "devices": {}}
    devices = payload.setdefault("devices", {})
    online = online_device_ids()
    for ordinal, device_id in enumerate(online, 1):
        devices[device_id] = merge_device_probe(
            device_id, probe(device_id), devices.get(device_id), ordinal=ordinal
        )
    save_device_profile_payload(payload)
    print(json.dumps({"online": online, "profile_path": str(PROFILE_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
