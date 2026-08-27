from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from comment_ai import analyze_topic


ROOT = Path(__file__).resolve().parent
DEFAULT_MODELS = (
    "google/gemini-3.1-flash-lite",
    "google/gemini-3.7-flash",
    "xiaomi/mimo-v2.5",
)


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
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    summary = {}
    for model in models:
        model_rows = [row for row in rows if row["model"] == model]
        summary[model] = {
            "correct": sum(bool(row["correct"]) for row in model_rows),
            "total": len(model_rows),
            "mean_latency_s": round(sum(float(row["latency_s"]) for row in model_rows) / len(model_rows), 3),
        }
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "summary": summary, "rows": rows}
    output = ROOT / "runtime" / "topic-model-benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
