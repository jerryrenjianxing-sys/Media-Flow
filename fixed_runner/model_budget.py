"""Shared model transport and usage recording (legacy import name, no budgets).

Historical budget databases and environment settings are not read or rewritten.
All business callers share configuration admission, deadlines and real usage.
"""
from __future__ import annotations

import json
import math
import time
import queue
import threading
from types import SimpleNamespace
from contextlib import contextmanager

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool


class _BodyWriteConnection(HTTPSConnection):
    """Keep TLS/connect short without applying that timeout to image uploads."""
    def send(self, data):
        if self.sock is None:
            self.connect()  # self.timeout remains the original connect limit.
        self.sock.settimeout(25)
        try:
            return super().send(data)
        except TimeoutError as error:
            error.mediaflow_phase = 'request_write'
            raise


class _BodyWritePool(HTTPSConnectionPool):
    ConnectionCls = _BodyWriteConnection


class _BodyWriteAdapter(HTTPAdapter):
    @staticmethod
    def _configure(manager):
        # PoolManager's default dictionary is shared: never mutate it globally.
        manager.pool_classes_by_scheme = {
            **manager.pool_classes_by_scheme, 'https': _BodyWritePool}
        return manager

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self._configure(self.poolmanager)

    def proxy_manager_for(self, *args, **kwargs):
        return self._configure(super().proxy_manager_for(*args, **kwargs))


def _post_request(url, **kwargs):
    from model_providers import QWEN_BASE_URL
    if url != QWEN_BASE_URL + '/chat/completions':
        return requests.post(url, **kwargs)
    # Same lifetime as requests.post; response streams remain owned by caller.
    with requests.Session() as session:
        session.mount(QWEN_BASE_URL + '/', _BodyWriteAdapter(max_retries=0))
        return session.post(url, **kwargs)


def _request_write_timeout(error):
    pending, seen = [error], set()
    while pending:
        exc = pending.pop()
        if not isinstance(exc, BaseException) or id(exc) in seen:
            continue
        seen.add(id(exc))
        if getattr(exc, 'mediaflow_phase', None) == 'request_write':
            return True
        pending.extend([exc.__cause__, exc.__context__, *exc.args])
    return False


def _before_deadline(operation, deadline, close=None):
    """Bound blocking I/O by wall time; abandoned output is never consumed."""
    from model_providers import ProviderError
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderError("network_timeout")
    result = queue.Queue(maxsize=1)
    abandoned = threading.Event()
    def run():
        try:
            value = operation()
            if abandoned.is_set():
                if hasattr(value, "close"):
                    value.close()
                return
            result.put((True, value))
        except BaseException as error:
            result.put((False, error))
    threading.Thread(target=run, daemon=True).start()
    try:
        ok, value = result.get(timeout=remaining)
    except queue.Empty:
        abandoned.set()
        if close:
            threading.Thread(target=close, daemon=True).start()
        raise ProviderError("network_timeout") from None
    if time.monotonic() >= deadline:
        if close:
            threading.Thread(target=close, daemon=True).start()
        raise ProviderError("network_timeout")
    if not ok:
        raise value
    return value


class _MeteredResponse:
    def __init__(self, response, receipt):
        self._response, self._receipt = response, receipt

    def __getattr__(self, name):
        return getattr(self._response, name)

    def _usage(self, value):
        usage = value.get("usage") if isinstance(value, dict) else None
        if not isinstance(usage, dict):
            return
        recorded = dict(self._receipt["usage"] or {})
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if type(usage.get(key)) is int and usage[key] >= 0:
                recorded[key] = max(recorded.get(key, 0), usage[key])
        cost = usage.get("cost")
        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
            recorded["cost"] = max(recorded.get("cost", 0), cost)
        if recorded:
            self._receipt["usage"] = recorded

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
def _openrouter_post(url: str, receipt, **kwargs):
    deadline = kwargs.pop("request_deadline", time.monotonic()+20)
    # No catalogue request, price ceiling, reservation or configured local cap.
    response = _before_deadline(lambda: _post_request(url, **kwargs), deadline)
    try:
        yield _MeteredResponse(_TokenPlanResponse(response, {"usage": None}, deadline), receipt)
    finally:
        if hasattr(response, "close"):
            threading.Thread(target=response.close, daemon=True).start()


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
        value = _before_deadline(self.response.json, self.deadline, getattr(self.response, "close", None))
        self._record(value)
        return value

    def iter_lines(self, *args, **kwargs):
        from model_providers import ProviderError, http_error
        size = 0
        lines = iter(self.response.iter_lines(*args, **kwargs))
        while True:
            try:
                line = _before_deadline(lambda: next(lines), self.deadline, getattr(self.response, "close", None))
            except StopIteration:
                return
            size += len(line)
            if time.monotonic() >= self.deadline:
                raise ProviderError("network_timeout")
            if size > 2_000_000:
                raise ProviderError("invalid_response")
            try:
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                if text.startswith("data:") and text[5:].strip() != "[DONE]":
                    value = json.loads(text[5:])
                    self._record(value)
                    if value.get("error"):
                        error = value["error"]
                        status = error.get("code", 500) if isinstance(error, dict) else 500
                        status = int(status) if str(status).isdigit() else 500
                        http_error(SimpleNamespace(status_code=status, json=lambda: value))
                        raise ProviderError("provider_failure")
            except (ValueError, UnicodeError, TypeError, AttributeError):
                error = ProviderError("invalid_response")
                error.diagnostics["category"] = "json"
                raise error from None
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
            with _openrouter_post(url, receipt, **kwargs) as response:
                yield response
            return
        deadline = kwargs.pop("request_deadline", time.monotonic()+20)
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
        kwargs["timeout"] = (min(5, remaining), min(25, remaining))
        stage = 'connect_or_headers'
        try:
            response = _before_deadline(lambda: _post_request(url, **kwargs), deadline)
            try:
                stage = 'response_read'
                if 300 <= response.status_code < 400:
                    raise ProviderError("provider_failure")
                wrapped = _TokenPlanResponse(response, receipt, deadline)
                http_error(wrapped)
                yield wrapped
            finally:
                if hasattr(response, "close"):
                    threading.Thread(target=response.close, daemon=True).start()
        except requests.RequestException as exc:
            error = ProviderError("network_timeout")
            # Only our finite diagnostic vocabulary crosses this boundary;
            # exception messages can contain URLs, proxy credentials or keys.
            category = ('write_timeout' if _request_write_timeout(exc)
                        else 'connect_timeout' if isinstance(exc, requests.ConnectTimeout)
                        else 'read_timeout' if isinstance(exc, requests.ReadTimeout)
                        else 'tls_error' if isinstance(exc, requests.exceptions.SSLError)
                        else 'proxy_error' if isinstance(exc, requests.exceptions.ProxyError)
                        else 'connection_error' if isinstance(exc, requests.ConnectionError)
                        else 'request_error')
            error.diagnostics.update(stage='request_write' if category == 'write_timeout' else stage,
                                     transport_error=category)
            raise error from None
