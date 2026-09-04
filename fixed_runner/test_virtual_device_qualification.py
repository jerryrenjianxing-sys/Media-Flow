from __future__ import annotations

import unittest

from virtual_device_qualification import assess_environment, qualify_virtual_device


class VirtualDeviceQualificationTests(unittest.TestCase):
    def test_only_resolution_and_density_define_machine_standard(self) -> None:
        result = assess_environment(
            {
                "resolution_width.custom": "900",
                "resolution_height.custom": "1600",
                "resolution_dpi.custom": "320",
                "root_permission": "false",
                "window_auto_rotate": "true",
                "android_version": "12",
            }
        )
        self.assertEqual(result["environment_status"], "standard")
        self.assertEqual(result["environment_mismatches"], [])

    def test_nonstandard_display_has_one_direct_remediation(self) -> None:
        result = qualify_virtual_device(
            {
                "managed": True,
                "presence_status": "present",
                "state": "adb_ready",
                "profile_status": "requires_verification",
                "provider_snapshot": {
                    "settings": {
                        "resolution_width.custom": "1080",
                        "resolution_height.custom": "1920",
                        "resolution_dpi.custom": "420",
                    }
                },
            },
            connected=True,
        )
        self.assertEqual(result["environment_status"], "needs_display_fix")
        self.assertTrue(result["task_eligibility"]["screen"])
        self.assertFalse(result["task_eligibility"]["browse"])
        self.assertIn("normalize_display", result["remediations"])

    def test_missing_model_does_not_disable_view_browse_or_engagement(self) -> None:
        result = qualify_virtual_device(
            {
                "managed": True,
                "presence_status": "present",
                "state": "ready",
                "profile_status": "ready",
                "provider_snapshot": {
                    "settings": {
                        "resolution_width.custom": "900",
                        "resolution_height.custom": "1600",
                        "resolution_dpi.custom": "320",
                    }
                },
            },
            connected=True,
            initialization_status="ready",
            model_ready=False,
            verified_capabilities={"main_feed": True, "engagement_v3": True},
        )
        self.assertEqual(result["capabilities"]["adb_view"]["status"], "ready")
        self.assertEqual(result["capabilities"]["browse_home"]["status"], "ready")
        self.assertEqual(result["capabilities"]["engagement_v3"]["status"], "ready")
        self.assertEqual(result["capabilities"]["topic_analysis"]["status"], "unavailable")

    def test_missing_input_or_shared_rule_only_disables_dependent_capabilities(self) -> None:
        result = qualify_virtual_device(
            {
                "managed": True,
                "presence_status": "present",
                "state": "ready",
                "profile_status": "ready",
                "provider_snapshot": {
                    "settings": {
                        "resolution_width.custom": "900",
                        "resolution_height.custom": "1600",
                        "resolution_dpi.custom": "320",
                    }
                },
            },
            connected=True,
            initialization_status="ready",
            model_ready=True,
            verified_capabilities={"main_feed": True, "like": True, "favorite": True},
        )
        self.assertEqual(result["capabilities"]["browse_home"]["status"], "ready")
        self.assertEqual(result["capabilities"]["like_favorite"]["status"], "ready")
        self.assertEqual(result["capabilities"]["search_input"]["status"], "unavailable")
        self.assertEqual(result["capabilities"]["engagement_v3"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
