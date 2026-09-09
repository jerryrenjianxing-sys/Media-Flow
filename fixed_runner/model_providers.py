"""Provider configuration and request accounting. Secrets never leave this boundary.

SQLite serializes activation and request admission across API/worker/analyzer
processes. Candidate secrets are immutable DPAPI files; testing never replaces
the active secret. OpenRouter's existing encrypted store and USD ledger remain.
"""
from __future__ import annotations

import base64
import io
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path

from model_runtime_config import OPENROUTER_PRIMARY_MODEL
from runtime_layout import RUNTIME_ROOT, SECRET_ROOT
from model_errors import CloudModelError

QWEN = "qwen_token_plan"
OPENROUTER = "openrouter"
QWEN_MODEL = "qwen3.8-flash"
QWEN_BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
QWEN_CONTRACT = QWEN_BASE_URL + "|" + QWEN_MODEL + "|image-json-thinking-off-v1"
CONFIG_DB = RUNTIME_ROOT / "model-providers.db"
TASK_DB = RUNTIME_ROOT / "tasks.db"
NOTICE_VERSION = "token-plan-personal-2026-09-05"
ERRORS = {
    "authentication": "Key 无效，请重新输入 Token Plan 专属 Key",
    "permission": "套餐过期、权限不足或服务拒绝，请在千问工作台检查",
    "model_unavailable": "当前套餐无法使用 qwen3.8-flash，请检查模型权限",
    "rate_limited": "请求过快，请稍后手动重试",
    "quota_exhausted": "套餐额度已耗尽，请在千问工作台检查；不会切换到按量付费",
    "network_timeout": "网络连接或模型响应超时，请稍后手动重试",
    "invalid_response": "模型没有返回符合要求的图片与结构化结果",
    "configuration_changed": "模型配置已变化，请重新开始当前验证",
    "upload_consent_required": "请先确认图片上传、套餐额度和个人版数据使用说明",
    "provider_failure": "模型服务暂时异常，请稍后手动重试",
    "credential_unreadable": "千问Key无法由当前Windows用户解密，请在本机重新输入并保存",
}


class ProviderError(CloudModelError):
    def __init__(self, code: str):
        self.code = code
        kind = {"authentication": "authentication", "permission": "permanent_rejection",
                "model_unavailable": "invalid_request", "rate_limited": "rate_limited",
                "quota_exhausted": "balance",
                "configuration_changed": "invalid_request", "credential_unreadable": "authentication",
                "upload_consent_required": "invalid_request",
                "network_timeout": "transient_network", "invalid_response": "invalid_response"}.get(code, "provider_failure")
        super().__init__(kind, ERRORS.get(code, "模型配置异常，请重新保存并测试"),
                         retryable=code in {"network_timeout", "rate_limited", "provider_failure"},
                         diagnostics={"reason_code": code})


