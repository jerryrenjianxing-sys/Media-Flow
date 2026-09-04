from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import profile_devices


class ProfileDevicesTests(unittest.TestCase):
    def test_adb_commands_are_bounded(self) -> None:
        with patch(
            "profile_devices.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["adb", "devices"], timeout=10),
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "timed out after 10 seconds"):
                profile_devices.adb("devices")

        self.assertEqual(run.call_args.kwargs["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
