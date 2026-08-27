from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Callable

from comment_ai import analyze_topic, generate_comment
from douyin_fixed_runner import DOUYIN_PACKAGE, PROFILE
from douyin_uia2_runner import (
    Uia2DouyinRunner,
    Uia2RunRecorder,
    _visible_aweme_signals,
    classify_mutation_gate,
    foreground_package,
)
from task_store import TaskRecord

PauseWaiter = Callable[[], None]
IncidentSink = Callable[[dict[str, Any]], None]
StopChecker = Callable[[], bool]


class DeviceFatalError(RuntimeError):
    """A task cannot safely continue because the device/feed was not recovered."""


class ConsecutiveAnomalyLimitError(RuntimeError):
    """Too many abnormal pages occurred without a normal video between them."""


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

    decision, sent = process_current_comment(
        device, recorder, runner, video=video, send=send
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
        "comment_screenshots": _comment_screenshot_evidence(recorder, video, sent),
    }


def process_current_comment(
    device,
    recorder: Uia2RunRecorder,
    runner: Uia2DouyinRunner,
    *,
    video: int,
    send: bool,
):
    runner.tap_control("comment-preview", "open_comments_for_ai_preview")
    time.sleep(1.0)
    panel = recorder.screenshot(device, f"video-{video}-comment-ai-input")
    from douyin_fixed_runner import comment_panel_visible

    if not comment_panel_visible(panel):
        raise RuntimeError("Comment panel did not open for AI preview")
    try:
        decision = generate_comment(
            recorder.run_dir / f"video-{video}-comment-ai-input.png"
        )
        raw_path = recorder.run_dir / "comment-ai-raw.txt"
        raw_path.write_text(decision.raw_response[:8000], encoding="utf-8")
        decision_path = recorder.run_dir / "comment-decision.json"
        decision_path.write_text(
            json.dumps(decision.public_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        recorder.emit("comment_ai_decision", **decision.public_dict())
        sent = False
        if send and decision.decision == "comment":
            sent = send_comment(
                device,
                recorder,
                decision.comment,
                video,
                expected_size=(runner.profile.width, runner.profile.height),
            )
    finally:
        runner.close_comment_panel(video, f"video-{video}-comment-ai-closed")
    return decision, sent


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
    decision, sent = process_current_comment(
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
    runner.ensure_app_ready()
    if content_mode == "search":
        runner.enter_topic_search(str(config.get("search_query", "")).strip())
    initial = recorder.screenshot(device, "topic-session-initial")
    runner.ensure_profile(initial)
    runner.require_main_feed(initial, "topic session")
    summary: dict[str, Any] = {
        "videos_seen": 0,
        "topic_matches": 0,
        "likes": 0,
        "favorites": 0,
        "comments_generated": 0,
        "comments_sent": 0,
        "comment_screenshots": [],
        "blocked_pages": 0,
        "visual_safety_blocks": 0,
        "video_errors": 0,
        "recovered_videos": 0,
        "skipped_videos": 0,
        "content_mode": content_mode,
    }
    decisions: list[dict[str, Any]] = []
    preview_only = bool(config["preview_only"])
    topic_filter_enabled = content_mode != "general"
    anomaly_limit = max(1, min(50, int(config.get("max_gate_skips", 3))))
    consecutive_anomalies = 0
    wait_for_resume = pause_waiter or (lambda: None)
    should_stop = stop_checker or (lambda: False)
    stopped_by_user = False

    for video in range(1, int(config["video_count"]) + 1):
        if should_stop():
            stopped_by_user = True
            recorder.emit(
                "task_stop_checkpoint",
                next_video=video,
                videos_seen=summary["videos_seen"],
            )
            break
        dwell = round(rng.uniform(float(config["dwell_min"]), float(config["dwell_max"])), 2)
        entry: dict[str, Any] = {"video": video, "dwell_s": dwell}
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
            summary["videos_seen"] += 1
            if not gate.allowed:
                summary["blocked_pages"] += 1
                consecutive_anomalies += 1
                entry.update({"matched": False, "reason": ",".join(gate.reasons), "actions": []})
                decisions.append(entry)
                if consecutive_anomalies >= anomaly_limit:
                    raise ConsecutiveAnomalyLimitError(
                        f"连续异常页面达到 {anomaly_limit} 条，当前任务已停止"
                    )
                continue

            frame_path = recorder.run_dir / f"video-{video}-topic-analysis-before.png"
            target_topic = (
                str(config["topic_prompt"])
                if topic_filter_enabled
                else "不限具体主题；只要是普通、清晰、非敏感且适合互动的内容即可"
            )
            stage = "topic_model"
            topic = analyze_topic(frame_path, target_topic)
            (recorder.run_dir / f"video-{video}-topic-ai-raw.txt").write_text(
                topic.raw_response[:8000], encoding="utf-8"
            )
            matched = bool(topic.matches and content_mode != "general")
            if matched:
                summary["topic_matches"] += 1
            entry.update(topic.public_dict())
            entry["matched"] = matched
            draws = {
                "like": round(rng.random(), 6),
                "favorite": round(rng.random(), 6),
                "comment": round(rng.random(), 6),
            }
            entry["random_draws"] = draws
            probabilities = routed_action_probabilities(config, matched)
            entry["probability_route"] = "matched" if matched else "general"
            entry["probabilities"] = probabilities
            recorder.emit(
                "topic_ai_decision", video=video,
                matched=matched, probability_route=entry["probability_route"],
                probabilities=probabilities, **topic.public_dict(), random_draws=draws,
            )

            safe_for_action = bool(getattr(topic, "safe", entry.get("safe", False)))
            safety_reason = str(getattr(topic, "reason", entry.get("reason", "")))
            safety_topic = str(getattr(topic, "topic", entry.get("topic", "")))
            safety_evidence = list(
                getattr(topic, "evidence", entry.get("evidence", ())) or ()
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
                and draws["like"] < probabilities["like"]
            ):
                wait_for_resume()
                stage = "like"
                like_frame, like_gate = runner.capture_gate(video, "like")
                if like_gate.allowed and runner.like_verified(video, like_frame):
                    actions.append("like")
                    summary["likes"] += 1

            if (
                safe_for_action
                and draws["favorite"] < probabilities["favorite"]
            ):
                wait_for_resume()
                stage = "favorite"
                favorite_frame, favorite_gate = runner.capture_gate(video, "favorite")
                if favorite_gate.allowed and runner.favorite_verified(video, favorite_frame):
                    actions.append("favorite")
                    summary["favorites"] += 1

            if (
                safe_for_action
                and draws["comment"] < probabilities["comment"]
            ):
                wait_for_resume()
                stage = "comment"
                _, comment_gate = runner.capture_gate(video, "comment-preview")
                if comment_gate.allowed:
                    comment, sent = process_current_comment(
                        device, recorder, runner, video=video, send=not preview_only
                    )
                    if comment.decision == "comment":
                        actions.append("comment-preview" if preview_only else "comment")
                        summary["comments_generated"] += 1
                    if sent:
                        summary["comments_sent"] += 1
                        summary["comment_screenshots"].extend(
                            _comment_screenshot_evidence(recorder, video, sent)
                        )
            entry["actions"] = actions
            decisions.append(entry)
            consecutive_anomalies = 0
        except Exception as exc:
            summary["video_errors"] += 1
            if not isinstance(exc, ConsecutiveAnomalyLimitError):
                consecutive_anomalies += 1
            screenshot_path: str | None = None
            ui_tree_path: str | None = None
            incident_image = None
            try:
                incident_image = recorder.screenshot(
                    device, f"video-{video}-incident-{stage}"
                )
                screenshot_path = str(
                    recorder.run_dir / f"video-{video}-incident-{stage}.png"
                )
            except Exception:
                pass
            try:
                ui_tree = device.dump_hierarchy(compressed=True, pretty=False)
                ui_path = recorder.run_dir / f"video-{video}-incident-{stage}.xml"
                ui_path.write_text(str(ui_tree), encoding="utf-8")
                ui_tree_path = str(ui_path)
            except Exception:
                pass

            already_on_feed = bool(
                incident_image is not None and runner.main_feed_confirmed(incident_image)
            )
            recovered = already_on_feed
            recovery_action = "already_main_feed" if already_on_feed else "back_close_or_relaunch"
            if not recovered:
                try:
                    recovered = runner.recover_main_feed(f"video-{video}-{stage}-error")
                except Exception:
                    recovered = False
            limit_reached = consecutive_anomalies >= anomaly_limit
            outcome = (
                "device_fatal"
                if limit_reached
                else "skipped"
                if already_on_feed
                else "recovered"
                if recovered
                else "device_fatal"
            )
            if outcome == "recovered":
                summary["recovered_videos"] += 1
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
                "context": {"actions": actions, "dwell_s": dwell},
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
                    "matched": False,
                    "actions": actions,
                    "error": f"{type(exc).__name__}: {exc}",
                    "outcome": outcome,
                }
            )
            decisions.append(entry)
            if not recovered or limit_reached:
                raise DeviceFatalError(
                    str(exc)
                    if limit_reached
                    else f"Video {video} failed at {stage} and the main feed could not be recovered"
                ) from exc

    result_path = recorder.run_dir / "topic-session-decisions.json"
    result_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8")
    if summary["videos_seen"] and summary["blocked_pages"] == summary["videos_seen"]:
        raise RuntimeError(
            "All sampled pages were blocked; check whether the phone is locked or the feed is unavailable"
        )
    return {
        "status": (
            "stopped"
            if stopped_by_user
            else "passed_with_recovery"
            if summary["video_errors"]
            else "passed"
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
        raise RuntimeError("Comment input box was not found")
    input_box.click()
    time.sleep(0.6)
    device.send_keys(comment, clear=True)
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
        )
    elif task.task_type == "douyin_comment":
        result = comment_preview(
            device,
            recorder,
            dwell_seconds=float(task.payload["dwell_seconds"]),
            max_gate_skips=task.payload.get("max_gate_skips", 3),
            send=True,
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
    else:
        raise ValueError(f"Unsupported task type: {task.task_type}")
    result = {**result, "task_id": task.id}
    recorder.emit("task_complete", **result)
    return result

