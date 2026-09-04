from __future__ import annotations

import os
import secrets
import json
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent
APP_ROOT = Path(
    os.environ.get("MEDIAFLOW_APP_ROOT")
    or os.environ.get("RISKFLOW_APP_ROOT")
    or SOURCE_ROOT.parent
).resolve()
IS_DISTRIBUTION = (APP_ROOT / "runtime" / "python" / "python.exe").is_file()
LOCAL_APP_DATA = Path(
    os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
)
BOOTSTRAP_CONFIG_PATH = LOCAL_APP_DATA / "MediaFlow" / "bootstrap.json"


def _bootstrap_data_root() -> Path | None:
    try:
        payload = json.loads(BOOTSTRAP_CONFIG_PATH.read_text(encoding="utf-8-sig"))
        value = str(payload.get("data_root") or "").strip()
        return Path(value).expanduser().resolve() if value else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None

if os.environ.get("MEDIAFLOW_DATA_ROOT") or os.environ.get("RISKFLOW_DATA_ROOT"):
    DATA_ROOT = Path(
        os.environ.get("MEDIAFLOW_DATA_ROOT") or os.environ["RISKFLOW_DATA_ROOT"]
    ).resolve()
elif IS_DISTRIBUTION:
    DATA_ROOT = _bootstrap_data_root() or (LOCAL_APP_DATA / "MediaFlow" / "data").resolve()
else:
    DATA_ROOT = SOURCE_ROOT / "runtime"

RUNTIME_ROOT = DATA_ROOT
SECRET_ROOT = DATA_ROOT / "secrets" if IS_DISTRIBUTION else APP_ROOT / ".secrets"
BUNDLED_PYTHON = APP_ROOT / "runtime" / "python" / "python.exe"
BUNDLED_PYTHONW = APP_ROOT / "runtime" / "python" / "pythonw.exe"
BUNDLED_NODE = APP_ROOT / "runtime" / "node" / "node.exe"
BUNDLED_ADB_ROOT = APP_ROOT / "runtime" / "platform-tools"
BUNDLED_ADB = BUNDLED_ADB_ROOT / "adb.exe"
UI_STANDALONE_ROOT = APP_ROOT / "control_console" / "standalone"
UI_STANDALONE_SERVER = UI_STANDALONE_ROOT / "server.js"
DEVICE_PROFILE_PATH = (
    DATA_ROOT / "device_profiles.json"
    if IS_DISTRIBUTION
    else SOURCE_ROOT / "device_profiles.json"
)
PLATFORM_PROFILE_PATH = (
    DATA_ROOT / "platform_profiles.json"
    if IS_DISTRIBUTION
    else SOURCE_ROOT / "platform_profiles.json"
)
INITIALIZATION_ARTIFACTS_ROOT = DATA_ROOT / "artifacts" / "initializations"
STREAM_HOST_ROOT = APP_ROOT / "device_stream_host"
STREAM_HOST_ENTRY = STREAM_HOST_ROOT / "src" / "server.mjs"
STREAM_SERVER_PATH = STREAM_HOST_ROOT / "vendor" / "scrcpy-server-v3.3.3"
STREAM_SECRET_PATH = SECRET_ROOT / "device-stream-host.secret"


def ensure_stream_secret() -> Path:
    """Return the shared loopback-gateway secret without exposing its value."""
    STREAM_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STREAM_SECRET_PATH.is_file() or STREAM_SECRET_PATH.stat().st_size < 32:
        value = secrets.token_urlsafe(48)
        try:
            with STREAM_SECRET_PATH.open("x", encoding="utf-8") as stream:
                stream.write(value)
        except FileExistsError:
            if STREAM_SECRET_PATH.stat().st_size < 32:
                temporary = STREAM_SECRET_PATH.with_suffix(".tmp")
                temporary.write_text(value, encoding="utf-8")
                temporary.replace(STREAM_SECRET_PATH)
    return STREAM_SECRET_PATH


def tool_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(base or os.environ)
    environment["MEDIAFLOW_APP_ROOT"] = str(APP_ROOT)
    environment["MEDIAFLOW_DATA_ROOT"] = str(DATA_ROOT)
    # One compatibility cycle for older helper scripts and already-installed workers.
    environment["RISKFLOW_APP_ROOT"] = str(APP_ROOT)
    environment["RISKFLOW_DATA_ROOT"] = str(DATA_ROOT)
    tool_paths = [
        str(BUNDLED_ADB_ROOT) if BUNDLED_ADB.is_file() else "",
        str(BUNDLED_NODE.parent) if BUNDLED_NODE.is_file() else "",
    ]
    existing = environment.get("PATH", "")
    environment["PATH"] = os.pathsep.join(
        [value for value in (*tool_paths, existing) if value]
    )
    return environment
