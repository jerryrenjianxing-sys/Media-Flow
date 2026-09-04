from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CalibrationResult:
    page_type: str
    controls: dict[str, dict[str, Any]]
    capabilities: dict[str, bool]
    evidence: list[str]
    warnings: list[str]


class PlatformAdapter(Protocol):
    """Small semantic surface shared by initialization and task workflows."""

    platform_id: str
    package_name: str
    adapter_version: str

    def app_version(self) -> str: ...

    def ensure_ready(self) -> None: ...

    def classify_page(self) -> str: ...

    def calibrate_navigation(self, search_query: str) -> CalibrationResult: ...

    def run_write_acceptance(self, comment: str) -> dict[str, Any]: ...


class MemoryPlatformAdapter:
    """Test adapter proving the workflow skeleton has no Douyin package dependency."""

    platform_id = "memory"
    package_name = "test.memory.platform"
    adapter_version = "memory-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def app_version(self) -> str:
        return "1.0"

    def ensure_ready(self) -> None:
        self.calls.append("ensure_ready")

    def classify_page(self) -> str:
        return "feed"

    def calibrate_navigation(self, search_query: str) -> CalibrationResult:
        self.calls.append("calibrate:" + search_query)
        return CalibrationResult(
            page_type="search_feed",
            controls={},
            capabilities={"screenshot": True},
            evidence=[],
            warnings=[],
        )

    def run_write_acceptance(self, comment: str) -> dict[str, Any]:
        self.calls.append("write:" + comment)
        return {"comment_verified": True}
