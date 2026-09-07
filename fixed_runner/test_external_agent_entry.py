"""Fallback cannot accidentally restart an embedded engine from old shortcuts."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows launcher contract')
class ExternalAgentEntryTests(unittest.TestCase):
    def test_old_start_entries_stop_before_runtime_or_registration(self):
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'manage-mediaflow-agent.ps1'
            shutil.copyfile(Path(__file__).resolve().parents[1] / target.name, target)
            for action in ('Start', 'Restart', 'Register', 'Run'):
                with self.subTest(action=action):
                    result = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-File', str(target), '-Action', action, '-NoBrowser'], capture_output=True,
                        timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b'external Skill', result.stderr)
                    self.assertEqual(sorted(p.name for p in Path(temp).iterdir()), [target.name])


if __name__ == '__main__':
    unittest.main()
