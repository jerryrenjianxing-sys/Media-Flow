from __future__ import annotations

import unittest

from product_version import distribution_version, source_version


class ProductVersionTests(unittest.TestCase):
    def test_development_stage_is_not_reported_as_release(self) -> None:
        result = distribution_version(
            {
                "version": "0.4.0",
                "channel": "development",
                "display_version": "0.4.0-dev+abc123.dirty",
                "source_revision": "abc123",
                "source_dirty": True,
                "build_identity": "0.4.0-dev+abc123.dirty",
            }
        )
        self.assertEqual(result["channel"], "development")
        self.assertEqual(result["display_version"], "0.4.0-dev+abc123.dirty")

    def test_clean_release_keeps_plain_semver(self) -> None:
        result = distribution_version(
            {"version": "0.4.1", "channel": "release", "source_revision": "abc123"}
        )
        self.assertEqual(result["channel"], "release")
        self.assertEqual(result["display_version"], "0.4.1")

    def test_source_runtime_uses_numbered_development_version(self) -> None:
        result = source_version(
            {"version": "0.4.1", "development_iteration": 1},
            revision="abc123",
            dirty=False,
        )
        self.assertEqual(result["version"], "0.4.1-dev.1")
        self.assertEqual(result["target_version"], "0.4.1")
        self.assertEqual(result["development_iteration"], 1)
        self.assertEqual(result["display_version"], "0.4.1-dev.1+abc123")

    def test_packaged_development_manifest_preserves_target_and_iteration(self) -> None:
        result = distribution_version(
            {
                "version": "0.4.1-dev.1",
                "target_version": "0.4.1",
                "development_iteration": 1,
                "channel": "development",
                "source_revision": "abc123",
            }
        )
        self.assertEqual(result["version"], "0.4.1-dev.1")
        self.assertEqual(result["target_version"], "0.4.1")
        self.assertEqual(result["development_iteration"], 1)
        self.assertEqual(result["display_version"], "0.4.1-dev.1+abc123")


if __name__ == "__main__":
    unittest.main()
