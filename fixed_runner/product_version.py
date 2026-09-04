from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from runtime_layout import APP_ROOT, IS_DISTRIBUTION


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def distribution_version(manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize release metadata without promoting a development stage."""
    version = str(manifest.get("version") or "unknown")
    dirty = bool(manifest.get("source_dirty", False))
    channel = str(manifest.get("channel") or ("development" if dirty else "release"))
    revision = str(manifest.get("source_revision") or "unknown")
    fallback_display = (
        f"{version}-dev+{revision}{'.dirty' if dirty else ''}"
        if channel == "development"
        else version
    )
    display_version = str(manifest.get("display_version") or fallback_display)
    return {
        "channel": channel,
        "version": version,
        "display_version": display_version,
        "source_revision": revision,
        "source_dirty": dirty,
        "build_identity": str(manifest.get("build_identity") or display_version),
    }


@lru_cache(maxsize=1)
def product_version() -> dict[str, Any]:
    """Return one public build identity for source and packaged runtimes."""
    if IS_DISTRIBUTION:
        return distribution_version(_json(APP_ROOT / "release-manifest.json"))

    canonical = _json(APP_ROOT / "packaging" / "version.json")
    version = str(canonical.get("version") or "unknown")
    revision = "unknown"
    dirty = False
    try:
        revision_result = subprocess.run(
            ["git", "-C", str(APP_ROOT), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if revision_result.returncode == 0 and revision_result.stdout.strip():
            revision = revision_result.stdout.strip()
        dirty_result = subprocess.run(
            ["git", "-C", str(APP_ROOT), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        dirty = dirty_result.returncode == 0 and bool(dirty_result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    suffix = f"+{revision}{'.dirty' if dirty else ''}"
    return {
        "channel": "development",
        "version": version,
        "display_version": f"{version}-dev{suffix}",
        "source_revision": revision,
        "source_dirty": dirty,
        "build_identity": f"{version}-dev{suffix}",
    }
