from __future__ import annotations

import hashlib
import gzip
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TypedDict

from PIL import Image

from douyin_fixed_runner import DOUYIN_PACKAGE, PROFILE
from douyin_uia2_runner import (
    find_bottom_navigation_bounds,
    find_control_bounds,
    foreground_package,
)
from incident_evidence import IncidentSink, record_incident_evidence


MAX_SECTION_ITEMS = 100
MAX_FIELD_LENGTH = 160
LOGIN_MARKERS = (
    "登录后，体验完整功能",
    "请输入手机号",
    "验证并登录",
)
IDENTITY_BLOCK_MARKERS = (
    "身份安全验证",
    "操作环境存在风险",
)
QUICK_ACTION_MARKERS = ("回赞", "关注", "戳一戳", "私信按钮")
CONVERSATION_SURFACE_MARKERS = (
    "发送消息",
    "快捷回复",
    "一键留资",
    "会话可能会被记录",
    "全部消息",
)


class EngagementSectionResult(TypedDict):
    status: str
    count: int | None
    unread_count: int | None
    entries: list[dict[str, Any]]
    truncated: bool
    reason: str | None


class EngagementInspectionResult(TypedDict):
    status: str
    task_type: str
    restored: bool
    failure_reason: str | None
    side_effect_notice: str
    sections: dict[str, EngagementSectionResult]
    evidence: list[str]


class _SectionHandled(RuntimeError):
    """Internal control flow for a safely recorded unavailable v2 section."""


class V2PreconditionMismatch(RuntimeError):
    """Structured action-free version/display drift eligible for one revalidation."""

    def __init__(
        self,
        code: str,
        *,
        expected_app_version: str,
        actual_app_version: str,
        expected_display_signature: str,
        actual_display_signature: str,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.expected_app_version = expected_app_version
        self.actual_app_version = actual_app_version
        self.expected_display_signature = expected_display_signature
        self.actual_display_signature = actual_display_signature
        self.recovery_eligible = True
        self.navigation_started = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "failure_class": "recoverable_precondition",
            "precondition_code": self.code,
            "expected_app_version": self.expected_app_version,
            "actual_app_version": self.actual_app_version,
            "expected_display_signature": self.expected_display_signature,
            "actual_display_signature": self.actual_display_signature,
            "recovery_eligible": self.recovery_eligible,
            "navigation_started": self.navigation_started,
        }


@dataclass(frozen=True)
class InspectionPolicy:
    max_items_per_section: int = MAX_SECTION_ITEMS
    field_length: int = MAX_FIELD_LENGTH

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "InspectionPolicy":
        values = raw or {}
        max_items = int(values.get("max_items_per_section", MAX_SECTION_ITEMS))
        if not 1 <= max_items <= MAX_SECTION_ITEMS:
            raise ValueError("max_items_per_section must be between 1 and 100")
        field_length = int(values.get("field_length", MAX_FIELD_LENGTH))
        if not 32 <= field_length <= MAX_FIELD_LENGTH:
            raise ValueError("field_length must be between 32 and 160")
        return cls(max_items_per_section=max_items, field_length=field_length)


def _clean(value: Any, limit: int = MAX_FIELD_LENGTH) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _nodes(xml_source: str) -> list[ET.Element]:
    root = ET.fromstring(xml_source)
    return [
        node
        for node in root.iter("node")
        if node.get("visible-to-user", "true") == "true"
        and node.get("package", DOUYIN_PACKAGE) in {"", DOUYIN_PACKAGE}
    ]


def _label(node: ET.Element) -> str:
    return _clean(
        " ".join(
            value
            for value in (node.get("text", ""), node.get("content-desc", ""))
            if value
        ),
        1000,
    )


def _labels(xml_source: str) -> list[str]:
    return [_label(node) for node in _nodes(xml_source) if _label(node)]


def _bounded_entries(
    entries: Iterable[dict[str, Any]], policy: InspectionPolicy
) -> tuple[list[dict[str, Any]], bool]:
    values = list(entries)
    bounded = [
        {
            str(key): (
                _clean(value, policy.field_length)
                if isinstance(value, str)
                else value
            )
            for key, value in entry.items()
        }
        for entry in values[: policy.max_items_per_section]
    ]
    return bounded, len(values) > len(bounded)


def parse_message_badge(xml_source: str) -> dict[str, Any]:
    """Read the bottom message badge without entering the message page."""
    candidates: list[str] = []
    for node in _nodes(xml_source):
        label = _label(node)
        if "消息" not in label:
            continue
        bounds = node.get("bounds", "")
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
        if match and int(match.group(2)) < 1600:
            continue
        candidates.append(label)
    combined = " ".join(candidates)
    numeric = re.search(r"(\d{1,3})\s*(?:条)?未读", combined)
    if numeric:
        return {
            "has_unread": True,
            "unread_count": int(numeric.group(1)),
            "indicator": "number",
        }
    has_dot = any(marker in combined for marker in ("有新消息", "未读", "红点"))
    return {
        "has_unread": has_dot,
        "unread_count": None if has_dot else 0,
        "indicator": "dot" if has_dot else "none",
    }


def _private_message_entry(label: str) -> dict[str, Any] | None:
    if any(marker in label for marker in ("系统通知", "互动消息", "新朋友", "推荐")):
        return None
    if not any(marker in label for marker in ("私信会话", "未读私信", "来自私信")):
        return None
    parts = [part.strip(" ，,|") for part in re.split(r"[，,|]", label) if part.strip(" ，,|")]
    parts = [
        part
        for part in parts
        if part not in {"私信会话", "未读私信", "来自私信"}
    ]
    unread = re.search(r"(\d{1,3})\s*(?:条)?未读", label)
    visible = [part for part in parts if not re.search(r"\d{1,3}\s*(?:条)?未读", part)]
    return {
        "display_name": visible[0] if visible else "",
        "time": visible[1] if len(visible) > 1 else "",
        "preview": visible[2] if len(visible) > 2 else "",
        "unread_count": int(unread.group(1)) if unread else None,
    }


def _structured_private_message_entries(xml_source: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_source)
    entries: list[dict[str, Any]] = []
    excluded = ("系统通知", "互动消息", "新朋友", "粉丝", "推荐", "通讯录")
    for node in root.iter("node"):
        if node.get("package") != DOUYIN_PACKAGE:
            continue
        if node.get("visible-to-user", "true") != "true":
            continue
        if node.get("clickable") != "true" or not node.get("class", "").endswith(
            "Button"
        ):
            continue
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        if left > 60 or right - left < 720 or bottom - top < 100 or top < 350:
            continue
        descendants = list(node.iter("node"))[1:]
        values = [
            (
                child.get("resource-id", "").rsplit("/", 1)[-1],
                _label(child),
            )
            for child in descendants
            if _label(child)
        ]
        combined = " ".join(value for _resource_id, value in values)
        if not combined or any(marker in combined for marker in excluded):
            continue
        title = next(
            (value for resource_id, value in values if resource_id == "tv_title"),
            values[0][1] if values else "",
        )
        unread_text = next(
            (
                value
                for resource_id, value in values
                if resource_id == "red_tips_count_view"
            ),
            "",
        )
        unread_match = re.search(r"\d{1,3}", unread_text)
        time_value = next(
            (
                value
                for _resource_id, value in values
                if re.search(
                    r"(?:刚刚|昨天|前天|\d+分钟前|\d+小时前|\d{1,2}:\d{2}|\d{1,2}/\d{1,2})",
                    value,
                )
            ),
            "",
        )
        preview = next(
            (
                value
                for resource_id, value in values
                if resource_id in {"yek", "preview"}
            ),
            "",
        )
        if not preview:
            preview = next(
                (
                    value
                    for resource_id, value in values
                    if value not in {title, time_value, unread_text}
                    and resource_id != "red_tips_count_view"
                ),
                "",
            )
        if not title or not (preview or time_value):
            continue
        entries.append(
            {
                "display_name": title,
                "time": time_value,
                "preview": preview,
                "unread_count": int(unread_match.group()) if unread_match else None,
            }
        )
    return entries


def parse_private_messages(
    xml_source: str, policy: InspectionPolicy
) -> dict[str, Any]:
    labels = _labels(xml_source)
    if not any(marker in " ".join(labels) for marker in ("消息", "私信")):
        return _section("failed", reason="message_page_not_recognized")
    entries = [entry for label in labels if (entry := _private_message_entry(label))]
    entries.extend(_structured_private_message_entries(xml_source))
    entries = list(
        {
            (
                str(entry.get("display_name", "")),
                str(entry.get("time", "")),
                str(entry.get("preview", "")),
            ): entry
            for entry in entries
        }.values()
    )
    if not entries and not any(marker in " ".join(labels) for marker in ("暂无私信", "还没有私信")):
        return _section("failed", reason="private_message_rows_ambiguous")
    bounded, truncated = _bounded_entries(entries, policy)
    unread_values = [
        int(entry["unread_count"])
        for entry in entries
        if isinstance(entry.get("unread_count"), int)
    ]
    return _section(
        "available",
        count=len(entries),
        unread_count=sum(unread_values) if unread_values else 0,
        entries=bounded,
        truncated=truncated,
    )


def parse_received_likes(
    xml_source: str, policy: InspectionPolicy
) -> dict[str, Any]:
    labels = _labels(xml_source)
    combined = " ".join(labels)
    if not any(marker in combined for marker in ("互动消息", "收到的赞", "赞了你")):
        return _section("failed", reason="likes_page_not_recognized")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label in labels:
        if "赞了你" not in label or any(marker in label for marker in QUICK_ACTION_MARKERS):
            continue
        value = _clean(label, 1000)
        if value in seen:
            continue
        seen.add(value)
        actor = value.split("赞了你", 1)[0].strip(" ，,")
        if not actor:
            continue
        entries.append({"display_name": actor, "summary": value})
    if not entries and not any(marker in combined for marker in ("暂无互动", "还没有收到赞", "暂无收到的赞")):
        return _section("failed", reason="like_rows_ambiguous")
    bounded, truncated = _bounded_entries(entries, policy)
    return _section(
        "available", count=len(entries), entries=bounded, truncated=truncated
    )


