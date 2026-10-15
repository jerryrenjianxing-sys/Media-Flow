from __future__ import annotations

import os
import secrets
import json
import re
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent


def _instance_profile() -> tuple[dict, str | None]:
    path = os.environ.get("MEDIAFLOW_INSTANCE_PROFILE")
    selected: list[str] = []
    index = 1
    while index < len(sys.argv):
        argument = sys.argv[index]
        if argument == "--instance-profile":
            if index + 1 >= len(sys.argv) or not sys.argv[index + 1] or sys.argv[index + 1].startswith("--"):
                raise ValueError("--instance-profile requires a path")
            selected.append(sys.argv[index + 1])
            index += 2
            continue
        if argument.startswith("--instance-profile="):
            value = argument.split("=", 1)[1]
            if not value:
                raise ValueError("--instance-profile requires a path")
            selected.append(value)
        index += 1
    if len(selected) > 1:
        raise ValueError("--instance-profile must be specified exactly once")
    if selected:
        path = selected[0]
    if not path:
        return {}, None
    resolved = str(Path(path).expanduser().resolve())
    payload = json.loads(Path(resolved).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema", payload.get("schema_version")) != 1:
        raise ValueError("Unsupported MediaFlow instance profile")
    return payload, resolved


INSTANCE_PROFILE, INSTANCE_PROFILE_PATH = _instance_profile()
INSTANCE_ID = str(os.environ.get("MEDIAFLOW_INSTANCE_ID") or INSTANCE_PROFILE.get("id")
                  or INSTANCE_PROFILE.get("instance_id") or "production")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", INSTANCE_ID):
    raise ValueError("Invalid MediaFlow instance id")
ACCEPTANCE = (os.environ.get("MEDIAFLOW_ACCEPTANCE", "1" if INSTANCE_PROFILE.get("acceptance") else "0") == "1")
if ACCEPTANCE and INSTANCE_ID == "production":
    raise ValueError("Acceptance requires a separate instance id")


def _port(name: str, key: str, default: int) -> int:
    value = int(os.environ.get(name) or INSTANCE_PROFILE.get(key) or default)
    if not 1 <= value <= 65535:
        raise ValueError(f"Invalid {name}")
    return value


API_PORT = _port("MEDIAFLOW_API_PORT", "api_port", 48138)
UI_PORT = _port("MEDIAFLOW_UI_PORT", "ui_port", 3001)
STREAM_PORT = _port("MEDIAFLOW_STREAM_PORT", "stream_port", 48139)
if INSTANCE_ID != "production" and len({API_PORT, UI_PORT, STREAM_PORT}) != 3:
    raise ValueError("Instance ports must be distinct")
if ACCEPTANCE and (API_PORT == 48138 or UI_PORT == 3001 or STREAM_PORT == 48139):
    raise ValueError("Acceptance requires separate service ports")
APP_ROOT = Path(
    os.environ.get("MEDIAFLOW_APP_ROOT")
    or os.environ.get("RISKFLOW_APP_ROOT")
    or INSTANCE_PROFILE.get("app_root")
    or SOURCE_ROOT.parent
).resolve()


def _verified_development_source() -> bool:
    return ((APP_ROOT / 'PROJECT.md').is_file()
            and (APP_ROOT / 'fixed_runner/runtime_layout.py').is_file()
            and (APP_ROOT / '.venv/Scripts/python.exe').is_file()
            and (APP_ROOT / 'fixed_runner').resolve() == SOURCE_ROOT
            and not (APP_ROOT / 'release-manifest.json').exists()
            and not (APP_ROOT / 'resources/platform/release-manifest.json').exists()
            and not (APP_ROOT.parent / 'app-update.yml').exists()
            and not (APP_ROOT.parent / 'app.asar').exists()
            and not (APP_ROOT.parent / 'instance-profile.path').exists())


DEVELOPMENT_SOURCE = (os.environ.get('MEDIAFLOW_DEVELOPMENT_SOURCE',
                      '1' if INSTANCE_PROFILE.get('development_source') is True else '0') == '1')
if DEVELOPMENT_SOURCE and (ACCEPTANCE or INSTANCE_ID != 'production' or not _verified_development_source()):
    raise ValueError('development_source requires a verified production source checkout; packaged and acceptance instances stay isolated')
IS_DISTRIBUTION = not DEVELOPMENT_SOURCE and (APP_ROOT / "runtime" / "python" / "python.exe").is_file()
LOCAL_APP_DATA = Path(
    os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
)
INSTANCE_ROOT = LOCAL_APP_DATA / "MediaFlow" if INSTANCE_ID == "production" else LOCAL_APP_DATA / "MediaFlow" / "instances" / INSTANCE_ID
BOOTSTRAP_CONFIG_PATH = INSTANCE_ROOT / "bootstrap.json"


def _bootstrap_data_root(path: Path | None = None) -> Path | None:
    try:
        path = path or BOOTSTRAP_CONFIG_PATH
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        value = str(payload.get("data_root") or "").strip()
        return Path(value).expanduser().resolve() if value else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None

if os.environ.get("MEDIAFLOW_DATA_ROOT") or os.environ.get("RISKFLOW_DATA_ROOT") or INSTANCE_PROFILE.get("data_root"):
    DATA_ROOT = Path(
        os.environ.get("MEDIAFLOW_DATA_ROOT") or os.environ.get("RISKFLOW_DATA_ROOT") or INSTANCE_PROFILE["data_root"]
    ).resolve()
elif INSTANCE_ID != "production":
    DATA_ROOT = (INSTANCE_ROOT / "data").resolve()
elif IS_DISTRIBUTION:
    DATA_ROOT = _bootstrap_data_root() or (LOCAL_APP_DATA / "MediaFlow" / "data").resolve()
else:
    DATA_ROOT = SOURCE_ROOT / "runtime"

RUNTIME_ROOT = DATA_ROOT
ISOLATED_STORAGE = IS_DISTRIBUTION or INSTANCE_ID != "production"
if ACCEPTANCE:
    protected_roots = [(SOURCE_ROOT / 'runtime').resolve(), (APP_ROOT / '.secrets').resolve(),
                       (APP_ROOT / 'runtime').resolve(), (LOCAL_APP_DATA / 'MediaFlow/data').resolve()]
    production_root = _bootstrap_data_root(LOCAL_APP_DATA / 'MediaFlow/bootstrap.json')
    if production_root:
        protected_roots.append(production_root)
    if any(DATA_ROOT.is_relative_to(root) for root in protected_roots):
        raise ValueError("Acceptance data root cannot use production data, credentials or bundled runtime")
SECRET_ROOT = DATA_ROOT / "secrets" if ISOLATED_STORAGE else APP_ROOT / ".secrets"
BUNDLED_PYTHON = APP_ROOT / "runtime" / "python" / "python.exe"
BUNDLED_PYTHONW = APP_ROOT / "runtime" / "python" / "pythonw.exe"
BUNDLED_NODE = APP_ROOT / "runtime" / "node" / "node.exe"
BUNDLED_ADB_ROOT = APP_ROOT / "runtime" / "platform-tools"
BUNDLED_ADB = BUNDLED_ADB_ROOT / "adb.exe"
UI_STANDALONE_ROOT = APP_ROOT / "control_console" / "standalone"
UI_STANDALONE_SERVER = UI_STANDALONE_ROOT / "server.js"
DEVICE_PROFILE_PATH = (
    DATA_ROOT / "device_profiles.json"
    if ISOLATED_STORAGE
    else SOURCE_ROOT / "device_profiles.json"
)
PLATFORM_PROFILE_PATH = (
    DATA_ROOT / "platform_profiles.json"
    if ISOLATED_STORAGE
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
    environment.update(MEDIAFLOW_INSTANCE_ID=INSTANCE_ID, MEDIAFLOW_API_PORT=str(API_PORT),
                       MEDIAFLOW_UI_PORT=str(UI_PORT), MEDIAFLOW_STREAM_PORT=str(STREAM_PORT),
                       MEDIAFLOW_ACCEPTANCE="1" if ACCEPTANCE else "0",
                       MEDIAFLOW_DEVELOPMENT_SOURCE='1' if DEVELOPMENT_SOURCE else '0')
    if INSTANCE_PROFILE_PATH:
        environment["MEDIAFLOW_INSTANCE_PROFILE"] = INSTANCE_PROFILE_PATH
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
