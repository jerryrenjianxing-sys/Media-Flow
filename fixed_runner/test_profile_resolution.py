from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from profile_resolution import load_shared_virtual_recipes, resolve_execution_profile


class ProfileResolutionTest(unittest.TestCase):
    def test_shared_recipe_rejects_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valid.json").write_text(json.dumps({"id": "ok", "signature": {}}), encoding="utf-8")
            (root / "bad.json").write_text(json.dumps({"id": "bad", "adb_endpoint": "x"}), encoding="utf-8")
            self.assertEqual([item["id"] for item in load_shared_virtual_recipes(root)], ["ok"])

    def test_physical_device_never_uses_shared_recipe(self) -> None:
        result = resolve_execution_profile(
            device_id="phone-1",
            is_virtual=False,
            runtime={"app_version": "40.3.0", "display": {"width": 1080, "height": 2400}},
            local_profile=None,
        )
        self.assertEqual(result["status"], "requires_verification")
        self.assertEqual(result["source"], "none")

    def test_virtual_shared_recipe_does_not_reject_new_douyin_minor_version(self) -> None:
        recipe = {
            "id": "shared-v3",
            "signature": {
                "display": {"width": 900, "height": 1600, "density": 320},
                "adapter_version": "douyin-adapter-v1",
                "douyin_version_prefixes": ["35."],
            },
        }
        with patch("profile_resolution.load_shared_virtual_recipes", return_value=[recipe]):
            result = resolve_execution_profile(
                device_id="127.0.0.1:16416",
                is_virtual=True,
                runtime={
                    "app_version": "40.3.0",
                    "display": {"width": 900, "height": 1600, "density": 320},
                },
                local_profile=None,
            )
        self.assertEqual(result["source"], "shared_virtual_recipe")
        self.assertEqual(result["status"], "requires_verification")


if __name__ == "__main__":
    unittest.main()
