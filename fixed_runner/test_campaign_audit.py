from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from campaign_audit import audit_campaign


TOPIC = "AI、智能制造与塑料包装"


def write_run(
    root: Path,
    name: str,
    *,
    device: str,
    time: str,
    mode: str,
    matches: int,
    comments: list[dict] | None = None,
    complete: bool = True,
    complete_status: str = "passed",
    comment_policy_enabled: bool = True,
    identity_blocked: bool = False,
    verified_like: bool = False,
    preview_only: bool = False,
) -> Path:
    run_dir = root / name
    run_dir.mkdir()
    events = [
        {
            "time": time,
            "event": "task_start",
            "task_id": name,
            "payload": {
                "device_id": device,
                "video_count": 20,
                "content_mode": mode,
                "topic_prompt": TOPIC,
                "comment_policy_enabled": comment_policy_enabled,
                "preview_only": preview_only,
            },
        }
    ]
    for item in comments or []:
        video = item["video"]
        events.extend(
            [
                {
                    "event": "topic_ai_decision",
                    "video": video,
                    "matched": item.get("matched", True),
                    "relevance": item.get("relevance", "exact"),
                    "safe": item.get("safe", True),
                    "evidence": item.get("evidence", ["画面证据"]),
                },
                {
                    "event": "comment_constraint_decision",
                    "video_index": item.get("policy_video", video),
                    "allowed": item.get("allowed", True),
                },
                {
                    "event": "comment_send_verification",
                    "video": video,
                    "verified": item.get("verified", True),
                },
            ]
        )
        if item.get("screenshot", True):
            (run_dir / f"video-{video}-comment-sent.png").write_bytes(b"png")
    if identity_blocked:
        events.append(
            {
                "time": time,
                "event": "reaction_session_circuit_opened",
                "video": 1,
                "reason": "platform_identity_safety_verification",
                "disabled_actions": ["like", "favorite"],
            }
        )
    if verified_like:
        events.extend(
            [
                {
                    "time": time,
                    "event": "like_state_before",
                    "video": 1,
                    "active": False,
                },
                {
                    "time": time,
                    "event": "like_state_after",
                    "video": 1,
                    "active": True,
                },
            ]
        )
    if complete:
        events.append(
            {
                "time": time,
                "event": "task_complete",
                "status": complete_status,
                "videos_seen": 20,
                "topic_matches": matches,
                "model_valid_response_rate": 1.0,
            }
        )
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in events),
        encoding="utf-8",
    )
    return run_dir


