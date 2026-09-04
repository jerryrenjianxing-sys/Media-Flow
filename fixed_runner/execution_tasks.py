from __future__ import annotations

import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

from comment_ai import (
    COMMENT_CONSTRAINT_PROMPT_VERSION,
    CloudModelError,
    CommentConstraintDecision,
    CommentDecision,
    analyze_topic,
    generate_comment,
    review_comment_constraint,
)
from comment_assets import select_comment_assets
from douyin_fixed_runner import DOUYIN_PACKAGE, PROFILE
from douyin_uia2_runner import (
    Uia2DouyinRunner,
    Uia2RunRecorder,
    _visible_aweme_signals,
    classify_mutation_gate,
    find_comment_input_bounds,
    foreground_package,
)
from engagement_inspection import EngagementInspector
from feed_orchestration import FeedPhase, HybridFeedPlanner
from task_store import TaskRecord

PauseWaiter = Callable[[], None]
IncidentSink = Callable[[dict[str, Any]], None]
StopChecker = Callable[[], bool]


class DeviceFatalError(RuntimeError):
    """A task cannot safely continue because the device/feed was not recovered."""


class ModelChannelError(RuntimeError):
    """Required model capability failed while the device remained healthy."""

    def __init__(self, code: str, result: dict[str, Any]) -> None:
        self.code = code
        self.result = result
        super().__init__(code)


class ConsecutiveAnomalyLimitError(RuntimeError):
    """Too many abnormal pages occurred without a normal video between them."""


class FeedContextDriftError(RuntimeError):
    """The device left the feed required by the current orchestration phase."""


class InsufficientVideoSupplyError(RuntimeError):
    """The current phase supplied only known non-video items after one re-entry."""


KNOWN_FEED_SKIP_REASONS = frozenset(
    {
        "advertising",
        "commerce",
        "commercial_content",
        "live",
        "effect",
        "share_sheet",
        "non_video_feed_item",
    }
)
NON_VIDEO_REENTRY_THRESHOLD = 20

INTERACTION_SAFETY_VERIFICATION_TITLE = "身份安全验证"
INTERACTION_SAFETY_VERIFICATION_MARKERS = (
    "操作环境存在风险",
    "请完成身份验证",
)


def _known_safe_feed_skip(reasons: tuple[str, ...]) -> bool:
    return bool(reasons) and all(reason in KNOWN_FEED_SKIP_REASONS for reason in reasons)


def _interaction_safety_verification_visible(xml_source: str) -> bool:
    """Match the platform verification modal, not incidental video captions."""
    return INTERACTION_SAFETY_VERIFICATION_TITLE in xml_source and any(
        marker in xml_source for marker in INTERACTION_SAFETY_VERIFICATION_MARKERS
    )


def _model_error_details(exc: Exception) -> dict[str, Any] | None:
    if isinstance(exc, CloudModelError):
        return exc.public_dict()
    message = str(exc)
    legacy = re.search(r"Cloud model HTTP\s+(\d{3})", message, re.IGNORECASE)
    if not legacy:
        return None
    status_code = int(legacy.group(1))
    if status_code == 403:
        kind, retryable = "permanent_rejection", False
    elif status_code == 401:
        kind, retryable = "authentication", False
    elif status_code == 402:
        kind, retryable = "balance", False
    elif status_code == 429:
        kind, retryable = "rate_limited", True
    elif status_code in {500, 502, 503, 504}:
        kind, retryable = "provider_failure", True
    else:
        kind, retryable = "invalid_request", False
    return {
        "kind": kind,
        "status_code": status_code,
        "retryable": retryable,
        "attempts": 1,
        "diagnostics": {},
    }


def _model_error_fingerprint(details: dict[str, Any]) -> str:
    diagnostics = details.get("diagnostics") or {}
    stable = {
        "kind": details.get("kind"),
        "status_code": details.get("status_code"),
        "provider_name": diagnostics.get("provider_name"),
        "message": diagnostics.get("message"),
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _require_browsable_feed(runner, image, stage: str) -> None:
    require_shell = getattr(runner, "require_main_feed_shell", None)
    if callable(require_shell):
        require_shell(image, stage)
    else:
        runner.require_main_feed(image, stage)


def _prepare_feed_phase(runner, phase: str, query: str, reason: str) -> bool:
    prepare = getattr(runner, "prepare_feed_phase", None)
    if callable(prepare):
        return bool(prepare(phase, query, reason))
    if phase == "search":
        runner.enter_topic_search(query)
    return True


def _required_feed_confirmed(runner, image) -> bool:
    confirmed = getattr(runner, "required_feed_confirmed", None)
    if callable(confirmed):
        return bool(confirmed(image))
    return bool(runner.main_feed_confirmed(image))


def _recover_required_feed(runner, reason: str) -> bool:
    recover = getattr(runner, "recover_required_feed", None)
    if callable(recover):
        return bool(recover(reason))
    return bool(runner.recover_main_feed(reason))


def _empty_comment_panel(source: str) -> bool:
    normalized = "".join(source.split())
    return bool(
        re.search(r"评论0(?:[^0-9]|$)", normalized)
        or any(
            marker in source
            for marker in ("暂无评论", "来发表第一条评论", "期待你的评论")
        )
    )


def healthcheck(device, recorder: Uia2RunRecorder) -> dict[str, Any]:
    runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
    runner.ensure_app_ready()
    image = recorder.screenshot(device, "healthcheck")
    runner.ensure_profile(image)
    if not runner.main_feed_confirmed(image):
        device.press("back")
        time.sleep(0.7)
        image = recorder.screenshot(device, "healthcheck-after-back")
    if not runner.main_feed_confirmed(image):
        raise RuntimeError("Healthcheck could not restore the Douyin main feed")
    started = time.monotonic()
    source = device.dump_hierarchy(compressed=True, pretty=False)
    foreground = foreground_package(device)
    decision = classify_mutation_gate(source, foreground, image.width, image.height)
    result = {
        "status": "passed",
        "task_type": "healthcheck",
        "foreground_package": foreground,
        "mutation_gate_allowed": decision.allowed,
        "mutation_gate_reasons": list(decision.reasons),
        "ui_inspection_s": round(time.monotonic() - started, 3),
    }
    recorder.emit("healthcheck_complete", **result)
    return result


def comment_preview(
    device,
    recorder: Uia2RunRecorder,
    *,
    dwell_seconds: float,
    max_gate_skips: int,
    send: bool,
    comment_policy_enabled: bool = False,
    comment_policy_prompt: str = "",
) -> dict[str, Any]:
    runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=max_gate_skips)
    runner.ensure_app_ready()
    initial = recorder.screenshot(device, "comment-preview-initial")
    runner.ensure_profile(initial)
    runner.require_main_feed(initial, "comment preview")
    video = 0
    blocked_pages = 0
    runner.swipe_next(video, video + 1)
    for attempt in range(max_gate_skips + 1):
        video += 1
        runner.watch(video, dwell_seconds)
        before, gate = runner.capture_gate(video, "comment-preview")
        if gate.allowed:
            break
        blocked_pages += 1
        recorder.emit(
            "gate_skip",
            video=video,
            action="comment-preview",
            attempt=attempt + 1,
            reasons=list(gate.reasons),
        )
        if attempt >= max_gate_skips:
            raise RuntimeError(
                f"No eligible page for comment preview after {max_gate_skips + 1} candidates"
            )
        runner.swipe_next(video, video + 1)

    decision, sent, policy_review = process_current_comment(
        device,
        recorder,
        runner,
        video=video,
        send=send,
        comment_policy_enabled=comment_policy_enabled,
        comment_policy_prompt=comment_policy_prompt,
    )
    return {
        "status": "passed",
        "task_type": _comment_task_type(send),
        "decision": decision.decision,
        "comment": decision.comment,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "commercial": decision.commercial,
        "blocked_pages": blocked_pages,
        "videos_seen": video,
        "sent": sent,
        "comment_policy_review": policy_review,
        "comment_asset_review": decision.asset_audit,
        "comment_screenshots": _comment_screenshot_evidence(recorder, video, sent),
    }


