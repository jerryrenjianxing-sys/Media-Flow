"""Cross-process acceptance budget; unknown charges remain reserved, never refunded."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class ModelBudgetError(RuntimeError):
    pass


class ModelBudget:
    def __init__(self, path: Path, limit_usd: float = 5.0):
        if not math.isfinite(limit_usd) or not 0 < limit_usd <= 5:
            raise ModelBudgetError("model_budget_invalid_limit")
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), ceiling TEXT NOT NULL)")
            c.execute("INSERT OR IGNORE INTO budget VALUES (1, ?)", (str(limit_usd),))
            c.execute("CREATE TABLE IF NOT EXISTS charges (id TEXT PRIMARY KEY, model TEXT, reserved TEXT, actual TEXT, created REAL)")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def reserve(self, model: str, amount: float) -> str:
        if not math.isfinite(amount) or amount <= 0:
            raise ModelBudgetError("model_budget_price_unavailable")
        token = uuid.uuid4().hex
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            ceiling = Decimal(c.execute("SELECT ceiling FROM budget WHERE id=1").fetchone()[0])
            used = sum((Decimal(r[0]) for r in c.execute("SELECT COALESCE(actual,reserved) FROM charges")), Decimal(0))
            if used + Decimal(str(amount)) > ceiling:
                raise ModelBudgetError("model_budget_exhausted")
            c.execute("INSERT INTO charges VALUES (?,?,?,?,?)", (token, model, str(amount), None, time.time()))
        return token

    def settle(self, token: str, cost: Any) -> None:
        if isinstance(cost, bool):
            return
        try:
            amount = Decimal(str(cost))
        except Exception:
            return
        if not amount.is_finite() or amount < 0:
            return
        with self._connect() as c:
            c.execute("UPDATE charges SET actual=? WHERE id=? AND actual IS NULL", (str(amount), token))

    def summary(self) -> dict[str, Any]:
        with self._connect() as c:
            ceiling = float(c.execute("SELECT ceiling FROM budget WHERE id=1").fetchone()[0])
            rows = c.execute("SELECT reserved,actual FROM charges").fetchall()
        return {"limit_usd": ceiling, "requests": len(rows),
                "committed_usd": sum(float(a if a is not None else r) for r, a in rows),
                "settled_usd": sum(float(a) for _, a in rows if a is not None),
                "unsettled_requests": sum(a is None for _, a in rows)}


def _reservation(url: str, payload: dict[str, Any], proxies) -> tuple[ModelBudget, str] | None:
    location = os.environ.get("MEDIAFLOW_MODEL_BUDGET_PATH")
    if not location:
        return None
    if urlparse(url).hostname != "openrouter.ai":
        raise ModelBudgetError("model_budget_price_unavailable")
    model = str(payload.get("model") or "")
    if payload.get("tools") or payload.get("plugins") or payload.get("models"):
        raise ModelBudgetError("model_budget_unsupported_request")
    # No cached guessed pricing. Reserve the full advertised context at the more
    # expensive token rate, plus per-request/per-image prices. Actual usage releases
    # the difference. No tools/audio are enabled by callers in this acceptance.
    try:
        response = requests.get("https://openrouter.ai/api/v1/models", timeout=(3, 5), proxies=proxies)
        response.raise_for_status()
        entry = next(r for r in response.json()["data"] if r["id"] == model)
        price = entry["pricing"]
        prompt, completion = float(price["prompt"]), float(price["completion"])
        context = int(entry["context_length"])
        images = sum(1 for msg in payload.get("messages", []) for part in msg.get("content", [])
                     if isinstance(part, dict) and part.get("type") == "image_url")
        request_price, image_price = float(price.get("request") or 0), float(price.get("image") or 0)
        amount = context * max(prompt, completion) + request_price + images * image_price
        if context <= 0 or any(not math.isfinite(p) or p < 0 for p in (prompt, completion, request_price, image_price)):
            raise ValueError("invalid pricing")
        # The catalogue advertises lowest rates, not a safe routing ceiling.
        # Enforce those same ceilings upstream; never route to a dearer provider.
        provider = dict(payload.get("provider") or {})
        ceilings = {"prompt": prompt * 1_000_000, "completion": completion * 1_000_000,
                    "request": request_price, "image": image_price}
        existing = provider.get("max_price") or {}
        provider["max_price"] = {k: min(v, float(existing.get(k, v))) for k, v in ceilings.items()}
        provider["allow_fallbacks"] = False
        payload["provider"] = provider
        payload["usage"] = {"include": True}
    except Exception:
        raise ModelBudgetError("model_budget_price_unavailable") from None
    budget = ModelBudget(Path(location))
    return budget, budget.reserve(model, max(amount, 0.000001))


class _MeteredResponse:
    def __init__(self, response, reservation):
        self._response, self._reservation = response, reservation
        self._cost = None

    def __getattr__(self, name):
        return getattr(self._response, name)

    def _usage(self, value):
        usage = value.get("usage") if isinstance(value, dict) else None
        if isinstance(usage, dict) and "cost" in usage and self._reservation:
            cost = usage["cost"]
            if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                self._cost = max(self._cost or 0, cost)

    def json(self):
        value = self._response.json()
        self._usage(value)
        return value

    def iter_lines(self, *args, **kwargs):
        for line in self._response.iter_lines(*args, **kwargs):
            try:
                value = line.decode("utf-8") if isinstance(line, bytes) else line
                if value.startswith("data:"):
                    self._usage(json.loads(value[5:]))
            except (ValueError, UnicodeError, AttributeError):
                pass
            yield line


@contextmanager
def _openrouter_post(url: str, **kwargs):
    deadline = kwargs.pop("request_deadline", None)
    payload = kwargs.get("json")
    if not isinstance(payload, dict):
        payload = json.loads(kwargs.get("data") or "{}")
    reservation = _reservation(url, payload, kwargs.get("proxies"))
    if reservation:
        if "json" in kwargs:
            kwargs["json"] = payload
        else:
            kwargs["data"] = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if deadline is not None and time.monotonic() >= deadline:
        if reservation:
            reservation[0].settle(reservation[1], 0)
        raise ModelBudgetError("visual_deadline_exceeded")
    # Unknown/timeout responses keep the full reserved charge, including crashes.
    with requests.post(url, **kwargs) as response:
        metered = _MeteredResponse(response, reservation)
        try:
            yield metered
        finally:
            if reservation and metered._cost is not None:
                reservation[0].settle(reservation[1], metered._cost)


class _TokenPlanResponse:
    def __init__(self, response, receipt, deadline):
        self.response, self.receipt, self.deadline = response, receipt, deadline

    def __getattr__(self, name):
        return getattr(self.response, name)

    def _record(self, value):
        usage = value.get("usage") if isinstance(value, dict) else None
        if isinstance(usage, dict):
            # Never serialize arbitrary upstream fields or infer Credits from tokens.
            self.receipt["usage"] = {k: v for k, v in usage.items()
                                     if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                                     and type(v) is int and v >= 0}

    def json(self):
        value = self.response.json()
        self._record(value)
        return value

    def iter_lines(self, *args, **kwargs):
        from model_providers import ProviderError
        size = 0
        for line in self.response.iter_lines(*args, **kwargs):
            size += len(line)
            if time.monotonic() >= self.deadline:
                raise ProviderError("network_timeout")
            if size > 2_000_000:
                raise ProviderError("invalid_response")
            try:
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                if text.startswith("data:") and text[5:].strip() != "[DONE]":
                    value = json.loads(text[5:])
                    if value.get("error"):
                        raise ProviderError("provider_failure")
                    self._record(value)
            except (ValueError, UnicodeError):
                raise ProviderError("invalid_response") from None
            yield line


@contextmanager
def budgeted_post(url: str, **kwargs):
    from model_providers import (QWEN, OPENROUTER, QWEN_BASE_URL, QWEN_MODEL,
                                 ProviderError, admitted_request, http_error)
    candidate_ref = kwargs.pop("candidate_ref", None)
    configuration_test = kwargs.pop("configuration_test", False)
    qwen = url == QWEN_BASE_URL + "/chat/completions"
    supplied_key = kwargs.get("headers", {}).get("Authorization", "").removeprefix("Bearer ")
    with admitted_request(QWEN if qwen else OPENROUTER, candidate_ref=candidate_ref,
                          configuration_test=configuration_test, expected_key=supplied_key) as receipt:
        if not qwen:
            with _openrouter_post(url, **kwargs) as response:
                yield response
            return
        deadline = min(kwargs.pop("request_deadline", time.monotonic()+20), time.monotonic()+20)
        if time.monotonic() >= deadline:
            raise ProviderError("network_timeout")
        payload = dict(kwargs.pop("json", None) or json.loads(kwargs.pop("data", None) or "{}"))
        if payload.get("model") != QWEN_MODEL or any(payload.get(k) for k in ("tools", "tool_choice", "plugins", "models")):
            raise ProviderError("invalid_response")
        for key in ("provider", "usage", "reasoning", "reasoning_effort"):
            payload.pop(key, None)
        payload.update(enable_thinking=False, response_format={"type": "json_object"})
        if payload.get("stream"):
            payload["stream_options"] = {"include_usage": True}
        kwargs["json"] = payload
        headers = kwargs.get("headers", {})
        kwargs["headers"] = {k: v for k, v in headers.items() if k.lower() in {"authorization", "content-type", "accept"}}
        kwargs["headers"]["Content-Type"] = "application/json"
        kwargs["allow_redirects"] = False
        remaining = max(.1, deadline-time.monotonic())
        kwargs["timeout"] = (min(3, remaining), min(16, remaining))
        try:
            with requests.post(url, **kwargs) as response:
                if 300 <= response.status_code < 400:
                    raise ProviderError("provider_failure")
                http_error(response)
                yield _TokenPlanResponse(response, receipt, deadline)
        except requests.RequestException:
            raise ProviderError("network_timeout") from None
