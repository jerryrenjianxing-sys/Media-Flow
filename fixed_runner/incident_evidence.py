from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping


IncidentSink = Callable[[dict[str, Any]], None]


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return normalized[:72] or "unknown"


def _emit(recorder, event: str, **payload: Any) -> None:
    try:
        recorder.emit(event, **payload)
    except Exception:
        # Evidence capture must never replace the original task outcome.
        pass


def record_incident_evidence(
    *,
    device,
    recorder,
    incident_sink: IncidentSink | None,
    stage: str,
    error_type: str,
    error_message: str,
    outcome: str,
    recovery_action: str | None,
    context: Mapping[str, Any] | None = None,
    source: str | None = None,
    video_index: int | None = None,
) -> bool:
    """Capture paired local evidence and persist one incident without raising.

    The caller supplies only incident meaning. This module owns the file names,
    partial-capture reporting and sink failure isolation so every workflow uses
    the same evidence contract.
    """
    if incident_sink is None:
        return False

    run_dir_value = getattr(recorder, "run_dir", None)
    run_dir = Path(run_dir_value) if run_dir_value is not None else None
    stem = f"incident-{_safe_name(stage)}-{_safe_name(error_message)}"
    screenshot_path: str | None = None
    ui_tree_path: str | None = None
    capture_errors: dict[str, str] = {}

    screenshot = getattr(recorder, "screenshot", None)
    if run_dir is None or not callable(screenshot):
        capture_errors["screenshot"] = "recorder_does_not_support_screenshot"
    else:
        try:
            screenshot(device, stem)
            image_path = run_dir / f"{stem}.png"
            if image_path.is_file():
                screenshot_path = str(image_path)
            else:
                capture_errors["screenshot"] = "screenshot_file_missing"
        except Exception as exc:
            capture_errors["screenshot"] = f"{type(exc).__name__}: {exc}"[:240]

    if source is None:
        try:
            source = str(device.dump_hierarchy(compressed=True, pretty=False))
        except Exception as exc:
            capture_errors["ui_tree"] = f"{type(exc).__name__}: {exc}"[:240]
    if source is not None:
        if run_dir is None:
            capture_errors["ui_tree"] = "recorder_run_directory_missing"
        else:
            try:
                tree_path = run_dir / f"{stem}.xml"
                tree_path.write_text(str(source), encoding="utf-8")
                ui_tree_path = str(tree_path)
            except Exception as exc:
                capture_errors["ui_tree"] = f"{type(exc).__name__}: {exc}"[:240]

    incident_context = dict(context or {})
    incident_context["evidence_capture"] = {
        "screenshot": "saved" if screenshot_path else "failed",
        "ui_tree": "saved" if ui_tree_path else "failed",
        "errors": capture_errors,
    }
    incident = {
        "video_index": video_index,
        "stage": stage,
        "error_type": error_type,
        "error_message": error_message,
        "outcome": outcome,
        "recovery_action": recovery_action,
        "screenshot_path": screenshot_path,
        "ui_tree_path": ui_tree_path,
        "context": incident_context,
    }
    try:
        incident_sink(incident)
    except Exception as exc:
        _emit(
            recorder,
            "incident_record_failed",
            stage=stage,
            error_type=type(exc).__name__,
            error=str(exc)[:240],
            evidence_capture=incident_context["evidence_capture"],
        )
        return False

    _emit(
        recorder,
        "incident_recorded",
        stage=stage,
        error_type=error_type,
        error_message=error_message,
        outcome=outcome,
        recovery_action=recovery_action,
        evidence_capture=incident_context["evidence_capture"],
    )
    return True
