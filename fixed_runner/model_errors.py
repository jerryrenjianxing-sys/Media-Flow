"""Provider-independent, redacted model errors shared by every workflow."""
from typing import Any

MODEL_ERROR_KINDS = {"permanent_rejection", "authentication", "balance", "rate_limited",
                     "provider_failure", "transient_network", "invalid_request", "invalid_response"}

def public_model_error(value):
    """Finite diagnostics only, also for historical incident dictionaries."""
    if not isinstance(value, dict) or value.get('kind') not in MODEL_ERROR_KINDS:
        return None
    raw = value.get('diagnostics') or {}
    diagnostics = {}
    if isinstance(raw, dict):
        for key in ('elapsed_ms', 'attempt'):
            if type(raw.get(key)) is int and raw[key] >= 0:
                diagnostics[key] = raw[key]
        vocabulary = {
            'stage': {'connect_or_headers', 'request_write', 'response_read', 'request', 'parse', 'schema'},
            'transport_error': {'write_timeout', 'connect_timeout', 'read_timeout', 'tls_error', 'proxy_error', 'connection_error', 'request_error'},
            'category': MODEL_ERROR_KINDS | {'json', 'field', 'truncation', 'empty'},
            'finish_reason': {'stop', 'length', 'content_filter', 'tool_calls'},
        }
        for key, allowed in vocabulary.items():
            if isinstance(raw.get(key), str) and raw[key] in allowed:
                diagnostics[key] = raw[key]
    return {'kind': value['kind'], 'retryable': value.get('retryable') is True,
            'status_code': value.get('status_code') if type(value.get('status_code')) is int else None,
            'attempts': value.get('attempts') if type(value.get('attempts')) is int else None,
            'diagnostics': diagnostics}


class CloudModelError(RuntimeError):
    def __init__(self, kind: str, message: str, *, status_code: int | None = None,
                 retryable: bool = False, attempts: int = 1, diagnostics: dict[str, Any] | None = None):
        if kind not in MODEL_ERROR_KINDS:
            raise ValueError(f"Unknown cloud model error kind: {kind}")
        self.kind, self.status_code, self.retryable = kind, status_code, retryable
        self.attempts, self.diagnostics = attempts, diagnostics or {}
        super().__init__(f"cloud_model:{kind}: {message}")

    def public_dict(self) -> dict[str, Any]:
        return public_model_error({"kind": self.kind, "status_code": self.status_code, "retryable": self.retryable,
                "attempts": self.attempts, "diagnostics": self.diagnostics})