def parse_profile_visitors(
    xml_source: str, policy: InspectionPolicy
) -> dict[str, Any]:
    labels = _labels(xml_source)
    combined = " ".join(labels)
    disabled_markers = (
        "开启主页访客记录",
        "开启后可查看访客",
        "访客记录已关闭",
    )
    if any(marker in combined for marker in disabled_markers):
        return _section("unavailable", reason="visitor_history_disabled")
    activity_summary_marker = "访问过你的主页"
    if not any(
        marker in combined
        for marker in ("主页访客", "访客记录", activity_summary_marker)
    ):
        return _section("failed", reason="visitor_page_not_recognized")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label in labels:
        is_history_row = "访客记录" in label
        is_activity_summary = activity_summary_marker in label
        if not (is_history_row or is_activity_summary) or any(
            marker in label for marker in disabled_markers
        ):
            continue
        if label in {"访客记录", "主页访客 访客记录"}:
            continue
        if label in seen:
            continue
        seen.add(label)
        if is_activity_summary:
            actor = re.split(
                r"[,，]?\s*等\s*\d+\s*人近期访问过你的主页",
                label,
                maxsplit=1,
            )[0].strip(" ，,|")
            if not actor:
                continue
            time_match = re.search(
                r"(?:刚刚|昨天|前天|\d+分钟前|\d+小时前|\d+天前|\d{1,2}:\d{2}|\d{1,2}/\d{1,2})",
                label,
            )
            group_match = re.search(r"等\s*(\d+)\s*人近期访问过你的主页", label)
            entries.append(
                {
                    "display_name": actor,
                    "time": time_match.group() if time_match else "",
                    "visitor_count": int(group_match.group(1)) if group_match else None,
                    "summary": label,
                }
            )
        else:
            parts = [
                part.strip(" ，,|")
                for part in re.split(r"[，,|]", label)
                if part.strip(" ，,|")
            ]
            parts = [
                part for part in parts if part not in {"访客记录", "主页访客"}
            ]
            entries.append(
                {
                    "display_name": parts[0] if parts else "",
                    "time": parts[1] if len(parts) > 1 else "",
                }
            )
    if not entries and not any(marker in combined for marker in ("暂无访客", "还没有访客")):
        return _section("failed", reason="visitor_rows_ambiguous")
    bounded, truncated = _bounded_entries(entries, policy)
    return _section(
        "available", count=len(entries), entries=bounded, truncated=truncated
    )


def _section(
    status: str,
    *,
    count: int | None = None,
    unread_count: int | None = None,
    entries: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "count": count,
        "unread_count": unread_count,
        "entries": entries or [],
        "truncated": bool(truncated),
        "reason": reason,
    }


V2_SECTION_NAMES = (
    "private_messages",
    "received_likes",
    "comment_danmaku",
    "profile_visitors",
)
V2_FILTER_ALIASES = {
    "received_likes": ("赞与收藏", "赞与其他"),
    "received_comments": ("收到的评论", "评论弹幕"),
    "received_danmaku": ("收到的弹幕",),
}
V2_END_MARKERS = ("暂时没有更多了", "没有更多了", "你还没有收到", "暂无")
V2_UNREAD_RESOURCE_MARKERS = ("red_tips_count_view", "badge", "unread", "zor", "zop")
V2_ENTRY_SKIP_LABELS = {
    "首页", "朋友", "消息", "我", "互动消息", "全部消息", "赞与收藏", "赞与其他",
    "收到的评论", "评论弹幕", "收到的弹幕", "主页访客", "新访客", "我的主页",
    "搜索", "更多", "返回", "关注", "回赞",
}

