from __future__ import annotations

import unittest
from datetime import datetime

from campaign_task_monitor import poll_status, task_snapshot


class CampaignTaskMonitorTest(unittest.TestCase):
    def test_transient_status_timeout_is_reported_without_raising(self) -> None:
        def timed_out_reader(_url: str) -> dict:
            raise TimeoutError("timed out")

        status, error = poll_status(
            "http://127.0.0.1:48138/api/status",
            reader=timed_out_reader,
        )

        self.assertIsNone(status)
        self.assertEqual(error, "TimeoutError: timed out")

    def test_successful_status_poll_is_returned(self) -> None:
        expected = {"tasks": []}

        status, error = poll_status(
            "http://127.0.0.1:48138/api/status",
            reader=lambda _url: expected,
        )

        self.assertIs(status, expected)
        self.assertIsNone(error)

    def test_pending_snapshot_shows_scheduled_time_and_remaining_wait(self) -> None:
        now = datetime.fromisoformat("2026-08-31T05:07:50+08:00")

        snapshot = task_snapshot(
            {
                "device_id": "device-1",
                "status": "pending",
                "not_before": "2026-08-31T06:52:00+08:00",
            },
            "abcdef012345",
            now=now,
        )

        self.assertEqual(snapshot["not_before"], "2026-08-31T06:52:00+08:00")
        self.assertEqual(snapshot["ready_in_seconds"], 6250)

    def test_running_snapshot_does_not_report_a_wait_countdown(self) -> None:
        snapshot = task_snapshot(
            {"device_id": "device-1", "status": "running"},
            "abcdef012345",
        )

        self.assertNotIn("not_before", snapshot)
        self.assertNotIn("ready_in_seconds", snapshot)


if __name__ == "__main__":
    unittest.main()
