"""Provider-independent, redacted model errors shared by every workflow."""
from typing import Any

MODEL_ERROR_KINDS = {"permanent_rejection", "authentication", "balance", "rate_limited",
                     "provider_failure", "transient_network", "invalid_request", "invalid_response"}


class CloudModelError(RuntimeError):
    def __init__(self, kind: str, message: str, *, status_code: int | None = None,
                 retryable: bool = False, attempts: int = 1, diagnostics: dict[str, Any] | None = None):
        if kind not in MODEL_ERROR_KINDS:
            raise ValueError(f"Unknown cloud model error kind: {kind}")
        self.kind, self.status_code, self.retryable = kind, status_code, retryable
        self.attempts, self.diagnostics = attempts, diagnostics or {}
        super().__init__(f"cloud_model:{kind}: {message}")

    def public_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "status_code": self.status_code, "retryable": self.retryable,
                "attempts": self.attempts, "diagnostics": self.diagnostics}
