from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from background_startup import StartupDiagnostics, begin_startup


class BackgroundStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.previous_hook = sys.excepthook
        self.environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'local'),
                                                   'USERPROFILE': str(self.root)}, clear=True)
        self.environment.start()

    def tearDown(self):
        sys.excepthook = self.previous_hook
        self.environment.stop()
        self.temporary.cleanup()

    def profile(self, **changes):
        value = {'schema': 1, 'id': 'acceptance-startup-test', 'acceptance': True,
                 'data_root': str(self.root / 'data'), 'app_root': str(self.root / 'app'),
                 'ui_port': 59301, 'api_port': 59338, 'stream_port': 59339}
        value.update(changes)
        path = self.root / 'profile.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def begin(self):
        profile = self.profile()
        return begin_startup('background_host.py', ['background_host.py', 'run', '--instance-profile', str(profile)])

    def read(self, diagnostics):
        return json.loads(diagnostics.path.read_text(encoding='utf-8'))

    def test_only_direct_run_creates_diagnostics(self):
        for action in ('status', 'request-start', 'request-stop', 'stop', 'tick'):
            self.assertIsNone(begin_startup('background_host.py', ['background_host.py', action]))
        self.assertIsNone(begin_startup('background_host.py', ['background_host.py', 'run', '--help']))
        self.assertIsNone(begin_startup('background_host.py', ['control_api.py', 'run']))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIs(sys.excepthook, self.previous_hook)

    def test_profile_identity_and_attempt_are_persisted(self):
        diagnostics = self.begin()
        value = self.read(diagnostics)
        self.assertEqual(value['instance_id'], 'acceptance-startup-test')
        self.assertEqual(value['phase'], 'imports')
        self.assertEqual(value['state'], 'starting')
        self.assertEqual(value['pid'], os.getpid())
        self.assertEqual(len(value['attempt_id']), 32)
        self.assertIn('+', value['started_at'])
        self.assertTrue(diagnostics.log_path.is_file())

    def test_options_before_action_are_supported(self):
        profile = self.profile()
        diagnostics = begin_startup('background_host.py', ['background_host.py', '--instance-profile', str(profile), 'run'])
        self.assertEqual(self.read(diagnostics)['state'], 'starting')

    def test_equals_profile_matches_runtime_without_inherited_environment(self):
        profile = self.profile()
        diagnostics = begin_startup('background_host.py', ['background_host.py', 'run', '--instance-profile=' + str(profile)])
        self.assertEqual(diagnostics.data_root, self.root / 'data')
        self.assertEqual(self.read(diagnostics)['instance_id'], 'acceptance-startup-test')

    def test_duplicate_or_empty_profile_is_a_configuration_failure(self):
        profile = self.profile()
        for arguments in (['--instance-profile', str(profile), '--instance-profile=' + str(profile)],
                          ['--instance-profile='], ['--instance-profile', '--timeout-seconds', '1']):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                begin_startup('background_host.py', ['background_host.py', 'run', *arguments])
        self.assertFalse((self.root / 'data').exists())

    def test_environment_data_boundary_matches_runtime(self):
        target = self.root / 'explicit-data'
        with patch.dict(os.environ, {'MEDIAFLOW_DATA_ROOT': str(target), 'MEDIAFLOW_INSTANCE_ID': 'acceptance-explicit'}):
            diagnostics = self.begin()
        self.assertEqual(diagnostics.path.parent, target)
        self.assertEqual(self.read(diagnostics)['instance_id'], 'acceptance-explicit')

    def test_source_default_does_not_choose_installed_data(self):
        entry = self.root / 'source/fixed_runner/background_host.py'
        diagnostics = begin_startup(entry, [str(entry), 'run'])
        self.assertEqual(diagnostics.path.parent, self.root / 'source/fixed_runner/runtime')

    def test_failure_retains_phase_but_not_raw_body(self):
        diagnostics = self.begin()
        diagnostics.phase('database')
        try:
            raise RuntimeError('sk-sp-secret test-provider-body user-content')
        except RuntimeError as error:
            diagnostics.fail(error)
        value = self.read(diagnostics)
        self.assertEqual(value['error_code'], 'background_startup_database_failed',
                         diagnostics.log_path.read_text(encoding='utf-8'))
        self.assertEqual(value['state'], 'failed')
        self.assertEqual(value['phase'], 'database')
        log = diagnostics.log_path.read_text(encoding='utf-8')
        self.assertIn('RuntimeError', log)
        self.assertNotIn('sk-sp-secret', log)
        self.assertNotIn('test-provider-body', log)
        self.assertNotIn('user-content', log)

    def test_ready_does_not_claim_api_ready(self):
        diagnostics = self.begin()
        diagnostics.ready()
        value = self.read(diagnostics)
        self.assertEqual(value['state'], 'ready')
        self.assertEqual(value['phase'], 'running')
        self.assertIn('仍需', value['message'])

    def test_status_does_not_overwrite_last_run(self):
        diagnostics = self.begin()
        diagnostics.ready()
        before = diagnostics.path.read_bytes()
        self.assertIsNone(begin_startup('background_host.py', ['background_host.py', 'status']))
        self.assertEqual(diagnostics.path.read_bytes(), before)

    def test_windowless_exception_hook_writes_without_streams(self):
        diagnostics = self.begin()
        diagnostics.phase('heartbeat')
        with patch.object(sys, 'stderr', None), patch.object(sys, 'stdout', None):
            sys.excepthook(OSError, OSError('private path and provider body'), None)
        self.assertEqual(self.read(diagnostics)['error_code'], 'background_startup_heartbeat_failed')
        self.assertNotIn('private path and provider body', diagnostics.log_path.read_text(encoding='utf-8'))

    def test_bad_configuration_fails_in_separate_diagnostic_location(self):
        profile = self.root / 'bad.json'
        profile.write_text('{"secret":"sk-sp-secret",', encoding='utf-8')
        with self.assertRaises(ValueError):
            begin_startup('background_host.py', ['background_host.py', 'run', '--instance-profile', str(profile)])
        paths = list((self.root / 'local/MediaFlow/instances').glob('unknown-*/startup-diagnostics/background-startup.json'))
        self.assertEqual(len(paths), 1)
        path = paths[0]
        value = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(value['error_code'], 'background_startup_configuration_failed')
        self.assertNotEqual(value['instance_id'], 'production')
        self.assertFalse((self.root / 'local/MediaFlow/startup-diagnostics/background-startup.json').exists())
        self.assertNotIn('sk-sp-secret', path.with_suffix('.log').read_text(encoding='utf-8'))

    def test_bad_schema_preserves_known_isolated_identity(self):
        profile = self.profile(schema=999)
        with self.assertRaises(ValueError):
            begin_startup('background_host.py', ['background_host.py', 'run', '--instance-profile', str(profile)])
        path = self.root / 'local/MediaFlow/instances/acceptance-startup-test/startup-diagnostics/background-startup.json'
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['instance_id'], 'acceptance-startup-test')

    def test_unwritable_primary_diagnostics_uses_instance_specific_fallback(self):
        diagnostics = StartupDiagnostics('acceptance-storage-test', self.root / 'primary')
        original = StartupDiagnostics._persist

        def persist(instance, details=None):
            if instance.path.parent == self.root / 'primary':
                raise PermissionError('private error body')
            return original(instance, details)

        with patch.object(StartupDiagnostics, '_persist', persist):
            diagnostics.phase('imports')
        self.assertEqual(diagnostics.path.parent, self.root / 'local/MediaFlow/instances/acceptance-storage-test/startup-diagnostics')
        self.assertTrue(self.read(diagnostics)['diagnostic_storage_fallback'])

    def test_real_host_dependency_failure_is_captured_before_main(self):
        source = Path(__file__).parent
        entry = self.root / 'background_host.py'
        entry.write_text((source / 'background_host.py').read_text(encoding='utf-8'), encoding='utf-8')
        (self.root / 'control_config.py').write_text("raise ImportError('sk-sp-secret upstream body')\n", encoding='utf-8')
        profile = self.profile()
        environment = dict(os.environ, PYTHONPATH=str(source), PYTHONUTF8='1',
                           PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(entry), 'run', '--instance-profile', str(profile)],
                                cwd=self.root, env=environment, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        value = json.loads((self.root / 'data/background-startup.json').read_text(encoding='utf-8'))
        self.assertEqual(value['error_code'], 'background_startup_imports_failed')
        self.assertNotIn(b'sk-sp-secret', result.stderr)
        self.assertNotIn('sk-sp-secret', (self.root / 'data/background-startup.log').read_text(encoding='utf-8'))

    def test_real_host_main_failure_does_not_print_raw_exception(self):
        source = Path(__file__).parent
        entry = self.root / 'background_host.py'
        content = (source / 'background_host.py').read_text(encoding='utf-8')
        injection = ("def _fixture_failure():\n"
                     "    _STARTUP.phase('database')\n"
                     "    raise RuntimeError('sk-sp-secret upstream body')\n"
                     "RuntimeSupervisor = _fixture_failure\n\n")
        content = content.replace('if __name__ == "__main__":', injection + 'if __name__ == "__main__":')
        entry.write_text(content, encoding='utf-8')
        profile = self.profile()
        environment = dict(os.environ, PYTHONPATH=str(source), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(entry), 'run', '--instance-profile', str(profile)],
                                cwd=self.root, env=environment, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        value = json.loads((self.root / 'data/background-startup.json').read_text(encoding='utf-8'))
        self.assertEqual(value['error_code'], 'background_startup_database_failed',
                         (self.root / 'data/background-startup.log').read_text(encoding='utf-8'))
        self.assertNotIn(b'sk-sp-secret', result.stderr)
        self.assertNotIn(b'upstream body', result.stderr)


if __name__ == '__main__':
    unittest.main()
