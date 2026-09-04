from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engagement_calibration import calibrate_engagement_v3
from task_store import TaskStore


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
