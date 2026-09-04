from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engagement_preflight import (
    acknowledge_visitor_reminder,
    visitor_reminder_status,
)
from task_store import TaskStore


class EngagementPreflightTest(unittest.TestCase):
    @staticmethod
    def save_virtual(
        store: TaskStore, identity: str, app_data_identity: str = "app-data-a"
    ) -> None:
        store.save_virtual_device(
            {
                "virtual_device_id": "virtual-1",
                "provider": "mumu",
                "provider_instance_id": "1",
                "name": "MediaFlow虚拟机1",
                "state": "ready",
                "recipe": {},
                "provider_snapshot": {"app_data_identity": app_data_identity},
                "adb_endpoint": "127.0.0.1:16416",
                "android_identity": identity,
                "discovery_source": "mediaflow_created",
                "provider_install_id": "install-1",
                "presence_status": "present",
                "profile_status": "ready",
                "standard_status": "standard",
                "managed": True,
                "display_index": 1,
            }
        )

    def test_acknowledgement_is_bound_to_permanent_vm_and_android_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self.save_virtual(store, "android-a")
            before = visitor_reminder_status(store, ["127.0.0.1:16416"])
            self.assertTrue(before["required"])

            after = acknowledge_visitor_reminder(store, ["127.0.0.1:16416"])
            self.assertFalse(after["required"])
            self.assertTrue(after["devices"][0]["acknowledged"])

            self.save_virtual(store, "android-b")
            changed = visitor_reminder_status(store, ["127.0.0.1:16416"])
            self.assertTrue(changed["required"])
            self.assertFalse(changed["devices"][0]["acknowledged"])

    def test_acknowledgement_resets_when_app_data_identity_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self.save_virtual(store, "android-a", "app-data-a")
            acknowledged = acknowledge_visitor_reminder(
                store, ["127.0.0.1:16416"]
            )
            self.assertFalse(acknowledged["required"])

            self.save_virtual(store, "android-a", "app-data-b")
            changed = visitor_reminder_status(store, ["127.0.0.1:16416"])
            self.assertTrue(changed["required"])
            self.assertFalse(changed["devices"][0]["acknowledged"])

    def test_acknowledgement_requires_a_connected_registered_virtual_device(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            with self.assertRaisesRegex(ValueError, "已连接的标准虚拟机"):
                acknowledge_visitor_reminder(store, ["127.0.0.1:16416"])


if __name__ == "__main__":
    unittest.main()
