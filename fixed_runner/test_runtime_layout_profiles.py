"""Profile arguments must resolve identically in normal Task Scheduler context."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RuntimeLayoutProfileArgumentsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = self.root / 'instance profile.json'
        self.payload = {
            'schema': 1, 'id': 'acceptance-profile-arguments', 'acceptance': True,
            'app_root': str(self.root / 'app'), 'data_root': str(self.root / 'data'),
            'ui_port': 59301, 'api_port': 59338, 'stream_port': 59339,
        }
        self.profile.write_text(json.dumps(self.payload), encoding='utf-8')

    def tearDown(self):
        self.temporary.cleanup()

    def import_layout(self, arguments, *, profile_environment=None):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('MEDIAFLOW_', 'RISKFLOW_'))}
        environment['LOCALAPPDATA'] = str(self.root / 'local')
        if profile_environment is not None:
            environment['MEDIAFLOW_INSTANCE_PROFILE'] = str(profile_environment)
        source = (
            'import sys,json; '
            f'sys.path.insert(0,{str(Path(__file__).resolve().parent)!r}); '
            f'sys.argv={list(arguments)!r}; '
            'import runtime_layout as r; '
            'print(json.dumps({"id":r.INSTANCE_ID,"data":str(r.DATA_ROOT),'
            '"app":str(r.APP_ROOT),"profile":r.INSTANCE_PROFILE_PATH}))'
        )
        return subprocess.run([sys.executable, '-I', '-B', '-c', source], env=environment,
                              text=True, encoding='utf-8', capture_output=True,
                              timeout=15, check=False)

    def assert_selected(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['id'], self.payload['id'])
        self.assertEqual(Path(value['data']), self.root / 'data')
        self.assertEqual(Path(value['app']), self.root / 'app')
        self.assertEqual(Path(value['profile']), self.profile)
        self.assertFalse((self.root / 'data').exists(), 'Import unexpectedly created a data store')

    def test_split_argument_without_inherited_environment(self):
        self.assert_selected(self.import_layout(['background_host.py', 'run', '--instance-profile', str(self.profile)]))

    def test_equals_argument_without_inherited_environment(self):
        self.assert_selected(self.import_layout(['background_host.py', 'run', '--instance-profile=' + str(self.profile)]))

    def test_explicit_argument_keeps_precedence_over_stale_environment(self):
        self.assert_selected(self.import_layout(['background_host.py', 'run', '--instance-profile=' + str(self.profile)],
                                               profile_environment=self.root / 'missing-old-profile.json'))

    def test_environment_only_profile_remains_compatible(self):
        self.assert_selected(self.import_layout(['background_host.py', 'status'], profile_environment=self.profile))

    def test_profile_option_before_action_remains_compatible(self):
        self.assert_selected(self.import_layout(['background_host.py', '--instance-profile', str(self.profile), 'run']))

    def test_duplicate_profile_forms_are_rejected_before_data_selection(self):
        split = ['--instance-profile', str(self.profile)]
        equals = ['--instance-profile=' + str(self.profile)]
        for first, second in ((split, split), (equals, equals), (split, equals), (equals, split)):
            with self.subTest(arguments=first + second):
                result = self.import_layout(['background_host.py', 'run', *first, *second])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('must be specified exactly once', result.stderr)
        self.assertFalse((self.root / 'data').exists())

    def test_missing_or_empty_profile_never_falls_back(self):
        for option in (['--instance-profile'], ['--instance-profile', ''],
                       ['--instance-profile='], ['--instance-profile', '--timeout-seconds', '1']):
            with self.subTest(arguments=option):
                result = self.import_layout(['background_host.py', 'run', *option])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('requires a path', result.stderr)
        self.assertFalse((self.root / 'data').exists())


if __name__ == '__main__':
    unittest.main()
