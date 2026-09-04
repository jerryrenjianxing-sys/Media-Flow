from pathlib import Path
import json
import tempfile
import unittest

from device_profiles import get_device_profile, load_device_profiles, merge_device_probe


class DeviceProfileTest(unittest.TestCase):
    def test_changed_display_signature_disables_old_coordinate_fallback(self) -> None:
        existing = {
            "friendly_name": "华为-01",
            "display": {"width": 1080, "height": 2340, "density": 480, "navigation_mode": "100"},
            "home_fallback": [0.1, 0.91],
            "verified": True,
            "verified_at": "2026-08-26T10:00:00+08:00",
        }
        merged = merge_device_probe(
            "serial-1",
            {
                "manufacturer": "HUAWEI",
                "model": "SEA-AL10",
                "display": {"width": 1080, "height": 2400, "density": 480, "navigation_mode": "100"},
            },
            existing,
            observed_at="2026-08-26T22:00:00+08:00",
        )
        self.assertEqual(merged["friendly_name"], "华为-01")
        self.assertFalse(merged["verified"])
        self.assertIsNone(merged["home_fallback"])

    def test_virtual_navigation_change_keeps_portable_display_profile(self) -> None:
        existing = {
            "display": {"width": 900, "height": 1600, "density": 320, "navigation_mode": "100"},
            "verified": True,
            "home_fallback": [0.1, 0.91],
        }
        merged = merge_device_probe(
            "127.0.0.1:16416",
            {"display": {"width": 900, "height": 1600, "density": 320, "navigation_mode": "2"}},
            existing,
            portable_virtual=True,
        )
        self.assertTrue(merged["verified"])
        self.assertEqual(merged["home_fallback"], [0.1, 0.91])

    def write_profile(self, directory: str, *, width: int = 1080) -> Path:
        path = Path(directory) / "devices.json"
        path.write_text(
            json.dumps(
                {
                    "devices": {
                        "phone-1": {
                            "friendly_name": "测试机-01",
                            "display": {"width": width, "height": 2400, "density": 480},
                            "home_fallback": [0.1, 0.9],
                            "verified": True,
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_verified_profile_returns_scaled_home_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = get_device_profile("phone-1", self.write_profile(directory))
        self.assertIsNotNone(profile)
        self.assertEqual(profile.friendly_name, "测试机-01")
        self.assertEqual(profile.fallback_point(1080, 2400), (108, 2160))

    def test_display_change_disables_old_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = get_device_profile("phone-1", self.write_profile(directory))
        self.assertIsNone(profile.fallback_point(720, 1600))

    def test_invalid_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(load_device_profiles(path), {})


if __name__ == "__main__":
    unittest.main()
