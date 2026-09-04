from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass


DOUYIN_PACKAGE = "com.ss.android.ugc.aweme"
BOUNDS_PATTERN = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass(frozen=True)
class RecoveryAction:
    action_id: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedRecoveryRule:
    rule_id: str
    version: str
    marker_groups: tuple[tuple[str, ...], ...]
    actions: tuple[RecoveryAction, ...]
    max_attempts: int

    def matches(self, source: str) -> bool:
        return bool(source) and all(
            any(marker in source for marker in group)
            for group in self.marker_groups
        )


MINOR_MODE_RULE = VerifiedRecoveryRule(
    rule_id="douyin-minor-mode-overlay",
    version="1.0.0",
    marker_groups=(
        ("未成年人模式", "青少年模式"),
        ("开启未成年人模式", "开启青少年模式", "未成年人保护", "青少年保护"),
    ),
    actions=(
        RecoveryAction("close_button", ("关闭",)),
        RecoveryAction("do_not_remind", ("不再提醒",)),
    ),
    max_attempts=2,
)

VERIFIED_RECOVERY_RULES = (MINOR_MODE_RULE,)


def match_verified_recovery_rule(source: str) -> VerifiedRecoveryRule | None:
    for rule in VERIFIED_RECOVERY_RULES:
        if rule.matches(source):
            return rule
    return None


def find_action_bounds(
    source: str,
    labels: tuple[str, ...],
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    """Find the largest visible clickable node with an exact semantic label."""
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return None
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in root.iter("node"):
        if node.get("package") != DOUYIN_PACKAGE:
            continue
        if node.get("visible-to-user", "true") != "true":
            continue
        if node.get("clickable") != "true":
            continue
        node_labels = {
            " ".join(node.get("text", "").split()),
            " ".join(node.get("content-desc", "").split()),
        }
        if not any(label in node_labels for label in labels):
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
