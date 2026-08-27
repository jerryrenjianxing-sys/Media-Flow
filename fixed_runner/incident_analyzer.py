from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from incident_analysis import analyze_incident
from task_store import TaskStore


DEFAULT_DB = Path(__file__).resolve().parent / "runtime" / "tasks.db"


def process_one(store: TaskStore) -> bool:
    incident = store.claim_incident_for_analysis()
    if incident is None:
        return False
    try:
        advice = analyze_incident(incident)
        store.finish_incident_analysis(
            incident.id,
            status="completed",
            analysis=advice.public_dict(),
        )
    except Exception as exc:
        store.finish_incident_analysis(
            incident.id,
            status="failed",
            analysis={
                "summary": "纠错分析暂时失败",
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:300],
                "auto_applicable": False,
            },
        )
    return True


def run(store: TaskStore, *, poll_seconds: float = 3.0, once: bool = False) -> int:
    store.requeue_interrupted_incident_analyses()
    while True:
        processed = process_one(store)
        if once:
            return 0
        if not processed:
            time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="RiskFlow read-only incident analyzer")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    result = run(TaskStore(args.db), poll_seconds=args.poll_seconds, once=args.once)
    if args.once:
        print(json.dumps({"ok": True}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
