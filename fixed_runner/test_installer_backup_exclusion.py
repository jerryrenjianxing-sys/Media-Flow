"""Recovery artifacts must not be published as source or program resources."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from repair_materials import build_bundle, read_bundle, source_path


class InstallerBackupExclusionTests(unittest.TestCase):
    def test_backup_paths_rejected_even_if_renamed_as_normal_json(self):
        with self.assertRaises(ValueError):
            source_path('fixed_runner/installer-backups/install-1/transaction.json')
        with self.assertRaises(ValueError):
            source_path('fixed_runner/INSTALLER-BACKUPS/profile.json')

    def test_bundle_omits_recovery_directory_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'fixed_runner/installer-backups/old/transaction.json'
            source.parent.mkdir(parents=True)
            source.write_text('{"private":true}', encoding='utf-8')
            (root / 'fixed_runner/example.py').write_text('pass\n', encoding='utf-8')
            result = build_bundle(root, root / 'source.zip', 'fixture')
            _, files = read_bundle(root / 'source.zip')
            self.assertNotIn(source.relative_to(root).as_posix(), files)
            self.assertIn('fixed_runner/example.py', files)
            self.assertTrue(source.exists())
            self.assertFalse(result['contains_runtime_data'])

    def test_release_audit_rejects_recovery_folder(self):
        path = Path(__file__).resolve().parents[1] / 'packaging/verify-electron-payload.py'
        spec = importlib.util.spec_from_file_location('payload_backup_audit', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in module.REQUIRED:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'fixture')
            private = root / 'fixed_runner/installer-backups/snapshot.json'
            private.parent.mkdir(parents=True)
            private.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'recovery data'):
                module.verify(root)


if __name__ == '__main__':
    unittest.main()
