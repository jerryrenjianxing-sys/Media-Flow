from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POLICY_DIR = Path(__file__).resolve().parent / "topic_policies"
DEFAULT_POLICY_PATH = POLICY_DIR / "ai-manufacturing-plastics-v1.json"


@dataclass(frozen=True)
class TopicCategory:
    category_id: str
    name: str
    qualifying_subjects: tuple[str, ...]
    inclusion_cues: tuple[str, ...]
    exclusions: tuple[str, ...]


@dataclass(frozen=True)
class TopicPolicy:
    policy_id: str
    version: str
    name: str
    eligible_categories: tuple[TopicCategory, ...]
    adjacent_categories: tuple[str, ...]
    global_exclusions: tuple[str, ...]
    evidence_requirement: str
    source_text: str = ""

    @property
    def version_id(self) -> str:
        return f"{self.policy_id}@{self.version}"


def _required_text(value: Any, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"主题策略字段不能为空: {field}")
    return text


def _text_items(value: Any, field: str, *, required: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"主题策略字段必须是数组: {field}")
    items = tuple(" ".join(str(item).split()) for item in value if " ".join(str(item).split()))
    if required and not items:
        raise ValueError(f"主题策略字段不能为空: {field}")
    return items


def topic_policy_from_dict(value: dict[str, Any]) -> TopicPolicy:
    raw_categories = value.get("eligible_categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ValueError("主题策略至少需要一个允许类别")
    categories: list[TopicCategory] = []
    for index, raw in enumerate(raw_categories, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"主题策略允许类别格式错误: {index}")
        prefix = f"eligible_categories[{index}]"
        categories.append(
            TopicCategory(
                category_id=_required_text(raw.get("id"), f"{prefix}.id"),
                name=_required_text(raw.get("name"), f"{prefix}.name"),
                qualifying_subjects=_text_items(
                    raw.get("qualifying_subjects"), f"{prefix}.qualifying_subjects"
                ),
                inclusion_cues=_text_items(
                    raw.get("inclusion_cues"), f"{prefix}.inclusion_cues"
                ),
                exclusions=_text_items(
                    raw.get("exclusions", []), f"{prefix}.exclusions", required=False
                ),
            )
        )
    return TopicPolicy(
        policy_id=_required_text(value.get("policy_id"), "policy_id"),
        version=_required_text(value.get("version"), "version"),
        name=_required_text(value.get("name"), "name"),
        eligible_categories=tuple(categories),
        adjacent_categories=_text_items(
            value.get("adjacent_categories"), "adjacent_categories"
        ),
        global_exclusions=_text_items(
            value.get("global_exclusions"), "global_exclusions"
        ),
        evidence_requirement=_required_text(
            value.get("evidence_requirement"), "evidence_requirement"
        ),
        source_text=str(value.get("source_text") or "").strip(),
    )


def load_topic_policy(path: Path) -> TopicPolicy:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("主题策略文件必须是 JSON 对象")
    return topic_policy_from_dict(value)


def _normalized_spec(value: str) -> str:
    return "".join(value.split()).replace("；", ";").rstrip(";。")


def _legacy_policy(topic: str) -> TopicPolicy:
    text = topic.strip()
    return TopicPolicy(
        policy_id="legacy-free-text",
        version="1",
        name="自由文本兼容主题",
        eligible_categories=(
            TopicCategory(
                category_id="user-topic",
                name="用户指定主题",
                qualifying_subjects=(text,),
                inclusion_cues=("能直接证明主要画面在讨论、展示或讲解该主题的文字、对象、流程或事件",),
                exclusions=("仅有相似风格、背景物体或偶然关键词，但主体不是该主题",),
            ),
        ),
        adjacent_categories=("与主题处于同一宽泛领域，但没有直接满足用户描述的内容",),
        global_exclusions=(
            "关键词只出现在用户名、评论、搜索建议、话题标签或背景装饰",
            "广告、直播、纯商品推销、政治、悲剧、未成年人或其他安全阻断内容",
            "画面证据不足、主体不可辨认或需要猜测画外音和前后帧",
        ),
        evidence_requirement="至少一项来自当前主要画面的可核对文字、对象、流程或事件证据",
        source_text=text,
    )


def resolve_topic_policy(topic_specification: str) -> TopicPolicy:
    text = str(topic_specification or "").strip()
    if not text:
        raise ValueError("主题不能为空")
    default = load_topic_policy(DEFAULT_POLICY_PATH)
    aliases = {default.policy_id, default.version_id, default.name}
    if text in aliases or (
        default.source_text
        and _normalized_spec(text) == _normalized_spec(default.source_text)
    ):
        return default
    return _legacy_policy(text)


def compile_topic_policy(policy: TopicPolicy) -> str:
    lines = [
        f"POLICY: {policy.version_id}",
        f"NAME: {policy.name}",
        "DECISION STANDARD: 关键词只是纳入线索，不是字符串命中规则。exact 必须同时满足："
        "主要画面语义属于允许类别、至少一项具体可核对证据、未命中明确排除或安全阻断。",
        "ELIGIBLE CATEGORIES:",
    ]
    for index, category in enumerate(policy.eligible_categories, start=1):
        lines.append(f"{index}. [{category.category_id}] {category.name}")
        lines.append("   主体范围: " + "；".join(category.qualifying_subjects))
        lines.append("   纳入线索: " + "；".join(category.inclusion_cues))
        if category.exclusions:
            lines.append("   类内排除: " + "；".join(category.exclusions))
    lines.extend(
        [
            "ADJACENT, NOT EXACT:",
            *[f"- {item}" for item in policy.adjacent_categories],
            "明确排除（优先于纳入线索）:",
            *[f"- {item}" for item in policy.global_exclusions],
            f"MINIMUM EVIDENCE: {policy.evidence_requirement}",
            "OUTPUT BOUNDARY: 证据不足返回 uncertain；同领域但不直接满足返回 adjacent；"
            "明确无关返回 unrelated。",
        ]
    )
    return "\n".join(lines)
