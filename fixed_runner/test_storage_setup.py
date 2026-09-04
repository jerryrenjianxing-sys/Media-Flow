from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import storage_setup


class StorageSetupTests(unittest.TestCase):
    def test_rejects_disk_root_and_application_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "app"
            app.mkdir()
            self.assertFalse(storage_setup.validate_data_root(app, app_root=app)["valid"])

    def test_prepare_copies_and_verifies_legacy_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "Local"
            legacy = local / "RiskFlow" / "data"
            legacy.mkdir(parents=True)
            database = legacy / "tasks.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE proof(value TEXT)")
                connection.execute("INSERT INTO proof VALUES ('kept')")
                connection.commit()
            target = local / "MediaFlow" / "data"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), patch(
                "storage_setup._drive_type", return_value=None
            ):
                result = storage_setup.prepare_data_root(target)
                payload = json.loads((local / "MediaFlow" / "bootstrap.json").read_text(encoding="utf-8"))
            self.assertTrue(result["migrated"])
            self.assertTrue(database.is_file())
            self.assertTrue((target / "tasks.db").is_file())
            self.assertEqual(payload["data_root"], str(target.resolve()))

    def test_migration_refuses_to_merge_into_nonempty_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "Local"
            legacy = local / "RiskFlow" / "data"
            target = local / "MediaFlow" / "data"
            legacy.mkdir(parents=True)
            target.mkdir(parents=True)
            (legacy / "source.txt").write_text("source", encoding="utf-8")
            existing = target / "existing.txt"
            existing.write_text("existing", encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), patch(
                "storage_setup._drive_type", return_value=None
            ):
                with self.assertRaisesRegex(RuntimeError, "目标数据目录已有文件"):
                    storage_setup.prepare_data_root(target)
            self.assertEqual(existing.read_text(encoding="utf-8"), "existing")
            self.assertTrue((legacy / "source.txt").is_file())

    def test_scheduled_migration_is_applied_once_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "Local"
            source = Path(directory) / "current-data"
            target = Path(directory) / "new-data"
            source.mkdir(parents=True)
            database = source / "tasks.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE proof(value TEXT)")
                connection.execute("INSERT INTO proof VALUES ('preserved')")
                connection.commit()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), patch(
                "storage_setup._drive_type", return_value=None
            ):
                storage_setup.write_bootstrap(source)
                scheduled = storage_setup.schedule_data_root(target)
                result = storage_setup.apply_pending_migration()
                status = storage_setup.storage_status()
                second = storage_setup.apply_pending_migration()
            self.assertTrue(scheduled["scheduled"])
            self.assertTrue(result["applied"])
            self.assertEqual(status["data_root"], str(target.resolve()))
            self.assertTrue(database.is_file())
            self.assertTrue((target / "tasks.db").is_file())
            self.assertFalse(second["applied"])


if __name__ == "__main__":
    unittest.main()
