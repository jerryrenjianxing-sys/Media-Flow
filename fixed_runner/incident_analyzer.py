from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from incident_analysis import analyze_incident
from runtime_layout import RUNTIME_ROOT
from task_store import TaskStore


DEFAULT_DB = RUNTIME_ROOT / "tasks.db"


def _verified_recovery_analysis(incident) -> dict | None:
    recovery = incident.context.get("verified_recovery")
    if (
        incident.outcome != "recovered"
        or not isinstance(recovery, dict)
        or recovery.get("verified") is not True
    ):
        return None
    rule_id = str(recovery.get("rule_id") or incident.error_message or "fixed-rule")
    action = str(recovery.get("action") or incident.recovery_action or "verified recovery")
    return {
        "classification": "navigation_drift",
        "summary": f"固定规则 {rule_id} 已恢复并复验当前页面。",
        "suggested_rule": f"继续使用已验证的 {action} 恢复规则；无需再次调用云模型分析。",
        "confidence": 1.0,
        "risk": "low",
        "auto_applicable": False,
        "prompt_version": "verified-fixed-recovery-v1-2026-09-03",
        "source": "verified_fixed_rule",
    }


def process_one(store: TaskStore) -> bool:
    incident = store.claim_incident_for_analysis()
    if incident is None:
        return False
    try:
        local_analysis = _verified_recovery_analysis(incident)
        if local_analysis is not None:
            analysis = local_analysis
        else:
            analysis = analyze_incident(incident).public_dict()
        store.finish_incident_analysis(
            incident.id,
            status="completed",
            analysis=analysis,
        )
    except Exception as exc:
        no_key = isinstance(exc, RuntimeError) and "No cloud model API key" in str(exc)
        store.finish_incident_analysis(
            incident.id,
            status="failed",
            analysis={
                "summary": (
                    "模型未配置；异常截图和UI结构已经保存。配置模型后可手动重试只读分析。"
                    if no_key
                    else "纠错分析暂时失败；异常证据已经保留，可稍后手动重试。"
                ),
                "error_code": "model_not_configured" if no_key else "analysis_failed",
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:300],
                "retryable": True,
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
    parser = argparse.ArgumentParser(description="MediaFlow read-only incident analyzer")
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
