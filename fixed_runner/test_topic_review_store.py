from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from topic_review_store import TopicReviewStore


class TopicReviewStoreTest(unittest.TestCase):
    def _manifest(self, root: Path) -> Path:
        image = root / "evidence" / "sample.png"
        image.parent.mkdir()
        image.write_bytes(b"png")
        manifest = root / "samples.jsonl"
        sample = {
            "id": "sample-1",
            "image_path": str(image),
            "tags": ["keyword-only"],
            "reference": {"relevance": "exact", "confirmed": False, "note": "candidate"},
            "riskflow": {"relevance": "unrelated", "prompt_version": "p1", "policy_version": "v1"},
        }
        manifest.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
        return manifest

    def test_seed_is_idempotent_and_does_not_confirm_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TopicReviewStore(root / "tasks.db", root)
            manifest = self._manifest(root)
            self.assertEqual(store.seed_manifests([manifest]), 1)
            self.assertEqual(store.seed_manifests([manifest]), 0)
            report = store.evaluation()
        self.assertEqual(report["confirmed_samples"], 0)
        self.assertIsNone(report["agreement_rate"])
        self.assertEqual(report["pending_review"], ["sample-1"])

    def test_human_confirmation_is_separate_and_survives_reseed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TopicReviewStore(root / "tasks.db", root)
            manifest = self._manifest(root)
            store.seed_manifests([manifest])
            store.confirm("sample-1", "unrelated", "人工复核")
            store.seed_manifests([manifest])
            row = store.get("sample-1")
            report = store.evaluation()
        self.assertEqual(row["candidate"]["relevance"], "exact")
        self.assertEqual(row["human_relevance"], "unrelated")
        self.assertEqual(report["confirmed_samples"], 1)
        self.assertEqual(report["agreement_rate"], 1.0)
        self.assertFalse(report["coverage"]["complete"])

    def test_image_path_is_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TopicReviewStore(root / "tasks.db", root)
            manifest = self._manifest(root)
            store.seed_manifests([manifest])
            self.assertTrue(store.image_path("sample-1", [root / "evidence"]).is_file())
            with self.assertRaises(KeyError):
                store.image_path("sample-1", [root / "other"])

    def test_invalid_human_label_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TopicReviewStore(root / "tasks.db", root)
            store.seed_manifests([self._manifest(root)])
            with self.assertRaises(ValueError):
                store.confirm("sample-1", "maybe")


if __name__ == "__main__":
    unittest.main()