V3_EMPTY_MARKERS = ("暂无互动消息", "还没有互动消息", "暂无新互动", "暂无互动")
V3_END_MARKERS = ("暂时没有更多了", "没有更多了", "已经到底了", "到底了")
V3_VISITOR_DISABLED_MARKERS = (
    "开启主页访客记录",
    "开启后可查看访客",
    "访客记录已关闭",
)
V3_CATEGORY_MARKERS = {
    "received_likes": ("赞了", "点赞了", "收藏了", "赞与收藏"),
    "comment_danmaku": ("评论了", "回复了", "回复:", "回复：", "弹幕"),
    "profile_visitors": ("访问过你的主页", "主页访客", "访客记录"),
}
V3_SKIP_LABELS = {
    "首页", "朋友", "消息", "我", "互动消息", "全部消息", "已读",
    "搜索", "更多", "返回", "关注", "回赞",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _bounds(node: ET.Element) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    return tuple(int(value) for value in match.groups()) if match else None


def _inside_screen(
    bounds: tuple[int, int, int, int], width: int, height: int
) -> bool:
    left, top, right, bottom = bounds
    return 0 <= left < right <= width and 0 <= top < bottom <= height


def find_unified_activity_entry_bounds(
    xml_source: str, width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Return the unique visible clickable row containing the activity label."""
    root = ET.fromstring(xml_source)
    parent = {child: node for node in root.iter() for child in node}
    candidates: set[tuple[int, int, int, int]] = set()
    for node in root.iter("node"):
        if node.get("visible-to-user", "true") != "true":
            continue
        label = _label(node)
        if not (
            label == "互动消息"
            or label.startswith("互动消息，")
            or label.startswith("互动消息,")
        ):
            continue
        clickable = node
        while clickable is not None and clickable.get("clickable") != "true":
            clickable = parent.get(clickable)
        if clickable is None:
            continue
        bounds = _bounds(clickable)
        if bounds is None or not _inside_screen(bounds, width, height):
            continue
        left, top, right, bottom = bounds
        if top < round(height * 0.12) or bottom > round(height * 0.92):
            continue
        if right - left < round(width * 0.45) or bottom - top < 44:
            continue
        candidates.add(bounds)
    if len(candidates) > 1:
        raise ValueError("interaction_entry_ambiguous")
    return next(iter(candidates), None)


def _v3_category(label: str) -> str | None:
    for category, markers in V3_CATEGORY_MARKERS.items():
        if any(marker in label for marker in markers):
            return category
    return None


def _v3_item(label: str, category: str, policy: InspectionPolicy) -> dict[str, Any]:
    cleaned = _clean(label, policy.field_length)
    action_markers = tuple(
        marker for markers in V3_CATEGORY_MARKERS.values() for marker in markers
    )
    actor = cleaned
    for marker in action_markers:
        if marker in actor:
            actor = actor.split(marker, 1)[0].strip(" ，,|")
            break
    time_matches = list(
        re.finditer(
            r"刚刚|昨天|前天|\d+\s*(?:秒|分钟|小时|天)前|"
            r"\d{1,2}:\d{2}|\d{1,2}[-/]\d{1,2}|\d{4}[/.-]\d{1,2}",
            cleaned,
        )
    )
    item: dict[str, Any] = {"category": category, "content": cleaned}
    if actor and len(actor) <= 40:
        item["display_name"] = actor
    if time_matches:
        item["time"] = time_matches[-1].group(0)
    return item


def _v3_row_candidates(
    nodes: list[ET.Element], width: int, height: int
) -> list[tuple[ET.Element, str, tuple[int, int, int, int]]]:
    """Prefer one full clickable list row, even when its own label is empty."""
    composite: list[tuple[ET.Element, str, tuple[int, int, int, int]]] = []
    for node in nodes:
        bounds = _bounds(node)
        if (
            node.get("clickable") != "true"
            or bounds is None
            or not _inside_screen(bounds, width, height)
        ):
            continue
        left, top, right, bottom = bounds
        if (
            top < round(height * 0.10)
            or bottom > round(height * 0.92)
            or right - left < round(width * 0.45)
            or not 44 <= bottom - top <= round(height * 0.30)
        ):
            continue
        labels = list(
            dict.fromkeys(
                _label(child)
                for child in node.iter("node")
                if _label(child)
            )
        )
        combined = "，".join(labels)
        if _v3_category(combined):
            composite.append((node, combined, bounds))
    if composite:
        # A row and one of its clickable children can both match. Keep the
        # outer row so one visible item never becomes multiple receipts.
        outer: list[tuple[ET.Element, str, tuple[int, int, int, int]]] = []
        for candidate in sorted(
            composite,
            key=lambda item: (-(item[2][2] - item[2][0]) * (item[2][3] - item[2][1])),
        ):
            left, top, right, bottom = candidate[2]
            if any(
                outer_left <= left
                and outer_top <= top
                and right <= outer_right
                and bottom <= outer_bottom
                for _node, _label_value, (
                    outer_left,
                    outer_top,
                    outer_right,
                    outer_bottom,
                ) in outer
            ):
                continue
            outer.append(candidate)
        return sorted(outer, key=lambda item: (item[2][1], item[2][0]))
    return [
        (node, _label(node), bounds)
        for node in nodes
        if _v3_category(_label(node))
        and (bounds := _bounds(node)) is not None
        and _inside_screen(bounds, width, height)
    ]


def parse_unified_activity_viewport(
    xml_source: str,
    policy: InspectionPolicy,
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Parse one v3 viewport without opening any activity row."""
    nodes = _nodes(xml_source)
    labels = [_label(node) for node in nodes if _label(node)]
    combined = " ".join(labels)
    page_recognized = any(
        label == "互动消息"
        or label.startswith("互动消息，")
        or label in {"全部消息", "消息通知"}
        for label in labels
    )
    read_tops = [
        bounds[1]
        for node in nodes
        if re.fullmatch(r"(?:全部)?已读(?:消息)?", _label(node).strip())
        and (bounds := _bounds(node)) is not None
        and _inside_screen(bounds, width, height)
    ]
    read_top = min(read_tops) if read_tops else None
    boundary = (
        "read"
        if read_top is not None
        else "explicit_empty"
        if any(marker in combined for marker in V3_EMPTY_MARKERS)
        else "end_of_list"
        if any(marker in combined for marker in V3_END_MARKERS)
        else None
    )
    candidates = _v3_row_candidates(nodes, width, height)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node, label, bounds in candidates:
        category = _v3_category(label)
        if (
            category is None
            or not label
            or label in V3_SKIP_LABELS
            or label in seen
            or bounds[1] < round(height * 0.10)
            or bounds[3] > round(height * 0.92)
            or (read_top is not None and bounds[1] >= read_top)
            or any(marker in label for marker in QUICK_ACTION_MARKERS)
            or any(marker in label for marker in V3_VISITOR_DISABLED_MARKERS)
        ):
            continue
        items.append(_v3_item(label, category, policy))
        seen.add(label)
    bounded, truncated = _bounded_entries(items, policy)
    categories = list(dict.fromkeys(str(item["category"]) for item in bounded))
    return {
        "page_recognized": page_recognized,
        "boundary": boundary,
        "items": bounded,
        "categories": categories,
        "truncated": truncated,
        "visitor_history_disabled": any(
            marker in combined for marker in V3_VISITOR_DISABLED_MARKERS
        ),
    }


def parse_entry_badge(xml_source: str, aliases: Iterable[str]) -> dict[str, Any]:
    """Read only a calibrated entry row's badge; ordinary red content is ignored."""
    alias_values = tuple(aliases)
    for node in _nodes(xml_source):
        descendants = list(node.iter("node"))
        combined = " ".join(_label(value) for value in descendants if _label(value))
        if not any(alias in combined for alias in alias_values):
            continue
        badge_labels = [
            _label(value)
            for value in descendants
            if any(marker in value.get("resource-id", "").lower() for marker in V2_UNREAD_RESOURCE_MARKERS)
            or any(marker in _label(value) for marker in ("未读", "红点", "新消息"))
        ]
        badge_text = " ".join(badge_labels)
        numeric = re.search(r"(?<!\d)(\d{1,3})(?!\d)", badge_text)
        if numeric:
            return {"has_unread": True, "unread_count": int(numeric.group(1)), "indicator": "number"}
        if badge_labels:
            return {"has_unread": True, "unread_count": None, "indicator": "dot"}
    return {"has_unread": False, "unread_count": 0, "indicator": "none"}


def find_v2_filter_title_bounds(
    xml_source: str, aliases: Iterable[str], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Match a calibrated filter title even when Douyin exposes it as non-clickable."""
    alias_values = tuple(aliases)
    for node in _nodes(xml_source):
        bounds = _bounds(node)
        trusted_legacy_id = (
            node.get("resource-id", "").endswith("/2ra")
            or node.get("resource-id", "") == "android:id/text1"
        )
        semantic_title = (
            node.get("class", "") == "android.widget.TextView"
            and any(alias == _label(node) for alias in alias_values)
            and bounds is not None
            and 20 <= bounds[3] - bounds[1] <= 120
            and 40 <= bounds[2] - bounds[0] <= min(width, 520)
        )
        if (
            bounds is not None
            and (trusted_legacy_id or semantic_title)
            and any(alias == _label(node) for alias in alias_values)
            and 0 <= bounds[0] < bounds[2] <= width
            and 40 <= bounds[1] < bounds[3] <= min(height, 320)
        ):
            return bounds
    return None


def visitor_ui_fingerprint(xml_source: str) -> dict[str, Any]:
    rows = [node for node in _nodes(xml_source) if node.get("resource-id", "").endswith("/root_layout")]
    names = [
        _label(node)
        for node in _nodes(xml_source)
        if node.get("resource-id", "").endswith("/3h6") and _label(node)
    ]
    first = names[0] if names else ""
    result: dict[str, Any] = {
        "row_count": len(rows),
        "first_row_hash": hashlib.sha256(first.encode("utf-8")).hexdigest()[:24] if first else "",
    }
    if first:
        result["first_row"] = {"display_name": first}
    return result


def readable_v2_entries(
    xml_source: str,
    policy: InspectionPolicy,
    *,
    section: str,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    """Keep useful visible list text without inventing empty fields."""
    nodes = list(_nodes(xml_source))
    preferred: list[ET.Element] = []
    if section == "private_messages":
        preferred = [
            node for node in nodes
            if node.get("resource-id", "").endswith("/xpu")
        ]
    elif section in {"received_likes", "received_comments", "received_danmaku"}:
        preferred = [
            node for node in nodes
            if node.get("resource-id", "").endswith("/r8y")
        ]
    elif section == "profile_visitors":
        visitor_rows = [
            node for node in nodes
            if node.get("resource-id", "").endswith("/root_layout") and _label(node)
        ]
        preferred = visitor_rows or [
            node for node in nodes
            if node.get("resource-id", "").endswith("/3h6") and _label(node)
        ]

    # v33 exposes one composite accessibility node per visible row. Prefer it
    # over its avatar/name/time children so the receipt contains one readable
    # item per row instead of a noisy accessibility-node dump. Sanitized test
    # fixtures and future versions can still use the semantic fallback below.
    candidates = preferred or [
        node for node in nodes
        if (
            section == "private_messages"
            and node.get("clickable") == "true"
            and ("," in _label(node) or "，" in _label(node))
        )
        or (section == "received_likes" and "赞了" in _label(node))
        or (
            section == "received_comments"
            and ("评论" in _label(node) or "回复:" in _label(node))
        )
        or (
            section == "received_danmaku"
            and "弹幕" in _label(node)
            and not any(marker in _label(node) for marker in V2_END_MARKERS)
        )
        or (
            section == "profile_visitors"
            and ("访客" in _label(node) or "访问过你的主页" in _label(node))
        )
    ]
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in candidates:
        label = _label(node)
        bounds = _bounds(node)
        if not label or label in V2_ENTRY_SKIP_LABELS or label in seen or bounds is None:
            continue
        left, top, right, bottom = bounds
        if left < 0 or right > width or top < round(height * 0.10) or bottom > round(height * 0.92):
            continue
        if any(marker in label for marker in QUICK_ACTION_MARKERS):
            continue
        if "的头像" in label or (
            node not in preferred and label.endswith("按钮") and len(label) <= 20
        ):
            continue
        if any(marker in label for marker in V2_END_MARKERS):
            continue
        parts = [
            _clean(part, policy.field_length)
            for part in re.split(r"[，,|]", label)
            if _clean(part, policy.field_length)
        ]
        entry: dict[str, Any] = {"content": _clean(label, policy.field_length)}
        if parts and len(parts[0]) <= 40 and not any(
            marker in parts[0] for marker in ("赞了", "评论", "访问", "弹幕", "未读")
        ):
            entry["display_name"] = parts[0]
        time_pattern = re.compile(
            r"刚刚|昨天|前天|\d+\s*(?:秒|分钟|小时|天)前|"
            r"\d{1,2}:\d{2}|\d{1,2}[-/]\d{1,2}|\d{4}[/.-]\d{1,2}"
        )
        time_matches = list(time_pattern.finditer(label))
        # Names may contain model numbers such as "尼龙 6/66". The visible
        # row timestamp is the final date-like token in the composite label.
        time_match = time_matches[-1] if time_matches else None
        time_value = time_match.group(0) if time_match else ""
        if time_value:
            entry["time"] = time_value
        if section == "private_messages" and len(parts) > 1:
            preview = _clean(parts[1], policy.field_length)
            preview_time = time_pattern.search(preview)
            if preview_time:
                preview = _clean(preview[: preview_time.start()], policy.field_length)
            if preview and preview != time_value:
                entry["preview"] = preview
        unread = re.search(r"(\d{1,3})\s*条未读", label)
        if unread:
            entry["unread_count"] = int(unread.group(1))
        if section in {"received_comments", "received_danmaku"}:
            entry["subsection"] = section
        values.append(entry)
        seen.add(label)
    bounded, _truncated = _bounded_entries(values, policy)
    return bounded


def visitor_visual_fingerprint(image: Image.Image | None) -> str:
    if image is None:
        return ""
    width, height = image.size
    crop = image.crop((0, round(height * 0.157), round(width * 0.61), round(height * 0.270)))
    values = list(crop.convert("L").resize((9, 8)).get_flattened_data())
    bits = [values[row * 9 + col] > values[row * 9 + col + 1] for row in range(8) for col in range(8)]
    return f"{sum((1 << index) for index, value in enumerate(bits) if value):016x}"


def compare_visitor_baseline(
    baseline: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> str:
    """Return baseline_created, unchanged, changed, or pending_review."""
    if baseline is None:
        return "baseline_created"
    ui_changed = (
        int(current.get("row_count", 0)) > int(baseline.get("row_count", 0))
        or bool(current.get("first_row_hash"))
        and current.get("first_row_hash") != baseline.get("first_row_hash")
    )
    visual_known = bool(current.get("visual_hash") and baseline.get("visual_hash"))
    visual_changed = visual_known and current.get("visual_hash") != baseline.get("visual_hash")
    if not ui_changed and (not visual_known or not visual_changed):
        return "unchanged"
    if ui_changed and visual_changed:
        return "changed"
    return "pending_review"


class EngagementInspector:
    """List-level engagement inspection with one public operation."""

    def __init__(
        self,
        device,
        recorder,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        store=None,
        device_id: str = "",
        task_id: str = "",
        incident_sink: IncidentSink | None = None,
    ) -> None:
        self._device = device
        self._recorder = recorder
        self._sleep = sleep
        self._clock = clock
        self._store = store
        self._device_id = device_id
        self._task_id = task_id
        self._incident_sink = incident_sink
        self._recorded_incidents: set[tuple[str, str]] = set()
        self._v2_artifacts: list[Path] = []
        self._v2_evidence: list[dict[str, Any]] = []
        self._v2_image_hashes: set[str] = set()
        self._v2_calibration: dict[str, Any] = {}
        self._v2_precondition: dict[str, Any] | None = None
        self._evidence_workflow = "v2"
        try:
            width, height = device.window_size()
            self._width, self._height = int(width), int(height)
        except Exception:
            self._width, self._height = PROFILE.width, PROFILE.height

    def inspect(
        self, policy: Mapping[str, Any] | None = None
    ) -> EngagementInspectionResult:
        workflow_version = str(
            (policy or {}).get("inspection_workflow_version", "v1")
        )
        if workflow_version == "v3":
            return self._inspect_v3(policy or {})
        if workflow_version == "v2":
            return self._inspect_v2(policy or {})
        rules = InspectionPolicy.from_mapping(policy)
        sections = {
            "private_messages": _section("failed", reason="not_checked"),
            "received_likes": _section("failed", reason="not_checked"),
            "profile_visitors": _section("failed", reason="not_checked"),
        }
        self._recorder.emit("engagement_inspection_started")
        fatal_reason: str | None = None
        try:
            source = self._prepare_feed()
            badge = parse_message_badge(source)
            message_source = self._open_bottom_tab(source, "消息")
            sections["private_messages"] = {
                **parse_private_messages(message_source, rules),
                "entry_badge": badge,
            }
            self._emit_section("private_messages", sections["private_messages"])
            self._record_section_failure(
                "private_messages", sections["private_messages"], message_source, "v1"
            )

            likes_source = self._open_control(message_source, "互动消息")
            if likes_source is None:
                sections["received_likes"] = _section(
                    "failed", reason="interaction_entry_not_found"
                )
            else:
                sections["received_likes"] = parse_received_likes(likes_source, rules)
            self._emit_section("received_likes", sections["received_likes"])
            self._record_section_failure(
                "received_likes",
                sections["received_likes"],
                likes_source or message_source,
                "v1",
            )

            current = self._dump()
            current_visitors = parse_profile_visitors(current, rules)
            if current_visitors["status"] == "available":
                sections["profile_visitors"] = current_visitors
            else:
                profile_source = self._open_bottom_tab(current, "我")
                visitor_source = self._open_control(profile_source, "主页访客")
                if visitor_source is None:
                    sections["profile_visitors"] = _section(
                        "unavailable", reason="visitor_entry_not_available"
                    )
                else:
                    sections["profile_visitors"] = parse_profile_visitors(
                        visitor_source, rules
                    )
            self._emit_section("profile_visitors", sections["profile_visitors"])
            self._record_section_failure(
                "profile_visitors",
                sections["profile_visitors"],
                visitor_source if "visitor_source" in locals() else current,
                "v1",
            )
        except V2PreconditionMismatch as exc:
            self._v2_precondition = exc.as_dict()
            fatal_reason = exc.code
            self._recorder.emit(
                "engagement_inspection_precondition_changed",
                workflow_version="v2",
                **self._v2_precondition,
            )
            self._record_failure(
                stage="engagement_precondition",
                reason=fatal_reason,
                error_type=type(exc).__name__,
                section="navigation",
                workflow_version="v1",
                outcome="skipped",
                recovery_action="revalidate_inspection_profile",
            )
        except Exception as exc:
            fatal_reason = self._public_reason(exc)
            self._recorder.emit(
                "engagement_inspection_stopped",
                reason=fatal_reason,
                error_type=type(exc).__name__,
            )
            self._record_failure(
                stage="engagement_navigation",
                reason=fatal_reason,
                error_type=type(exc).__name__,
                section="navigation",
                workflow_version="v1",
                outcome="device_fatal",
                recovery_action="restore_home_then_revalidate",
            )

        restored = self._restore_home()
        if not restored:
            self._record_failure(
                stage="engagement_restore",
                reason="home_restore_failed",
                error_type="EngagementRestoreError",
                section="restore",
                workflow_version="v1",
                outcome="device_fatal",
                recovery_action="restore_home_then_revalidate",
            )
        statuses = [section["status"] for section in sections.values()]
        if fatal_reason or not restored:
            status = "failed"
        elif all(value == "available" for value in statuses):
            status = "completed"
        else:
            status = "degraded"
        result: EngagementInspectionResult = {
            "status": status,
            "task_type": "douyin_engagement_inspection",
            "restored": restored,
            "failure_reason": fatal_reason or (None if restored else "home_restore_failed"),
            "side_effect_notice": "打开列表可能改变未读角标；未打开会话或执行对外互动",
            "sections": sections,
            "evidence": [
                f"section:{name}:{section['status']}"
                for name, section in sections.items()
                if section["status"] != "available"
            ],
        }
        self._recorder.emit(
            "engagement_inspection_complete",
            status=status,
            restored=restored,
            section_statuses={name: value["status"] for name, value in sections.items()},
        )
        return result

    def _inspect_v3(self, policy: Mapping[str, Any]) -> EngagementInspectionResult:
        calibration_only = bool(policy.get("_calibration_only", False))
        self._evidence_workflow = "v3"
        started_at = _now_iso()
        rules = InspectionPolicy.from_mapping(policy)
        sections = {
            name: {
                **_section("failed", reason="not_checked"),
                "scroll_count": 0,
                "complete": False,
            }
            for name in ("received_likes", "comment_danmaku", "profile_visitors")
        }
        unified: dict[str, Any] = {
            "status": "failed",
            "complete": False,
            "read_boundary": None,
            "scroll_count": 0,
            "unread_item_count": None,
            "categories": [],
            "items": [],
            "reason_code": "not_checked",
        }
        metadata: dict[str, Any] = {
            "workflow_version": "v3",
            "alert_sources": [],
        }
        fatal_reason: str | None = None
        restored = False
        last_source: str | None = None
        self._recorder.emit("engagement_inspection_started", workflow_version="v3")
        try:
            self._validate_v3_calibration(policy)
            source = self._prepare_feed()
            last_source = source
            self._capture_v2("home-before-message", source)
            message_source = self._open_bottom_tab(source, "消息")
            last_source = message_source
            self._capture_v2("message-entry", message_source)
            if "消息" not in " ".join(_labels(message_source)):
                raise RuntimeError("message_page_not_recognized")
            entry_bounds = find_unified_activity_entry_bounds(
                message_source, self._width, self._height
            )
            if entry_bounds is None:
                raise RuntimeError("interaction_entry_not_found")
            self._click(entry_bounds, "list_entry:互动消息")
            activity_source = self._dump()
            last_source = activity_source
            first = parse_unified_activity_viewport(
                activity_source, rules, width=self._width, height=self._height
            )
            self._capture_v2("unified-activity-entry", activity_source)
            if not first["page_recognized"]:
                raise RuntimeError("unified_activity_page_not_recognized")
            unified, last_source = self._scan_v3_activity(
                activity_source, rules, first=first
            )
            sections = self._v3_sections(unified)
            for name, section in sections.items():
                self._emit_section(name, section)
            if not unified["complete"]:
                fatal_reason = str(
                    unified.get("reason_code") or "list_boundary_not_confirmed"
                )
                self._record_failure(
                    stage="engagement_boundary",
                    reason=fatal_reason,
                    error_type="EngagementBoundaryError",
                    section="unified_activity",
                    workflow_version="v3",
                    outcome="skipped",
                    recovery_action="calibrate_unified_activity_boundary",
                    source=last_source,
                    context={
                        "read_boundary": unified.get("read_boundary"),
                        "scroll_count": unified.get("scroll_count"),
                    },
                )
        except V2PreconditionMismatch as exc:
            self._v2_precondition = exc.as_dict()
            fatal_reason = exc.code
            self._recorder.emit(
                "engagement_inspection_precondition_changed",
                workflow_version="v3",
                **self._v2_precondition,
            )
            self._record_failure(
                stage="engagement_precondition",
                reason=fatal_reason,
                error_type=type(exc).__name__,
                section="navigation",
                workflow_version="v3",
                outcome="skipped",
                recovery_action="revalidate_inspection_profile",
                source=last_source,
                context=self._v2_precondition,
            )
        except Exception as exc:
            fatal_reason = self._public_reason(exc)
            self._recorder.emit(
                "engagement_inspection_stopped",
                workflow_version="v3",
                reason=fatal_reason,
                error_type=type(exc).__name__,
            )
            self._record_failure(
                stage="engagement_navigation",
                reason=fatal_reason,
                error_type=type(exc).__name__,
                section="navigation",
                workflow_version="v3",
                outcome="device_fatal",
                recovery_action="restore_home_then_revalidate",
                source=last_source,
            )
        finally:
            restored = self._restore_home()
            if restored:
                try:
                    self._capture_v2("home-restored", self._dump())
                except Exception:
                    pass
            else:
                self._record_failure(
                    stage="engagement_restore",
                    reason="home_restore_failed",
                    error_type="EngagementRestoreError",
                    section="restore",
                    workflow_version="v3",
                    outcome="device_fatal",
                    recovery_action="restore_home_then_revalidate",
                    source=last_source,
                )

        visitor_disabled = bool(unified.get("visitor_history_disabled"))
        if visitor_disabled and not unified.get("reason_code"):
            unified["reason_code"] = "visitor_history_disabled"
        if fatal_reason or not restored:
            status = "failed"
        elif visitor_disabled:
            status = "degraded"
        elif unified.get("complete"):
            status = "completed"
        else:
            status = "failed"
        if calibration_only:
            metadata.update({"alert_sources": [], "alert_created": False})
        elif unified.get("complete"):
            metadata.update(
                self._record_v2_alert(
                    sections,
                    "changed"
                    if sections["profile_visitors"].get("entries")
                    else "unchanged",
                )
            )
        finished_at = _now_iso()
        if calibration_only:
            metadata.update(
                {"calibration_only": True, "evidence_count": len(self._v2_evidence)}
            )
        else:
            metadata.update(
                self._record_v2_receipt(
                    status=status,
                    restored=restored,
                    sections=sections,
                    metadata=metadata,
                    started_at=started_at,
                    finished_at=finished_at,
                    workflow_version="v3",
                    unified_activity=unified,
                )
            )
        result: dict[str, Any] = {
            "status": status,
            "task_type": "douyin_engagement_inspection",
            "workflow_version": "v3",
            "restored": restored,
            "failure_reason": fatal_reason
            or (None if restored else "home_restore_failed"),
            "side_effect_notice": "打开互动列表可能改变未读状态；未打开具体记录或执行对外互动",
            "unified_activity": unified,
            "sections": sections,
            "evidence": (
                []
                if status == "completed"
                else [f"unified_activity:{unified.get('reason_code') or status}"]
            ),
            "inspection_metadata": metadata,
        }
        if self._v2_precondition:
            result.update(self._v2_precondition)
        self._recorder.emit(
            "engagement_inspection_complete",
            workflow_version="v3",
            status=status,
            restored=restored,
            read_boundary=unified.get("read_boundary"),
            scroll_count=unified.get("scroll_count"),
        )
        return result  # type: ignore[return-value]

    def _validate_v3_calibration(self, policy: Mapping[str, Any]) -> None:
        expected_app = str(policy.get("expected_app_version") or "")
        expected_display = str(policy.get("expected_display_signature") or "")
        calibration = policy.get("inspection_calibration")
        if not expected_app or not expected_display or not isinstance(calibration, Mapping):
            raise RuntimeError("v3_calibration_missing")
        if self._device_id and str(calibration.get("device_id") or "") != self._device_id:
            raise RuntimeError("v3_calibration_device_mismatch")
        if (
            str(calibration.get("app_version") or "") != expected_app
            or str(calibration.get("display_signature") or "") != expected_display
        ):
            raise RuntimeError("v3_calibration_signature_mismatch")
        controls = calibration.get("controls")
        if (
            int(calibration.get("passes") or 0) < 3
            or calibration.get("later_passes_semantically_equal") is not True
            or not isinstance(controls, Mapping)
            or "互动消息" not in list(controls.get("aggregate") or [])
        ):
            raise RuntimeError("v3_calibration_unstable")
        if self._width != 900 or self._height != 1600 or not expected_display.startswith(
            "900x1600x320x0x"
        ):
            raise RuntimeError("v3_standard_display_required")
        self._v2_calibration = dict(calibration)
        app_info = getattr(self._device, "app_info", None)
        info = app_info(DOUYIN_PACKAGE) if callable(app_info) else {}
        actual_app = str(
            (info or {}).get("versionName") or (info or {}).get("version_name") or ""
        )
        actual_display = self._runtime_display_signature()
        if actual_app != expected_app or actual_display != expected_display:
            mismatch = V2PreconditionMismatch(
                "v3_app_version_changed"
                if actual_app != expected_app
                else "v3_display_signature_changed",
                expected_app_version=expected_app,
                actual_app_version=actual_app,
                expected_display_signature=expected_display,
                actual_display_signature=actual_display,
            )
            raise mismatch

    def _scan_v3_activity(
        self,
        source: str,
        policy: InspectionPolicy,
        *,
        first: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        started = self._clock()
        parsed = dict(
            first
            or parse_unified_activity_viewport(
                source, policy, width=self._width, height=self._height
            )
        )
        items = list(parsed.get("items") or [])
        seen = {
            json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items
        }
        visitor_disabled = bool(parsed.get("visitor_history_disabled"))
        boundary = parsed.get("boundary")
        effective_scrolls = 0
        stagnant = 0
        previous_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        while boundary is None and effective_scrolls < 12 and self._clock() - started < 45:
            self._device.swipe(
                round(self._width * 0.5),
                round(self._height * 0.79),
                round(self._width * 0.5),
                round(self._height * 0.30),
                duration=0.35,
            )
            self._sleep(0.55)
            current = self._dump()
            current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
            if current_hash == previous_hash:
                stagnant += 1
            else:
                stagnant = 0
                effective_scrolls += 1
                source = current
                self._capture_v2(
                    f"unified-activity-swipe-{effective_scrolls}", source
                )
                parsed = parse_unified_activity_viewport(
                    source, policy, width=self._width, height=self._height
                )
                if not parsed["page_recognized"]:
                    return (
                        {
                            "status": "failed",
                            "complete": False,
                            "read_boundary": None,
                            "scroll_count": effective_scrolls,
                            "unread_item_count": None,
                            "categories": [],
                            "items": items,
                            "reason_code": "unified_activity_page_changed",
                            "visitor_history_disabled": visitor_disabled,
                        },
                        source,
                    )
                for item in parsed.get("items") or []:
                    key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                    if key not in seen:
                        seen.add(key)
                        items.append(dict(item))
                visitor_disabled = visitor_disabled or bool(
                    parsed.get("visitor_history_disabled")
                )
                boundary = parsed.get("boundary")
            previous_hash = current_hash
            if stagnant >= 2:
                break
        complete = boundary is not None
        read_boundary = (
            "first_screen"
            if boundary == "read" and effective_scrolls == 0
            else "after_scroll"
            if boundary == "read"
            else boundary
        )
        bounded, truncated = _bounded_entries(items, policy)
        categories = list(
            dict.fromkeys(str(item.get("category")) for item in bounded if item.get("category"))
        )
        return (
            {
                "status": "available" if complete else "failed",
                "complete": complete,
                "read_boundary": read_boundary,
                "scroll_count": effective_scrolls,
                "unread_item_count": len(items) if complete else None,
                "categories": categories,
                "items": bounded,
                "truncated": truncated,
                "reason_code": None if complete else "list_boundary_not_confirmed",
                "visitor_history_disabled": visitor_disabled,
            },
            source,
        )

    @staticmethod
    def _v3_sections(unified: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        complete = bool(unified.get("complete"))
        items = [
            dict(item)
            for item in list(unified.get("items") or [])
            if isinstance(item, Mapping)
        ]
        sections: dict[str, dict[str, Any]] = {}
        for name in ("received_likes", "comment_danmaku", "profile_visitors"):
            category_items = [item for item in items if item.get("category") == name]
            visitor_disabled = name == "profile_visitors" and bool(
                unified.get("visitor_history_disabled")
            )
            status = "unavailable" if visitor_disabled else "available" if complete else "failed"
            reason = (
                "visitor_history_disabled"
                if visitor_disabled
                else None
                if complete
                else str(unified.get("reason_code") or "list_boundary_not_confirmed")
            )
            sections[name] = {
                **_section(
                    status,
                    count=len(category_items) if complete else None,
                    unread_count=len(category_items) if complete else None,
                    entries=category_items,
                    truncated=bool(unified.get("truncated")),
                    reason=reason,
                ),
                "entry_badge": {
                    "has_unread": bool(category_items),
                    "unread_count": len(category_items),
                    "indicator": "number" if category_items else "none",
                },
                "scroll_count": int(unified.get("scroll_count") or 0),
                "complete": complete and not visitor_disabled,
                "alert_source": "unified_activity" if category_items and complete else None,
            }
        return sections

    def _inspect_v2(self, policy: Mapping[str, Any]) -> EngagementInspectionResult:
        calibration_only = bool(policy.get("_calibration_only", False))
        started_at = _now_iso()
        rules = InspectionPolicy.from_mapping(policy)
        sections = {
            name: _section("failed", reason="not_checked") for name in V2_SECTION_NAMES
        }
        metadata: dict[str, Any] = {
            "workflow_version": "v2",
            "alert_sources": [],
            "visitor_comparison": "not_checked",
        }
        fatal_reason: str | None = None
        restored = False
        self._recorder.emit("engagement_inspection_started", workflow_version="v2")
        try:
            self._validate_v2_calibration(policy)
            source = self._prepare_feed()
            home_image = self._capture_v2("home-before-message", source)
            message_badge = parse_message_badge(source)
            if not message_badge["has_unread"] and self._bottom_message_badge(home_image):
                message_badge = {"has_unread": True, "unread_count": None, "indicator": "dot"}
            message_source = self._open_bottom_tab(source, "消息")
            self._capture_v2("message-entry", message_source)
            sections["private_messages"] = {
                **_section(
                    "available",
                    unread_count=message_badge.get("unread_count"),
                    entries=readable_v2_entries(
                        message_source,
                        rules,
                        section="private_messages",
                        width=self._width,
                        height=self._height,
                    ),
                    reason=None,
                ),
                "entry_badge": message_badge,
                "scroll_count": 0,
                "complete": True,
                "alert_source": "entry_badge" if message_badge["has_unread"] else None,
            }
            self._emit_section("private_messages", sections["private_messages"])

            likes_supported = self._v2_section_supported("received_likes")
            comments_supported = self._v2_section_supported("received_comments")
            danmaku_supported = self._v2_section_supported("received_danmaku")
            aggregate_source = message_source
            if likes_supported or comments_supported or danmaku_supported:
                aggregate_source = self._open_required_alias(
                    message_source, self._v2_aliases("aggregate", ("互动消息",))
                )
                self._capture_v2("all-messages-entry", aggregate_source)

            if likes_supported:
                likes_menu_badge, likes_source = self._select_v2_filter(
                    aggregate_source,
                    self._v2_aliases("received_likes", V2_FILTER_ALIASES["received_likes"]),
                )
                sections["received_likes"] = self._scan_v2_section(
                    "received-likes",
                    likes_source,
                    likes_menu_badge,
                    section="received_likes",
                    policy=rules,
                    max_scrolls=10,
                )
            else:
                sections["received_likes"] = {
                    **_section("unavailable", reason="calibrated_entry_not_available"),
                    "scroll_count": 0,
                    "complete": False,
                }
            self._emit_section("received_likes", sections["received_likes"])
            self._record_section_failure(
                "received_likes",
                sections["received_likes"],
                likes_source if likes_supported else aggregate_source,
                "v2",
            )

            if comments_supported:
                comments_badge, comments_source = self._select_v2_filter(
                    self._dump(),
                    self._v2_aliases(
                        "received_comments", V2_FILTER_ALIASES["received_comments"]
                    ),
                )
                comments = self._scan_v2_section(
                    "received-comments",
                    comments_source,
                    comments_badge,
                    section="received_comments",
                    policy=rules,
                    max_scrolls=10,
                )
                self._record_section_failure(
                    "received_comments", comments, None, "v2"
                )
            else:
                comments_badge = {"has_unread": False, "unread_count": None, "indicator": "none"}
                comments = {**_section("unavailable", reason="calibrated_entry_not_available"), "scroll_count": 0, "complete": False}
            if danmaku_supported:
                danmaku_badge, danmaku_source = self._select_v2_filter(
                    self._dump(),
                    self._v2_aliases(
                        "received_danmaku", V2_FILTER_ALIASES["received_danmaku"]
                    ),
                )
                danmaku = self._scan_v2_section(
                    "received-danmaku",
                    danmaku_source,
                    danmaku_badge,
                    section="received_danmaku",
                    policy=rules,
                    max_scrolls=10,
                )
                self._record_section_failure(
                    "received_danmaku", danmaku, None, "v2"
                )
            else:
                danmaku_badge = {"has_unread": False, "unread_count": None, "indicator": "none"}
                danmaku = {**_section("unavailable", reason="calibrated_entry_not_available"), "scroll_count": 0, "complete": False}
            comment_unread = self._sum_badges(comments_badge, danmaku_badge)
            comment_has_unread = bool(
                comments_badge.get("has_unread") or danmaku_badge.get("has_unread")
            )
            comment_status = (
                "available"
                if comments["status"] == danmaku["status"] == "available"
                else "unavailable"
                if comments["status"] == danmaku["status"] == "unavailable"
                else "failed"
            )
            sections["comment_danmaku"] = {
                **_section(
                    comment_status,
                    unread_count=comment_unread,
                    count=(
                        int(comments.get("count") or 0) + int(danmaku.get("count") or 0)
                        if comment_status == "available"
                        else None
                    ),
                    entries=[
                        *list(comments.get("entries") or []),
                        *list(danmaku.get("entries") or []),
                    ][: rules.max_items_per_section],
                    truncated=bool(
                        comments.get("truncated")
                        or danmaku.get("truncated")
                        or int(comments.get("count") or 0) + int(danmaku.get("count") or 0)
                        > rules.max_items_per_section
                    ),
                    reason=(
                        None
                        if comment_status == "available"
                        else "calibrated_entry_not_available"
                        if comment_status == "unavailable"
                        else "comment_or_danmaku_incomplete"
                    ),
                ),
                "entry_badge": {
                    "has_unread": comment_has_unread,
                    "unread_count": comment_unread,
                    "indicator": "number" if comment_unread else "dot" if comment_has_unread else "none",
                },
                "scroll_count": int(comments["scroll_count"]) + int(danmaku["scroll_count"]),
                "complete": bool(comments["complete"] and danmaku["complete"]),
                "subsections": {
                    "received_comments": self._public_subsection(comments),
                    "received_danmaku": self._public_subsection(danmaku),
                },
                "alert_source": "entry_badge" if comment_has_unread else None,
            }
            self._emit_section("comment_danmaku", sections["comment_danmaku"])

            bottom_source = self._return_to_bottom_navigation("我")
            profile_source = self._open_bottom_tab(bottom_source, "我")
            self._capture_v2("profile-entry", profile_source)
            visitor_entry_count = self._visitor_entry_count(profile_source)
            if not self._v2_section_supported("profile_visitors"):
                raise RuntimeError("visitor_entry_not_calibrated")
            try:
                visitor_source = self._open_required_alias(
                    profile_source,
                    self._v2_aliases("visitor", ("新访客", "主页访客")),
                )
            except RuntimeError as exc:
                if not str(exc).startswith("required_control_not_found:"):
                    raise
                metadata["visitor_comparison"] = "unavailable"
                sections["profile_visitors"] = {
                    **_section("unavailable", reason="visitor_entry_not_found"),
                    "scroll_count": 0,
                    "complete": False,
                }
                self._emit_section("profile_visitors", sections["profile_visitors"])
                visitor_source = ""
            if not visitor_source:
                raise _SectionHandled("profile_visitors")
            visitor_image = self._capture_v2("visitors-entry", visitor_source)
            visitor_ui = visitor_ui_fingerprint(visitor_source)
            current_visitor = {
                **visitor_ui,
                "visual_hash": visitor_visual_fingerprint(visitor_image),
            }
            visitor_scrolls, visitor_complete, visitor_entries, visitor_truncated = (
                self._scroll_visitor_list(visitor_source, rules)
            )
            comparison, previous_visitor = (
                ("calibration_only", None)
                if calibration_only
                else self._update_visitor_baseline(policy, current_visitor)
            )
            metadata["visitor_comparison"] = comparison
            if previous_visitor:
                metadata["previous_visitor"] = previous_visitor
            metadata["current_visitor"] = current_visitor
            sections["profile_visitors"] = {
                **_section(
                    "available" if visitor_complete else "failed",
                    count=visitor_ui["row_count"],
                    entries=visitor_entries,
                    truncated=visitor_truncated,
                ),
                "scroll_count": visitor_scrolls,
                "complete": visitor_complete,
                "entry_badge": {
                    "has_unread": visitor_entry_count is not None and visitor_entry_count > 0,
                    "unread_count": visitor_entry_count,
                    "indicator": "number" if visitor_entry_count else "none",
                },
                "baseline_status": comparison,
                "alert_source": "visitor_baseline" if comparison == "changed" else None,
            }
            self._emit_section("profile_visitors", sections["profile_visitors"])
            self._record_section_failure(
                "profile_visitors", sections["profile_visitors"], None, "v2"
            )
        except V2PreconditionMismatch as exc:
            self._v2_precondition = exc.as_dict()
            fatal_reason = exc.code
            self._recorder.emit(
                "engagement_inspection_precondition_changed",
                workflow_version="v2",
                **self._v2_precondition,
            )
            self._record_failure(
                stage="engagement_precondition",
                reason=fatal_reason,
                error_type=type(exc).__name__,
                section="navigation",
                workflow_version="v2",
                outcome="skipped",
                recovery_action="revalidate_inspection_profile",
                context=self._v2_precondition,
            )
        except _SectionHandled:
            pass
        except Exception as exc:
            fatal_reason = self._public_reason(exc)
            self._recorder.emit(
                "engagement_inspection_stopped",
                workflow_version="v2",
                reason=fatal_reason,
                error_type=type(exc).__name__,
            )
            self._record_failure(
                stage="engagement_navigation",
                reason=fatal_reason,
                error_type=type(exc).__name__,
                section="navigation",
                workflow_version="v2",
                outcome="device_fatal",
                recovery_action="restore_home_then_revalidate",
            )
        finally:
            restored = self._restore_home()
            if not restored:
                self._record_failure(
                    stage="engagement_restore",
                    reason="home_restore_failed",
                    error_type="EngagementRestoreError",
                    section="restore",
                    workflow_version="v2",
                    outcome="device_fatal",
                    recovery_action="restore_home_then_revalidate",
                )

        statuses = [section["status"] for section in sections.values()]
        if fatal_reason or not restored:
            status = "failed"
        elif all(value == "available" for value in statuses):
            status = "completed"
        else:
            status = "degraded"
        if calibration_only:
            metadata.update({"alert_sources": [], "alert_created": False})
        else:
            metadata.update(
                self._record_v2_alert(
                    sections, str(metadata.get("visitor_comparison") or "not_checked")
                )
            )
        finished_at = _now_iso()
        if calibration_only:
            metadata.update({"calibration_only": True, "evidence_count": len(self._v2_evidence)})
        else:
            metadata.update(
                self._record_v2_receipt(
                    status=status,
                    restored=restored,
                    sections=sections,
                    metadata=metadata,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
        result: dict[str, Any] = {
            "status": status,
            "task_type": "douyin_engagement_inspection",
            "workflow_version": "v2",
            "restored": restored,
            "failure_reason": fatal_reason or (None if restored else "home_restore_failed"),
            "side_effect_notice": "打开列表可能清除未读角标；未打开会话或执行对外互动",
            "sections": sections,
            "evidence": [
                f"section:{name}:{section['status']}"
                for name, section in sections.items()
                if section["status"] != "available"
            ],
            "inspection_metadata": metadata,
        }
        if self._v2_precondition:
            result.update(self._v2_precondition)
        self._recorder.emit(
            "engagement_inspection_complete",
            workflow_version="v2",
            status=status,
            restored=restored,
            section_statuses={name: value["status"] for name, value in sections.items()},
        )
        return result  # type: ignore[return-value]

    def discard_v2_artifacts(self) -> None:
        """Remove temporary successful revalidation screenshots and UI trees."""
        for path in self._v2_artifacts:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._v2_artifacts.clear()

    def _validate_v2_calibration(self, policy: Mapping[str, Any]) -> None:
        expected_app = str(policy.get("expected_app_version") or "")
        expected_display = str(policy.get("expected_display_signature") or "")
        calibration = policy.get("inspection_calibration")
        if not expected_app or not expected_display or not isinstance(calibration, Mapping):
            raise RuntimeError("v2_calibration_missing")
        if self._device_id and str(calibration.get("device_id") or "") != self._device_id:
            raise RuntimeError("v2_calibration_device_mismatch")
        if (
            str(calibration.get("app_version") or "") != expected_app
            or str(calibration.get("display_signature") or "") != expected_display
        ):
            raise RuntimeError("v2_calibration_signature_mismatch")
        if (
            int(calibration.get("passes") or 0) < 3
            or calibration.get("later_passes_semantically_equal") is not True
            or not isinstance(calibration.get("controls"), Mapping)
        ):
            raise RuntimeError("v2_calibration_unstable")
        self._v2_calibration = dict(calibration)
        app_info = getattr(self._device, "app_info", None)
        info = app_info(DOUYIN_PACKAGE) if callable(app_info) else {}
        actual_app = str((info or {}).get("versionName") or (info or {}).get("version_name") or "")
        actual_display = self._runtime_display_signature()
        if actual_app != expected_app or actual_display != expected_display:
            raise V2PreconditionMismatch(
                "v2_app_version_changed"
                if actual_app != expected_app
                else "v2_display_signature_changed",
                expected_app_version=expected_app,
                actual_app_version=actual_app,
                expected_display_signature=expected_display,
                actual_display_signature=actual_display,
            )

    def _runtime_display_signature(self) -> str:
        override = getattr(self._device, "riskflow_display_signature", None)
        if isinstance(override, str):
            return override
        density = self._shell_number("wm density", r"(?:Override|Physical) density:\s*(\d+)")
        navigation = self._shell_value("settings get secure navigation_mode")
        try:
            rotation = int((getattr(self._device, "info", {}) or {}).get("displayRotation", 0))
        except Exception:
            rotation = 0
        if density is None or not navigation:
            return "unavailable"
        return f"{self._width}x{self._height}x{density}x{rotation}x{navigation}"

    def _shell_value(self, command: str) -> str:
        try:
            response = self._device.shell(command)
            output = getattr(response, "output", response)
            return str(output).strip().splitlines()[-1].strip()
        except Exception:
            return ""

    def _v2_aliases(self, key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        controls = self._v2_calibration.get("controls")
        values = controls.get(key) if isinstance(controls, Mapping) else None
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            calibrated = tuple(value.strip() for value in values if value.strip())
            if calibrated:
                return calibrated
        return fallback

    def _v2_section_supported(self, key: str) -> bool:
        sections = self._v2_calibration.get("sections")
        return bool(sections.get(key)) if isinstance(sections, Mapping) else True

    def _shell_number(self, command: str, pattern: str) -> int | None:
        try:
            response = self._device.shell(command)
            output = getattr(response, "output", response)
            match = re.search(pattern, str(output))
            return int(match.group(1)) if match else None
        except Exception:
            return None

    def _capture_v2(self, name: str, source: str) -> Image.Image | None:
        image: Image.Image | None = None
        captured_at = _now_iso()
        screenshot = getattr(self._recorder, "screenshot", None)
        image_path: Path | None = None
        artifact_name = f"inspection-{self._evidence_workflow}-{name}"
        if callable(screenshot):
            image = screenshot(self._device, artifact_name)
            run_dir = getattr(self._recorder, "run_dir", None)
            if run_dir is not None:
                image_path = Path(run_dir) / f"{artifact_name}.png"
                self._v2_artifacts.append(image_path)
        run_dir = getattr(self._recorder, "run_dir", None)
        image_hash = hashlib.sha256(image.tobytes()).hexdigest() if image is not None else ""
        duplicate_image = bool(image_hash and image_hash in self._v2_image_hashes)
        if duplicate_image:
            if image_path is not None:
                image_path.unlink(missing_ok=True)
            self._recorder.emit(
                "engagement_evidence_deduplicated", name=name, image_sha256=image_hash
            )
            image_path = None
            if self._evidence_workflow != "v3":
                return image
        elif image_hash:
            self._v2_image_hashes.add(image_hash)
        if run_dir is not None:
            xml_path = Path(run_dir) / f"{artifact_name}.xml.gz"
            with gzip.open(xml_path, "wt", encoding="utf-8") as handle:
                handle.write(source)
            self._v2_artifacts.append(xml_path)
            evidence_id = hashlib.sha256(
                f"{self._task_id}:{name}:{image_hash}".encode("utf-8")
            ).hexdigest()[:24]
            item: dict[str, Any] = {
                "id": evidence_id,
                "name": name,
                "label": self._evidence_label(name),
                "section": self._evidence_section(name),
                "captured_at": captured_at,
                "ui_tree_name": xml_path.name,
            }
            if image_path is not None and image_path.is_file():
                item["image_name"] = image_path.name
                item["image_sha256"] = image_hash
            elif duplicate_image:
                item["image_sha256"] = image_hash
                item["image_deduplicated"] = True
            self._v2_evidence.append(item)
        self._recorder.emit("engagement_observation", name=name)
        return image

    @staticmethod
    def _evidence_section(name: str) -> str:
        if name.startswith("unified-activity"):
            return "unified_activity"
        if name.startswith(("home", "message", "all-messages", "filter-menu", "wait-control")):
            return "private_messages"
        if name.startswith("received-likes"):
            return "received_likes"
        if name.startswith(("received-comments", "received-danmaku")):
            return "comment_danmaku"
        if name.startswith(("profile", "visitors")):
            return "profile_visitors"
        return "navigation"

    @staticmethod
    def _evidence_label(name: str) -> str:
        labels = {
            "home-before-message": "首页消息角标",
            "message-entry": "消息列表",
            "all-messages-entry": "互动消息入口",
            "unified-activity-entry": "互动消息首屏",
            "home-restored": "返回首页确认",
            "profile-entry": "我的主页",
            "visitors-entry": "主页访客首屏",
        }
        if name in labels:
            return labels[name]
        unified_swipe = re.search(r"unified-activity-swipe-(\d+)", name)
        if unified_swipe:
            return f"互动消息第 {unified_swipe.group(1)} 次有效滑动"
        swipe = re.search(r"(?:received-likes|received-comments|received-danmaku|visitors)-swipe-(\d+)", name)
        if swipe:
            return f"第 {swipe.group(1)} 次有效滑动"
        if name.startswith("filter-menu-"):
            return "互动分类菜单"
        if name.startswith("return-navigation-"):
            return "返回底部导航"
        if name.startswith("wait-control-"):
            return "等待页面控件"
        return name.replace("-", " ")

    def _find_alias_bounds(self, source: str, aliases: Iterable[str]) -> tuple[int, int, int, int] | None:
        for alias in aliases:
            result = find_control_bounds(
                source, alias, self._width, self._height, require_button_label=False
            )
            if result is not None:
                return result
        return None

    def _open_required_alias(self, source: str, aliases: Iterable[str]) -> str:
        alias_values = tuple(aliases)
        bounds = self._find_alias_bounds(source, alias_values)
        for attempt in range(1, 5):
            if bounds is not None:
                break
            self._sleep(0.5)
            source = self._dump()
            self._capture_v2(f"wait-control-{alias_values[0]}-{attempt}", source)
            bounds = self._find_alias_bounds(source, alias_values)
        if bounds is None:
            raise RuntimeError("required_control_not_found:" + "/".join(alias_values))
        self._click(bounds, "v2_control:" + alias_values[0])
        return self._dump()

    def _return_to_bottom_navigation(self, label: str) -> str:
        source = self._dump()
        for attempt in range(4):
            if find_bottom_navigation_bounds(source, label, self._width, self._height) is not None:
                return source
            if attempt == 3:
                break
            self._recorder.emit("engagement_navigation", action="v2_key:back")
            self._device.press("back")
            self._sleep(0.45)
            source = self._dump()
            self._capture_v2(f"return-navigation-{attempt + 1}", source)
        raise RuntimeError(f"bottom_tab_not_found:{label}")

    def _select_v2_filter(
        self, source: str, aliases: tuple[str, ...]
    ) -> tuple[dict[str, Any], str]:
        current_titles = self._v2_aliases(
            "filter_titles",
            (
                "互动消息", "全部消息", "赞与收藏", "赞与其他",
                "收到的评论", "评论弹幕", "收到的弹幕",
            ),
        )
        title_bounds = find_v2_filter_title_bounds(
            source, current_titles, self._width, self._height
        ) or self._find_alias_bounds(source, current_titles)
        if title_bounds is None:
            raise RuntimeError("message_filter_title_not_found")
        self._click(title_bounds, "v2_filter:open")
        menu_source = self._dump()
        target = self._find_alias_bounds(menu_source, aliases)
        if target is None:
            raise RuntimeError("message_filter_not_found:" + "/".join(aliases))
        menu_image = self._capture_v2("filter-menu-" + aliases[0], menu_source)
        badge = parse_entry_badge(menu_source, aliases)
        if not badge["has_unread"] and self._red_badge_in_bounds(menu_image, target):
            badge = {"has_unread": True, "unread_count": None, "indicator": "dot"}
        self._click(target, "v2_filter:" + aliases[0])
        selected = self._dump()
        all_menu_aliases = tuple(
            alias
            for key in ("received_likes", "received_comments", "received_danmaku")
            for alias in self._v2_aliases(key, V2_FILTER_ALIASES[key])
        )
        menu_still_open = sum(alias in selected for alias in all_menu_aliases) >= 2
        title_confirmed = any(title in selected for title in current_titles)
        if menu_still_open or not (any(alias in selected for alias in aliases) or title_confirmed):
            raise RuntimeError("message_filter_page_ambiguous")
        return badge, selected

    def _scan_v2_section(
        self,
        name: str,
        source: str,
        badge: Mapping[str, Any],
        *,
        section: str,
        policy: InspectionPolicy,
        max_scrolls: int,
    ) -> dict[str, Any]:
        image = self._capture_v2(f"{name}-entry", source)
        entries = readable_v2_entries(
            source, policy, section=section, width=self._width, height=self._height
        )
        scrolls = 0
        stagnant = 0
        complete = True
        previous_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        while not any(marker in source for marker in V2_END_MARKERS) and self._localized_red_badge(image):
            if scrolls >= max_scrolls:
                complete = False
                break
            self._device.swipe(
                round(self._width * 0.5), round(self._height * 0.79),
                round(self._width * 0.5), round(self._height * 0.30), duration=0.35,
            )
            self._sleep(0.55)
            source = self._dump()
            scrolls += 1
            image = self._capture_v2(f"{name}-swipe-{scrolls}", source)
            for entry in readable_v2_entries(
                source, policy, section=section, width=self._width, height=self._height
            ):
                if entry not in entries:
                    entries.append(entry)
            current_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
            stagnant = stagnant + 1 if current_hash == previous_hash else 0
            previous_hash = current_hash
            if stagnant >= 2:
                complete = False
                break
        bounded, truncated = _bounded_entries(entries, policy)
        return {
            **_section(
                "available" if complete else "failed",
                count=len(entries),
                unread_count=badge.get("unread_count"),
                entries=bounded,
                truncated=truncated,
                reason=None if complete else "list_incomplete",
            ),
            "entry_badge": dict(badge),
            "scroll_count": scrolls,
            "complete": complete,
            "alert_source": "entry_badge" if badge.get("has_unread") else None,
        }

    def _scroll_visitor_list(
        self, source: str, policy: InspectionPolicy
    ) -> tuple[int, bool, list[dict[str, Any]], bool]:
        previous_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        stagnant = 0
        scrolls = 0
        entries = readable_v2_entries(
            source,
            policy,
            section="profile_visitors",
            width=self._width,
            height=self._height,
        )
        for index in range(1, 6):
            self._device.swipe(
                round(self._width * 0.5), round(self._height * 0.79),
                round(self._width * 0.5), round(self._height * 0.30), duration=0.35,
            )
            self._sleep(0.55)
            source = self._dump()
            self._capture_v2(f"visitors-swipe-{index}", source)
            for entry in readable_v2_entries(
                source,
                policy,
                section="profile_visitors",
                width=self._width,
                height=self._height,
            ):
                if entry not in entries:
                    entries.append(entry)
            current_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
            if current_hash == previous_hash:
                stagnant += 1
            else:
                scrolls += 1
                stagnant = 0
            previous_hash = current_hash
            if stagnant >= 1:
                bounded, truncated = _bounded_entries(entries, policy)
                return scrolls, True, bounded, truncated
        bounded, truncated = _bounded_entries(entries, policy)
        return scrolls, True, bounded, truncated

    @staticmethod
    def _localized_red_badge(image: Image.Image | None) -> bool:
        if image is None:
            return False
        width, height = image.size
        crop = image.crop((round(width * 0.84), round(height * 0.15), round(width * 0.99), round(height * 0.88))).convert("RGB")
        small = crop.resize((max(1, crop.width // 4), max(1, crop.height // 4)))
        red = [(r > 190 and r > g * 1.45 and r > b * 1.35) for r, g, b in small.get_flattened_data()]
        red_count = sum(red)
        return 3 <= red_count <= max(40, len(red) // 12)

    @staticmethod
    def _red_badge_in_bounds(
        image: Image.Image | None, bounds: tuple[int, int, int, int]
    ) -> bool:
        if image is None:
            return False
        left, top, right, bottom = bounds
        width = max(1, right - left)
        crop = image.crop((max(left, right - round(width * 0.30)), top, right, bottom)).convert("RGB")
        red_count = sum(
            1 for r, g, b in crop.get_flattened_data()
            if r > 190 and r > g * 1.45 and r > b * 1.35
        )
        area = crop.width * crop.height
        return 25 <= red_count <= max(4000, area // 5)

    def _bottom_message_badge(self, image: Image.Image | None) -> bool:
        return self._red_badge_in_bounds(
            image,
            (
                round(self._width * 0.60), round(self._height * 0.86),
                round(self._width * 0.80), round(self._height * 0.96),
            ),
        )

    @staticmethod
    def _sum_badges(*badges: Mapping[str, Any]) -> int | None:
        values = [badge.get("unread_count") for badge in badges]
        numeric = [int(value) for value in values if isinstance(value, int)]
        return sum(numeric) if numeric else None

    @staticmethod
    def _public_subsection(section: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "status": section.get("status"),
            "scroll_count": section.get("scroll_count"),
            "complete": bool(section.get("complete")),
        }

    @staticmethod
    def _visitor_entry_count(source: str) -> int | None:
        match = re.search(r"新访客\s*(\d{1,3})", source)
        return int(match.group(1)) if match else None

    def _update_visitor_baseline(
        self, policy: Mapping[str, Any], current: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any] | None]:
        if self._store is None or not self._device_id:
            return "unavailable", None
        baseline = self._store.get_visitor_baseline(self._device_id)
        comparison = compare_visitor_baseline(baseline, current)
        if comparison in {"baseline_created", "unchanged", "changed"}:
            self._store.upsert_visitor_baseline(
                device_id=self._device_id,
                app_version=str(policy["expected_app_version"]),
                display_signature=str(policy["expected_display_signature"]),
                row_count=int(current.get("row_count", 0)),
                first_row_hash=str(current.get("first_row_hash") or ""),
                visual_hash=str(current.get("visual_hash") or ""),
                first_row=(
                    dict(current.get("first_row"))
                    if isinstance(current.get("first_row"), Mapping)
                    else None
                ),
            )
        previous = None
        if baseline is not None:
            previous = {
                key: baseline[key]
                for key in ("row_count", "first_row")
                if key in baseline and baseline[key] not in (None, "", {})
            }
        return comparison, previous

    def _record_v2_alert(
        self, sections: Mapping[str, Mapping[str, Any]], visitor_comparison: str
    ) -> dict[str, Any]:
        sources = [
            name for name, section in sections.items()
            if section.get("alert_source") and (name != "profile_visitors" or visitor_comparison == "changed")
        ]
        if not sources:
            return {"alert_sources": [], "alert_created": False}
        summary_sources: dict[str, Any] = {}
        for name in sources:
            badge = sections[name].get("entry_badge") or {}
            value: dict[str, Any] = {
                "indicator": badge.get("indicator") or "none",
                "complete": bool(sections[name].get("complete", True)),
            }
            if isinstance(badge.get("unread_count"), int) and badge.get("unread_count") > 0:
                value["unread_count"] = int(badge["unread_count"])
            items = [
                dict(item) for item in list(sections[name].get("entries") or [])[:3]
                if isinstance(item, Mapping) and item
            ]
            if items:
                value["items"] = items
            summary_sources[name] = value
        summary = {
            "source_count": len(sources),
            "conclusion": "检测到新互动",
            "evidence_count": len(self._v2_evidence),
            "inspection_id": self._inspection_id(),
            "sources": summary_sources,
        }
        fingerprint_input = {
            "device_id": self._device_id,
            "sources": sources,
            "summary": summary,
            "visitor": visitor_comparison,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if self._store is None or not self._device_id or not self._task_id:
            return {"alert_sources": sources, "alert_created": False}
        alert, created = self._store.record_interaction_alert(
            task_id=self._task_id,
            device_id=self._device_id,
            sources=sources,
            summary=summary,
            fingerprint=fingerprint,
        )
        return {"alert_sources": sources, "alert_created": created, "alert_id": alert["id"]}

    def _record_v2_receipt(
        self,
        *,
        status: str,
        restored: bool,
        sections: Mapping[str, Mapping[str, Any]],
        metadata: Mapping[str, Any],
        started_at: str,
        finished_at: str,
        workflow_version: str = "v2",
        unified_activity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        sources = list(metadata.get("alert_sources") or [])
        visitor_status = str(metadata.get("visitor_comparison") or "not_checked")
        result_kind = (
            "alert"
            if sources
            else "clear"
            if status == "completed"
            and (
                bool(unified_activity and unified_activity.get("complete"))
                if workflow_version == "v3"
                else visitor_status not in {"pending_review", "unavailable", "not_checked"}
            )
            else "incomplete"
        )
        public_sections: dict[str, Any] = {}
        for name, section in sections.items():
            value: dict[str, Any] = {
                "status": section.get("status"),
                "complete": bool(section.get("complete", False)),
                "scroll_count": int(section.get("scroll_count") or 0),
            }
            badge = section.get("entry_badge")
            if isinstance(badge, Mapping) and badge.get("has_unread"):
                value["indicator"] = badge.get("indicator") or "dot"
                if isinstance(badge.get("unread_count"), int) and badge.get("unread_count") > 0:
                    value["unread_count"] = int(badge["unread_count"])
            if isinstance(section.get("count"), int) and section.get("count") > 0:
                value["item_count"] = int(section["count"])
            entries = [
                dict(item) for item in list(section.get("entries") or [])
                if isinstance(item, Mapping) and item
            ]
            if entries:
                value["items"] = entries
            if section.get("reason"):
                value["reason"] = str(section["reason"])
            if name == "profile_visitors" and section.get("baseline_status"):
                value["baseline_status"] = section["baseline_status"]
            public_sections[name] = value
        summary: dict[str, Any] = {
            "conclusion": (
                "检测到新互动"
                if result_kind == "alert"
                else "互动消息已完整检查，无新互动"
                if workflow_version == "v3" and result_kind == "clear"
                else "四个分区已检查，无新互动"
                if result_kind == "clear"
                else "巡检未完整完成"
            ),
            "sections": public_sections,
            "evidence_count": len(self._v2_evidence),
        }
        if sources:
            summary["alert_sources"] = sources
        if workflow_version == "v3" and unified_activity is not None:
            summary["unified_activity"] = {
                key: unified_activity.get(key)
                for key in (
                    "status",
                    "complete",
                    "read_boundary",
                    "scroll_count",
                    "unread_item_count",
                    "categories",
                    "items",
                    "reason_code",
                )
            }
        visitor_change: dict[str, Any] = {}
        for key in ("previous_visitor", "current_visitor"):
            raw = metadata.get(key)
            if not isinstance(raw, Mapping):
                continue
            readable = {
                field: raw[field]
                for field in ("row_count", "first_row")
                if field in raw and raw[field] not in (None, "", {})
            }
            if readable:
                visitor_change[key] = readable
        if visitor_status not in {"", "not_checked"}:
            visitor_change["comparison"] = visitor_status
        if visitor_change:
            summary["visitor_change"] = visitor_change
        run_dir = getattr(self._recorder, "run_dir", None)
        inspection_id = self._inspection_id()
        if self._store is not None and self._task_id and self._device_id and run_dir is not None:
            self._store.record_interaction_inspection(
                inspection_id=inspection_id,
                task_id=self._task_id,
                device_id=self._device_id,
                workflow_version=workflow_version,
                status=status,
                result_kind=result_kind,
                restored=restored,
                summary=summary,
                evidence=self._v2_evidence,
                run_dir=str(run_dir),
                started_at=started_at,
                finished_at=finished_at,
            )
        return {
            "inspection_id": inspection_id,
            "result_kind": result_kind,
            "evidence_count": len(self._v2_evidence),
            "conclusion": summary["conclusion"],
        }

    def _inspection_id(self) -> str:
        if not self._task_id:
            return ""
        return hashlib.sha256(
            f"interaction-inspection:{self._device_id}:{self._task_id}".encode("utf-8")
        ).hexdigest()[:24]

    def _prepare_feed(self) -> str:
        if foreground_package(self._device) != DOUYIN_PACKAGE:
            app_start = getattr(self._device, "app_start", None)
            if not callable(app_start):
                raise RuntimeError("douyin_not_foreground")
            app_start(DOUYIN_PACKAGE, stop=False, wait=True)
            self._sleep(0.8)
        source = self._dump()
        self._guard_page(source)
        if not self._is_home(source):
            restored = self._restore_home()
            if not restored:
                raise RuntimeError("main_feed_not_ready")
            source = self._dump()
        return source

    def _dump(self) -> str:
        if foreground_package(self._device) != DOUYIN_PACKAGE:
            raise RuntimeError("foreground_package_changed")
        source = str(self._device.dump_hierarchy(compressed=True, pretty=False))
        self._guard_page(source)
        return source

    def _guard_page(self, source: str) -> None:
        if any(marker in source for marker in LOGIN_MARKERS):
            raise RuntimeError("login_required")
        if all(marker in source for marker in IDENTITY_BLOCK_MARKERS):
            raise RuntimeError("identity_verification_required")

    def _is_home(self, source: str) -> bool:
        """Recognize the navigation home used by the read-only inspector.

        The inspector only needs a safe launch point from which it can open the
        bottom ``消息`` tab.  It must not borrow the stricter video-feed proof
        used by browsing and mutation actions: that proof intentionally needs
        reaction controls, while the home shell can still be loading or expose
        a sparse accessibility tree.  Keeping this predicate local prevents a
        future feed-safety change from disabling interaction-message reads.
        """
        if any(marker in source for marker in (*LOGIN_MARKERS, *IDENTITY_BLOCK_MARKERS)):
            return False
        if any(marker in source for marker in CONVERSATION_SURFACE_MARKERS):
            return False
        try:
            home_bounds = find_bottom_navigation_bounds(
                source, "首页", self._width, self._height
            )
        except ET.ParseError:
            return False
        return home_bounds is not None and "推荐" in source

    def _open_bottom_tab(self, source: str, label: str) -> str:
        bounds = find_bottom_navigation_bounds(source, label, self._width, self._height)
        if bounds is None:
            raise RuntimeError(f"bottom_tab_not_found:{label}")
        self._click(bounds, f"bottom_tab:{label}")
        return self._dump()

    def _open_control(self, source: str, label: str) -> str | None:
        bounds = find_control_bounds(
            source,
            label,
            self._width,
            self._height,
            require_button_label=False,
        )
        if bounds is None:
            return None
        self._click(bounds, f"list_entry:{label}")
        return self._dump()

    def _click(self, bounds: tuple[int, int, int, int], action: str) -> None:
        left, top, right, bottom = bounds
        self._recorder.emit("engagement_navigation", action=action, bounds=list(bounds))
        self._device.click(round((left + right) / 2), round((top + bottom) / 2))
        self._sleep(0.5)

    def _restore_home(self) -> bool:
        for attempt in range(4):
            try:
                source = self._dump()
                if self._is_home(source):
                    return True
                bounds = (
                    None
                    if any(marker in source for marker in CONVERSATION_SURFACE_MARKERS)
                    else find_bottom_navigation_bounds(
                        source, "首页", self._width, self._height
                    )
                )
                if bounds is not None:
                    self._click(bounds, "bottom_tab:首页")
                    if self._wait_for_home():
                        return True
                elif attempt < 2:
                    self._device.press("back")
                    self._sleep(0.4)
                else:
                    app_start = getattr(self._device, "app_start", None)
                    if callable(app_start):
                        app_start(DOUYIN_PACKAGE, stop=False, wait=True)
                        self._sleep(0.8)
                        if self._wait_for_home():
                            return True
            except Exception:
                if foreground_package(self._device) != DOUYIN_PACKAGE:
                    return False
        return False

    def _wait_for_home(self, attempts: int = 4) -> bool:
        """Poll after app launch so a late-rendering home shell is not rejected."""
        for attempt in range(attempts):
            source = self._dump()
            if self._is_home(source):
                return True
            if attempt + 1 < attempts:
                self._sleep(0.5)
        return False

    def _emit_section(self, name: str, section: Mapping[str, Any]) -> None:
        self._recorder.emit(
            "engagement_section_complete",
            section=name,
            status=section.get("status"),
            count=section.get("count"),
            reason=section.get("reason"),
        )

    def _record_section_failure(
        self,
        name: str,
        section: Mapping[str, Any],
        source: str | None,
        workflow_version: str,
    ) -> None:
        if section.get("status") != "failed":
            return
        reason = str(section.get("reason") or f"{name}_not_recognized")
        self._record_failure(
            stage="engagement_section",
            reason=reason,
            error_type="EngagementSectionError",
            section=name,
            workflow_version=workflow_version,
            outcome="skipped",
            recovery_action="calibrate_engagement_section",
            source=source,
        )

    def _record_failure(
        self,
        *,
        stage: str,
        reason: str,
        error_type: str,
        section: str,
        workflow_version: str,
        outcome: str,
        recovery_action: str,
        source: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        key = (section, reason)
        if key in self._recorded_incidents:
            return
        self._recorded_incidents.add(key)
        record_incident_evidence(
            device=self._device,
            recorder=self._recorder,
            incident_sink=self._incident_sink,
            stage=stage,
            error_type=error_type,
            error_message=reason,
            outcome=outcome,
            recovery_action=recovery_action,
            source=source,
            context={
                "task_type": "douyin_engagement_inspection",
                "workflow_version": workflow_version,
                "inspection_section": section,
                **dict(context or {}),
            },
        )

    @staticmethod
    def _public_reason(exc: Exception) -> str:
        value = _clean(str(exc), 120)
        return value if value else type(exc).__name__
