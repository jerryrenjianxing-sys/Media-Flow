from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any


MAX_THEMES = 20
MAX_POOL_ITEMS = 100


def _clean_text(value: Any, *, label: str, maximum: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{label}不能为空")
    if len(text) > maximum:
        raise ValueError(f"{label}最多 {maximum} 个字符")
    return text


def _stable_id(value: Any, prefix: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]", "", str(value or ""))[:80]
    return text or f"{prefix}_{uuid.uuid4().hex[:12]}"


def normalize_comment_pool(value: Any, *, label: str) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label}必须是列表")
    if len(value) > MAX_POOL_ITEMS:
        raise ValueError(f"{label}最多 {MAX_POOL_ITEMS} 条")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        item = {"text": raw} if isinstance(raw, str) else raw
        if not isinstance(item, dict):
            raise ValueError(f"{label}词条格式无效")
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text:
            continue
        if len(text) > 80:
            raise ValueError(f"{label}单条最多 80 个字符")
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "id": _stable_id(item.get("id"), "line"),
                "text": text,
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return normalized


def normalize_content_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("内容计划格式无效")
    themes = raw.get("themes")
    if not isinstance(themes, list) or not 1 <= len(themes) <= MAX_THEMES:
        raise ValueError("内容计划需要 1 到 20 个主题")
    normalized_themes: list[dict[str, Any]] = []
    theme_ids: set[str] = set()
    for index, item in enumerate(themes, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个主题格式无效")
        theme_id = _stable_id(item.get("id"), "theme")
        if theme_id in theme_ids:
            raise ValueError("主题标识不能重复")
        theme_ids.add(theme_id)
        normalized_themes.append(
            {
                "id": theme_id,
                "name": _clean_text(
                    item.get("name"), label=f"第 {index} 个主题名称", maximum=80, required=True
                ),
                "topic_prompt": _clean_text(
                    item.get("topic_prompt"), label=f"第 {index} 个主题判定标准", maximum=800, required=True
                ),
                "search_query": _clean_text(
                    item.get("search_query"), label=f"第 {index} 个搜索词", maximum=80, required=True
                ),
                "comment_template": _clean_text(
                    item.get("comment_template"), label=f"第 {index} 个模板补充", maximum=1000
                ),
                "comment_pool": normalize_comment_pool(
                    item.get("comment_pool", []), label=f"第 {index} 个主题词池"
                ),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    if not any(theme["enabled"] for theme in normalized_themes):
        raise ValueError("内容计划至少需要一个启用主题")
    return {
        "name": _clean_text(raw.get("name"), label="计划名称", maximum=80, required=True),
        "comment_template": _clean_text(
            raw.get("comment_template"), label="全局评论模板", maximum=1000
        ),
        "common_comment_pool": normalize_comment_pool(
            raw.get("common_comment_pool", []), label="通用评论词池"
        ),
        "themes": normalized_themes,
    }


def enabled_themes(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [deepcopy(item) for item in document.get("themes", []) if item.get("enabled")]


def round_snapshot(
    revision: dict[str, Any], round_index: int
) -> dict[str, Any]:
    themes = enabled_themes(revision["document"])
    if not themes:
        raise ValueError("内容计划没有启用主题")
    position = (int(round_index) - 1) % len(themes)
    theme = themes[position]
    return {
        "content_plan_id": revision["plan_id"],
        "content_plan_revision_id": revision.get("revision_id", revision["id"]),
        "content_plan_revision_number": revision["revision_number"],
        "content_plan_name": revision["document"]["name"],
        "theme_queue_index": position + 1,
        "theme_queue_size": len(themes),
        "theme": theme,
        "comment_template": revision["document"].get("comment_template", ""),
        "common_comment_pool": deepcopy(
            revision["document"].get("common_comment_pool", [])
        ),
    }
