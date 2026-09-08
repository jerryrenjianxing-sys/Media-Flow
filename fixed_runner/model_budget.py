"""Shared model transport and usage recording (legacy import name, no budgets).

Historical budget databases and environment settings are not read or rewritten.
All business callers share configuration admission, deadlines and real usage.
"""
from __future__ import annotations

import json
import math
import time
from contextlib import contextmanager

import requests


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
    deadline = kwargs.pop("request_deadline", None)
    if deadline is not None and time.monotonic() >= deadline:
        raise RuntimeError("visual_deadline_exceeded")
    # No catalogue request, price ceiling, reservation or configured local cap.
    with requests.post(url, **kwargs) as response:
        yield _MeteredResponse(response, receipt)


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
            with _openrouter_post(url, receipt, **kwargs) as response:
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