@contextmanager
def database():
    CONFIG_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(CONFIG_DB, timeout=3)
    c.row_factory = sqlite3.Row
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), provider TEXT, revision INTEGER, active_key TEXT, active_contract TEXT);
            INSERT OR IGNORE INTO state (id,provider,revision,active_key) VALUES (1,'openrouter',0,NULL);
            CREATE TABLE IF NOT EXISTS candidate (provider TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, provider TEXT, revision INTEGER, pid INTEGER, started REAL, finished REAL, usage TEXT, error_code TEXT);
        """)
        if "active_contract" not in {row[1] for row in c.execute("PRAGMA table_info(state)")}:
            c.execute("BEGIN IMMEDIATE")
            if "active_contract" not in {row[1] for row in c.execute("PRAGMA table_info(state)")}:
                c.execute("ALTER TABLE state ADD COLUMN active_contract TEXT")
            c.commit()
        with c:
            yield c
    finally:
        c.close()


def selection() -> dict:
    if not CONFIG_DB.is_file():
        return {"provider": OPENROUTER, "revision": 0, "active_key": None}
    with database() as c:
        return dict(c.execute("SELECT * FROM state WHERE id=1").fetchone())


def _candidate(c) -> dict:
    row = c.execute("SELECT payload FROM candidate WHERE provider=?", (QWEN,)).fetchone()
    return json.loads(row[0]) if row else {}


def _write_candidate(c, value):
    c.execute("INSERT OR REPLACE INTO candidate VALUES (?,?)", (QWEN, json.dumps(value)))


def _secret(name: str) -> Path:
    # Only opaque local references generated here may address encrypted files.
    if not name or len(name) != 32 or any(ch not in "0123456789abcdef" for ch in name):
        raise ProviderError("configuration_changed")
    return SECRET_ROOT / ("qwen-token-plan-" + name + ".dpapi")


def _key(name: str) -> str:
    from model_connection import _decrypt
    try:
        return _decrypt(_secret(name))
    except (OSError, RuntimeError, ValueError):
        raise ProviderError("credential_unreadable") from None


def resolve_runtime_model(api_key=None, base_url=None, model=None):
    """Resolve all business callers, preserving explicit legacy tests pre-migration."""
    active = selection()
    if active["provider"] == QWEN:
        if active.get("active_contract") != QWEN_CONTRACT:
            raise ProviderError("configuration_changed")
        return _key(active["active_key"]), QWEN_BASE_URL, QWEN_MODEL
    if active["revision"]:
        from model_connection import _decrypt, OPENROUTER_KEY_PATH
        return _decrypt(OPENROUTER_KEY_PATH), "https://openrouter.ai/api/v1", OPENROUTER_PRIMARY_MODEL
    return api_key, base_url, model


def status(provider: str | None = None) -> dict:
    from model_connection import status as openrouter_status
    active = selection()
    old = {**openrouter_status(), "provider_id": OPENROUTER,
           "request_limit": None, "requests_remaining": None, "local_limits_enabled": False}
    candidate, count = {}, 0
    if CONFIG_DB.is_file():
        with database() as c:
            c.execute("BEGIN IMMEDIATE")
            _reconcile_calls(c)
            candidate = _candidate(c)
            count = c.execute("SELECT COUNT(*) FROM calls WHERE provider=?", (QWEN,)).fetchone()[0]
    passed = candidate.get("model_test_status") == "passed" and candidate.get("model") == QWEN_MODEL and candidate.get("tested_contract") == QWEN_CONTRACT
    qwen = {
        "provider": "千问AI平台 · Token Plan（实验性）", "provider_id": QWEN,
        "model": QWEN_MODEL, "key_configured": bool(candidate.get("key_ref")),
        "storage_status": "unreadable" if candidate.get("reason_code") == "credential_unreadable" else "stored" if candidate.get("key_ref") else "empty",
        "auth_status": "authenticated" if passed else "unverified",
        "model_test_status": "untested" if candidate.get("model_test_status") == "passed" and not passed else candidate.get("model_test_status", "untested"),
        "model_ready": passed, "has_pending_key": False,
        "last_model_test_at": candidate.get("tested_at"),
        "last_model_latency_ms": candidate.get("latency_ms"),
        "message": candidate.get("message", "请输入 Token Plan 专属 Key；保存不会发起联网鉴权"),
        "reason_code": candidate.get("reason_code"), "experimental": True,
        "request_limit": None, "requests_used": count, "requests_remaining": None,
        "local_limits_enabled": False,
        "credits": None, "usage_message": "Credits以千问工作台为准；不等同于美元或零费用",
        "can_enable": bool(passed and candidate.get("consent") == NOTICE_VERSION),
    }
    profiles = {OPENROUTER: old, QWEN: qwen}
    for name, profile in profiles.items():
        profile["enabled"] = active["provider"] == name
    current = dict(profiles[active["provider"]])
    if active["provider"] == QWEN:
        # A failed NEW candidate never invalidates the previous tested active key.
        current["model_ready"] = bool(active.get("active_key")) and active.get("active_contract") == QWEN_CONTRACT and _secret(active["active_key"]).is_file()
        current.update(auth_status="authenticated" if current["model_ready"] else "unverified",
                       model_test_status="passed" if current["model_ready"] else "untested", storage_status="stored")
        if candidate.get("key_ref") != active.get("active_key"):
            current["message"] = "当前仍使用上一份已测试千问配置；新候选尚未启用。" + qwen["message"]
        if current["model_ready"]:
            try:
                _key(active["active_key"])
            except ProviderError:
                current.update(model_ready=False, storage_status="unreadable", auth_status="unreadable",
                               message=ERRORS["credential_unreadable"], reason_code="credential_unreadable")
    target = profiles.get(provider, current) if provider else current
    return {**target, "active_provider": active["provider"], "config_version": active["revision"],
            "providers": profiles}


def save_candidate(value, provider=OPENROUTER) -> dict:
    if provider == OPENROUTER:
        from model_connection import save_candidate as save
        with configuration_edit():
            result = save(value)
            if result.get("accepted") and result.get("auth_status") == "authenticated":
                with database() as c:
                    c.execute("UPDATE state SET revision=revision+1 WHERE provider='openrouter'")
        return {**status(OPENROUTER), "accepted": bool(result.get("accepted"))}
    if provider != QWEN:
        raise ValueError("不支持的模型服务商")
    key = str(value or "").strip()
    if not key.startswith("sk-sp-") or not 20 <= len(key) <= 512 or any(ch.isspace() for ch in key):
        raise ValueError("请使用千问 Token Plan 专属 sk-sp- Key，不能使用OpenRouter或按量Key")
    from model_connection import _encrypt_to
    name = uuid.uuid4().hex
    try:
        _encrypt_to(key, _secret(name))
        if _key(name) != key:
            raise ValueError("加密回读不一致")
    except Exception:
        raise ValueError("千问 Key 本机加密回读失败；原有有效配置未改变") from None
    with database() as c:
        c.execute("BEGIN IMMEDIATE")
        _write_candidate(c, {"key_ref": name, "model": QWEN_MODEL, "model_test_status": "untested",
                             "message": "已保存，尚未验证；请确认说明后测试图片理解"})
    return {**status(QWEN), "accepted": True}


def _require_idle(c, task_db: Path):
    _reconcile_calls(c)
    if c.execute("SELECT 1 FROM calls WHERE finished IS NULL LIMIT 1").fetchone():
        raise ValueError("仍有模型请求或结果未收口，暂时不能切换；请等待，异常退出后需重启后台核对")
    if not task_db.is_file():
        raise ValueError("任务状态不可读取，暂时不能切换模型")
    with closing(sqlite3.connect(task_db.as_uri() + "?mode=ro", uri=True, timeout=3)) as tasks:
        repairing = tasks.execute("SELECT 1 FROM tasks WHERE status IN ('waiting_model','waiting_user') LIMIT 1").fetchone()
        for table, column, values in (
            ("tasks", "status", ("running",) if repairing else ("running", "pending")),
            ("device_initializations", "status", ("queued", "running", "waiting_user")),
            ("incidents", "analysis_status", ("analyzing",)),
        ):
            if tasks.execute(f"SELECT 1 FROM {table} WHERE {column} IN ({','.join('?' for _ in values)}) LIMIT 1", values).fetchone():
                raise ValueError("任务、初始化或纠错分析尚未空闲，不能在流程中切换模型")
        row = tasks.execute("SELECT value FROM system_state WHERE key='paused'").fetchone()
        if not row or str(row[0]).lower() not in {"true", "1"}:
            raise ValueError("请先暂停任务队列，再切换模型")


def activate(provider, *, task_db=None):
    if provider not in {OPENROUTER, QWEN}:
        raise ValueError("不支持的模型服务商")
    with database() as c:
        c.execute("BEGIN IMMEDIATE")
        _require_idle(c, task_db or TASK_DB)
        key_ref = None
        if provider == QWEN:
            candidate = _candidate(c)
            if candidate.get("model_test_status") != "passed" or candidate.get("tested_contract") != QWEN_CONTRACT or candidate.get("model") != QWEN_MODEL or candidate.get("consent") != NOTICE_VERSION:
                raise ValueError("请先确认说明并通过图片与结构化测试，原有效配置保持不变")
            key_ref = candidate["key_ref"]
            _key(key_ref)  # Must still be readable by this Windows user.
        else:
            from model_connection import status as old_status
            if not old_status()["model_ready"]:
                raise ValueError("请先测试OpenRouter配置")
        c.execute("UPDATE state SET provider=?,revision=revision+1,active_key=?,active_contract=? WHERE id=1", (provider, key_ref, QWEN_CONTRACT if provider == QWEN else None))
    return {**status(provider), "ok": True, "message": "已启用；全平台后续调用使用同一模型配置，不自动回退"}


@contextmanager
def configuration_edit():
    """Protect legacy OpenRouter save/verify, which can promote a candidate."""
    token = uuid.uuid4().hex
    with database() as c:
        c.execute("BEGIN IMMEDIATE")
        _require_idle(c, TASK_DB)
        c.execute("INSERT INTO calls VALUES (?,?,?,?,?,NULL,NULL,NULL)", (token, "settings", 0, os.getpid(), time.time()))
    try:
        yield
    finally:
        with database() as c:
            c.execute("UPDATE calls SET finished=? WHERE id=?", (time.time(), token))


def verify_openrouter():
    from model_connection import verify
    with configuration_edit():
        result = verify()
        if result.get("auth_status") == "authenticated":
            with database() as c:
                c.execute("UPDATE state SET revision=revision+1 WHERE provider='openrouter'")
    return status(OPENROUTER)


@contextmanager
def admitted_request(provider, *, candidate_ref=None, configuration_test=False, expected_key=None):
    """Record before transmission and serialize configuration, without quotas."""
    token = uuid.uuid4().hex
    with database() as c:
        c.execute("BEGIN IMMEDIATE")
        active = dict(c.execute("SELECT * FROM state WHERE id=1").fetchone())
        if c.execute("SELECT 1 FROM calls WHERE provider='settings' AND finished IS NULL").fetchone():
            raise ProviderError("configuration_changed")
        if not candidate_ref and not configuration_test and active["revision"] and active["provider"] != provider:
            raise ProviderError("configuration_changed")
        if provider == QWEN:
            candidate = _candidate(c)
            if candidate_ref:
                if candidate.get("key_ref") != candidate_ref or candidate.get("consent") != NOTICE_VERSION:
                    raise ProviderError("upload_consent_required")
            elif active["provider"] != QWEN:
                raise ProviderError("configuration_changed")
            elif expected_key is not None and not secrets.compare_digest(expected_key, _key(active["active_key"])):
                raise ProviderError("configuration_changed")
        c.execute("INSERT INTO calls VALUES (?,?,?,?,?,NULL,NULL,NULL)", (token, provider, active["revision"], os.getpid(), time.time()))
    receipt = {"usage": None, "error_code": None}
    try:
        yield receipt
    except ProviderError as exc:
        receipt["error_code"] = exc.code
        raise
    finally:
        with database() as c:
            c.execute("UPDATE calls SET finished=?,usage=?,error_code=? WHERE id=?", (time.time(), json.dumps(receipt["usage"]), receipt["error_code"], token))


def http_error(response):
    """Classify upstream text transiently; never retain or return raw text."""
    if response.status_code < 400:
        return
    try:
        detail = str(response.json().get("error", "")).lower()
    except CloudModelError:
        raise
    except Exception:
        detail = ""
    code = "provider_failure"
    if response.status_code == 401:
        code = "authentication"
    elif response.status_code == 429:
        code = "quota_exhausted" if any(s in detail for s in ("allocated quota", "insufficient_quota", "quota exceeded", "quota_exhausted", "额度")) else "rate_limited"
    elif response.status_code in {402, 403}:
        code = "permission"
    elif response.status_code == 404 or "modelnotfound" in detail or "model_not_found" in detail:
        code = "model_unavailable"
    elif response.status_code == 400:
        code = "invalid_response"
    raise ProviderError(code)


def test_candidate(*, consent=False):
    """One bounded text+image+JSON probe. No desktop/device access or actions."""
    from PIL import Image, ImageDraw
    from model_budget import budgeted_post
    from control_vision import _bounded_call
    if consent is not True:
        raise ProviderError("upload_consent_required")
    number = str(secrets.randbelow(9000) + 1000)
    picture = Image.new("RGB", (320, 160), "white")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((15, 15, 100, 140), fill="red")
    draw.text((130, 60), number, fill="black", font_size=34)
    data = io.BytesIO()
    picture.save(data, format="PNG")
    with database() as c:
        c.execute("BEGIN IMMEDIATE")
        _reconcile_calls(c)
        candidate = _candidate(c)
        if not candidate.get("key_ref"):
            raise ValueError("请先保存千问Key")
        if candidate.get("model_test_status") == "testing":
            raise ValueError("图片测试正在进行，请等待结果，不要重复点击")
        candidate["consent"] = NOTICE_VERSION
        candidate.update(model_test_status="testing", testing_pid=os.getpid())
        _write_candidate(c, candidate)
    name = candidate["key_ref"]
    started = time.monotonic()

    def request():
        with budgeted_post(QWEN_BASE_URL + "/chat/completions", candidate_ref=name,
                           headers={"Authorization": "Bearer " + _key(name)},
                           json={"model": QWEN_MODEL, "messages": [{"role": "user", "content": [
                               {"type": "text", "text": 'Read this test image. Return JSON only: {"number":"digits in image","color":"rectangle color in English","text_check":"OK"}.'},
                               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()}}]}],
                                 "max_tokens": 200}, timeout=(3, 16), request_deadline=started+20) as response:
            return response.json()
    try:
        payload = _bounded_call(request, 20)
        answer = json.loads(payload["choices"][0]["message"]["content"])
        if answer != {"number": number, "color": "red", "text_check": "OK"}:
            raise ProviderError("invalid_response")
        result = {"model_test_status": "passed", "tested_contract": QWEN_CONTRACT, "reason_code": None, "message": "文本、图片和结构化测试通过；尚未启用，请在队列空闲时点击启用"}
    except Exception as exc:
        code = exc.code if isinstance(exc, ProviderError) else "network_timeout" if "timeout" in type(exc).__name__.lower() or time.monotonic()-started >= 19 else "invalid_response"
        result = {"model_test_status": "failed", "reason_code": code, "message": ERRORS[code] + "；原有效配置未改变"}
    with database() as c:
        c.execute("BEGIN IMMEDIATE")
        latest = _candidate(c)
        if latest.get("key_ref") == name:
            _write_candidate(c, {**latest, **result, "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "latency_ms": round((time.monotonic()-started)*1000)})
    return status(QWEN)


def _reconcile_calls(c):
    # Inspect process identity, not elapsed time: a slow surviving request must
    # still block activation. Never replay interrupted calls or erase history.
    from runtime_control import SystemProcessInspector
    inspector = SystemProcessInspector()
    for row in c.execute("SELECT id,pid FROM calls WHERE finished IS NULL").fetchall():
        if inspector.snapshot(row["pid"]) is None:
            c.execute("UPDATE calls SET finished=?,error_code='interrupted' WHERE id=?", (time.time(), row["id"]))
    candidate = _candidate(c)
    if candidate.get("model_test_status") == "testing" and inspector.snapshot(candidate.get("testing_pid", 0)) is None:
        candidate.update(model_test_status="failed", reason_code="interrupted", message="上次测试被中断；原配置未改变，可手动重新测试")
        _write_candidate(c, candidate)