class CampaignAuditTest(unittest.TestCase):
    def test_strict_comments_use_nested_payload_video_index_and_screenshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-search",
                device="device-1",
                time="2026-08-30T08:00:00+08:00",
                mode="search",
                matches=4,
                comments=[
                    {"video": 1},
                    {"video": 2, "screenshot": False},
                    {"video": 3, "safe": False},
                    {"video": 4, "policy_video": 99},
                    {"video": 5, "relevance": "adjacent"},
                ],
            )

            result = audit_campaign(root, topic=TOPIC)

        self.assertEqual(result["devices"][0]["verified_comments"], 1)
        self.assertEqual(result["devices"][0]["comment_evidence_runs"], 1)
        self.assertEqual(result["devices"][0]["natural_rounds"], [])
        self.assertEqual(len(result["devices"][0]["search_rounds"]), 1)

    def test_only_complete_search_twenty_video_rounds_build_streak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, "run-mixed", device="device-1", time="2026-08-30T08:00:00+08:00", mode="mixed", matches=20)
            write_run(root, "run-search-1", device="device-1", time="2026-08-30T09:00:00+08:00", mode="search", matches=6)
            write_run(root, "run-search-2", device="device-1", time="2026-08-30T10:00:00+08:00", mode="search", matches=7)

            result = audit_campaign(root, topic=TOPIC)

        device = result["devices"][0]
        self.assertEqual(device["search_qualifying_streak"], 2)
        self.assertTrue(device["topic_goal_met"])
        self.assertEqual(len(device["search_rounds"]), 2)
        self.assertEqual(len(device["natural_rounds"]), 1)
        self.assertEqual(result["campaign_strategy"], "search_only")

    def test_comment_without_enabled_pre_send_policy_is_not_strict_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-policy-off",
                device="device-1",
                time="2026-08-30T08:00:00+08:00",
                mode="search",
                matches=1,
                comments=[{"video": 1}],
                comment_policy_enabled=False,
            )

            result = audit_campaign(root, topic=TOPIC)

        self.assertEqual(result["devices"][0]["verified_comments"], 0)

    def test_failed_or_below_threshold_latest_search_round_resets_streak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, "run-search-1", device="device-1", time="2026-08-30T09:00:00+08:00", mode="search", matches=8)
            write_run(root, "run-search-2", device="device-1", time="2026-08-30T10:00:00+08:00", mode="search", matches=5)
            write_run(root, "run-search-3", device="device-2", time="2026-08-30T11:00:00+08:00", mode="search", matches=8, complete=False)

            result = audit_campaign(root, topic=TOPIC)

        devices = {item["device_id"]: item for item in result["devices"]}
        self.assertEqual(devices["device-1"]["search_qualifying_streak"], 0)
        self.assertEqual(devices["device-2"]["search_qualifying_streak"], 0)
        self.assertFalse(devices["device-2"]["search_rounds"][0]["eligible"])

    def test_complete_round_with_verified_recovery_counts_as_search_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-recovered",
                device="device-1",
                time="2026-08-30T10:00:00+08:00",
                mode="search",
                matches=7,
                complete_status="passed_with_recovery",
            )

            round_result = audit_campaign(root, topic=TOPIC)["devices"][0][
                "search_rounds"
            ][0]

        self.assertTrue(round_result["completed"])
        self.assertTrue(round_result["eligible"])
        self.assertEqual(round_result["terminal_status"], "passed_with_recovery")
        self.assertEqual(round_result["rate"], 0.35)

    def test_since_limits_campaign_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-old",
                device="device-1",
                time="2026-08-29T23:00:00+08:00",
                mode="search",
                matches=1,
                comments=[{"video": 1}],
            )
            write_run(
                root,
                "run-current",
                device="device-1",
                time="2026-08-30T01:00:00+08:00",
                mode="search",
                matches=1,
                comments=[{"video": 2}],
            )

            result = audit_campaign(
                root,
                topic=TOPIC,
                since="2026-08-30T00:00:00+08:00",
            )

        self.assertEqual(result["devices"][0]["verified_comments"], 1)
        self.assertEqual(result["since"], "2026-08-30T00:00:00+08:00")

    def test_since_does_not_forget_older_identity_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-old-block",
                device="device-1",
                time="2026-08-29T23:00:00+08:00",
                mode="search",
                matches=1,
                identity_blocked=True,
            )
            write_run(
                root,
                "run-current-read-only",
                device="device-1",
                time="2026-08-30T01:00:00+08:00",
                mode="mixed",
                matches=2,
            )

            device = audit_campaign(
                root,
                topic=TOPIC,
                since="2026-08-30T00:00:00+08:00",
                now="2026-08-30T03:01:00+08:00",
            )["devices"][0]

        self.assertTrue(device["platform_identity_verification_blocked"])
        self.assertEqual(device["action_mode"], "read_only")
        self.assertEqual(device["verified_comments"], 0)

    def test_cooldown_exposes_wait_and_next_stage_without_device_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-mixed",
                device="device-1",
                time="2026-08-30T10:00:00+08:00",
                mode="mixed",
                matches=2,
            )

            waiting = audit_campaign(
                root,
                topic=TOPIC,
                now="2026-08-30T11:00:00+08:00",
            )["devices"][0]
            ready = audit_campaign(
                root,
                topic=TOPIC,
                now="2026-08-30T12:00:00+08:00",
            )["devices"][0]

        self.assertEqual(waiting["next_campaign_stage"], "wait")
        self.assertEqual(waiting["cooldown_remaining_seconds"], 3600)
        self.assertEqual(waiting["cooldown_ready_at"], "2026-08-30T12:00:00+08:00")
        self.assertEqual(ready["next_campaign_stage"], "search_conditioning")
        self.assertEqual(ready["cooldown_remaining_seconds"], 0)

    def test_search_round_routes_to_search_again_after_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-search",
                device="device-1",
                time="2026-08-30T10:00:00+08:00",
                mode="search",
                matches=15,
            )

            device = audit_campaign(
                root,
                topic=TOPIC,
                now="2026-08-30T12:01:00+08:00",
            )["devices"][0]

        self.assertEqual(device["next_campaign_stage"], "search_conditioning")

    def test_preview_only_search_round_still_drives_campaign_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-mixed",
                device="device-1",
                time="2026-08-30T10:00:00+08:00",
                mode="mixed",
                matches=2,
            )
            write_run(
                root,
                "run-search-preview-comments",
                device="device-1",
                time="2026-08-30T12:00:00+08:00",
                mode="search",
                matches=15,
                preview_only=True,
            )

            waiting = audit_campaign(
                root,
                topic=TOPIC,
                now="2026-08-30T13:00:00+08:00",
            )["devices"][0]
            ready = audit_campaign(
                root,
                topic=TOPIC,
                now="2026-08-30T14:01:00+08:00",
            )["devices"][0]

        self.assertEqual(waiting["last_content_mode"], "search")
        self.assertEqual(waiting["next_campaign_stage"], "wait")
        self.assertEqual(waiting["cooldown_remaining_seconds"], 3600)
        self.assertEqual(ready["next_campaign_stage"], "search_conditioning")

    def test_identity_verification_block_persists_until_later_verified_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(
                root,
                "run-blocked",
                device="device-1",
                time="2026-08-30T10:00:00+08:00",
                mode="mixed",
                matches=8,
                identity_blocked=True,
            )

            blocked = audit_campaign(
                root,
                topic=TOPIC,
                now="2026-08-30T12:01:00+08:00",
            )["devices"][0]
            write_run(
                root,
                "run-verified",
                device="device-1",
                time="2026-08-30T13:00:00+08:00",
                mode="search",
                matches=10,
                verified_like=True,
            )
            cleared = audit_campaign(
                root,
                topic=TOPIC,
                now="2026-08-30T15:01:00+08:00",
            )["devices"][0]

        self.assertTrue(blocked["platform_identity_verification_blocked"])
        self.assertEqual(blocked["action_mode"], "read_only")
        self.assertFalse(cleared["platform_identity_verification_blocked"])
        self.assertEqual(cleared["action_mode"], "eligible")


if __name__ == "__main__":
    unittest.main()
