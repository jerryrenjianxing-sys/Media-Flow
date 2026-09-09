from __future__ import annotations

import argparse
import importlib.metadata
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import uiautomator2 as u2
from PIL import Image
from control_vision import VisionCandidateLocator, observe_navigation, verify_navigation_observation, visual_navigation_enabled
from virtual_device_qualification import require_visual_navigation_device

from device_profiles import DeviceProfile, get_device_profile
from recovery_rules import (
    find_action_bounds,
    match_verified_recovery_rule,
)

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


# uiautomator2 defaults JSON-RPC calls to 300 seconds. A stalled hierarchy
# read can therefore strand one device worker for several minutes before the
# runner gets a chance to recover. Keep each transport attempt short; the
# library's own bounded retries still absorb ordinary transient delays.
UIA2_HTTP_TIMEOUT_SECONDS = 20.0
u2.HTTP_TIMEOUT = UIA2_HTTP_TIMEOUT_SECONDS

# A search video can sit five Android-back layers above the home feed:
# immersive video -> video results -> general results -> focused search page ->
# unfocused search page -> home.  Keep one extra bounded step for minor overlays.
FEED_RECOVERY_BACK_ATTEMPTS = 6


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

COMMENT_PANEL_MARKERS = (
    "分享你此刻的想法",
    "有什么想法",
    "留下你的精彩评论",
    "爱评论的人",
    "期待你的评论",
    "暂无评论",
)

COMMENT_INPUT_MARKERS = (
    "评论",
    "想法",
    "说点",
    "分享",
    "运气不会差",
    "友善",
)

SHARE_SHEET_PRIMARY_MARKERS = (
    "转发到日常",
    "不感兴趣",
)

SHARE_SHEET_SECONDARY_MARKERS = (
    "倍速",
    "清屏播放",
    "添加至稍后再看",
    "合拍",
)


def share_sheet_visible(xml_source: str) -> bool:
    """Match Douyin's long-press/share sheet using stable, distinctive labels."""
    return all(marker in xml_source for marker in SHARE_SHEET_PRIMARY_MARKERS) and any(
        marker in xml_source for marker in SHARE_SHEET_SECONDARY_MARKERS
    )


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


def minor_mode_overlay_visible(xml_source: str) -> bool:
    """Recognize only the known youth-protection modal, not incidental video text."""
    rule = match_verified_recovery_rule(xml_source)
    return bool(rule and rule.rule_id == "douyin-minor-mode-overlay")


def comment_panel_source_visible(xml_source: str) -> bool:
    return any(marker in xml_source for marker in COMMENT_PANEL_MARKERS)


