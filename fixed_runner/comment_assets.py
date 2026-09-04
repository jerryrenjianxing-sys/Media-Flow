from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommentCandidateSet:
    template: str
    candidates: tuple[dict[str, str], ...]


def _entries(value: Any, source: str, used: set[str]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        candidate_id = str(item.get("id") or "").strip()
        text = " ".join(str(item.get("text") or "").split()).strip()
        if candidate_id and text and candidate_id not in used:
            result.append({"id": candidate_id, "text": text, "source": source})
    return result


def select_comment_assets(
    snapshot: Any,
    *,
    seed: int,
    video_index: int,
    used_candidate_ids: set[str] | None = None,
    limit: int = 5,
) -> CommentCandidateSet:
    if not isinstance(snapshot, dict):
        return CommentCandidateSet("", ())
    theme = snapshot.get("theme") if isinstance(snapshot.get("theme"), dict) else {}
    parts = [
        " ".join(str(snapshot.get("comment_template") or "").split()).strip(),
        " ".join(str(theme.get("comment_template") or "").split()).strip(),
    ]
    used = used_candidate_ids or set()
    theme_entries = _entries(theme.get("comment_pool"), "theme_pool", used)
    common_entries = _entries(snapshot.get("common_comment_pool"), "common_pool", used)
    digest = hashlib.sha256(f"{int(seed)}:{int(video_index)}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    rng.shuffle(theme_entries)
    rng.shuffle(common_entries)
    return CommentCandidateSet(
        "\n".join(part for part in parts if part)[:2000],
        tuple((theme_entries + common_entries)[: max(0, min(int(limit), 5))]),
    )
