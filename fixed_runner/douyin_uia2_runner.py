from __future__ import annotations

import argparse
import importlib.metadata
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uiautomator2 as u2
from PIL import Image

from douyin_fixed_runner import (
    DOUYIN_PACKAGE,
    PROFILE,
    RUNTIME_ROOT,
    DeviceLock,
    FixedDouyinRunner,
    LayoutProfile,
    RunRecorder,
    comment_panel_visible,
    main_feed_visible,
    parse_dwell,
)


BOUNDS_PATTERN = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
RESUMED_ACTIVITY_PATTERN = re.compile(
    r"(?:topResumedActivity|mResumedActivity)=ActivityRecord\{[^\r\n]*?"
    r"\su\d+\s+([A-Za-z0-9_.$]+)/[^\s}]+"
)

BLOCK_MARKERS: dict[str, tuple[str, ...]] = {
    "advertising": (
        "广告",
        "赞助",
        "推广内容",
        "立即咨询",
        "在线咨询",
        "人已咨询",
        "获取报价",
        "预约咨询",
        "打开应用",
        "立即下载",
    ),
    "commerce": (
        "立即购买",
        "去购买",
        "购买商品",
        "商品橱窗",
        "加入购物车",
        "进店",
        "去逛逛",
        "立即抢购",
        "领取优惠券",
        "到手价",
        "种草榜",
        "商品推荐",
        "同款商品",
        "¥",
        "￥",
    ),
    "commercial_content": (
        "厂家",
        "源头工厂",
        "工厂直销",
        "批发",
        "定制",
        "招商",
        "加盟",
        "代理",
        "供应商",
        "采购",
        "获取报价",
        "门窗",
        "装修",
        "房产",
        "楼盘",
        "置业",
        "买房",
        "卖房",
        "二手车",
        "汽车销售",
        "课程咨询",
        "培训报名",
    ),
    "live": (
        "正在直播",
        "直播中",
        "进入直播间",
        "点击进入直播间",
        "直播已结束",
        "直播间观众",
    ),
    "effect": (
        "收藏特效",
        "使用特效",
        "特效详情",
        "使用同款特效",
        "道具详情",
    ),
}


def foreground_package(device) -> str:
    """Prefer Android's resumed activity over uiautomator2's cached value."""
    try:
        response = device.shell("dumpsys activity activities")
        output = getattr(response, "output", response)
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        match = RESUMED_ACTIVITY_PATTERN.search(str(output))
        if match:
            return match.group(1)
    except Exception:
        pass
    return str(device.app_current().get("package", ""))


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reasons: tuple[str, ...]
    matched_signals: tuple[str, ...]


