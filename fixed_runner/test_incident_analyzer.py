from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from incident_analysis import IncidentAdvice
from incident_analyzer import process_one
from task_store import TaskStore


class IncidentAnalyzerTests(unittest.TestCase):
    def test_verified_recovery_is_archived_without_cloud_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            incident_id = store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=None,
                stage="startup_recovery",
                error_type="RecoveredPage",
                error_message="douyin-navigation-drift",
                outcome="recovered",
                recovery_action="back",
                context={
                    "verified_recovery": {
                        "rule_id": "douyin-navigation-drift",
                        "rule_version": "1.0.0",
                        "action": "back",
                        "verified": True,
                    }
                },
            )
            with patch("incident_analyzer.analyze_incident") as analyze:
                self.assertTrue(process_one(store))

            incident = store.get_incident(incident_id)
            self.assertEqual(incident.analysis_status, "completed")
            self.assertEqual(incident.analysis["classification"], "navigation_drift")
            self.assertEqual(incident.analysis["source"], "verified_fixed_rule")
            analyze.assert_not_called()

    def test_process_one_writes_advice_without_changing_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            incident_id = store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=1,
                stage="comment",
                error_type="RuntimeError",
                error_message="empty panel",
                outcome="skipped",
                recovery_action="continue",
            )
            with patch(
                "incident_analyzer.analyze_incident",
                return_value=IncidentAdvice(
                    "empty_content",
                    "评论区为空",
                    "识别空态后跳过本条",
                    0.95,
                    "low",
                ),
            ):
                self.assertTrue(process_one(store))

            incident = store.get_incident(incident_id)
            self.assertEqual(incident.analysis_status, "completed")
            self.assertFalse(incident.analysis["auto_applicable"])
            self.assertEqual(store.get(task_id).status, "pending")

    def test_process_one_contains_model_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            incident_id = store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=1,
                stage="swipe",
                error_type="RuntimeError",
                error_message="navigation changed",
                outcome="recovered",
                recovery_action="back_to_feed",
            )
            with patch(
                "incident_analyzer.analyze_incident",
                side_effect=RuntimeError("provider unavailable"),
            ):
                self.assertTrue(process_one(store))

            incident = store.get_incident(incident_id)
            self.assertEqual(incident.analysis_status, "failed")
            self.assertIn("provider unavailable", incident.analysis["error_message"])
            self.assertEqual(store.get(task_id).status, "pending")

    def test_missing_model_key_preserves_evidence_and_waits_for_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = root / "incident.png"
            screenshot.write_bytes(b"png")
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            incident_id = store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=None,
                stage="engagement_navigation",
                error_type="RuntimeError",
                error_message="main_feed_not_ready",
                outcome="device_fatal",
                recovery_action="restore_home_then_revalidate",
                screenshot_path=str(screenshot),
            )
            with patch(
                "incident_analyzer.analyze_incident",
                side_effect=RuntimeError(
                    "No cloud model API key is available in the environment"
                ),
            ):
                self.assertTrue(process_one(store))
                self.assertFalse(process_one(store))

            incident = store.get_incident(incident_id)
            self.assertEqual(incident.analysis_status, "failed")
            self.assertEqual(incident.analysis["error_code"], "model_not_configured")
            self.assertTrue(incident.analysis["retryable"])
            self.assertEqual(incident.screenshot_path, str(screenshot))
            self.assertEqual(store.retry_failed_incident_analyses(), 1)
            self.assertEqual(store.get_incident(incident_id).analysis_status, "queued")


if __name__ == "__main__":
    unittest.main()
