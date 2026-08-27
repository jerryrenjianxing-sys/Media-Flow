from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from evidence_governance import EvidenceGovernance


class EvidenceGovernanceTest(unittest.TestCase):
    def test_inventory_policy_and_backup_are_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "runtime" / "tasks.db"
            db_path.parent.mkdir()
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("CREATE TABLE stable(value TEXT)")
                connection.execute("INSERT INTO stable VALUES ('kept')")
                connection.commit()
            evidence = root / "artifacts"
            evidence.mkdir()
            source = evidence / "proof.png"
            source.write_bytes(b"evidence")
            governance = EvidenceGovernance(db_path, [evidence], root / "runtime" / "backups")

            inventory = governance.inventory()
            policy = governance.save_policy(45)
            backup = governance.create_backup()

            self.assertEqual(inventory["file_count"], 1)
            self.assertEqual(policy["retention_days"], 45)
            self.assertFalse(policy["auto_delete"])
            self.assertTrue(Path(backup["database_backup"]).is_file())
            self.assertTrue(Path(backup["manifest"]).is_file())
            self.assertEqual(source.read_bytes(), b"evidence")
            with closing(sqlite3.connect(backup["database_backup"])) as connection:
                self.assertEqual(connection.execute("SELECT value FROM stable").fetchone()[0], "kept")

    def test_invalid_retention_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            governance = EvidenceGovernance(root / "tasks.db", [], root / "backups")
            for value in (0, 3651):
                with self.assertRaises(ValueError):
                    governance.save_policy(value)


if __name__ == "__main__":
    unittest.main()
