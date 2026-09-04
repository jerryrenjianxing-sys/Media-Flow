from __future__ import annotations

import json
import os
import subprocess
import threading
import base64
import ctypes
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

import requests

from model_runtime_config import OPENROUTER_PRIMARY_MODEL
from runtime_layout import RUNTIME_ROOT, SECRET_ROOT
from windows_powershell import native_windows_powershell_environment


OPENROUTER_KEY_PATH = SECRET_ROOT / "openrouter-api-key.dpapi"
OPENROUTER_PENDING_KEY_PATH = SECRET_ROOT / "openrouter-api-key.pending.dpapi"
MODEL_CONNECTION_PATH = RUNTIME_ROOT / "model-connection.json"
ENCRYPT_SCRIPT = Path(__file__).resolve().parent / "set-openrouter-key-from-stdin.ps1"
DECRYPT_SCRIPT = Path(__file__).resolve().parent / "read-openrouter-key.ps1"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_OPERATION_LOCK = threading.Lock()
DPAPI_PREFIX = "MEDIAFLOW-DPAPI-V1:"


def exclusive_model_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not MODEL_OPERATION_LOCK.acquire(blocking=False):
            raise RuntimeError("已有模型配置操作正在进行，请稍候查看结果，不要重复提交")
        try:
            return function(*args, **kwargs)
        finally:
            MODEL_OPERATION_LOCK.release()

    return wrapped


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def validate_openrouter_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key.startswith("sk-or-v1-") or not 32 <= len(key) <= 512:
        raise ValueError("请输入有效的 OpenRouter API Key")
    return key


def _powershell() -> Path:
    return (
        Path(os.environ.get("WINDIR", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi_transform(value: bytes, *, protect: bool) -> bytes:
    """Encrypt/decrypt with the current Windows user and no UI prompts."""
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI仅支持Windows")
    source = ctypes.create_string_buffer(value)
    source_blob = _DataBlob(
        len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte))
    )
    result_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_DataBlob),
    ]
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    description = "MediaFlow OpenRouter Key" if protect else None
    success = function(
        ctypes.byref(source_blob), description, None, None, None, 0x1,
        ctypes.byref(result_blob),
    )
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result_blob.data, result_blob.size)
    finally:
        if result_blob.data:
            kernel32.LocalFree(result_blob.data)


