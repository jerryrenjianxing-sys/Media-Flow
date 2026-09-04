from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from incident_evidence import record_incident_evidence


class Device:
    def dump_hierarchy(self, compressed=True, pretty=False):
        return '<hierarchy><node text="当前异常页面" /></hierarchy>'


class Recorder:
    def __init__(self, root: Path, *, fail_screenshot: bool = False) -> None:
        self.run_dir = root
        self.fail_screenshot = fail_screenshot
        self.events: list[tuple[str, dict]] = []

    def screenshot(self, _device, name: str):
        if self.fail_screenshot:
            raise RuntimeError("camera unavailable")
        image = Image.new("RGB", (10, 20), "white")
        image.save(self.run_dir / f"{name}.png")
        return image

    def emit(self, event: str, **payload) -> None:
        self.events.append((event, payload))


class IncidentEvidenceTest(unittest.TestCase):
    def test_partial_capture_still_persists_text_and_ui_tree(self) -> None:
        incidents: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(Path(directory), fail_screenshot=True)
            recorded = record_incident_evidence(
                device=Device(),
                recorder=recorder,
                incident_sink=incidents.append,
                stage="engagement_section",
                error_type="EngagementSectionError",
                error_message="private_message_rows_ambiguous",
                outcome="skipped",
                recovery_action="calibrate_engagement_section",
                context={"inspection_section": "private_messages"},
            )
            self.assertTrue(recorded)
            self.assertIsNone(incidents[0]["screenshot_path"])
            self.assertTrue(Path(incidents[0]["ui_tree_path"]).is_file())
            capture = incidents[0]["context"]["evidence_capture"]
            self.assertEqual(capture["screenshot"], "failed")
            self.assertEqual(capture["ui_tree"], "saved")

    def test_store_failure_is_traced_without_replacing_task_error(self) -> None:
        def broken_sink(_incident: dict) -> None:
            raise RuntimeError("database locked")

        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(Path(directory))
            recorded = record_incident_evidence(
                device=Device(),
                recorder=recorder,
                incident_sink=broken_sink,
                stage="engagement_restore",
                error_type="EngagementRestoreError",
                error_message="home_restore_failed",
                outcome="device_fatal",
                recovery_action="restore_home_then_revalidate",
            )
        self.assertFalse(recorded)
        event, payload = recorder.events[-1]
        self.assertEqual(event, "incident_record_failed")
        self.assertEqual(payload["error_type"], "RuntimeError")
        self.assertEqual(payload["evidence_capture"]["screenshot"], "saved")


if __name__ == "__main__":
    unittest.main()
