from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Iterable

from comment_ai import TOPIC_PROMPT_VERSION
from comment_ai import analyze_topic
from topic_policy import resolve_topic_policy


RELEVANCE_VALUES = ("exact", "adjacent", "unrelated", "uncertain")


def _relevance(value: Any) -> str:
    normalized = str(value or "uncertain").strip().lower()
    return normalized if normalized in RELEVANCE_VALUES else "uncertain"


def evaluate_topic_samples(
    samples: Iterable[dict[str, Any]],
    *,
    prompt_version: str = TOPIC_PROMPT_VERSION,
    policy_version: str = "unknown",
) -> dict[str, Any]:
    rows = list(samples)
    confusion = {
        expected: {actual: 0 for actual in RELEVANCE_VALUES}
        for expected in RELEVANCE_VALUES
    }
    pending_review: list[str] = []
    differences: list[dict[str, Any]] = []
    candidate_differences: list[dict[str, Any]] = []
    confirmed = 0
    agreements = 0
    candidate_agreements = 0
    confirmed_relevance: set[str] = set()
    confirmed_hard_negatives: set[str] = set()

    for index, sample in enumerate(rows, start=1):
        sample_id = str(sample.get("id") or f"sample-{index}")
        reference = sample.get("reference") or {}
        riskflow = sample.get("riskflow") or {}
        expected = _relevance(reference.get("relevance"))
        actual = _relevance(riskflow.get("relevance"))
        difference = {
            "id": sample_id,
            "image_path": str(sample.get("image_path") or ""),
            "reference": expected,
            "riskflow": actual,
            "reference_evidence": reference.get("evidence") or [],
            "riskflow_evidence": riskflow.get("evidence") or [],
            "reference_note": str(reference.get("note") or ""),
            "riskflow_reason": str(riskflow.get("reason") or ""),
        }
        if not bool(reference.get("confirmed", False)):
            pending_review.append(sample_id)
            if expected == actual:
                candidate_agreements += 1
            else:
                candidate_differences.append(difference)
            continue
        confirmed += 1
        confirmed_relevance.add(expected)
        raw_tags = sample.get("tags") or []
        if isinstance(raw_tags, list):
            confirmed_hard_negatives.update(
                str(tag) for tag in raw_tags if str(tag) in {"keyword-only", "visual-commerce"}
            )
        confusion[expected][actual] += 1
        if expected == actual:
            agreements += 1
            continue
        differences.append(difference)

    missing_relevance = [
        value for value in RELEVANCE_VALUES if value not in confirmed_relevance
    ]
    required_hard_negatives = ("keyword-only", "visual-commerce")
    missing_hard_negatives = [
        value for value in required_hard_negatives if value not in confirmed_hard_negatives
    ]
    coverage_complete = not missing_relevance and not missing_hard_negatives
    return {
        "prompt_version": prompt_version,
        "policy_version": policy_version,
        "result_status": "complete" if coverage_complete else "provisional",
        "coverage": {
            "complete": coverage_complete,
            "missing_relevance": missing_relevance,
            "missing_hard_negatives": missing_hard_negatives,
        },
        "total_samples": len(rows),
        "confirmed_samples": confirmed,
        "pending_review": pending_review,
        "candidate_agreements": candidate_agreements,
        "candidate_agreement_rate": (
            round(candidate_agreements / len(pending_review), 4)
            if pending_review
            else None
        ),
        "candidate_differences": candidate_differences,
        "agreements": agreements,
        "agreement_rate": round(agreements / confirmed, 4) if confirmed else None,
        "confusion": confusion,
        "differences": differences,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Line {line_number} is not a JSON object")
            samples.append(value)
    return samples


def write_jsonl(path: Path, samples: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")


def attach_riskflow_decisions(
    samples: Iterable[dict[str, Any]],
    *,
    target_topic: str,
    model: str,
    base_url: str,
    analyze=analyze_topic,
) -> list[dict[str, Any]]:
    """Run RiskFlow's current prompt on existing images; never controls a device."""
    policy_version = resolve_topic_policy(target_topic).version_id
    updated: list[dict[str, Any]] = []
    for sample in samples:
        row = copy.deepcopy(sample)
        image_path = Path(str(row.get("image_path") or ""))
        try:
            decision = analyze(
                image_path,
                target_topic,
                model=model,
                base_url=base_url,
            )
            row["riskflow"] = {
                "relevance": _relevance(decision.relevance),
                "topic": decision.topic,
                "evidence": list(decision.evidence),
                "reason": decision.reason,
                "safe": bool(decision.safe),
                "model": model,
                "prompt_version": TOPIC_PROMPT_VERSION,
                "policy_version": policy_version,
            }
        except Exception as exc:
            row["riskflow"] = {
                "relevance": "uncertain",
                "topic": "",
                "evidence": [],
                "reason": f"evaluation_error:{type(exc).__name__}:{exc}",
                "safe": False,
                "model": model,
                "prompt_version": TOPIC_PROMPT_VERSION,
                "policy_version": policy_version,
            }
        updated.append(row)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare confirmed topic references with RiskFlow decisions"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prompt-version", default=TOPIC_PROMPT_VERSION)
    parser.add_argument("--run-model", action="store_true")
    parser.add_argument("--target-topic-file", type=Path)
    parser.add_argument("--model", default="google/gemini-3.1-flash-lite")
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--evaluated-manifest", type=Path)
    args = parser.parse_args()
    samples = load_jsonl(args.manifest)
    prompt_version = args.prompt_version
    policy_version = next(
        (
            str((sample.get("riskflow") or {}).get("policy_version"))
            for sample in samples
            if (sample.get("riskflow") or {}).get("policy_version")
        ),
        "unknown",
    )
    if args.run_model:
        if not args.target_topic_file or not args.evaluated_manifest:
            parser.error(
                "--run-model requires --target-topic-file and --evaluated-manifest"
            )
        target_topic = args.target_topic_file.read_text(encoding="utf-8").strip()
        samples = attach_riskflow_decisions(
            samples,
            target_topic=target_topic,
            model=args.model,
            base_url=args.base_url,
        )
        write_jsonl(args.evaluated_manifest, samples)
        prompt_version = TOPIC_PROMPT_VERSION
        policy_version = resolve_topic_policy(target_topic).version_id
    report = evaluate_topic_samples(
        samples,
        prompt_version=prompt_version,
        policy_version=policy_version,
    )
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
