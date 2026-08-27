from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from topic_evaluation import RELEVANCE_VALUES, evaluate_topic_samples, load_jsonl


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class TopicReviewStore:
    """Human review ledger. Candidate/model output never becomes ground truth implicitly."""

    def __init__(self, db_path: Path, project_root: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.project_root = (project_root or Path(__file__).resolve().parents[1]).resolve()
        self._initialize()

    @contextmanager
    def connection(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_reviews (
                    sample_id TEXT PRIMARY KEY,
                    image_path TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    riskflow_json TEXT NOT NULL,
                    human_relevance TEXT,
                    human_note TEXT,
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_reviews_confirmed "
                "ON topic_reviews(confirmed_at, sample_id)"
            )

    def seed_manifests(self, paths: Iterable[Path]) -> int:
        inserted = 0
        for manifest in paths:
            if not manifest.is_file():
                continue
            for sample in load_jsonl(manifest):
                sample_id = str(sample.get("id") or "").strip()
                if not sample_id:
                    continue
                image_path = Path(str(sample.get("image_path") or ""))
                if not image_path.is_absolute():
                    image_path = (self.project_root / image_path).resolve()
                timestamp = _now_iso()
                with self.connection() as connection:
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO topic_reviews "
                        "(sample_id, image_path, tags_json, candidate_json, riskflow_json, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            sample_id,
                            str(image_path),
                            json.dumps(sample.get("tags") or [], ensure_ascii=False),
                            json.dumps(sample.get("reference") or {}, ensure_ascii=False),
                            json.dumps(sample.get("riskflow") or {}, ensure_ascii=False),
                            timestamp,
                            timestamp,
                        ),
                    )
                    inserted += cursor.rowcount
        return inserted

    def confirm(self, sample_id: str, relevance: str, note: str = "") -> dict[str, Any]:
        normalized = str(relevance).strip().lower()
        if normalized not in RELEVANCE_VALUES:
            raise ValueError("人工结论必须是 exact、adjacent、unrelated 或 uncertain")
        if len(note) > 500:
            raise ValueError("复核说明不能超过 500 字")
        timestamp = _now_iso()
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE topic_reviews SET human_relevance=?, human_note=?, "
                "confirmed_at=?, updated_at=? WHERE sample_id=?",
                (normalized, note.strip(), timestamp, timestamp, sample_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(sample_id)
        return self.get(sample_id)

    def get(self, sample_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM topic_reviews WHERE sample_id=?", (sample_id,)
            ).fetchone()
        if row is None:
            raise KeyError(sample_id)
        return self._record(row)

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be 1-200 and offset must be non-negative")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM topic_reviews ORDER BY confirmed_at IS NOT NULL, "
                "sample_id LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [self._record(row) for row in rows]

    def image_path(self, sample_id: str, allowed_roots: Iterable[Path]) -> Path:
        candidate = Path(self.get(sample_id)["image_path"]).resolve()
        roots = [Path(root).resolve() for root in allowed_roots]
        if not candidate.is_file() or not any(
            candidate == root or root in candidate.parents for root in roots
        ):
            raise KeyError(sample_id)
        return candidate

    def evaluation(self) -> dict[str, Any]:
        rows = self.list(200)
        samples = []
        for row in rows:
            reference = dict(row["candidate"])
            if row["human_relevance"]:
                reference["relevance"] = row["human_relevance"]
                reference["note"] = row["human_note"]
                reference["confirmed"] = True
            else:
                reference["confirmed"] = False
            samples.append(
                {
                    "id": row["sample_id"],
                    "image_path": row["image_path"],
                    "tags": row["tags"],
                    "reference": reference,
                    "riskflow": row["riskflow"],
                }
            )
        prompt_version = next(
            (str(row["riskflow"].get("prompt_version")) for row in rows if row["riskflow"].get("prompt_version")),
            "unknown",
        )
        policy_version = next(
            (str(row["riskflow"].get("policy_version")) for row in rows if row["riskflow"].get("policy_version")),
            "unknown",
        )
        return evaluate_topic_samples(
            samples, prompt_version=prompt_version, policy_version=policy_version
        )

    def payload(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        evaluation = self.evaluation()
        rows = self.list(limit, offset)
        return {
            "items": rows,
            "total": evaluation["total_samples"],
            "limit": limit,
            "offset": offset,
            "evaluation": evaluation,
        }

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sample_id": row["sample_id"],
            "image_path": row["image_path"],
            "tags": json.loads(row["tags_json"]),
            "candidate": json.loads(row["candidate_json"]),
            "riskflow": json.loads(row["riskflow_json"]),
            "human_relevance": row["human_relevance"],
            "human_note": row["human_note"] or "",
            "confirmed_at": row["confirmed_at"],
        }
