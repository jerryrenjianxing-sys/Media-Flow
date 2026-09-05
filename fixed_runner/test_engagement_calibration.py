from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engagement_calibration import calibrate_engagement_v3
from engagement_inspection import EngagementInspector
from task_store import TaskStore
from test_engagement_inspection import FakeDevice, V3Device, hierarchy, node
from PIL import Image


class FakeInspector:
    policies: list[dict] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def inspect(self, policy):
        type(self).policies.append(dict(policy))
        return {
            "status": "completed",
            "restored": True,
            "unified_activity": {
                "complete": True,
                "read_boundary": "first_screen",
                "reason_code": None,
            },
            "sections": {
                "received_likes": {"status": "available", "complete": True},
                "comment_danmaku": {"status": "available", "complete": True},
                "profile_visitors": {"status": "available", "complete": True},
            },
        }

    def discard_v2_artifacts(self) -> None:
        return None


class EngagementCalibrationTest(unittest.TestCase):
    def test_real_v3_calibration_failure_retains_paired_incident_before_recovery(self):
        class EvidenceDevice(V3Device):
            def screenshot(self, **kwargs):
                return Image.new("RGB", (900, 1600), "white")

        device = EvidenceDevice([hierarchy(node("未知页面"))])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            with self.assertRaisesRegex(RuntimeError, "unified_activity_page_not_recognized"):
                calibrate_engagement_v3(
                    device=device, store=store, device_id="test-vm",
                    app_version="35.8.0", display_signature=device.riskflow_display_signature,
                    artifacts_root=root, origin_id="test-init",
                    inspector_factory=lambda *args, **kwargs: EngagementInspector(
                        *args, **kwargs, sleep=lambda _: None
                    ),
                )
            incidents = store.list_incidents()
            self.assertEqual(len(incidents), 1)
            incident = incidents[0]
            self.assertEqual(incident.context["workflow_version"], "v3")
            self.assertTrue(Path(incident.screenshot_path).is_file())
            self.assertIn("未知页面", Path(incident.ui_tree_path).read_text(encoding="utf-8"))
            self.assertEqual(device.state, "home")

    def test_incomplete_unified_result_cannot_be_promoted(self):
        class IncompleteInspector(FakeInspector):
            def inspect(self, policy):
                result = super().inspect(policy)
                result["unified_activity"]["complete"] = False
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "v3_semantic_calibration_failed"):
                calibrate_engagement_v3(
                    device=object(), store=TaskStore(root / "tasks.db"),
                    device_id="test-vm", app_version="40.3.0",
                    display_signature="900x1600x320x0xgesture",
                    artifacts_root=root, origin_id="test-init",
                    inspector_factory=IncompleteInspector,
                )

    def test_calibration_dispatches_actual_v3_and_wires_incidents(self) -> None:
        seen = []

        def run_v3(inspector, policy):
            seen.append(inspector._incident_sink)
            return FakeInspector().inspect(policy)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(EngagementInspector, "_inspect_v3", run_v3):
                calibrate_engagement_v3(
                    device=FakeDevice(), store=TaskStore(root / "tasks.db"),
                    device_id="test-vm", app_version="40.3.0",
                    display_signature="900x1600x320x0xgesture",
                    artifacts_root=root, origin_id="test-init",
                    inspector_factory=lambda *args, **kwargs: EngagementInspector(
                        *args, **kwargs, sleep=lambda _: None
                    ),
                )
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(callable(sink) for sink in seen))

    def test_v3_profile_requires_three_matching_action_free_passes(self) -> None:
        FakeInspector.policies = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = calibrate_engagement_v3(
                device=object(),
                store=TaskStore(root / "tasks.db"),
                device_id="127.0.0.1:16416",
                app_version="40.3.0",
                display_signature="900x1600x320x0xgesture",
                artifacts_root=root / "artifacts",
                origin_id="initialization-1",
                inspector_factory=FakeInspector,
            )
        self.assertEqual(len(FakeInspector.policies), 3)
        self.assertTrue(all(item["_calibration_only"] for item in FakeInspector.policies))
        self.assertEqual(profile["passes"], 3)
        self.assertEqual(profile["controls"], {"aggregate": ["互动消息"]})


if __name__ == "__main__":
    unittest.main()