def find_bottom_navigation_bounds(
    xml_source: str,
    label: str,
    width: int = 1080,
    height: int = 2400,
) -> tuple[int, int, int, int] | None:
    """Find a visible clickable bottom-tab whose own or child label matches."""
    root = ET.fromstring(xml_source)
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in root.iter("node"):
        if node.get("package") != DOUYIN_PACKAGE:
            continue
        if node.get("visible-to-user", "true") != "true":
            continue
        if node.get("clickable") != "true":
            continue
        labels = []
        for descendant in node.iter("node"):
            labels.extend(
                " ".join(descendant.get(attribute, "").split())
                for attribute in ("text", "content-desc")
            )
        if not any(label in value for value in labels):
            continue
        match = BOUNDS_PATTERN.fullmatch(node.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        if (
            left < 0
            or top < int(height * 0.72)
            or right > width
            or bottom > height
            or right <= left
            or bottom <= top
            or right - left < max(72, int(width * 0.08))
            or bottom - top < max(32, int(height * 0.02))
        ):
            continue
        bounds = (left, top, right, bottom)
        candidates.append(((right - left) * (bottom - top), bounds))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def find_editable_bounds(
    xml_source: str, width: int = 1080, height: int = 2400
) -> tuple[int, int, int, int] | None:
    root = ET.fromstring(xml_source)
    for node in root.iter("node"):
        if node.get("package") != DOUYIN_PACKAGE:
            continue
        if node.get("visible-to-user", "true") != "true":
            continue
        if node.get("class") != "android.widget.EditText":
            continue
        match = BOUNDS_PATTERN.fullmatch(node.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        if 0 <= left < right <= width and 0 <= top < bottom <= height:
            return left, top, right, bottom
    return None


def find_comment_input_bounds(
    xml_source: str, width: int = 1080, height: int = 2400
) -> tuple[int, int, int, int] | None:
    """Find a bottom comment entry by semantics, without device coordinates."""
    root = ET.fromstring(xml_source)
    parents = {
        child: parent for parent in root.iter("node") for child in list(parent)
    }
    candidates: list[tuple[int, int, int, tuple[int, int, int, int]]] = []
    for node in root.iter("node"):
        if node.get("package") != DOUYIN_PACKAGE:
            continue
        if node.get("visible-to-user", "true") != "true":
            continue
        label = " ".join(
            " ".join(node.get(attribute, "").split())
            for attribute in ("text", "content-desc", "hint")
        )
        is_editor = node.get("class") == "android.widget.EditText"
        semantic_hits = sum(marker in label for marker in COMMENT_INPUT_MARKERS)
        if not is_editor and not semantic_hits:
            continue
        target = node
        promoted = False
        if not is_editor and node.get("clickable") != "true":
            target = parents.get(node)
            while target is not None and (
                target.get("package") != DOUYIN_PACKAGE
                or target.get("visible-to-user", "true") != "true"
                or target.get("clickable") != "true"
            ):
                target = parents.get(target)
            if target is None:
                continue
            promoted = True
        match = BOUNDS_PATTERN.fullmatch(target.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        if not (
            0 <= left < right <= width
            and int(height * 0.55) <= top < bottom <= height
            and right - left >= int(width * 0.20)
            and bottom - top <= int(height * 0.14)
        ):
            continue
        priority = 0 if is_editor else 2 if promoted else 1
        candidates.append(
            (priority, -semantic_hits, -(right - left), (left, top, right, bottom))
        )
    return min(candidates, default=(0, 0, 0, None))[3]


def comment_input_activation_source_confirmed(
    xml_source: str, width: int = 1080, height: int = 2400
) -> bool:
    """Confirm that a comment prompt click opened a real editing state."""
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return False
    if find_editable_bounds(xml_source, width, height) is not None:
        return True
    for node in root.iter("node"):
        if (
            node.get("package") != DOUYIN_PACKAGE
            or node.get("visible-to-user", "true") != "true"
            or node.get("focused") != "true"
        ):
            continue
        match = BOUNDS_PATTERN.fullmatch(node.get("bounds", ""))
        if match and int(match.group(2)) >= int(height * 0.55):
            return True
    keyboard_packages = (
        "com.baidu.input",
        "com.sohu.inputmethod",
        "com.google.android.inputmethod",
        "com.iflytek.inputmethod",
        "com.android.inputmethod",
    )
    return any(package in xml_source for package in keyboard_packages)


def find_search_result_bounds(
    xml_source: str, width: int = 1080, height: int = 2400
) -> tuple[int, int, int, int] | None:
    """Select the first semantic video/work result, never a blind screen point."""
    root = ET.fromstring(xml_source)
    candidates: list[tuple[int, int, tuple[int, int, int, int]]] = []
    for node in root.iter("node"):
        if node.get("package") != DOUYIN_PACKAGE:
            continue
        if node.get("visible-to-user", "true") != "true" or node.get("clickable") != "true":
            continue
        descendants = list(node.iter("node"))
        labels = " ".join(
            " ".join(descendant.get(attribute, "").split())
            for descendant in descendants
            for attribute in ("text", "content-desc")
        )
        has_caption = any(
            descendant.get("resource-id", "").endswith("/desc")
            and bool(descendant.get("text", "").strip())
            for descendant in descendants
        )
        has_video_cover = any(
            descendant.get("resource-id", "").endswith("/cover")
            for descendant in descendants
        )
        has_duration = any(
            re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", descendant.get("text", "").strip())
            for descendant in descendants
        )
        if (
            not has_caption
            and not any(marker in labels for marker in ("视频", "作品"))
            and not (has_video_cover and has_duration)
        ):
            continue
        match = BOUNDS_PATTERN.fullmatch(node.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        area = (right - left) * (bottom - top)
        minimum_top = int(
            height * (0.12 if has_caption or (has_video_cover and has_duration) else 0.18)
        )
        if (
            0 <= left < right <= width
            and minimum_top <= top < bottom <= int(height * 0.94)
            and area >= int(width * height * 0.02)
            and area <= int(width * height * 0.65)
        ):
            bounds = (left, top, right, bottom)
            clickable_children: list[
                tuple[int, int, tuple[int, int, int, int]]
            ] = []
            for descendant in descendants[1:]:
                if descendant.get("clickable") != "true":
                    continue
                child_match = BOUNDS_PATTERN.fullmatch(
                    descendant.get("bounds", "")
                )
                if not child_match:
                    continue
                child = tuple(int(value) for value in child_match.groups())
                child_left, child_top, child_right, child_bottom = child
                child_area = (child_right - child_left) * (child_bottom - child_top)
                if (
                    left <= child_left < child_right <= right
                    and top <= child_top < child_bottom <= bottom
                    and int(width * height * 0.02)
                    <= child_area
                    <= int(width * height * 0.40)
                ):
                    clickable_children.append((child_top, child_left, child))
            if clickable_children:
                candidates.append(
                    min(clickable_children, key=lambda item: (item[0], item[1]))
                )
            else:
                candidates.append((top, left, bounds))
    return min(candidates, default=(0, 0, None), key=lambda item: (item[0], item[1]))[2]


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
        "video": lambda value: (
            value == "视频"
            or value.startswith("视频，")
            or value in {"暂停视频，按钮", "播放视频，按钮"}
        ),
        "like": lambda value: "喜欢" in value and "按钮" in value,
        "comment": lambda value: "评论" in value and "按钮" in value,
        "favorite": lambda value: "收藏" in value and "按钮" in value,
    }
    return [
        name
        for name, predicate in required_controls.items()
        if not any(predicate(signal) for signal in signals)
    ]


def _home_image_note_source_visible(
    xml_source: str,
    signals: list[str] | None = None,
    width: int = 1080,
    height: int = 2400,
) -> bool:
    """Recognize a browsable home image-note without granting video actions."""
    current_signals = signals or _visible_aweme_signals(xml_source, width, height)
    if find_bottom_navigation_bounds(xml_source, "首页", width, height) is None:
        return False
    has_image_note = any(signal == "图文" for signal in current_signals)
    has_image_page = any(
        re.match(r"^图片\s*\d+", signal) for signal in current_signals
    )
    reaction_controls = {
        "like": any("喜欢" in signal and "按钮" in signal for signal in current_signals),
        "comment": any("评论" in signal and "按钮" in signal for signal in current_signals),
        "favorite": any("收藏" in signal and "按钮" in signal for signal in current_signals),
    }
    return bool(has_image_note and has_image_page and all(reaction_controls.values()))


def _profile_page_source_visible(signals: list[str]) -> bool:
    """Recognize a user profile from a combination of profile-only labels."""
    joined = "\n".join(signals)
    strong_markers = (
        "编辑主页",
        "添加朋友",
        "我的订单",
        "我的客服",
        "全部功能",
        "获赞",
        "互关",
        "粉丝",
    )
    tab_markers = ("作品", "日常", "商家", "收藏", "喜欢")
    return (
        sum(marker in joined for marker in strong_markers) >= 2
        and sum(marker in joined for marker in tab_markers) >= 2
    )


def main_feed_shell_source_confirmed(
    xml_source: str, width: int = 1080, height: int = 2400
) -> bool:
    """Confirm the visible home feed from UI semantics without using pixels."""
    if minor_mode_overlay_visible(xml_source) or any(
        marker in xml_source
        for marker in (
            "登录后，体验完整功能",
            "请输入手机号",
            "验证并登录",
            "发送消息",
            "快捷回复",
            "一键留资",
            "会话可能会被记录",
        )
    ):
        return False
    if comment_panel_source_visible(xml_source):
        return False
    root = ET.fromstring(xml_source)
    for node in root.iter("node"):
        label = " ".join(
            value.strip()
            for value in (node.get("text", ""), node.get("content-desc", ""))
            if value.strip()
        )
        match = BOUNDS_PATTERN.fullmatch(node.get("bounds", ""))
        if label == "消息" and match and int(match.group(2)) < int(height * 0.2):
            return False
    signals = _visible_aweme_signals(xml_source, width, height)
    if _profile_page_source_visible(signals):
        return False
    if _search_results_grid_visible(xml_source, width, height):
        return False
    if _search_session_shell_visible(xml_source):
        return False
    home_bounds = find_bottom_navigation_bounds(xml_source, "首页", width, height)
    if home_bounds is None:
        return False
    # Home image notes are a valid browsing shell but never a video mutation
    # target.  Sparse discovery/profile shells remain rejected above.
    return _home_image_note_source_visible(
        xml_source, signals, width, height
    ) or not _missing_feed_controls(signals)


def _search_results_grid_visible(
    xml_source: str, width: int = 1080, height: int = 2400
) -> bool:
    """Recognize the search-results shell without mistaking it for a video feed."""
    try:
        signals = _visible_aweme_signals(xml_source, width, height)
    except ET.ParseError:
        return False
    search_tabs = {"综合", "视频", "用户", "图文", "商品", "直播", "音乐"}
    exact_tabs = {signal for signal in signals if signal in search_tabs}
    # The home recommendation header itself currently exposes the exact
    # labels “视频 / 图文 / 直播”.  Three tabs are therefore not enough to
    # prove a search-results grid; require the wider results taxonomy.
    return len(exact_tabs) >= 4


def _search_session_shell_visible(xml_source: str) -> bool:
    """Require the stable semantic shell shared by immersive search videos."""
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return False
    visible_resource_ids = {
        node.get("resource-id", "")
        for node in root.iter("node")
        if node.get("package") == DOUYIN_PACKAGE
        and node.get("visible-to-user", "true") == "true"
    }
    return any(
        resource_id.endswith("/back_btn") for resource_id in visible_resource_ids
    ) and any(
        resource_id.endswith("/et_search_kw") for resource_id in visible_resource_ids
    )


def _search_continuation_marker_visible(
    xml_source: str, width: int = 1080, height: int = 2400
) -> bool:
    """Recognize later search videos after Douyin collapses the top search bar."""
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return False
    if find_bottom_navigation_bounds(xml_source, "首页", width, height) is not None:
        return False
    for node in root.iter("node"):
        if (
            node.get("package") != DOUYIN_PACKAGE
            or node.get("visible-to-user", "true") != "true"
        ):
            continue
        label = " ".join(
            value.strip()
            for value in (node.get("text", ""), node.get("content-desc", ""))
            if value.strip()
        )
        if not (label == "相关搜索" or label.startswith("相关搜索 ")):
            continue
        match = BOUNDS_PATTERN.fullmatch(node.get("bounds", ""))
        if match and int(match.group(2)) >= int(height * 0.65):
            return True
    return False


def _search_context_evidence_visible(
    xml_source: str, width: int = 1080, height: int = 2400
) -> bool:
    return _search_session_shell_visible(
        xml_source
    ) or _search_continuation_marker_visible(xml_source, width, height)


def classify_douyin_page_source(
    xml_source: str,
    foreground: str,
    width: int = 1080,
    height: int = 2400,
) -> str:
    """Classify navigation state before any browse or mutation action."""
    if foreground != DOUYIN_PACKAGE:
        return "external"
    try:
        signals = _visible_aweme_signals(xml_source, width, height)
    except ET.ParseError:
        return "unknown"
    if comment_panel_source_visible(xml_source):
        return "comment_panel"
    if _profile_page_source_visible(signals):
        return "profile"
    if minor_mode_overlay_visible(xml_source):
        return "known_skip"
    joined = "\n".join(signals)
    if any(any(marker in joined for marker in markers) for markers in BLOCK_MARKERS.values()):
        return "known_skip"
    if _home_image_note_source_visible(xml_source, signals, width, height):
        return "home_image_note"
    missing = _missing_feed_controls(signals)
    if _search_context_evidence_visible(xml_source, width, height) and not missing:
        return "search_feed"
    if main_feed_shell_source_confirmed(xml_source, width, height):
        return "home_feed"
    return "unknown"


def classify_mutation_gate(
    xml_source: str,
    foreground_package: str,
    width: int = 1080,
    height: int = 2400,
    *,
    require_feed_controls: bool = True,
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
    if _home_image_note_source_visible(xml_source, signals, width, height):
        reasons.append("non_video_feed_item")
    missing = _missing_feed_controls(signals)
    if require_feed_controls and missing:
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
        device_id: str | None = None,
    ) -> None:
        actual_profile = profile
        try:
            width, height = device.window_size()
            if int(width) > 0 and int(height) > 0:
                actual_profile = replace(profile, width=int(width), height=int(height))
        except Exception:
            pass
        super().__init__(device, recorder, actual_profile)
        self.max_gate_skips = max_gate_skips
        serial = device_id or str(getattr(device, "serial", ""))
        self.device_id = serial
        self.visual_navigation_enabled = visual_navigation_enabled()
        self.vision_locator = VisionCandidateLocator()
        self._visual_sequence = 0
        self.device_profile: DeviceProfile | None = get_device_profile(serial)
        # window_size can report the preceding landscape orientation while the
        # first captured frame has already returned to the verified portrait.
        # Only an exact transpose of a verified layout may change this baseline;
        # ensure_profile still rejects an actually landscape screenshot.
        saved = self.device_profile
        if (saved is not None and saved.verified and saved.width and saved.height
                and saved.width < saved.height
                and (self.profile.width, self.profile.height) == (saved.height, saved.width)):
            self.profile = replace(self.profile, width=saved.width, height=saved.height)
        self.allow_search_feed = False
        self.search_query = ""
        self.feed_phase = "home"
        self._search_visual_fallback_active = False
        self.control_bounds: dict[str, tuple[int, int, int, int]] = {}
        self.control_states: dict[str, bool | None] = {}
        self.recovery_events: list[dict[str, Any]] = []
        self._pending_overlay_recovery: dict[str, Any] | None = None

    def _visual_observation(self, target: str, pages: set[str], *, click: bool = False):
        """Optional read-only fallback. Mutation gates never consult this result."""
        if not self.visual_navigation_enabled:
            return None
        if (self.profile.width, self.profile.height) != (900, 1600) or not DeviceLock.owns(self.device_id):
            raise RuntimeError("navigation_lock_required")
        require_visual_navigation_device(self.device_id)
        for attempt in range(2):
            if foreground_package(self.device) != DOUYIN_PACKAGE:
                raise RuntimeError("foreground_package_changed")
            self._visual_sequence += 1
            stem = f"navigation-{target}-{self._visual_sequence}"
            source = self.device.dump_hierarchy(compressed=True, pretty=False)
            if any(marker in source for marker in ("请输入手机号", "验证并登录", "身份安全验证", "发送消息", "快捷回复")):
                raise RuntimeError("visual_navigation_unsafe_page")
            known_page = classify_douyin_page_source(source, DOUYIN_PACKAGE, self.profile.width, self.profile.height)
            if known_page in {"profile", "comment_panel", "known_skip", "external"}:
                raise RuntimeError("visual_navigation_unsafe_page")
            self.recorder.screenshot(self.device, stem)
            (self.recorder.run_dir / f"{stem}.xml").write_text(source, encoding="utf-8")
            path = self.recorder.run_dir / f"{stem}.png"
            observation = observe_navigation(path, target=target, expected_pages=pages, fixed_bounds=None, locator=self.vision_locator)
            current = self.recorder.screenshot(self.device, f"{stem}-before-action")
            try:
                bounds = verify_navigation_observation(observation, current, package=foreground_package(self.device),
                                                       expected_package=DOUYIN_PACKAGE, lock_owned=DeviceLock.owns(self.device_id))
            except RuntimeError as exc:
                if str(exc) == "visual_candidate_stale" and attempt == 0:
                    continue
                raise
            self.recorder.emit("page_observation", phase="action_verification" if click else "visual_recognition",
                               target=target, bounds=list(bounds), screenshot_id=observation.screenshot_id,
                               page_type=observation.page_type, evidence=observation.evidence)
            if click:
                self.device.click(round((bounds[0] + bounds[2]) / 2), round((bounds[1] + bounds[3]) / 2))
            return observation
        return None

    def _wait_source(self, predicate, *, seconds: float = 8):
        deadline = time.monotonic() + seconds
        source = ""
        for _ in range(17):
            source = self.device.dump_hierarchy(compressed=True, pretty=False)
            if predicate(source) or time.monotonic() >= deadline:
                return source
            time.sleep(min(.5, max(0, deadline - time.monotonic())))
        return source

    def _visual_feed_kind(self) -> str | None:
        observation = self._visual_observation("video_page", {"home_video", "search_video", "home_image_note", "live", "advertisement"})
        return observation.page_type if observation is not None else None

    def _record_recovery(
        self,
        *,
        rule_id: str,
        rule_version: str,
        action: str,
        reason: str,
    ) -> dict[str, Any]:
        event = {
            "rule_id": rule_id,
            "rule_version": rule_version,
            "action": action,
            "reason": reason,
            "verified": True,
        }
        self.recovery_events.append(event)
        self.recorder.emit("verified_recovery", **event)
        return event

    def drain_recovery_events(self) -> list[dict[str, Any]]:
        events = [dict(event) for event in self.recovery_events]
        self.recovery_events.clear()
        return events

    def _complete_pending_overlay_recovery(self) -> None:
        if self._pending_overlay_recovery is None:
            return
        self._record_recovery(**self._pending_overlay_recovery)
        self._pending_overlay_recovery = None

    def try_verified_overlay_recovery(self, source: str, reason: str) -> bool:
        """Apply one whitelisted overlay rule and verify the resulting feed."""
        rule = match_verified_recovery_rule(source)
        if rule is None:
            return False
        attempts = 0
        current_source = source
        for action in rule.actions:
            if attempts >= rule.max_attempts:
                break
            bounds = find_action_bounds(
                current_source,
                action.labels,
                self.profile.width,
                self.profile.height,
            )
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            x, y = round((left + right) / 2), round((top + bottom) / 2)
            self.recorder.emit(
                "verified_recovery_attempt",
                rule_id=rule.rule_id,
                rule_version=rule.version,
                action=action.action_id,
                reason=reason,
                attempt=attempts + 1,
                x=x,
                y=y,
                bounds=list(bounds),
            )
            self.device.click(x, y)
            attempts += 1
            time.sleep(0.8)
            image = self.device.screenshot(format="pillow").convert("RGB")
            try:
                current_source = self.device.dump_hierarchy(
                    compressed=True, pretty=False
                )
            except Exception:
                current_source = ""
            if self.main_feed_shell_confirmed(image, current_source):
                self._record_recovery(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    action=action.action_id,
                    reason=reason,
                )
                return True
            if not rule.matches(current_source):
                self._pending_overlay_recovery = {
                    "rule_id": rule.rule_id,
                    "rule_version": rule.version,
                    "action": action.action_id,
                    "reason": reason,
                }
                return False
        return False

    def tap(self, point: tuple[float, float], action: str) -> None:
        x, y = self.profile.absolute(point)
        self.recorder.emit("tap", action=action, x=x, y=y)
        self.device.click(x, y)

    def main_feed_confirmed(self, image: Image.Image) -> bool:
        try:
            source = self.device.dump_hierarchy(compressed=True, pretty=False)
            signals = _visible_aweme_signals(
                source, self.profile.width, self.profile.height
            )
            standard_feed = (
                foreground_package(self.device) == DOUYIN_PACKAGE
                and "首页" in source
                and not _home_image_note_source_visible(
                    source, signals, self.profile.width, self.profile.height
                )
                and not _missing_feed_controls(signals)
            )
            search_feed = (
                (self.feed_phase == "search" or self.allow_search_feed)
                and foreground_package(self.device) == DOUYIN_PACKAGE
                and any(marker in source for marker in ("暂停视频，按钮", "播放视频，按钮"))
            )
            return search_feed if self.feed_phase == "search" else standard_feed
        except Exception:
            return bool(self.feed_phase == "home" and main_feed_visible(image))

    def _home_feed_shell_confirmed(
        self, image: Image.Image, source: str | None = None
    ) -> bool:
        try:
            current_source = source
            if current_source is None:
                current_source = self.device.dump_hierarchy(
                    compressed=True, pretty=False
                )
            return bool(
                foreground_package(self.device) == DOUYIN_PACKAGE
                and main_feed_shell_source_confirmed(
                    current_source, self.profile.width, self.profile.height
                )
            )
        except Exception:
            return False

    def main_feed_shell_confirmed(
        self, image: Image.Image, source: str | None = None
    ) -> bool:
        """Confirm a browsable feed shell without granting mutation access."""
        try:
            current_source = source
            if current_source is None:
                current_source = self.device.dump_hierarchy(
                    compressed=True, pretty=False
                )
            if foreground_package(self.device) != DOUYIN_PACKAGE:
                return False
            if minor_mode_overlay_visible(current_source) or any(
                marker in current_source
                for marker in (
                    "登录后，体验完整功能",
                    "请输入手机号",
                    "验证并登录",
                )
            ):
                return False
            signals = _visible_aweme_signals(
                current_source, self.profile.width, self.profile.height
            )
            if comment_panel_source_visible(current_source) and not (
                self.allow_search_feed and not _missing_feed_controls(signals)
            ):
                return False
            search_required = self.feed_phase == "search" or self.allow_search_feed
            if search_required and self.search_feed_confirmed(
                current_source,
                image,
                allow_visual_fallback=self._search_visual_fallback_active,
            ):
                return True
            if search_required:
                return False
            return self._home_feed_shell_confirmed(image, current_source)
        except Exception:
            return False

    def required_feed_confirmed(
        self, image: Image.Image, source: str | None = None
    ) -> bool:
        """Confirm exactly the feed required by the current orchestration phase."""
        return self.main_feed_shell_confirmed(image, source)

    def wait_for_main_feed_shell(
        self, reason: str, *, attempts: int = 16, delay_s: float = 0.75
    ) -> bool:
        """Poll a bounded number of times after a slow application restart."""
        for attempt in range(1, attempts + 1):
            try:
                image = self.device.screenshot(format="pillow").convert("RGB")
                self.ensure_profile(image)
                source = self.device.dump_hierarchy(compressed=True, pretty=False)
                if self._home_feed_shell_confirmed(image, source):
                    self.recorder.emit(
                        "feed_recovery_wait",
                        reason=reason,
                        outcome="ready",
                        attempt=attempt,
                    )
                    self.recorder.screenshot(
                        self.device, f"feed-recovery-{reason}-app-start-ready"
                    )
                    return True
            except Exception as exc:
                self.recorder.emit(
                    "feed_recovery_wait",
                    reason=reason,
                    outcome="observation_failed",
                    attempt=attempt,
                    error=type(exc).__name__,
                )
            if attempt < attempts:
                time.sleep(delay_s)
        self.recorder.emit(
            "feed_recovery_wait",
            reason=reason,
            outcome="timeout",
            attempts=attempts,
        )
        self.recorder.screenshot(
            self.device, f"feed-recovery-{reason}-app-start-timeout"
        )
        return False

    def search_feed_confirmed(
        self,
        source: str,
        image: Image.Image | None = None,
        *,
        allow_visual_fallback: bool = False,
    ) -> bool:
        """Confirm read-only search browsing without granting mutation access."""
        try:
            if foreground_package(self.device) != DOUYIN_PACKAGE:
                return False
            if _search_results_grid_visible(
                source, self.profile.width, self.profile.height
            ):
                return False
            signals = _visible_aweme_signals(
                source, self.profile.width, self.profile.height
            )
            has_douyin_nodes = DOUYIN_PACKAGE in source
            if has_douyin_nodes and not _search_context_evidence_visible(
                source, self.profile.width, self.profile.height
            ):
                return False
            if has_douyin_nodes and not _missing_feed_controls(signals):
                return True
            if has_douyin_nodes and any(
                marker in source for marker in ("暂停视频，按钮", "播放视频，按钮")
            ):
                return True
            # Some devices render the app correctly but expose only SystemUI
            # nodes through uiautomator.  Accept the existing conservative
            # visual feed shell only after a verified search-result transition.
            # If any Douyin node is present, semantic verification remains
            # authoritative and a missing play marker fails closed.
            return bool(
                image is not None
                and not has_douyin_nodes
                and allow_visual_fallback
                and main_feed_visible(image)
            )
        except Exception:
            return False

    def require_main_feed(self, image: Image.Image, stage: str) -> None:
        if not self.main_feed_confirmed(image):
            raise RuntimeError(f"Main feed precondition failed before {stage}")

    def require_main_feed_shell(self, image: Image.Image, stage: str) -> None:
        if not (
            self.main_feed_confirmed(image)
            or self.main_feed_shell_confirmed(image)
        ):
            raise RuntimeError(f"Browsable feed precondition failed before {stage}")

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
        def accept_closed_state(
            current_image: Image.Image,
            current_source: str,
            evidence_suffix: str,
        ) -> Image.Image | None:
            # A generic “关闭” control can also belong to search overlays or
            # video cards. Only comment-panel semantics prove the panel is
            # still open. Once the panel is gone, do not keep pressing Back:
            # the close action can leave Douyin on a search landing/results
            # page and further Back presses only unwind more valid context.
            if comment_panel_source_visible(current_source):
                return None
            if self.main_feed_shell_confirmed(current_image, current_source):
                return current_image

            self.recorder.emit(
                "comment_close_recovery",
                video=video,
                action="rebuild_required_feed",
                reason="panel_closed_navigation_drift",
            )
            if self.recover_required_feed(f"comment-panel-close-drift-{video}"):
                return self.recorder.screenshot(
                    self.device,
                    f"{screenshot_name}-{evidence_suffix}-feed-recovered",
                )
            raise RuntimeError(
                "Comment panel closed but required feed could not be recovered"
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
        closed_source = self.device.dump_hierarchy(compressed=True, pretty=False)
        accepted = accept_closed_state(closed, closed_source, "close")
        if accepted is not None:
            return accepted

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
            closed_source = self.device.dump_hierarchy(
                compressed=True, pretty=False
            )
            accepted = accept_closed_state(
                closed, closed_source, f"back-{attempt}"
            )
            if accepted is not None:
                return accepted

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
                closed_source = self.device.dump_hierarchy(
                    compressed=True, pretty=False
                )
                accepted = accept_closed_state(
                    closed, closed_source, f"retry-close-{attempt}"
                )
                if accepted is not None:
                    return accepted
        raise RuntimeError("Comment panel did not close after bounded recovery")

    def recover_main_feed(self, reason: str) -> bool:
        """Return to the feed from an accidental landing page, with verification."""
        try:
            source = self.device.dump_hierarchy(compressed=True, pretty=False)
            home_bounds = find_bottom_navigation_bounds(
                source, "首页", self.profile.width, self.profile.height
            )
            home_label_visible = "首页" in source
        except Exception:
            home_bounds = None
            home_label_visible = False
        if home_bounds is not None:
            left, top, right, bottom = home_bounds
            x, y = round((left + right) / 2), round((top + bottom) / 2)
            self.recorder.emit(
                "feed_recovery", reason=reason, action="home_tab", x=x, y=y,
                source="semantic_ui_tree", bounds=list(home_bounds),
            )
            self.device.click(x, y)
            time.sleep(0.8)
            image = self.recorder.screenshot(
                self.device, f"feed-recovery-{reason}-home-tab"
            )
            if self._home_feed_shell_confirmed(image):
                self._record_recovery(
                    rule_id="douyin-navigation-drift",
                    rule_version="1.0.0",
                    action="home_tab",
                    reason=reason,
                )
                return True

        fallback = (
            self.device_profile.fallback_point(self.profile.width, self.profile.height)
            if self.device_profile
            else None
        )
        if home_bounds is None and home_label_visible and fallback is not None:
            x, y = fallback
            self.recorder.emit(
                "feed_recovery", reason=reason, action="home_tab_fallback", x=x, y=y,
                source="verified_device_profile",
            )
            self.device.click(x, y)
            time.sleep(0.8)
            image = self.recorder.screenshot(
                self.device, f"feed-recovery-{reason}-home-fallback"
            )
            if self._home_feed_shell_confirmed(image):
                self._record_recovery(
                    rule_id="douyin-navigation-drift",
                    rule_version="1.0.0",
                    action="home_tab_fallback",
                    reason=reason,
                )
                return True

        for attempt in range(1, FEED_RECOVERY_BACK_ATTEMPTS + 1):
            self.recorder.emit(
                "feed_recovery", reason=reason, attempt=attempt, action="back"
            )
            self.device.press("back")
            time.sleep(0.8)
            image = self.recorder.screenshot(
                self.device, f"feed-recovery-{reason}-back-{attempt}"
            )
            if self._home_feed_shell_confirmed(image):
                self._record_recovery(
                    rule_id="douyin-navigation-drift",
                    rule_version="1.0.0",
                    action="back",
                    reason=reason,
                )
                return True

            try:
                source = self.device.dump_hierarchy(
                    compressed=True, pretty=False
                )
            except Exception as exc:
                # UIAutomator can transiently return an empty hierarchy while
                # Douyin is animating between search layers.  A failed read is
                # not evidence of page drift; continue the already bounded
                # read-only unwind and verify again after the next step.
                self.recorder.emit(
                    "feed_recovery_observation_failed",
                    reason=reason,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                )
                continue
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
                if self._home_feed_shell_confirmed(image):
                    self._record_recovery(
                        rule_id="douyin-navigation-drift",
                        rule_version="1.0.0",
                        action="close_button",
                        reason=reason,
                    )
                    return True

        self.recorder.emit("feed_recovery", reason=reason, action="app_restart")
        try:
            self.device.app_stop(DOUYIN_PACKAGE)
        except Exception:
            pass
        self.device.app_start(DOUYIN_PACKAGE, wait=False, stop=False)
        recovered = self.wait_for_main_feed_shell(reason)
        if recovered:
            self._record_recovery(
                rule_id="douyin-navigation-drift",
                rule_version="1.0.0",
                action="app_restart",
                reason=reason,
            )
        return recovered

    def recover_required_feed(self, reason: str) -> bool:
        """Recover the required browsing origin, including search re-entry."""
        target_phase = self.feed_phase
        target_query = self.search_query
        if target_phase == "home":
            self.allow_search_feed = False
            self.search_query = ""
            self._search_visual_fallback_active = False
        if not self.recover_main_feed(reason):
            return False
        if target_phase != "search":
            self.feed_phase = "home"
            return True
        for attempt in range(1, 3):
            try:
                self.enter_topic_search(target_query)
                self._record_recovery(
                    rule_id="douyin-search-context-drift",
                    rule_version="1.1.0",
                    action="search_reentry",
                    reason=reason,
                )
                return True
            except Exception as exc:
                self.recorder.emit(
                    "feed_recovery",
                    reason=reason,
                    action="search_reentry_failed",
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if attempt >= 2 or not self.recover_main_feed(
                    f"{reason}-search-retry"
                ):
                    return False
        return False

    def prepare_feed_phase(self, phase: str, query: str, reason: str) -> bool:
        """Enter a phase from a verified home feed without carrying stale state."""
        if phase not in {"home", "search"}:
            raise ValueError(f"unsupported feed phase: {phase}")
        self.feed_phase = "home"
        self.allow_search_feed = False
        self.search_query = ""
        self._search_visual_fallback_active = False
        if not self.recover_main_feed(reason):
            return False
        if phase == "home":
            self.recorder.emit("feed_phase_ready", phase="home", reason=reason)
            return True
        self.enter_topic_search(query)
        self.recorder.emit(
            "feed_phase_ready", phase="search", reason=reason, query=query
        )
        return True

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
            minor_mode_overlay = minor_mode_overlay_visible(source)
            if minor_mode_overlay and close_attempts < 2:
                recovered_now = self.try_verified_overlay_recovery(
                    source, "app-ready-overlay"
                )
                close_attempts += 1
                if recovered_now:
                    self.recorder.emit(
                        "app_ready",
                        launch_needed=launch_needed,
                        close_attempts=close_attempts,
                        recovered=True,
                        elapsed_s=round(time.monotonic() - started, 3),
                    )
                    return
                continue
            if close_bounds is not None and login_overlay and close_attempts < 2:
                left, top, right, bottom = close_bounds
                self.device.click(round((left + right) / 2), round((top + bottom) / 2))
                close_attempts += 1
                time.sleep(0.8)
                continue
            if comment_panel_source_visible(source):
                self.recorder.emit(
                    "startup_overlay_recovery",
                    overlay="comment_panel",
                    action="bounded_feed_recovery",
                )
                if self.recover_main_feed("app-ready-comment-panel"):
                    self._complete_pending_overlay_recovery()
                    self.recorder.emit(
                        "app_ready",
                        launch_needed=launch_needed,
                        close_attempts=close_attempts,
                        recovered=True,
                        elapsed_s=round(time.monotonic() - started, 3),
                    )
                    return
                break
            if self.main_feed_shell_confirmed(image, source):
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
                if self.main_feed_shell_confirmed(verify_image, verify_source):
                    self._complete_pending_overlay_recovery()
                    self.recorder.emit(
                        "app_ready",
                        launch_needed=launch_needed,
                        close_attempts=close_attempts,
                        elapsed_s=round(time.monotonic() - started, 3),
                    )
                    return
            time.sleep(0.5)
        if self.recover_main_feed("app-ready"):
            self._complete_pending_overlay_recovery()
            self.recorder.emit(
                "app_ready",
                launch_needed=launch_needed,
                close_attempts=close_attempts,
                recovered=True,
                elapsed_s=round(time.monotonic() - started, 3),
            )
            return
        raise RuntimeError("Douyin did not reach the main feed")

    def enter_topic_search(self, query: str) -> None:
        if not query:
            raise RuntimeError("Search mode requires a non-empty query")
        source = self.device.dump_hierarchy(compressed=True, pretty=False)
        search_bounds = find_control_bounds(
            source,
            "搜索",
            self.profile.width,
            self.profile.height,
            require_button_label=False,
        )
        if search_bounds is None:
            source = self._wait_source(lambda xml: find_control_bounds(xml, "搜索", self.profile.width, self.profile.height, require_button_label=False) is not None)
            search_bounds = find_control_bounds(source, "搜索", self.profile.width, self.profile.height, require_button_label=False)
        if search_bounds is None:
            if self._visual_observation("search_entry", {"home"}, click=True) is None:
                raise RuntimeError("Search button was not found on the main feed")
        else:
            left, top, right, bottom = search_bounds
            self.device.click(round((left + right) / 2), round((top + bottom) / 2))
        self.recorder.emit("topic_search", action="open_search", query=query)
        source = self._wait_source(lambda xml: find_editable_bounds(xml, self.profile.width, self.profile.height) is not None)
        input_bounds = find_editable_bounds(source, self.profile.width, self.profile.height)
        if input_bounds is None:
            raise RuntimeError("Search input was not found")
        left, top, right, bottom = input_bounds
        self.device.click(round((left + right) / 2), round((top + bottom) / 2))
        used_selector = False
        try:
            editor = self.device(className="android.widget.EditText")
            if bool(editor.exists):
                editor.set_text(query)
                used_selector = True
                if str(editor.get_text()).strip() != query:
                    raise RuntimeError("Search query was not committed to the input")
        except TypeError:
            used_selector = False
        if not used_selector:
            self.device.send_keys(query, clear=True)
            self.device.press("enter")
        else:
            submit = self.device(description="搜索")
            if bool(submit.exists):
                submit.click()
            else:
                self.device.press("enter")
        self.recorder.emit("topic_search", action="submit", query=query)
        time.sleep(0.8)

        # Prefer the dedicated video tab. The general-results tab may prepend
        # an AI answer card and leave only a clipped row of videos near the
        # navigation bar, which is a poor and unstable click target.
        try:
            video_tab = self.device(text="视频")
            if bool(video_tab.exists):
                video_tab.click()
                self.recorder.emit(
                    "topic_search", action="open_video_tab", query=query
                )
                time.sleep(1.0)
        except Exception:
            # Older/local test drivers do not expose selector calls. The
            # verified result-card path below remains the compatibility route.
            pass

        source = ""
        result_bounds = None
        for _attempt in range(1, 13):
            source = self.device.dump_hierarchy(compressed=True, pretty=False)
            result_bounds = find_search_result_bounds(
                source, self.profile.width, self.profile.height
            )
            if result_bounds is not None:
                # The UI tree can expose result cards while the rendered page is
                # still on its loading frame. Require one later observation so
                # the click is delivered to a live card instead of being lost.
                time.sleep(1.0)
                refreshed_source = self.device.dump_hierarchy(
                    compressed=True, pretty=False
                )
                refreshed_bounds = find_search_result_bounds(
                    refreshed_source, self.profile.width, self.profile.height
                )
                if refreshed_bounds is not None:
                    source = refreshed_source
                    result_bounds = refreshed_bounds
                    break
                result_bounds = None
            time.sleep(0.8)
        (self.recorder.run_dir / "topic-search-results.xml").write_text(
            source, encoding="utf-8"
        )
        self.recorder.screenshot(self.device, "topic-search-results")
        if result_bounds is None:
            observation = self._visual_observation("search_video_result", {"search_results"})
            if observation is None:
                raise RuntimeError("Search results did not expose a verified video item")
            result_bounds = observation.bounds
        left, top, right, bottom = result_bounds
        self.device.click(round((left + right) / 2), round((top + bottom) / 2))
        self.recorder.emit(
            "topic_search", action="open_video_result", query=query,
            bounds=list(result_bounds),
        )
        time.sleep(1.0)
        image = None
        last_source = ""
        retried_result_card = False
        for _attempt in range(1, 9):
            image = self.recorder.screenshot(
                self.device, f"topic-search-entered-video-{_attempt}"
            )
            last_source = self.device.dump_hierarchy(
                compressed=False, pretty=False
            )
            if self.search_feed_confirmed(
                last_source, image, allow_visual_fallback=True
            ):
                self.allow_search_feed = True
                self.search_query = query
                self.feed_phase = "search"
                self._search_visual_fallback_active = DOUYIN_PACKAGE not in last_source
                return
            if (
                not self.visual_navigation_enabled
                and not retried_result_card
                and _search_results_grid_visible(
                    last_source, self.profile.width, self.profile.height
                )
            ):
                # The first click has a known non-effect: the same verified
                # search-results grid is still visible.  A single repeat is
                # safe here; ambiguous or changed states are never replayed.
                self.device.click(
                    round((left + right) / 2), round((top + bottom) / 2)
                )
                retried_result_card = True
                self.recorder.emit(
                    "topic_search",
                    action="open_video_result_retry",
                    query=query,
                    bounds=list(result_bounds),
                    reason="verified_search_grid_still_visible",
                )
                time.sleep(1.0)
                continue
            time.sleep(0.8)
        if image is None or not self.search_feed_confirmed(
            last_source, image, allow_visual_fallback=True
        ):
            (self.recorder.run_dir / "topic-search-entered-video.xml").write_text(
                last_source, encoding="utf-8"
            )
            raise RuntimeError("Search result did not enter a verified video feed")

    def swipe_next(self, from_video: int, to_video: int) -> None:
        before = self.recorder.screenshot(
            self.device, f"video-{from_video}-pre-swipe-state"
        )
        self.ensure_profile(before)
        search_mode = bool(self.allow_search_feed and self.search_query)
        if search_mode:
            try:
                source = self.device.dump_hierarchy(compressed=True, pretty=False)
                feed_ready = self.search_feed_confirmed(
                    source,
                    before,
                    allow_visual_fallback=self._search_visual_fallback_active,
                )
            except Exception:
                feed_ready = False
        else:
            feed_ready = self.main_feed_shell_confirmed(before)
        if not feed_ready:
            kind = self._visual_feed_kind()
            feed_ready = kind == ("search_video" if search_mode else "home_video")
        if not feed_ready:
            if not self.recover_required_feed(f"before-swipe-{from_video}"):
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

    def capture_gate(
        self,
        video: int,
        action: str,
        *,
        evidence_name: str | None = None,
    ) -> tuple[Image.Image, GateDecision]:
        before = self.recorder.screenshot(
            self.device, f"video-{video}-{evidence_name or action}-before"
        )
        self.ensure_profile(before)
        source: str | None = None
        search_mode = bool(self.allow_search_feed and self.search_query)
        if search_mode:
            try:
                source = self.device.dump_hierarchy(compressed=True, pretty=False)
                search_feed = self.search_feed_confirmed(
                    source,
                    before,
                    allow_visual_fallback=self._search_visual_fallback_active,
                )
            except Exception:
                search_feed = False
            if not search_feed:
                if action == "topic-analysis":
                    search_feed = self._visual_feed_kind() == "search_video"
            if not search_feed:
                recovered = self.recover_required_feed(
                    f"search-context-before-{action}-{video}"
                )
                if recovered:
                    before = self.recorder.screenshot(
                        self.device,
                        f"video-{video}-{evidence_name or action}-search-recovered",
                    )
                    self.ensure_profile(before)
                    try:
                        source = self.device.dump_hierarchy(
                            compressed=True, pretty=False
                        )
                        search_feed = self.search_feed_confirmed(
                            source,
                            before,
                            allow_visual_fallback=self._search_visual_fallback_active,
                        )
                    except Exception:
                        search_feed = False
            if not search_feed:
                return before, GateDecision(False, ("search_context_drift",), ())
        strict_feed = self.main_feed_confirmed(before)
        browsable_feed = search_feed if search_mode else strict_feed
        if action == "topic-analysis" and not browsable_feed:
            try:
                source = self.device.dump_hierarchy(compressed=True, pretty=False)
                browsable_feed = self.main_feed_shell_confirmed(before, source)
            except Exception:
                browsable_feed = False
        if (
            action == "topic-analysis"
            and not browsable_feed
            and source is not None
            and share_sheet_visible(source)
        ):
            recovered = self.recover_required_feed(f"share-sheet-before-topic-{video}")
            if recovered:
                before = self.recorder.screenshot(
                    self.device, f"video-{video}-topic-analysis-recovered"
                )
                self.ensure_profile(before)
                try:
                    source = self.device.dump_hierarchy(compressed=True, pretty=False)
                    browsable_feed = self.main_feed_shell_confirmed(before, source)
                except Exception:
                    browsable_feed = False
            if not browsable_feed:
                return before, GateDecision(False, ("share_sheet",), ())
        if action == "topic-analysis" and not browsable_feed and source is not None:
            known_skip = classify_mutation_gate(
                source,
                foreground_package(self.device),
                before.width,
                before.height,
                require_feed_controls=False,
            )
            if known_skip.reasons and all(
                reason in BLOCK_MARKERS for reason in known_skip.reasons
            ):
                return before, known_skip
        if not browsable_feed:
            if action == "topic-analysis":
                kind = self._visual_feed_kind()
                if kind in {"home_image_note", "live", "advertisement"}:
                    reason = {"home_image_note": "non_video_feed_item", "live": "live", "advertisement": "advertising"}[kind]
                    return before, GateDecision(False, (reason,), ())
                if kind == ("search_video" if search_mode else "home_video"):
                    # This grants only video observation/counting. Likes, favorites
                    # and comments always re-enter the original strict UI gates.
                    return before, GateDecision(True, (), ())
            decision = GateDecision(False, ("visual_main_feed_check_failed",), ())
        else:
            started = time.monotonic()
            try:
                if source is None:
                    source = self.device.dump_hierarchy(compressed=True, pretty=False)
                foreground = foreground_package(self.device)
                decision = classify_mutation_gate(
                    source,
                    foreground,
                    before.width,
                    before.height,
                    require_feed_controls=action != "topic-analysis",
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

    def _retry_confirmed_inactive_reaction_once(
        self,
        video: int,
        action: str,
        *,
        color: str,
        threshold: float,
    ) -> bool:
        if getattr(self, 'observe_reactions_only', False):
            return self._observe_reaction(video, action, color=color, threshold=threshold)
        retry_before, retry_gate = self.capture_gate(
            video,
            action,
            evidence_name=f"{action}-retry",
        )
        if not retry_gate.allowed:
            self.recorder.emit(
                "reaction_retry_aborted",
                video=video,
                action=action,
                reason="gate_not_allowed",
                gate_reasons=list(retry_gate.reasons),
            )
            return False

        bounds = self.control_bounds[action]
        semantic_active = self.control_states.get(action)
        visual_active = color_active_in_bounds(
            retry_before, bounds, color, threshold
        )
        if semantic_active is True or visual_active:
            self.recorder.emit(
                f"{action}_state_after",
                video=video,
                active=True,
                verification_attempt=2,
                resolved_without_replay=True,
            )
            return True
        if semantic_active is not False:
            self.recorder.emit(
                "reaction_retry_aborted",
                video=video,
                action=action,
                reason="state_unknown",
            )
            return False

        self.recorder.emit(
            "reaction_retry",
            video=video,
            action=action,
            attempt=2,
            reason="confirmed_inactive",
        )
        self.tap_control(action, f"{action}_retry")
        time.sleep(0.8)
        retry_after = self.recorder.screenshot(
            self.device, f"video-{video}-{action}-after-retry"
        )
        retry_active = color_active_in_bounds(
            retry_after, bounds, color, threshold
        )
        self.recorder.emit(
            f"{action}_state_after",
            video=video,
            active=retry_active,
            verification_attempt=2,
            resolved_without_replay=False,
        )
        return retry_active

    def _observe_reaction(self, video, action, *, color, threshold):
        """Bounded read-only confirmation. Never navigate or tap again here."""
        deadline = time.monotonic() + 4
        keyword = '喜欢' if action == 'like' else '收藏'
        for attempt in range(8):
            if time.monotonic() >= deadline:
                break
            time.sleep(min(.5, max(0, deadline - time.monotonic())))
            frame = self.recorder.screenshot(self.device, f'video-{video}-{action}-observe-{attempt + 1}')
            if time.monotonic() >= deadline or not self.main_feed_confirmed(frame):
                return False
            try:
                source = self.device.dump_hierarchy(compressed=True, pretty=False)
                bounds = find_control_bounds(source, keyword, frame.width, frame.height)
                semantic = control_semantic_state(find_control_description(source, keyword, bounds), action) if bounds else None
            except Exception:
                bounds, semantic = None, None
            # Never reuse a moved target's old coordinates. UI-less visual
            # confirmation is allowed only after the current feed check above.
            bounds = bounds or self.control_bounds[action]
            active = semantic if semantic is not None else color_active_in_bounds(frame, bounds, color, threshold)
            self.recorder.emit(f'{action}_state_after', video=video, active=active,
                               verification_attempt=attempt + 2, resolved_without_replay=True)
            if active and time.monotonic() < deadline:
                return True
        return False

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
        if getattr(self, 'observe_reactions_only', False):
            if not self._observe_reaction(video, 'like', color='red', threshold=0.035):
                raise RuntimeError('Like verification unresolved; no retry')
            return True
        self.recorder.emit("like_state_after", video=video, active=after_active)
        if not after_active and not self._retry_confirmed_inactive_reaction_once(
            video, "like", color="red", threshold=0.035
        ):
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
        if getattr(self, 'observe_reactions_only', False):
            if not self._observe_reaction(video, 'favorite', color='yellow', threshold=0.025):
                raise RuntimeError('Favorite verification unresolved; no retry')
            return True
        self.recorder.emit(
            "favorite_state_after", video=video, active=after_active
        )
        if not after_active and not self._retry_confirmed_inactive_reaction_once(
            video, "favorite", color="yellow", threshold=0.025
        ):
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
    parser.add_argument("--device-id", required=True)
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