def process_current_comment(
    device,
    recorder: Uia2RunRecorder,
    runner: Uia2DouyinRunner,
    *,
    video: int,
    send: bool,
    comment_policy_enabled: bool = False,
    comment_policy_prompt: str = "",
    content_plan_snapshot: dict[str, Any] | None = None,
    task_seed: int = 0,
    used_candidate_ids: set[str] | None = None,
):
    decision: CommentDecision | None = None
    policy_review: dict[str, Any] | None = None
    asset_audit: dict[str, Any] | None = None
    sent = False
    runner.tap_control("comment-preview", "open_comments_for_ai_preview")
    time.sleep(1.0)
    panel = recorder.screenshot(device, f"video-{video}-comment-ai-input")
    from douyin_fixed_runner import comment_panel_visible

    if not comment_panel_visible(panel):
        raise RuntimeError("Comment panel did not open for AI preview")
    try:
        panel_source = device.dump_hierarchy(compressed=True, pretty=False)
        if _empty_comment_panel(str(panel_source)):
            decision = CommentDecision(
                "skip", "", "评论区为空", 1.0, False, ""
            )
            recorder.emit(
                "comment_empty_panel_skip", video=video, reason=decision.reason
            )
            return decision, False, None
        assets = select_comment_assets(
            content_plan_snapshot,
            seed=task_seed,
            video_index=video,
            used_candidate_ids=used_candidate_ids,
        )
        decision = generate_comment(
            recorder.run_dir / f"video-{video}-comment-ai-input.png",
            style_template=assets.template,
            candidates=assets.candidates,
        )
        if decision.source_candidate_id and used_candidate_ids is not None:
            used_candidate_ids.add(decision.source_candidate_id)
        asset_audit = {
            "video_index": video,
            "content_plan_revision_id": (content_plan_snapshot or {}).get(
                "content_plan_revision_id"
            ),
            "template": assets.template,
            "candidates": [dict(item) for item in assets.candidates],
            "source_type": decision.source_type,
            "source_candidate_id": decision.source_candidate_id,
            "final_comment": decision.comment,
            "decision": decision.decision,
            "reason": decision.reason,
        }
        decision = CommentDecision(
            decision=decision.decision,
            comment=decision.comment,
            reason=decision.reason,
            confidence=decision.confidence,
            commercial=decision.commercial,
            raw_response=decision.raw_response,
            source_type=decision.source_type,
            source_candidate_id=decision.source_candidate_id,
            asset_audit=asset_audit,
        )
        raw_path = recorder.run_dir / "comment-ai-raw.txt"
        raw_path.write_text(decision.raw_response[:8000], encoding="utf-8")
        decision_path = recorder.run_dir / "comment-decision.json"
        decision_path.write_text(
            json.dumps(decision.public_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        recorder.emit("comment_ai_decision", **decision.public_dict())
        policy_allowed = True
        if comment_policy_enabled and decision.decision == "comment":
            try:
                constraint = review_comment_constraint(
                    recorder.run_dir / f"video-{video}-comment-ai-input.png",
                    decision.comment,
                    comment_policy_prompt,
                )
            except Exception as exc:
                constraint = CommentConstraintDecision(
                    decision="block",
                    category="policy_uncertain",
                    reason=f"review_error:{type(exc).__name__}",
                    confidence=0.0,
                    policy_version=COMMENT_CONSTRAINT_PROMPT_VERSION,
                    raw_response="",
                )
            policy_allowed = constraint.allowed
            policy_review = {
                "video_index": video,
                "candidate_comment": decision.comment,
                **constraint.public_dict(),
            }
            (recorder.run_dir / f"video-{video}-comment-constraint.json").write_text(
                json.dumps(policy_review, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if constraint.raw_response:
                (recorder.run_dir / f"video-{video}-comment-constraint-raw.txt").write_text(
                    constraint.raw_response[:8000], encoding="utf-8"
                )
            recorder.emit("comment_constraint_decision", **policy_review)
        asset_audit["comment_policy_review"] = policy_review
        (recorder.run_dir / f"video-{video}-comment-assets.json").write_text(
            json.dumps(asset_audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        recorder.emit("comment_asset_decision", **asset_audit)
        if send and decision.decision == "comment" and policy_allowed:
            sent = send_comment(
                device,
                recorder,
                decision.comment,
                video,
                expected_size=(runner.profile.width, runner.profile.height),
            )
    except Exception as exc:
        try:
            evidence_name = f"video-{video}-comment-failure-before-recovery"
            recorder.screenshot(device, evidence_name)
            setattr(
                exc,
                "evidence_screenshot_path",
                str(recorder.run_dir / f"{evidence_name}.png"),
            )
        except Exception:
            pass
        try:
            evidence_ui = device.dump_hierarchy(compressed=True, pretty=False)
            evidence_ui_path = (
                recorder.run_dir / f"video-{video}-comment-failure-before-recovery.xml"
            )
            evidence_ui_path.write_text(str(evidence_ui), encoding="utf-8")
            setattr(exc, "evidence_ui_tree_path", str(evidence_ui_path))
        except Exception:
            pass
        recorder.emit(
            "comment_failure_evidence",
            video=video,
            error_type=type(exc).__name__,
            error=str(exc),
            screenshot_path=getattr(exc, "evidence_screenshot_path", None),
            ui_tree_path=getattr(exc, "evidence_ui_tree_path", None),
        )
        raise
    finally:
        try:
            runner.close_comment_panel(video, f"video-{video}-comment-ai-closed")
        except Exception as close_exc:
            if sent and decision is not None:
                setattr(
                    close_exc,
                    "confirmed_comment_outcome",
                    (decision, sent, policy_review),
                )
            raise
    return decision, sent, policy_review


def two_video_demo(
    device,
    recorder: Uia2RunRecorder,
    *,
    dwell: list[float],
    max_gate_skips: int,
) -> dict[str, Any]:
    if len(dwell) != 2:
        raise ValueError("Exactly two dwell values are required for the demo")
    wall_started = time.monotonic()
    runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=max_gate_skips)
    runner.ensure_app_ready()
    initial = recorder.screenshot(device, "two-video-demo-initial")
    runner.ensure_profile(initial)
    runner.require_main_feed(initial, "two-video demo")
    video = 0
    blocked_pages = 0
    completed_actions: list[str] = []

    def next_allowed(action_name: str, seconds: float):
        nonlocal video, blocked_pages
        runner.swipe_next(video, video + 1)
        for attempt in range(max_gate_skips + 1):
            video += 1
            runner.watch(video, seconds)
            before, gate = runner.capture_gate(video, action_name)
            if gate.allowed:
                return before
            blocked_pages += 1
            recorder.emit(
                "gate_skip",
                video=video,
                action=action_name,
                attempt=attempt + 1,
                reasons=list(gate.reasons),
            )
            if attempt >= max_gate_skips:
                raise RuntimeError(
                    f"No eligible page for {action_name} after "
                    f"{max_gate_skips + 1} candidates"
                )
            runner.swipe_next(video, video + 1)
        raise AssertionError("unreachable")

    first = next_allowed("like", dwell[0])
    if runner.like_verified(video, first):
        completed_actions.append("like")

    second = next_allowed("favorite", dwell[1])
    if runner.favorite_verified(video, second):
        completed_actions.append("favorite")

    _, comment_gate = runner.capture_gate(video, "comment-preview")
    if not comment_gate.allowed:
        raise RuntimeError(
            "Second video became ineligible before comment: "
            + ",".join(comment_gate.reasons)
        )
    decision, sent, policy_review = process_current_comment(
        device, recorder, runner, video=video, send=True
    )
    if sent:
        completed_actions.append("comment")

    wall_s = round(time.monotonic() - wall_started, 3)
    return {
        "status": "passed",
        "task_type": "douyin_two_video_demo",
        "decision": decision.decision,
        "comment": decision.comment,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "commercial": decision.commercial,
        "blocked_pages": blocked_pages,
        "videos_seen": video,
        "sent": sent,
        "comment_policy_review": policy_review,
        "comment_asset_review": decision.asset_audit,
        "completed_actions": completed_actions,
        "wall_s": wall_s,
    }


def routed_action_probabilities(
    config: dict[str, Any], matched: bool
) -> dict[str, float]:
    prefix = "matched_" if matched else ""
    return {
        action: float(
            config.get(prefix + action + "_probability", config[action + "_probability"])
        )
        for action in ("like", "favorite", "comment")
    }


def routed_action_plan(
    config: dict[str, Any], *, matched: bool, safe: bool, feed_phase: str | None = None
) -> tuple[dict[str, float], dict[str, str]]:
    """Return effective probabilities and explicit per-action route evidence."""
    if not safe:
        return (
            {action: 0.0 for action in ("like", "favorite", "comment")},
            {action: "safety_blocked" for action in ("like", "favorite", "comment")},
        )

    content_mode = str(config.get("content_mode", ""))
    if content_mode == "hybrid" and feed_phase not in {"search", "home"}:
        return (
            {action: 0.0 for action in ("like", "favorite", "comment")},
            {action: "feed_phase_unverified" for action in ("like", "favorite", "comment")},
        )

    search_phase = content_mode == "search" or (
        content_mode == "hybrid" and feed_phase == "search"
    )
    search_trusted = bool(
        search_phase
        and config.get("search_trust_results") is True
    )
    if content_mode == "hybrid" and feed_phase == "home":
        probabilities = {
            "like": float(config["like_probability"]),
            "favorite": float(config["favorite_probability"]),
            "comment": float(config["comment_probability"]) if matched else 0.0,
        }
        return probabilities, {
            "like": "home_safe_random",
            "favorite": "home_safe_random",
            "comment": "topic_matched" if matched else "topic_mismatch_blocked",
        }

    if not search_trusted:
        route = "topic_matched" if matched else "other_safe_content"
        probabilities = routed_action_probabilities(config, matched)
        routes = {action: route for action in ("like", "favorite", "comment")}
        if search_phase and not matched:
            probabilities["comment"] = 0.0
            routes["comment"] = "topic_mismatch_blocked"
        return probabilities, routes

    probabilities = {
        "like": float(config["matched_like_probability"]),
        "favorite": float(config["matched_favorite_probability"]),
        "comment": (
            float(config["matched_comment_probability"]) if matched else 0.0
        ),
    }
    routes = {
        "like": "search_source_trusted",
        "favorite": "search_source_trusted",
        "comment": "topic_matched" if matched else "topic_mismatch_blocked",
    }
    return probabilities, routes


def topic_session(
    device,
    recorder: Uia2RunRecorder,
    *,
    config: dict[str, Any],
    pause_waiter: PauseWaiter | None = None,
    incident_sink: IncidentSink | None = None,
    stop_checker: StopChecker | None = None,
) -> dict[str, Any]:
    """Run a bounded, reproducible topic-matching safety-test session."""
    wall_started = time.monotonic()
    rng = random.Random(int(config["seed"]))
    runner = Uia2DouyinRunner(
        device,
        recorder,
        PROFILE,
        max_gate_skips=int(config.get("max_gate_skips", 3)),
        device_id=str(config.get("device_id", "")),
    )
    content_mode = str(
        config.get(
            "content_mode",
            "mixed" if config.get("topic_filter_enabled", True) else "general",
        )
    )
    search_query = str(config.get("search_query", "")).strip()
    hybrid_planner: HybridFeedPlanner | None = None
    initial_hybrid_phase_name: str | None = None
    if content_mode == "hybrid":
        hybrid_planner = HybridFeedPlanner(
            total_videos=int(config["video_count"]),
            rng=rng,
            search_min=int(config.get("search_segment_min", 7)),
            search_max=int(config.get("search_segment_max", 14)),
            home_min=int(config.get("home_segment_min", 5)),
            home_max=int(config.get("home_segment_max", 10)),
        )
    runner.ensure_app_ready()
    if content_mode == "search":
        runner.enter_topic_search(search_query)
    elif hybrid_planner is not None:
        first_phase = hybrid_planner.current_or_start()
        if first_phase is None or not _prepare_feed_phase(
            runner, first_phase.name, search_query, "hybrid-phase-1"
        ):
            raise DeviceFatalError("Could not enter the first hybrid feed phase")
        initial_hybrid_phase_name = first_phase.name
        recorder.emit(
            "feed_phase_started",
            phase=first_phase.name,
            target=first_phase.target,
            processed=first_phase.processed,
            total_target=int(config["video_count"]),
        )
    initial = recorder.screenshot(device, "topic-session-initial")
    runner.ensure_profile(initial)
    _require_browsable_feed(runner, initial, "topic session")
    startup_recovery_events = (
        runner.drain_recovery_events()
        if hasattr(runner, "drain_recovery_events")
        else []
    )
    if startup_recovery_events and incident_sink is not None:
        initial_ui_path = recorder.run_dir / "topic-session-initial.xml"
        try:
            initial_ui_path.write_text(
                str(device.dump_hierarchy(compressed=True, pretty=False)),
                encoding="utf-8",
            )
            initial_ui_tree_path: str | None = str(initial_ui_path)
        except Exception:
            initial_ui_tree_path = None
        for recovery_event in startup_recovery_events:
            incident_sink(
                {
                    "video_index": None,
                    "stage": "startup_recovery",
                    "error_type": "RecoveredPage",
                    "error_message": str(recovery_event.get("rule_id", "known-page")),
                    "outcome": "recovered",
                    "recovery_action": str(recovery_event.get("action", "verified-rule")),
                    "screenshot_path": str(
                        recorder.run_dir / "topic-session-initial.png"
                    ),
                    "ui_tree_path": initial_ui_tree_path,
                    "context": {"verified_recovery": recovery_event},
                }
            )
    summary: dict[str, Any] = {
        "videos_seen": 0,
        "feed_items_seen": 0,
        "topic_matches": 0,
        "likes": 0,
        "favorites": 0,
        "comments_generated": 0,
        "comments_sent": 0,
        "comment_screenshots": [],
        "comment_policy_reviews": [],
        "comment_policy_allowed": 0,
        "comment_policy_blocked": 0,
        "comment_asset_reviews": [],
        "blocked_pages": 0,
        "known_safe_skips": 0,
        "non_video_feed_items": 0,
        "feed_phase_reentries": 0,
        "unknown_blocked_pages": 0,
        "page_drifts": 0,
        "successful_recoveries": 0,
        "visual_safety_blocks": 0,
        "topic_analysis_skipped": 0,
        "model_attempts": 0,
        "model_valid_decisions": 0,
        "model_errors": 0,
        "model_error_counts": {},
        "model_circuit_opened": False,
        "reaction_circuit_opened": False,
        "reaction_actions_disabled": [],
        "comment_circuit_opened": False,
        "comment_actions_disabled": [],
        "video_errors": 0,
        "recovered_videos": 0,
        "skipped_videos": 0,
        "recovery_events": startup_recovery_events,
        "content_mode": content_mode,
        "search_trust_results": bool(
            content_mode in {"search", "hybrid"}
            and config.get("search_trust_results") is True
        ),
    }
    if hybrid_planner is not None:
        summary["phase_summaries"] = {
            phase: {
                "label": "搜索视频流" if phase == "search" else "主页视频流",
                "videos": 0,
                "model_attempts": 0,
                "model_valid_decisions": 0,
                "topic_exact": 0,
                "likes": 0,
                "favorites": 0,
                "comments_sent": 0,
                "known_safe_skips": 0,
                "non_video_feed_items": 0,
                "phase_reentries": 0,
                "unknown_blocked_pages": 0,
                "incidents": 0,
                "recoveries": 0,
            }
            for phase in ("search", "home")
        }
    decisions: list[dict[str, Any]] = []
    preview_only = bool(config["preview_only"])
    topic_filter_enabled = content_mode != "general"
    topic_analysis_required = topic_filter_enabled or any(
        float(config.get(f"{action}_probability", 0.0)) > 0.0
        for action in ("like", "favorite", "comment")
    )
    anomaly_limit = max(1, min(50, int(config.get("max_gate_skips", 3))))
    consecutive_anomalies = 0
    permanent_model_fingerprint = ""
    permanent_model_failures = 0
    disabled_reactions: set[str] = set()
    comment_disabled = False
    wait_for_resume = pause_waiter or (lambda: None)
    should_stop = stop_checker or (lambda: False)
    stopped_by_user = False
    used_comment_candidates: set[str] = set()
    consecutive_non_video_items = 0
    non_video_phase_reentry_used = False

    active_phase_name = "search" if content_mode == "search" else "home"
    active_phase_target = int(config["video_count"])
    prepared_hybrid_phase = initial_hybrid_phase_name
    while int(summary["videos_seen"]) < int(config["video_count"]):
        if hybrid_planner is not None:
            phase = hybrid_planner.current_or_start()
            if phase is None:
                break
            active_phase_name = phase.name
            active_phase_target = phase.target
            if prepared_hybrid_phase != phase.name:
                if not _prepare_feed_phase(
                    runner,
                    phase.name,
                    search_query,
                    f"hybrid-phase-{phase.name}-{hybrid_planner.total_processed + 1}",
                ):
                    raise DeviceFatalError(
                        f"Could not enter hybrid {phase.name} feed phase"
                    )
                prepared_hybrid_phase = phase.name
                recorder.emit(
                    "feed_phase_started",
                    phase=phase.name,
                    target=phase.target,
                    processed=phase.processed,
                    total_processed=hybrid_planner.total_processed,
                    total_target=int(config["video_count"]),
                )
        summary["feed_items_seen"] += 1
        video = int(summary["feed_items_seen"])
        if should_stop():
            stopped_by_user = True
            recorder.emit(
                "task_stop_checkpoint",
                next_video=video,
                videos_seen=summary["videos_seen"],
                feed_phase=active_phase_name,
            )
            break
        dwell = round(rng.uniform(float(config["dwell_min"]), float(config["dwell_max"])), 2)
        entry: dict[str, Any] = {
            "video": video,
            "dwell_s": dwell,
            "feed_phase": active_phase_name,
            "phase_target": active_phase_target,
        }
        actions: list[str] = []
        stage = "swipe"
        try:
            wait_for_resume()
            runner.swipe_next(video - 1, video)
            stage = "watch"
            runner.watch(video, dwell)
            wait_for_resume()
            stage = "topic_gate"
            frame, gate = runner.capture_gate(video, "topic-analysis")
            if not gate.allowed:
                summary["blocked_pages"] += 1
                known_safe_skip = _known_safe_feed_skip(gate.reasons)
                if known_safe_skip:
                    summary["known_safe_skips"] += 1
                    if hybrid_planner is not None:
                        summary["phase_summaries"][active_phase_name]["known_safe_skips"] += 1
                    consecutive_anomalies = 0
                else:
                    summary["unknown_blocked_pages"] += 1
                    summary["page_drifts"] += 1
                    if hybrid_planner is not None:
                        summary["phase_summaries"][active_phase_name]["unknown_blocked_pages"] += 1
                    consecutive_anomalies += 1
                entry.update(
                    {
                        "matched": False,
                        "reason": ",".join(gate.reasons),
                        "actions": [],
                        "outcome": (
                            "non_video_feed_item"
                            if gate.reasons == ("non_video_feed_item",)
                            else "known_safe_skip"
                            if known_safe_skip
                            else "unknown_block"
                        ),
                    }
                )
                if known_safe_skip:
                    if gate.reasons == ("non_video_feed_item",):
                        summary["non_video_feed_items"] += 1
                        consecutive_non_video_items += 1
                        if hybrid_planner is not None:
                            summary["phase_summaries"][active_phase_name][
                                "non_video_feed_items"
                            ] += 1
                        recorder.emit(
                            "non_video_feed_item",
                            video=video,
                            feed_phase=active_phase_name,
                            consecutive=consecutive_non_video_items,
                        )
                        if consecutive_non_video_items >= NON_VIDEO_REENTRY_THRESHOLD:
                            if non_video_phase_reentry_used:
                                decisions.append(entry)
                                raise InsufficientVideoSupplyError(
                                    "当前阶段视频供给不足：重新进入后仍连续遇到20个图文项目"
                                )
                            if not _prepare_feed_phase(
                                runner,
                                active_phase_name,
                                search_query,
                                f"non-video-supply-{active_phase_name}",
                            ):
                                decisions.append(entry)
                                raise InsufficientVideoSupplyError(
                                    "当前阶段视频供给不足，且重新进入阶段失败"
                                )
                            non_video_phase_reentry_used = True
                            consecutive_non_video_items = 0
                            summary["feed_phase_reentries"] += 1
                            if hybrid_planner is not None:
                                summary["phase_summaries"][active_phase_name][
                                    "phase_reentries"
                                ] += 1
                            recorder.emit(
                                "feed_phase_reentered",
                                reason="non_video_supply",
                                feed_phase=active_phase_name,
                                query=search_query if active_phase_name == "search" else "",
                            )
                    decisions.append(entry)
                    continue
                if consecutive_anomalies >= anomaly_limit:
                    raise ConsecutiveAnomalyLimitError(
                        f"连续异常页面达到 {anomaly_limit} 条，当前任务已停止"
                    )
                raise FeedContextDriftError(
                    "Current page is not the feed required by this phase: "
                    + ",".join(gate.reasons)
                )

            summary["videos_seen"] += 1
            consecutive_non_video_items = 0
            non_video_phase_reentry_used = False
            if hybrid_planner is not None:
                phase_after = hybrid_planner.consume_valid_video()
                summary["phase_summaries"][active_phase_name]["videos"] += 1
                entry["phase_processed"] = phase_after.processed
                entry["valid_video_index"] = hybrid_planner.total_processed
            else:
                entry["valid_video_index"] = int(summary["videos_seen"])
            recorder.emit(
                "valid_video_processed",
                video=video,
                valid_video_index=entry["valid_video_index"],
                videos_seen=int(summary["videos_seen"]),
                target=int(config["video_count"]),
                feed_phase=active_phase_name,
                phase_processed=int(entry.get("phase_processed", summary["videos_seen"])),
                phase_target=active_phase_target,
            )

            if not topic_analysis_required:
                zero_probabilities = {
                    action: 0.0 for action in ("like", "favorite", "comment")
                }
                disabled_routes = {
                    action: "observation_only"
                    for action in ("like", "favorite", "comment")
                }
                summary["topic_analysis_skipped"] += 1
                entry.update(
                    {
                        "matched": False,
                        "actions": [],
                        "probability_route": "observation_only",
                        "probabilities": zero_probabilities,
                        "action_routes": disabled_routes,
                        "visual_safety": {
                            "evaluated": False,
                            "allowed": False,
                            "reason": "no_enabled_actions",
                            "evidence": [],
                        },
                    }
                )
                recorder.emit(
                    "topic_analysis_skipped",
                    video=video,
                    valid_video_index=entry["valid_video_index"],
                    feed_phase=active_phase_name,
                    reason="general_feed_with_no_enabled_actions",
                    probabilities=zero_probabilities,
                    action_routes=disabled_routes,
                )
                decisions.append(entry)
                consecutive_anomalies = 0
                continue

            frame_path = recorder.run_dir / f"video-{video}-topic-analysis-before.png"
            target_topic = (
                str(config["topic_prompt"])
                if topic_filter_enabled
                else "不限具体主题；只要是普通、清晰、非敏感且适合互动的内容即可"
            )
            stage = "topic_model"
            summary["model_attempts"] += 1
            if hybrid_planner is not None:
                summary["phase_summaries"][active_phase_name]["model_attempts"] += 1
            try:
                topic = analyze_topic(frame_path, target_topic)
            except Exception as model_exc:
                model_error = _model_error_details(model_exc)
                if model_error is None:
                    raise
                summary["model_errors"] += 1
                error_kind = str(model_error["kind"])
                error_counts = summary["model_error_counts"]
                error_counts[error_kind] = int(error_counts.get(error_kind, 0)) + 1
                fingerprint = _model_error_fingerprint(model_error)
                if not bool(model_error.get("retryable")):
                    if fingerprint == permanent_model_fingerprint:
                        permanent_model_failures += 1
                    else:
                        permanent_model_fingerprint = fingerprint
                        permanent_model_failures = 1
                else:
                    permanent_model_fingerprint = ""
                    permanent_model_failures = 0
                circuit_open = permanent_model_failures >= 3
                summary["model_circuit_opened"] = circuit_open
                outcome = "model_circuit_open" if circuit_open else "model_failed"
                ui_tree_path: str | None = None
                try:
                    ui_path = recorder.run_dir / f"video-{video}-incident-topic_model.xml"
                    ui_path.write_text(
                        str(device.dump_hierarchy(compressed=True, pretty=False)),
                        encoding="utf-8",
                    )
                    ui_tree_path = str(ui_path)
                except Exception:
                    pass
                incident = {
                    "video_index": video,
                    "stage": "topic_model",
                    "error_type": type(model_exc).__name__,
                    "error_message": str(model_exc),
                    "outcome": outcome,
                    "recovery_action": "none_model_channel",
                    "screenshot_path": str(frame_path),
                    "ui_tree_path": ui_tree_path,
                    "context": {
                        "actions": [],
                        "dwell_s": dwell,
                        "model_error": model_error,
                        "model_error_fingerprint": fingerprint,
                        "permanent_failure_count": permanent_model_failures,
                    },
                }
                recorder.emit("video_incident", **incident)
                if incident_sink is not None:
                    incident_sink(incident)
                entry.update(
                    {
                        "matched": False,
                        "actions": [],
                        "error": str(model_exc),
                        "outcome": outcome,
                        "model_error": model_error,
                    }
                )
                decisions.append(entry)
                if circuit_open:
                    decisions_path = recorder.run_dir / "topic-session-decisions.json"
                    decisions_path.write_text(
                        json.dumps(decisions, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    attempts = int(summary["model_attempts"])
                    valid = int(summary["model_valid_decisions"])
                    summary["model_valid_response_rate"] = round(
                        valid / attempts if attempts else 1.0, 4
                    )
                    code = f"model_channel_circuit_open:{error_kind}"
                    raise ModelChannelError(
                        code,
                        {
                            "status": "failed",
                            "failure_reason": {
                                "code": code,
                                "model_error": model_error,
                            },
                            "stopped_by_user": False,
                            "task_type": "douyin_topic_session",
                            "preview_only": preview_only,
                            "content_mode": content_mode,
                            "topic_filter_enabled": topic_filter_enabled,
                            "seed": int(config["seed"]),
                            "round_index": int(config.get("round_index", 1)),
                            "action_control": "probability_only",
                            **summary,
                            "wall_s": round(time.monotonic() - wall_started, 3),
                        },
                    ) from model_exc
                continue
            summary["model_valid_decisions"] += 1
            if hybrid_planner is not None:
                summary["phase_summaries"][active_phase_name]["model_valid_decisions"] += 1
            (recorder.run_dir / f"video-{video}-topic-ai-raw.txt").write_text(
                topic.raw_response[:8000], encoding="utf-8"
            )
            matched = bool(topic.matches and content_mode != "general")
            if matched:
                summary["topic_matches"] += 1
                if hybrid_planner is not None:
                    summary["phase_summaries"][active_phase_name]["topic_exact"] += 1
            entry.update(topic.public_dict())
            entry["matched"] = matched
            safe_for_action = bool(getattr(topic, "safe", entry.get("safe", False)))
            safety_reason = str(getattr(topic, "reason", entry.get("reason", "")))
            safety_topic = str(getattr(topic, "topic", entry.get("topic", "")))
            safety_evidence = list(
                getattr(topic, "evidence", entry.get("evidence", ())) or ()
            )
            draws = {
                "like": round(rng.random(), 6),
                "favorite": round(rng.random(), 6),
                "comment": round(rng.random(), 6),
            }
            entry["random_draws"] = draws
            probabilities, action_routes = routed_action_plan(
                config,
                matched=matched,
                safe=safe_for_action,
                feed_phase=active_phase_name,
            )
            entry["probability_route"] = (
                "search_trusted"
                if summary["search_trust_results"] and active_phase_name == "search"
                else "matched"
                if matched
                else "home_random"
                if content_mode == "hybrid" and active_phase_name == "home"
                else "general"
            )
            entry["probabilities"] = probabilities
            entry["action_routes"] = action_routes
            recorder.emit(
                "topic_ai_decision", video=video,
                valid_video_index=entry["valid_video_index"],
                feed_phase=active_phase_name,
                matched=matched, probability_route=entry["probability_route"],
                probabilities=probabilities, action_routes=action_routes,
                **topic.public_dict(), random_draws=draws,
            )

            entry["visual_safety"] = {
                "allowed": safe_for_action,
                "reason": safety_reason,
                "evidence": safety_evidence,
            }
            if not safe_for_action:
                summary["visual_safety_blocks"] += 1
                recorder.emit(
                    "visual_safety_block",
                    video=video,
                    topic=safety_topic,
                    reason=safety_reason,
                    evidence=safety_evidence,
                )
            if (
                safe_for_action
                and "like" not in disabled_reactions
                and draws["like"] < probabilities["like"]
            ):
                wait_for_resume()
                stage = "like"
                like_frame, like_gate = runner.capture_gate(video, "like")
                if like_gate.allowed and runner.like_verified(video, like_frame):
                    actions.append("like")
                    summary["likes"] += 1
                    if hybrid_planner is not None:
                        summary["phase_summaries"][active_phase_name]["likes"] += 1
                elif any(reason in {"visual_main_feed_check_failed", "search_context_drift"} for reason in like_gate.reasons):
                    raise FeedContextDriftError("Page drifted before like")

            if (
                safe_for_action
                and "favorite" not in disabled_reactions
                and draws["favorite"] < probabilities["favorite"]
            ):
                wait_for_resume()
                stage = "favorite"
                favorite_frame, favorite_gate = runner.capture_gate(video, "favorite")
                if favorite_gate.allowed and runner.favorite_verified(video, favorite_frame):
                    actions.append("favorite")
                    summary["favorites"] += 1
                    if hybrid_planner is not None:
                        summary["phase_summaries"][active_phase_name]["favorites"] += 1
                elif any(reason in {"visual_main_feed_check_failed", "search_context_drift"} for reason in favorite_gate.reasons):
                    raise FeedContextDriftError("Page drifted before favorite")

            if (
                safe_for_action
                and not comment_disabled
                and draws["comment"] < probabilities["comment"]
            ):
                wait_for_resume()
                stage = "comment"
                _, comment_gate = runner.capture_gate(video, "comment-preview")
                if comment_gate.allowed:
                    comment, sent, policy_review = process_current_comment(
                        device,
                        recorder,
                        runner,
                        video=video,
                        send=not preview_only,
                        comment_policy_enabled=bool(
                            config.get("comment_policy_enabled", False)
                        ),
                        comment_policy_prompt=str(
                            config.get("comment_policy_prompt", "")
                        ),
                        content_plan_snapshot=config.get("content_plan_snapshot"),
                        task_seed=int(config.get("seed", 0)),
                        used_candidate_ids=used_comment_candidates,
                    )
                    _record_comment_outcome(
                        summary,
                        actions,
                        entry,
                        comment,
                        sent,
                        policy_review,
                        comment.asset_audit,
                        preview_only=preview_only,
                        recorder=recorder,
                        video=video,
                    )
                    if sent and hybrid_planner is not None:
                        summary["phase_summaries"][active_phase_name]["comments_sent"] += 1
                elif any(reason in {"visual_main_feed_check_failed", "search_context_drift"} for reason in comment_gate.reasons):
                    raise FeedContextDriftError("Page drifted before comment")
            entry["actions"] = actions
            decisions.append(entry)
            consecutive_anomalies = 0
        except (DeviceFatalError, ModelChannelError):
            raise
        except Exception as exc:
            confirmed_comment = getattr(exc, "confirmed_comment_outcome", None)
            if confirmed_comment is not None:
                comment, sent, policy_review = confirmed_comment[:3]
                asset_audit = (
                    confirmed_comment[3]
                    if len(confirmed_comment) > 3
                    else comment.asset_audit
                )
                _record_comment_outcome(
                    summary,
                    actions,
                    entry,
                    comment,
                    sent,
                    policy_review,
                    asset_audit,
                    preview_only=preview_only,
                    recorder=recorder,
                    video=video,
                )
            summary["video_errors"] += 1
            if not isinstance(exc, (ConsecutiveAnomalyLimitError, FeedContextDriftError)):
                consecutive_anomalies += 1
            if isinstance(exc, FeedContextDriftError) and stage != "topic_gate":
                summary["page_drifts"] += 1
            if hybrid_planner is not None:
                summary["phase_summaries"][active_phase_name]["incidents"] += 1
            screenshot_path: str | None = getattr(
                exc, "evidence_screenshot_path", None
            )
            ui_tree_path: str | None = getattr(exc, "evidence_ui_tree_path", None)
            ui_tree_source = ""
            incident_image = None
            try:
                if screenshot_path is None:
                    incident_image = recorder.screenshot(
                        device, f"video-{video}-incident-{stage}"
                    )
                    screenshot_path = str(
                        recorder.run_dir / f"video-{video}-incident-{stage}.png"
                    )
                else:
                    incident_image = device.screenshot(format="pillow").convert("RGB")
            except Exception:
                pass
            if ui_tree_path is None:
                try:
                    ui_tree = device.dump_hierarchy(compressed=True, pretty=False)
                    ui_tree_source = str(ui_tree)
                    ui_path = recorder.run_dir / f"video-{video}-incident-{stage}.xml"
                    ui_path.write_text(ui_tree_source, encoding="utf-8")
                    ui_tree_path = str(ui_path)
                except Exception:
                    pass
            else:
                try:
                    ui_tree_source = Path(ui_tree_path).read_text(encoding="utf-8")
                except Exception:
                    pass

            reaction_circuit_opened_now = bool(
                stage in {"like", "favorite"}
                and _interaction_safety_verification_visible(ui_tree_source)
                and not disabled_reactions
            )
            if reaction_circuit_opened_now:
                disabled_reactions.update({"like", "favorite"})
                summary["reaction_circuit_opened"] = True
                summary["reaction_actions_disabled"] = sorted(disabled_reactions)
                recorder.emit(
                    "reaction_session_circuit_opened",
                    video=video,
                    trigger_action=stage,
                    reason="platform_identity_safety_verification",
                    disabled_actions=sorted(disabled_reactions),
                )

            comment_circuit_opened_now = bool(
                stage == "comment"
                and _interaction_safety_verification_visible(ui_tree_source)
                and not comment_disabled
            )
            if comment_circuit_opened_now:
                comment_disabled = True
                summary["comment_circuit_opened"] = True
                summary["comment_actions_disabled"] = ["comment"]
                recorder.emit(
                    "comment_session_circuit_opened",
                    video=video,
                    trigger_action=stage,
                    reason="platform_identity_safety_verification",
                    disabled_actions=["comment"],
                )

            insufficient_supply = isinstance(exc, InsufficientVideoSupplyError)
            already_on_feed = bool(
                not insufficient_supply
                and incident_image is not None
                and _required_feed_confirmed(runner, incident_image)
            )
            force_recovery = (
                isinstance(exc, FeedContextDriftError) or stage == "topic_gate"
            ) and not insufficient_supply
            recovered = bool(already_on_feed and not force_recovery)
            recovery_action = "already_required_feed" if recovered else "back_close_or_relaunch"
            recovery_event: dict[str, Any] | None = None
            recovery_count = len(getattr(runner, "recovery_events", []))
            if not recovered:
                try:
                    recovered = _recover_required_feed(
                        runner, f"video-{video}-{stage}-error"
                    )
                except Exception:
                    recovered = False
                recovery_events = getattr(runner, "recovery_events", [])
                if recovered and len(recovery_events) > recovery_count:
                    recovery_event = dict(recovery_events[-1])
                    recovery_action = str(recovery_event.get("action", recovery_action))
                    summary["recovery_events"].append(recovery_event)
            if recovered and force_recovery and recovery_event is None:
                recovery_action = "required_feed_revalidated"
            limit_reached = consecutive_anomalies >= anomaly_limit
            outcome = (
                "device_fatal"
                if limit_reached or insufficient_supply
                else "skipped"
                if already_on_feed and not force_recovery
                else "recovered"
                if recovered
                else "device_fatal"
            )
            if outcome == "recovered":
                summary["recovered_videos"] += 1
                summary["successful_recoveries"] += 1
                if hybrid_planner is not None:
                    summary["phase_summaries"][active_phase_name]["recoveries"] += 1
            elif outcome == "skipped":
                summary["skipped_videos"] += 1
            incident = {
                "video_index": video,
                "stage": stage,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "outcome": outcome,
                "recovery_action": recovery_action,
                "screenshot_path": screenshot_path,
                "ui_tree_path": ui_tree_path,
                "context": {
                    "actions": actions,
                    "dwell_s": dwell,
                    "feed_phase": active_phase_name,
                    "phase_target": active_phase_target,
                    "verified_recovery": recovery_event,
                    "reaction_circuit_opened": reaction_circuit_opened_now,
                    "reaction_actions_disabled": sorted(disabled_reactions),
                    "comment_circuit_opened": comment_circuit_opened_now,
                    "comment_actions_disabled": ["comment"] if comment_disabled else [],
                },
            }
            recorder.emit("video_incident", **incident)
            if incident_sink is not None:
                try:
                    incident_sink(incident)
                except Exception as sink_error:
                    recorder.emit(
                        "incident_record_failed",
                        error_type=type(sink_error).__name__,
                        error=str(sink_error),
                    )
            entry.update(
                {
                    "matched": bool(entry.get("matched", False)),
                    "actions": actions,
                    "error": f"{type(exc).__name__}: {exc}",
                    "outcome": outcome,
                }
            )
            decisions.append(entry)
            if not recovered or limit_reached or insufficient_supply:
                raise DeviceFatalError(
                    str(exc)
                    if limit_reached
                    else f"Video {video} failed at {stage} and the required feed could not be recovered"
                ) from exc

    result_path = recorder.run_dir / "topic-session-decisions.json"
    result_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8")
    model_attempts = int(summary["model_attempts"])
    model_valid_decisions = int(summary["model_valid_decisions"])
    model_valid_response_rate = round(
        model_valid_decisions / model_attempts if model_attempts else 1.0, 4
    )
    summary["model_valid_response_rate"] = model_valid_response_rate
    model_required = topic_analysis_required
    if summary["model_errors"] and model_valid_response_rate < 0.95 and model_required:
        code = "model_channel_quality_below_threshold"
        raise ModelChannelError(
            code,
            {
                "status": "failed",
                "failure_reason": {
                    "code": code,
                    "model_valid_response_rate": model_valid_response_rate,
                    "model_error_counts": dict(summary["model_error_counts"]),
                },
                "stopped_by_user": False,
                "task_type": "douyin_topic_session",
                "preview_only": preview_only,
                "content_mode": content_mode,
                "topic_filter_enabled": topic_filter_enabled,
                "seed": int(config["seed"]),
                "round_index": int(config.get("round_index", 1)),
                "action_control": "probability_only",
                **summary,
                "wall_s": round(time.monotonic() - wall_started, 3),
            },
        )
    degraded = bool(
        summary["model_errors"]
        and model_valid_response_rate < 0.95
        and not model_required
    )
    return {
        "status": (
            "stopped"
            if stopped_by_user
            else "degraded"
            if degraded
            else "passed_with_recovery"
            if summary["video_errors"]
            else "passed"
        ),
        "degraded_reason": (
            {
                "code": "model_channel_partial",
                "model_valid_response_rate": model_valid_response_rate,
                "model_error_counts": dict(summary["model_error_counts"]),
            }
            if degraded
            else None
        ),
        "stopped_by_user": stopped_by_user,
        "task_type": "douyin_topic_session",
        "preview_only": preview_only,
        "content_mode": content_mode,
        "topic_filter_enabled": topic_filter_enabled,
        "seed": int(config["seed"]),
        "round_index": int(config.get("round_index", 1)),
        "action_control": "probability_only",
        **summary,
        "wall_s": round(time.monotonic() - wall_started, 3),
    }


def _comment_task_type(send: bool) -> str:
    return "douyin_comment" if send else "douyin_comment_preview"


def _comment_screenshot_evidence(
    recorder: Uia2RunRecorder, video: int, sent: bool
) -> list[dict[str, Any]]:
    if not sent:
        return []
    return [
        {
            "video_index": video,
            "screenshot_path": str(
                recorder.run_dir / f"video-{video}-comment-sent.png"
            ),
        }
    ]


def _record_comment_outcome(
    summary: dict[str, Any],
    actions: list[str],
    entry: dict[str, Any],
    decision: CommentDecision,
    sent: bool,
    policy_review: dict[str, Any] | None,
    asset_audit: dict[str, Any] | None,
    *,
    preview_only: bool,
    recorder: Uia2RunRecorder,
    video: int,
) -> None:
    """Persist a confirmed comment outcome before later UI cleanup can fail."""
    if asset_audit is not None:
        summary.setdefault("comment_asset_reviews", []).append(asset_audit)
        entry["comment_asset_review"] = asset_audit
    if decision.decision == "comment":
        summary["comments_generated"] += 1
        if policy_review is not None:
            summary["comment_policy_reviews"].append(policy_review)
            if policy_review["allowed"]:
                summary["comment_policy_allowed"] += 1
            else:
                summary["comment_policy_blocked"] += 1
        if policy_review is None or policy_review["allowed"]:
            actions.append("comment-preview" if preview_only else "comment")
        else:
            actions.append("comment-constraint-blocked")
        entry["comment_policy_review"] = policy_review
    if sent:
        summary["comments_sent"] += 1
        summary["comment_screenshots"].extend(
            _comment_screenshot_evidence(recorder, video, sent)
        )


def _normalized_visible_text(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _comment_is_visible(comment: str, signals: list[str]) -> bool:
    expected = _normalized_visible_text(comment)
    if not expected:
        return False
    for signal in signals:
        observed = _normalized_visible_text(signal)
        if expected == observed or expected in observed:
            return True
        if len(observed) >= 8 and observed in expected:
            return True
    return False


def _input_comment_text(device, input_box, comment: str, recorder, video: int) -> str:
    try:
        input_box.set_text(comment)
        observed = str(input_box.get_text() or "").strip()
        if observed != comment:
            raise RuntimeError("Comment text readback did not match")
        recorder.emit("comment_input_method", video=video, method="selector_set_text")
        return "selector_set_text"
    except Exception as exc:
        recorder.emit(
            "comment_input_method_fallback",
            video=video,
            method="uiautomator_ime",
            reason=type(exc).__name__,
        )
        device.send_keys(comment, clear=True)
        return "uiautomator_ime"


def send_comment(
    device,
    recorder: Uia2RunRecorder,
    comment: str,
    video: int,
    *,
    expected_size: tuple[int, int] = (PROFILE.width, PROFILE.height),
) -> bool:
    if device(text=comment).exists(timeout=0):
        raise RuntimeError("Exact comment already exists; duplicate send was blocked")
    input_box = None
    input_candidates = (
        ("resource_id", {"resourceId": "com.ss.android.ugc.aweme:id/eyz"}),
        ("edit_text", {"className": "android.widget.EditText"}),
        ("current_placeholder", {"textContains": "分享你此刻的想法"}),
        ("accessibility_hint", {"textContains": "发条评论"}),
        ("legacy_placeholder", {"textContains": "有什么想法"}),
        ("generic_placeholder", {"textContains": "说点什么"}),
        ("legacy_generic", {"textContains": "留下你的精彩评论"}),
    )
    for input_source, selector in input_candidates:
        candidate = device(**selector)
        if candidate.exists(timeout=0):
            input_box = candidate
            recorder.emit(
                "comment_input_found", video=video, source=input_source
            )
            break
    if input_box is None:
        source = device.dump_hierarchy(compressed=True, pretty=False)
        bounds = find_comment_input_bounds(
            source, expected_size[0], expected_size[1]
        )
        if bounds is not None:
            left, top, right, bottom = bounds
            device.click(round((left + right) / 2), round((top + bottom) / 2))
            recorder.emit(
                "comment_input_found",
                video=video,
                source="semantic_bottom_entry",
                bounds=list(bounds),
            )
            time.sleep(0.6)
            candidate = device(className="android.widget.EditText")
            if candidate.exists(timeout=0):
                input_box = candidate
    if input_box is None:
        raise RuntimeError("Comment input box was not found")
    input_box.click()
    time.sleep(0.6)
    _input_comment_text(device, input_box, comment, recorder, video)
    time.sleep(0.5)
    typed = recorder.screenshot(device, f"video-{video}-comment-typed")
    if typed.size != expected_size:
        raise RuntimeError("Unexpected screenshot size after comment input")
    send_button = None
    send_candidates = (
        ("clickable_container", {"resourceId": "com.ss.android.ugc.aweme:id/e27"}),
        ("send_label_resource", {"resourceId": "com.ss.android.ugc.aweme:id/e2+"}),
        ("text", {"text": "发送"}),
        ("accessibility_description", {"description": "发送"}),
    )
    for send_source, selector in send_candidates:
        candidate = device(**selector)
        if candidate.exists(timeout=0):
            send_button = candidate
            recorder.emit("comment_send_button_found", video=video, source=send_source)
            break
    if send_button is None:
        raise RuntimeError("Send button was not found after comment input")
    recorder.emit("comment_send_attempt", video=video, comment=comment)
    send_button.click()
    time.sleep(1.3)
    foreground = foreground_package(device)
    if foreground != DOUYIN_PACKAGE:
        recorder.emit("post_send_dialog", foreground_package=foreground)
        device.press("back")
        time.sleep(0.7)
    after = recorder.screenshot(device, f"video-{video}-comment-sent")
    if after.size != expected_size:
        raise RuntimeError("Unexpected screenshot size after comment send")
    verified = False
    for attempt in range(1, 4):
        source = device.dump_hierarchy(compressed=True, pretty=False)
        verified = _comment_is_visible(comment, _visible_aweme_signals(source))
        recorder.emit(
            "comment_send_verification",
            video=video,
            verified=verified,
            attempt=attempt,
        )
        if verified:
            break
        time.sleep(0.7)
    if not verified:
        raise RuntimeError(
            "Comment send could not be verified; task will not retry automatically"
        )
    return True


def execute_task(
    device,
    task: TaskRecord,
    recorder: Uia2RunRecorder,
    *,
    pause_waiter: PauseWaiter | None = None,
    incident_sink: IncidentSink | None = None,
    stop_checker: StopChecker | None = None,
    task_store=None,
) -> dict[str, Any]:
    recorder.emit(
        "task_start", task_id=task.id, task_type=task.task_type, payload=task.payload
    )
    if task.task_type == "healthcheck":
        result = healthcheck(device, recorder)
    elif task.task_type == "douyin_benchmark":
        runner = Uia2DouyinRunner(
            device,
            recorder,
            PROFILE,
            max_gate_skips=task.payload.get("max_gate_skips", 3),
        )
        result = runner.run([float(value) for value in task.payload["dwell"]])
    elif task.task_type == "douyin_comment_preview":
        result = comment_preview(
            device,
            recorder,
            dwell_seconds=float(task.payload["dwell_seconds"]),
            max_gate_skips=task.payload.get("max_gate_skips", 3),
            send=False,
            comment_policy_enabled=bool(
                task.payload.get("comment_policy_enabled", False)
            ),
            comment_policy_prompt=str(task.payload.get("comment_policy_prompt", "")),
        )
    elif task.task_type == "douyin_comment":
        result = comment_preview(
            device,
            recorder,
            dwell_seconds=float(task.payload["dwell_seconds"]),
            max_gate_skips=task.payload.get("max_gate_skips", 3),
            send=True,
            comment_policy_enabled=bool(
                task.payload.get("comment_policy_enabled", False)
            ),
            comment_policy_prompt=str(task.payload.get("comment_policy_prompt", "")),
        )
    elif task.task_type == "douyin_two_video_demo":
        result = two_video_demo(
            device,
            recorder,
            dwell=[float(value) for value in task.payload["dwell"]],
            max_gate_skips=task.payload.get("max_gate_skips", 3),
        )
    elif task.task_type == "douyin_topic_session":
        result = topic_session(
            device,
            recorder,
            config=task.payload,
            pause_waiter=pause_waiter,
            incident_sink=incident_sink,
            stop_checker=stop_checker,
        )
    elif task.task_type == "douyin_engagement_inspection":
        result = EngagementInspector(
            device,
            recorder,
            store=task_store,
            device_id=getattr(task, "device_id", ""),
            task_id=task.id,
        ).inspect(task.payload)
    else:
        raise ValueError(f"Unsupported task type: {task.task_type}")
    result = {**result, "task_id": task.id}
    recorder.emit("task_complete", **result)
    return result

