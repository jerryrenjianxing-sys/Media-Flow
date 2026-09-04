from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from runtime_layout import APP_ROOT, BUNDLED_ADB, tool_environment


_LOOPBACK_ENDPOINT = re.compile(r"^(?:127\.0\.0\.1|localhost):(\d{1,5})$")


def resolve_adb_executable() -> str | None:
    """Resolve one ADB binary for both packaged and source runtimes."""
    candidates = (
        BUNDLED_ADB,
        APP_ROOT / ".venv" / "Lib" / "site-packages" / "adbutils" / "binaries" / "adb.exe",
        Path(sys.executable).resolve().parent.parent
        / "Lib"
        / "site-packages"
        / "adbutils"
        / "binaries"
        / "adb.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("adb.exe") or shutil.which("adb")


def connect_loopback_adb(endpoint: str, *, timeout_seconds: int = 8) -> bool:
    """Connect one MuMu loopback transport and confirm it is authorized."""
    normalized = endpoint.replace("localhost:", "127.0.0.1:")
    match = _LOOPBACK_ENDPOINT.fullmatch(normalized)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(match.group(1))), timeout=0.75):
            pass
    except OSError:
        return False
    adb = resolve_adb_executable()
    if not adb:
        return False
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    environment = tool_environment()
    try:
        connected = subprocess.run(
            [adb, "connect", normalized],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout_seconds)),
            check=False,
            creationflags=flags,
            env=environment,
        )
        output = f"{connected.stdout}\n{connected.stderr}".lower()
        if connected.returncode or any(
            marker in output
            for marker in ("cannot connect", "unable to connect", "failed to connect", "refused")
        ):
            return False
        state = subprocess.run(
            [adb, "-s", normalized, "get-state"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout_seconds)),
            check=False,
            creationflags=flags,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return state.returncode == 0 and state.stdout.strip() == "device"