def _encrypt_to(key: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    protected = _dpapi_transform(key.encode("utf-8"), protect=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        DPAPI_PREFIX + base64.b64encode(protected).decode("ascii"),
        encoding="ascii",
    )
    temporary.replace(path)


def _decrypt_legacy_powershell(path: Path) -> str:
    result = subprocess.run(
        [
            str(_powershell()),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(DECRYPT_SCRIPT),
            "-Path",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
        env=native_windows_powershell_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError("OpenRouter Key 本机解密回读失败")
    return result.stdout.strip()


def _decrypt(path: Path) -> str:
    payload = path.read_text(encoding="ascii").strip()
    if not payload.startswith(DPAPI_PREFIX):
        return _decrypt_legacy_powershell(path)
    try:
        protected = base64.b64decode(payload[len(DPAPI_PREFIX):], validate=True)
        return _dpapi_transform(protected, protect=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        raise RuntimeError("OpenRouter Key 本机解密回读失败") from exc


def _read_metadata() -> dict[str, Any]:
    try:
        payload = json.loads(MODEL_CONNECTION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _write_metadata(patch: dict[str, Any]) -> None:
    payload = {**_read_metadata(), **patch}
    MODEL_CONNECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MODEL_CONNECTION_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(MODEL_CONNECTION_PATH)


def _auth(key: str, *, timeout: tuple[float, float] = (4.0, 8.0)) -> tuple[str, str]:
    try:
        response = requests.get(
            OPENROUTER_KEY_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
    except requests.RequestException:
        return "pending", "Key 已安全保存为候选，联网后可重新验证"
    if response.status_code == 200:
        return "authenticated", "OpenRouter 鉴权成功"
    if response.status_code in {401, 403}:
        return "invalid", "Key 无效或无权访问 OpenRouter"
    if response.status_code == 429 or response.status_code >= 500:
        return "pending", "OpenRouter 暂时不可用，Key 已保存为待验证候选"
    return "pending", f"OpenRouter 返回 {response.status_code}，请稍后重新验证"


def _promote_pending() -> None:
    OPENROUTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENROUTER_PENDING_KEY_PATH.replace(OPENROUTER_KEY_PATH)


def status() -> dict[str, Any]:
    metadata = _read_metadata()
    stored_file = OPENROUTER_KEY_PATH.is_file() and OPENROUTER_KEY_PATH.stat().st_size > 0
    pending = (
        OPENROUTER_PENDING_KEY_PATH.is_file()
        and OPENROUTER_PENDING_KEY_PATH.stat().st_size > 0
    )
    recorded_storage = str(metadata.get("storage_status") or "")
    unreadable = stored_file and recorded_storage == "unreadable"
    configured = stored_file and not unreadable
    auth_status = str(metadata.get("auth_status") or ("stored" if configured else "unconfigured"))
    if not configured and pending:
        auth_status = "pending"
    model_test_status = str(metadata.get("model_test_status") or "untested")
    candidate_status = str(metadata.get("candidate_status") or "none")
    tested_model = str(metadata.get("tested_model") or "")
    model_ready = bool(
        configured
        and auth_status == "authenticated"
        and model_test_status == "passed"
        and tested_model == OPENROUTER_PRIMARY_MODEL
    )
    return {
        "provider": "OpenRouter",
        "model": OPENROUTER_PRIMARY_MODEL,
        "key_configured": configured,
        "storage_status": "unreadable" if unreadable else "stored" if configured else "pending" if pending else "empty",
        "auth_status": auth_status,
        "model_test_status": model_test_status,
        "last_verified_at": metadata.get("last_verified_at"),
        "last_model_test_at": metadata.get("last_model_test_at"),
        "last_model_latency_ms": metadata.get("last_model_latency_ms"),
        "message": str(metadata.get("message") or ("尚未配置 OpenRouter Key" if not configured else "Key 已加密保存在本机")),
        "model_ready": model_ready,
        "has_pending_key": pending,
        "candidate_status": candidate_status,
    }


@exclusive_model_operation
def save_candidate(value: Any) -> dict[str, Any]:
    key = validate_openrouter_key(value)
    _encrypt_to(key, OPENROUTER_PENDING_KEY_PATH)
    if _decrypt(OPENROUTER_PENDING_KEY_PATH) != key:
        OPENROUTER_PENDING_KEY_PATH.unlink(missing_ok=True)
        raise RuntimeError("OpenRouter Key 加密回读不一致，未修改当前配置")
    auth_status, message = _auth(key)
    if auth_status == "authenticated":
        _promote_pending()
        _write_metadata(
            {
                "auth_status": auth_status,
                "storage_status": "stored",
                "model_test_status": "untested",
                "tested_model": None,
                "last_verified_at": now_iso(),
                "message": message + "；请测试当前模型",
                "candidate_status": "none",
            }
        )
    elif auth_status == "invalid":
        OPENROUTER_PENDING_KEY_PATH.unlink(missing_ok=True)
        if OPENROUTER_KEY_PATH.is_file():
            _write_metadata(
                {
                    "candidate_status": "invalid",
                    "message": message + "；未替换原有配置",
                }
            )
        else:
            _write_metadata(
                {"auth_status": auth_status, "candidate_status": "invalid", "message": message}
            )
    else:
        if OPENROUTER_KEY_PATH.is_file():
            _write_metadata(
                {"candidate_status": "pending", "message": message + "；当前配置未被替换"}
            )
        else:
            _write_metadata(
                {"auth_status": auth_status, "candidate_status": "pending", "message": message}
            )
    return {**status(), "accepted": auth_status in {"authenticated", "pending"}}


@exclusive_model_operation
def verify() -> dict[str, Any]:
    path = OPENROUTER_PENDING_KEY_PATH if OPENROUTER_PENDING_KEY_PATH.is_file() else OPENROUTER_KEY_PATH
    if not path.is_file():
        raise ValueError("尚未配置 OpenRouter Key")
    try:
        key = _decrypt(path)
    except RuntimeError:
        _write_metadata(
            {
                "storage_status": "unreadable",
                "auth_status": "unreadable",
                "model_test_status": "failed",
                "tested_model": None,
                "message": "当前 Key 无法由 MediaFlow 后台读取，请在本机重新输入并保存",
            }
        )
        return status()
    auth_status, message = _auth(key)
    verifying_pending = path == OPENROUTER_PENDING_KEY_PATH
    if auth_status == "authenticated" and verifying_pending:
        _promote_pending()
    elif auth_status == "invalid" and verifying_pending:
        OPENROUTER_PENDING_KEY_PATH.unlink(missing_ok=True)
    if verifying_pending and auth_status != "authenticated" and OPENROUTER_KEY_PATH.is_file():
        _write_metadata(
            {
                "candidate_status": auth_status,
                "message": message + "；当前配置未被替换",
            }
        )
    else:
        _write_metadata(
            {
                "auth_status": auth_status,
                "storage_status": "stored",
                "candidate_status": "none" if auth_status == "authenticated" else auth_status,
                "last_verified_at": now_iso() if auth_status == "authenticated" else None,
                "model_test_status": "untested" if verifying_pending and auth_status == "authenticated" else _read_metadata().get("model_test_status", "untested"),
                "tested_model": None if verifying_pending and auth_status == "authenticated" else _read_metadata().get("tested_model"),
                "message": message + ("；请测试当前模型" if auth_status == "authenticated" else ""),
            }
        )
    return status()


@exclusive_model_operation
def test_current_model(*, timeout: tuple[float, float] = (5.0, 25.0)) -> dict[str, Any]:
    if not OPENROUTER_KEY_PATH.is_file():
        raise ValueError("请先完成 OpenRouter 鉴权")
    try:
        key = _decrypt(OPENROUTER_KEY_PATH)
    except RuntimeError:
        _write_metadata(
            {
                "storage_status": "unreadable",
                "auth_status": "unreadable",
                "model_test_status": "failed",
                "tested_model": None,
                "last_model_test_at": now_iso(),
                "message": "当前 Key 无法由 MediaFlow 后台读取，请在本机重新输入并保存",
            }
        )
        return status()
    started = datetime.now().astimezone()
    try:
        response = requests.post(
            OPENROUTER_CHAT_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": OPENROUTER_PRIMARY_MODEL,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 3,
                "temperature": 0,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("模型没有返回有效内容")
        latency_ms = round((datetime.now().astimezone() - started).total_seconds() * 1000)
        _write_metadata(
            {
                "auth_status": "authenticated",
                "storage_status": "stored",
                "model_test_status": "passed",
                "tested_model": OPENROUTER_PRIMARY_MODEL,
                "last_verified_at": now_iso(),
                "last_model_test_at": now_iso(),
                "last_model_latency_ms": latency_ms,
                "message": f"当前模型已测试，耗时 {latency_ms} ms",
            }
        )
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        _write_metadata(
            {
                "model_test_status": "failed",
                "tested_model": OPENROUTER_PRIMARY_MODEL,
                "last_model_test_at": now_iso(),
                "message": f"当前模型测试失败：{str(exc)[:180]}",
            }
        )
    return status()
