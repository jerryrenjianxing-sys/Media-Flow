from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

from comment_ai import CloudModelError, analyze_topic


ROOT = Path(__file__).resolve().parent
DEFAULT_MODELS = (
    "google/gemini-3.1-flash-lite",
    "z-ai/glm-5.3-flash",
)

PRICE_PER_MILLION = {
    "google/gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "z-ai/glm-5.3-flash": {"input": 0.075, "output": 0.25},
}
ESTIMATED_TOKENS_PER_REQUEST = {"input": 1800, "output": 220}


def resolve_image(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> None:
    fixture = json.loads((ROOT / "topic_eval_cases.json").read_text(encoding="utf-8"))
    models = tuple(sys.argv[1:]) or DEFAULT_MODELS
    rows: list[dict[str, object]] = []
    for model in models:
        for case in fixture["cases"]:
            started = time.perf_counter()
            try:
                decision = analyze_topic(
                    resolve_image(case["path"]),
                    fixture["topic"],
                    model=model,
                    base_url=os.environ.get("PHONE_AGENT_BASE_URL", "https://openrouter.ai/api/v1"),
                )
                row = {
                    "model": model,
                    "case": case["id"],
                    "expected": case["expected"],
                    "actual": decision.relevance,
                    "correct": decision.relevance == case["expected"],
                    "safe": decision.safe,
                    "evidence": list(decision.evidence),
                    "reason": decision.reason,
                    "latency_s": round(time.perf_counter() - started, 3),
                }
                if decision.reason.startswith("invalid_model_response"):
                    row["raw_response_preview"] = decision.raw_response[:500]
            except Exception as exc:  # benchmark records failures instead of hiding them
                row = {
                    "model": model,
                    "case": case["id"],
                    "expected": case["expected"],
                    "actual": "error",
                    "correct": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_s": round(time.perf_counter() - started, 3),
                }
                if isinstance(exc, CloudModelError):
                    row["error_kind"] = exc.kind
                    row["status_code"] = exc.status_code
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    summary: dict[str, dict[str, object]] = {}
    for model in models:
        model_rows = [row for row in rows if row["model"] == model]
        successful = [row for row in model_rows if row["actual"] != "error"]
        latencies = sorted(float(row["latency_s"]) for row in model_rows)
        price = PRICE_PER_MILLION.get(model)
        estimated_cost = None
        if price:
            estimated_cost = round(
                len(model_rows)
                * (
                    ESTIMATED_TOKENS_PER_REQUEST["input"] * price["input"]
                    + ESTIMATED_TOKENS_PER_REQUEST["output"] * price["output"]
                )
                / 1_000_000,
                6,
            )
        error_counts: dict[str, int] = {}
        for row in model_rows:
            if row.get("error_kind"):
                key = str(row["error_kind"])
                error_counts[key] = error_counts.get(key, 0) + 1
        summary[model] = {
            "correct": sum(bool(row["correct"]) for row in model_rows),
            "total": len(model_rows),
            "request_success_rate": round(len(successful) / len(model_rows), 4),
            "valid_json_rate": round(len(successful) / len(model_rows), 4),
            "topic_accuracy": round(
                sum(bool(row["correct"]) for row in model_rows) / len(model_rows), 4
            ),
            "p50_latency_s": round(statistics.median(latencies), 3),
            "p95_latency_s": round(
                latencies[min(len(latencies) - 1, max(0, int(len(latencies) * 0.95 + 0.999) - 1))],
                3,
            ),
            "error_counts": error_counts,
            "estimated_cost_usd": estimated_cost,
        }
    agreement_cases = 0
    for case in fixture["cases"]:
        actuals = {
            str(row["actual"])
            for row in rows
            if row["case"] == case["id"] and row["actual"] != "error"
        }
        if len(actuals) == 1 and len(
            [row for row in rows if row["case"] == case["id"] and row["actual"] != "error"]
        ) == len(models):
            agreement_cases += 1
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "read_only_existing_screenshots",
        "sample_count": len(fixture["cases"]),
        "request_count": len(rows),
        "estimated_token_assumption": ESTIMATED_TOKENS_PER_REQUEST,
        "pricing_note": "Configured USD per million token snapshot; cost is an estimate, not a provider invoice.",
        "cross_model_topic_consistency": round(
            agreement_cases / len(fixture["cases"]), 4
        ),
        "summary": summary,
        "rows": rows,
    }
    output = ROOT / "runtime" / "topic-model-benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
