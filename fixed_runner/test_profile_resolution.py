from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