def find_control_bounds(
    xml_source: str,
    keyword: str,
    width: int = 1080,
    height: int = 2400,
    *,
    require_button_label: bool = True,
) -> tuple[int, int, int, int] | None:
    """Return the largest visible clickable Douyin control matching keyword."""
    root = ET.fromstring(xml_source)
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in root.iter("node"):
        if node.get("package") != DOUYIN_PACKAGE:
            continue
        if node.get("visible-to-user", "true") != "true":
            continue
        if node.get("clickable") != "true":
            continue
        description = " ".join(node.get("content-desc", "").split())
        if keyword not in description:
            continue
        if require_button_label and "按钮" not in description:
            continue
        match = BOUNDS_PATTERN.fullmatch(node.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        if left < 0 or top < 0 or right > width or bottom > height:
            continue
        if right <= left or bottom <= top:
            continue
        candidates.append(((right - left) * (bottom - top), (left, top, right, bottom)))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def control_semantic_state(description: str, control: str) -> bool | None:
    """Read reaction state from accessibility text before using image colors."""
    normalized = " ".join(description.split())
    if control == "like":
        if any(marker in normalized for marker in ("未点赞", "未喜欢")):
            return False
        if any(marker in normalized for marker in ("已点赞", "已喜欢")):
            return True
    if control == "favorite":
        if any(marker in normalized for marker in ("未选中", "未收藏")):
            return False
        if any(marker in normalized for marker in ("已选中", "已收藏")):
            return True
    return None


def find_control_description(
    xml_source: str,
    keyword: str,
    bounds: tuple[int, int, int, int],
) -> str:
    root = ET.fromstring(xml_source)
    bounds_text = f"[{bounds[0]},{bounds[1]}][{bounds[2]},{bounds[3]}]"
    for node in root.iter("node"):
        description = " ".join(node.get("content-desc", "").split())
        if (
            node.get("package") == DOUYIN_PACKAGE
            and node.get("bounds") == bounds_text
            and keyword in description
        ):
            return description
    return ""


def color_active_in_bounds(
    image: Image.Image,
    bounds: tuple[int, int, int, int],
    color: str,
    threshold: float,
) -> bool:
    from douyin_fixed_runner import color_ratio

    patch = image.crop(bounds)
    return color_ratio(patch, color) >= threshold


def _visible_aweme_signals(
    xml_source: str, width: int = 1080, height: int = 2400
) -> list[str]:
    root = ET.fromstring(xml_source)
    signals: list[str] = []
    for node in root.iter("node"):
        if node.get("package") != DOUYIN_PACKAGE:
            continue
        if node.get("visible-to-user", "true") != "true":
            continue
        match = BOUNDS_PATTERN.fullmatch(node.get("bounds", ""))
        if match:
            left, top, right, bottom = (int(value) for value in match.groups())
            if right <= 0 or bottom <= 0 or left >= width or top >= height:
                continue
        for attribute in ("text", "content-desc"):
            value = " ".join(node.get(attribute, "").split())
            if value and value not in signals:
                signals.append(value)
    return signals


def _missing_feed_controls(signals: list[str]) -> list[str]:
    required_controls = {
        "video": lambda value: value == "视频" or value.startswith("视频，"),
        "like": lambda value: "喜欢" in value and "按钮" in value,
        "comment": lambda value: "评论" in value and "按钮" in value,
        "favorite": lambda value: "收藏" in value and "按钮" in value,
    }
    return [
        name
        for name, predicate in required_controls.items()
        if not any(predicate(signal) for signal in signals)
    ]


def classify_mutation_gate(
    xml_source: str,
    foreground_package: str,
    width: int = 1080,
    height: int = 2400,
) -> GateDecision:
    reasons: list[str] = []
    matches: list[str] = []
    if foreground_package != DOUYIN_PACKAGE:
        reasons.append(f"unexpected_package:{foreground_package or 'unknown'}")
    try:
        signals = _visible_aweme_signals(xml_source, width, height)
    except ET.ParseError:
        return GateDecision(False, ("invalid_ui_tree",), ())

    joined = "\n".join(signals)
    missing = _missing_feed_controls(signals)
    if missing:
        reasons.append("missing_feed_controls:" + ",".join(missing))

    for category, markers in BLOCK_MARKERS.items():
        category_matches = [marker for marker in markers if marker in joined]
        if category_matches:
            reasons.append(category)
            matches.extend(category_matches)

    return GateDecision(not reasons, tuple(reasons), tuple(dict.fromkeys(matches)))


class Uia2RunRecorder(RunRecorder):
    def screenshot(self, device, name: str) -> Image.Image:
        started = time.monotonic()
        image = device.screenshot(format="pillow").convert("RGB")
        path = self.run_dir / f"{name}.png"
        image.save(path)
        self.emit(
            "screenshot",
            name=name,
            path=str(path),
            size=list(image.size),
            elapsed_s=round(time.monotonic() - started, 3),
        )
        return image


class Uia2DouyinRunner(FixedDouyinRunner):
    def __init__(
        self,
        device,
        recorder: Uia2RunRecorder,
        profile: LayoutProfile,
        max_gate_skips: int,
    ) -> None:
        super().__init__(device, recorder, profile)
        self.max_gate_skips = max_gate_skips
        self.control_bounds: dict[str, tuple[int, int, int, int]] = {}
        self.control_states: dict[str, bool | None] = {}

    def tap(self, point: tuple[float, float], action: str) -> None:
        x, y = self.profile.absolute(point)
        self.recorder.emit("tap", action=action, x=x, y=y)
        self.device.click(x, y)

    def main_feed_confirmed(self, image: Image.Image) -> bool:
        if main_feed_visible(image):
            return True
        try:
            source = self.device.dump_hierarchy(compressed=True, pretty=False)
            signals = _visible_aweme_signals(
                source, self.profile.width, self.profile.height
            )
            return (
                foreground_package(self.device) == DOUYIN_PACKAGE
                and not _missing_feed_controls(signals)
            )
        except Exception:
            return False

    def require_main_feed(self, image: Image.Image, stage: str) -> None:
        if not self.main_feed_confirmed(image):
            raise RuntimeError(f"Main feed precondition failed before {stage}")

    def tap_control(self, control: str, action: str) -> None:
        bounds = self.control_bounds.get(control)
        if bounds is None:
            raise RuntimeError(f"No verified UI bounds for {control}")
        left, top, right, bottom = bounds
        x = round((left + right) / 2)
        y = round((top + bottom) / 2)
        self.recorder.emit(
            "tap", action=action, x=x, y=y, source="verified_ui_tree", bounds=list(bounds)
        )
        self.device.click(x, y)

    def close_comment_panel(self, video: int, screenshot_name: str) -> Image.Image:
        def panel_still_open() -> bool:
            current_source = self.device.dump_hierarchy(
                compressed=True, pretty=False
            )
            if any(
                marker in current_source
                for marker in (
                    "分享你此刻的想法",
                    "有什么想法",
                    "留下你的精彩评论",
                )
            ):
                return True
            return (
                find_control_bounds(
                    current_source,
                    "关闭",
                    self.profile.width,
                    self.profile.height,
                    require_button_label=False,
                )
                is not None
            )

        source = self.device.dump_hierarchy(compressed=True, pretty=False)
        bounds = find_control_bounds(
            source,
            "关闭",
            self.profile.width,
            self.profile.height,
            require_button_label=False,
        )
        if bounds is None:
            raise RuntimeError("Comment panel close button was not found")
        left, top, right, bottom = bounds
        x = round((left + right) / 2)
        y = round((top + bottom) / 2)
        self.recorder.emit(
            "tap",
            action="close_comments",
            x=x,
            y=y,
            source="verified_ui_tree",
            bounds=list(bounds),
        )
        self.device.click(x, y)
        time.sleep(0.7)
        closed = self.recorder.screenshot(self.device, screenshot_name)
        if self.main_feed_confirmed(closed) and not panel_still_open():
            return closed

        # The close button occasionally only dismisses the editor/keyboard.
        # Recover in small verified steps instead of continuing on a stale panel.
        for attempt in range(1, 4):
            self.recorder.emit(
                "comment_close_recovery",
                video=video,
                attempt=attempt,
                action="back",
            )
            self.device.press("back")
            time.sleep(0.7)
            closed = self.recorder.screenshot(
                self.device, f"{screenshot_name}-recovery-{attempt}"
            )
            if self.main_feed_confirmed(closed) and not panel_still_open():
                return closed

            # If back only hid the keyboard, the panel X should still be present.
            retry_source = self.device.dump_hierarchy(compressed=True, pretty=False)
            retry_bounds = find_control_bounds(
                retry_source,
                "关闭",
                self.profile.width,
                self.profile.height,
                require_button_label=False,
            )
            if retry_bounds is not None:
                retry_left, retry_top, retry_right, retry_bottom = retry_bounds
                retry_x = round((retry_left + retry_right) / 2)
                retry_y = round((retry_top + retry_bottom) / 2)
                self.recorder.emit(
                    "comment_close_recovery",
                    video=video,
                    attempt=attempt,
                    action="close_button",
                    x=retry_x,
                    y=retry_y,
                )
                self.device.click(retry_x, retry_y)
                time.sleep(0.7)
                closed = self.recorder.screenshot(
                    self.device, f"{screenshot_name}-retry-close-{attempt}"
                )
                if self.main_feed_confirmed(closed) and not panel_still_open():
                    return closed
        raise RuntimeError("Comment panel did not close after bounded recovery")

    def recover_main_feed(self, reason: str) -> bool:
        """Return to the feed from an accidental landing page, with verification."""
        for attempt in range(1, 3):
            self.recorder.emit(
                "feed_recovery", reason=reason, attempt=attempt, action="back"
            )
            self.device.press("back")
            time.sleep(0.8)
            image = self.recorder.screenshot(
                self.device, f"feed-recovery-{reason}-back-{attempt}"
            )
            if self.main_feed_confirmed(image):
                return True

            source = self.device.dump_hierarchy(compressed=True, pretty=False)
            close_bounds = find_control_bounds(
                source,
                "关闭",
                self.profile.width,
                self.profile.height,
                require_button_label=False,
            )
            if close_bounds is not None:
                left, top, right, bottom = close_bounds
                x = round((left + right) / 2)
                y = round((top + bottom) / 2)
                self.recorder.emit(
                    "feed_recovery",
                    reason=reason,
                    attempt=attempt,
                    action="close_button",
                    x=x,
                    y=y,
                )
                self.device.click(x, y)
                time.sleep(0.8)
                image = self.recorder.screenshot(
                    self.device, f"feed-recovery-{reason}-close-{attempt}"
                )
                if self.main_feed_confirmed(image):
                    return True

        self.recorder.emit("feed_recovery", reason=reason, action="app_start")
        self.device.app_start(DOUYIN_PACKAGE, wait=False, stop=False)
        time.sleep(1.2)
        image = self.recorder.screenshot(
            self.device, f"feed-recovery-{reason}-app-start"
        )
        return self.main_feed_confirmed(image)

    def ensure_app_ready(self) -> None:
        started = time.monotonic()
        launch_needed = foreground_package(self.device) != DOUYIN_PACKAGE
        if launch_needed:
            self.device.app_start(DOUYIN_PACKAGE, wait=False, stop=False)
        deadline = time.monotonic() + 15.0
        close_attempts = 0
        while time.monotonic() < deadline:
            if foreground_package(self.device) != DOUYIN_PACKAGE:
                time.sleep(0.4)
                continue
            image = self.device.screenshot(format="pillow").convert("RGB")
            self.ensure_profile(image)
            try:
                source = self.device.dump_hierarchy(compressed=True, pretty=False)
                close_bounds = find_control_bounds(
                    source,
                    "关闭",
                    self.profile.width,
                    self.profile.height,
                    require_button_label=False,
                )
            except Exception:
                source = ""
                close_bounds = None
            login_overlay = any(
                marker in source
                for marker in ("登录后，体验完整功能", "请输入手机号", "验证并登录")
            )
            if close_bounds is not None and login_overlay and close_attempts < 2:
                left, top, right, bottom = close_bounds
                self.device.click(round((left + right) / 2), round((top + bottom) / 2))
                close_attempts += 1
                time.sleep(0.8)
                continue
            if self.main_feed_confirmed(image):
                # Douyin can show its feed first and animate a login sheet a
                # fraction of a second later. Require a second observation so
                # the delayed overlay is handled before the task begins.
                time.sleep(0.8)
                verify_image = self.device.screenshot(format="pillow").convert("RGB")
                try:
                    verify_source = self.device.dump_hierarchy(
                        compressed=True, pretty=False
                    )
                except Exception:
                    verify_source = ""
                delayed_login_overlay = any(
                    marker in verify_source
                    for marker in ("登录后，体验完整功能", "请输入手机号", "验证并登录")
                )
                if delayed_login_overlay and close_attempts < 2:
                    delayed_close = find_control_bounds(
                        verify_source,
                        "关闭",
                        self.profile.width,
                        self.profile.height,
                        require_button_label=False,
                    )
                    if delayed_close is not None:
                        left, top, right, bottom = delayed_close
                        self.device.click(
                            round((left + right) / 2), round((top + bottom) / 2)
                        )
                        close_attempts += 1
                        time.sleep(0.8)
                        continue
                if self.main_feed_confirmed(verify_image):
                    self.recorder.emit(
                        "app_ready",
                        launch_needed=launch_needed,
                        close_attempts=close_attempts,
                        elapsed_s=round(time.monotonic() - started, 3),
                    )
                    return
            time.sleep(0.5)
        if self.recover_main_feed("app-ready"):
            self.recorder.emit(
                "app_ready",
                launch_needed=launch_needed,
                close_attempts=close_attempts,
                recovered=True,
                elapsed_s=round(time.monotonic() - started, 3),
            )
            return
        raise RuntimeError("Douyin did not reach the main feed")

    def swipe_next(self, from_video: int, to_video: int) -> None:
        before = self.recorder.screenshot(
            self.device, f"video-{from_video}-pre-swipe-state"
        )
        if not self.main_feed_confirmed(before):
            if not self.recover_main_feed(f"before-swipe-{from_video}"):
                raise RuntimeError("Could not recover the main feed before swipe")
        start_x, start_y = self.profile.absolute(self.profile.swipe_start)
        end_x, end_y = self.profile.absolute(self.profile.swipe_end)
        started = time.monotonic()
        self.device.swipe(start_x, start_y, end_x, end_y, duration=0.35)
        time.sleep(0.7)
        self.recorder.emit(
            "swipe",
            from_video=from_video,
            to_video=to_video,
            elapsed_s=round(time.monotonic() - started, 3),
        )

    def capture_gate(self, video: int, action: str) -> tuple[Image.Image, GateDecision]:
        before = self.recorder.screenshot(
            self.device, f"video-{video}-{action}-before"
        )
        self.ensure_profile(before)
        if not self.main_feed_confirmed(before):
            decision = GateDecision(False, ("visual_main_feed_check_failed",), ())
        else:
            started = time.monotonic()
            try:
                source = self.device.dump_hierarchy(compressed=True, pretty=False)
                foreground = foreground_package(self.device)
                decision = classify_mutation_gate(
                    source, foreground, before.width, before.height
                )
                control_name = "comments" if action == "comment-preview" else action
                keyword = {
                    "like": "喜欢",
                    "favorite": "收藏",
                    "comments": "评论",
                }.get(control_name)
                if decision.allowed and keyword is not None:
                    bounds = find_control_bounds(
                        source, keyword, before.width, before.height
                    )
                    if bounds is None:
                        decision = GateDecision(
                            False, (f"control_bounds_missing:{control_name}",), ()
                        )
                    else:
                        self.control_bounds[action] = bounds
                        if control_name in {"like", "favorite"}:
                            description = find_control_description(source, keyword, bounds)
                            self.control_states[action] = control_semantic_state(
                                description, control_name
                            )
                ui_dump_s = round(time.monotonic() - started, 3)
            except Exception as exc:
                decision = GateDecision(
                    False, (f"ui_inspection_failed:{type(exc).__name__}",), ()
                )
                ui_dump_s = round(time.monotonic() - started, 3)
            self.recorder.emit(
                "gate_check",
                video=video,
                action=action,
                allowed=decision.allowed,
                reasons=list(decision.reasons),
                matched_signals=list(decision.matched_signals),
                ui_inspection_s=ui_dump_s,
            )
            return before, decision

        self.recorder.emit(
            "gate_check",
            video=video,
            action=action,
            allowed=False,
            reasons=list(decision.reasons),
            matched_signals=[],
            ui_inspection_s=0.0,
        )
        return before, decision

    def like_verified(self, video: int, before: Image.Image) -> bool:
        bounds = self.control_bounds["like"]
        visual_active = color_active_in_bounds(before, bounds, "red", 0.035)
        semantic_active = self.control_states.get("like")
        before_active = semantic_active if semantic_active is not None else visual_active
        self.recorder.emit(
            "like_state_before",
            video=video,
            active=before_active,
            semantic_active=semantic_active,
            visual_active=visual_active,
        )
        if before_active:
            self.recorder.emit("reaction_skip", video=video, action="like", reason="already_active")
            return False
        self.tap_control("like", "like")
        time.sleep(0.8)
        after = self.recorder.screenshot(self.device, f"video-{video}-like-after")
        after_active = color_active_in_bounds(after, bounds, "red", 0.035)
        self.recorder.emit("like_state_after", video=video, active=after_active)
        if not after_active:
            raise RuntimeError("Like verification failed; stopping")
        return True

    def favorite_verified(self, video: int, before: Image.Image) -> bool:
        bounds = self.control_bounds["favorite"]
        visual_active = color_active_in_bounds(before, bounds, "yellow", 0.025)
        semantic_active = self.control_states.get("favorite")
        before_active = semantic_active if semantic_active is not None else visual_active
        self.recorder.emit(
            "favorite_state_before",
            video=video,
            active=before_active,
            semantic_active=semantic_active,
            visual_active=visual_active,
        )
        if before_active:
            self.recorder.emit(
                "reaction_skip", video=video, action="favorite", reason="already_active"
            )
            return False
        self.tap_control("favorite", "favorite")
        time.sleep(0.8)
        after = self.recorder.screenshot(
            self.device, f"video-{video}-favorite-after"
        )
        after_active = color_active_in_bounds(after, bounds, "yellow", 0.025)
        self.recorder.emit(
            "favorite_state_after", video=video, active=after_active
        )
        if not after_active:
            raise RuntimeError("Favorite verification failed; stopping")
        return True

    def comments_verified(self, video: int, before: Image.Image) -> None:
        self.tap_control("comments", "open_comments")
        time.sleep(1.0)
        opened = self.recorder.screenshot(
            self.device, f"video-{video}-comments-open"
        )
        visible = comment_panel_visible(opened)
        self.recorder.emit("comment_panel_state", video=video, visible=visible)
        if not visible:
            raise RuntimeError("Comment panel verification failed")
        self.close_comment_panel(video, f"video-{video}-comments-closed")

    def run(self, dwell: list[float]) -> dict[str, Any]:
        if len(dwell) != 4:
            raise ValueError("Exactly four dwell values are required")
        wall_started = time.monotonic()
        self.ensure_app_ready()
        time.sleep(0.5)
        initial = self.recorder.screenshot(self.device, "initial")
        self.ensure_profile(initial)
        self.require_main_feed(initial, "initial swipe")

        video = 1
        actual_planned_dwell = 0.0
        blocked_pages = 0
        self.swipe_next(0, video)
        self.watch(video, dwell[0])
        actual_planned_dwell += dwell[0]
        self.swipe_next(video, video + 1)

        action_specs = (
            ("like", dwell[1], self.like_verified),
            ("favorite", dwell[2], self.favorite_verified),
            ("comments", dwell[3], self.comments_verified),
        )
        completed_actions: list[str] = []
        for action_index, (action_name, seconds, action) in enumerate(action_specs):
            for attempt in range(self.max_gate_skips + 1):
                video += 1
                self.watch(video, seconds)
                actual_planned_dwell += seconds
                before, decision = self.capture_gate(video, action_name)
                if decision.allowed:
                    changed = action(video, before)
                    if changed is not False:
                        completed_actions.append(action_name)
                    break
                blocked_pages += 1
                self.recorder.emit(
                    "gate_skip",
                    video=video,
                    action=action_name,
                    attempt=attempt + 1,
                    reasons=list(decision.reasons),
                )
                if attempt >= self.max_gate_skips:
                    raise RuntimeError(
                        f"No eligible page for {action_name} after "
                        f"{self.max_gate_skips + 1} candidates"
                    )
                self.swipe_next(video, video + 1)
            if action_index < len(action_specs) - 1:
                self.swipe_next(video, video + 1)

        wall_s = round(time.monotonic() - wall_started, 3)
        planned_s = round(actual_planned_dwell, 3)
        summary = {
            "status": "passed",
            "engine": "uiautomator2",
            "engine_version": importlib.metadata.version("uiautomator2"),
            "base_dwell_s": round(sum(dwell), 3),
            "planned_dwell_s": planned_s,
            "wall_s": wall_s,
            "control_and_verification_overhead_s": round(wall_s - planned_s, 3),
            "blocked_pages": blocked_pages,
            "completed_actions": completed_actions,
            "videos_seen": video,
        }
        self.recorder.emit("run_complete", **summary)
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="uiautomator2 Douyin internal-test benchmark"
    )
    parser.add_argument("--device-id", default="P7HUDEKF4XVODY4D")
    parser.add_argument("--dwell", type=parse_dwell, default=parse_dwell("4,5,4,5"))
    parser.add_argument("--max-gate-skips", type=int, default=3)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent / "artifacts-uia2",
    )
    args = parser.parse_args()
    if args.max_gate_skips < 0:
        parser.error("--max-gate-skips must be non-negative")

    recorder = Uia2RunRecorder(args.output_root, args.device_id)
    try:
        with DeviceLock(RUNTIME_ROOT, args.device_id):
            recorder.emit(
                "run_start",
                device_id=args.device_id,
                dwell=args.dwell,
                engine="uiautomator2",
            )
            device = u2.connect(args.device_id)
            Uia2DouyinRunner(
                device, recorder, PROFILE, args.max_gate_skips
            ).run(args.dwell)
        return 0
    except Exception as exc:
        recorder.emit("run_failed", error_type=type(exc).__name__, error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
