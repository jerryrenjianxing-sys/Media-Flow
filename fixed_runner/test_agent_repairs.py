import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from agent_repairs import AgentRepairs
from repair_materials import read_bundle, source_path


class AgentRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'source.zip'
        self.contents = {'fixed_runner/page_rule.py': b'def is_home(label):\n    return label == "HOME-old"\n',
            'fixed_runner/test_page_rule.py': b'import unittest\nfrom page_rule import is_home\nclass RuleTest(unittest.TestCase):\n    def test_home(self):\n        self.assertTrue(is_home("HOME"))\n'}
        manifest = {'schema': 1, 'source_revision': 'fixture-base-commit', 'contains_runtime_data': False,
                    'files': {k: hashlib.sha256(v).hexdigest() for k, v in self.contents.items()}}
        with zipfile.ZipFile(self.bundle, 'w') as z:
            for name, data in self.contents.items():
                z.writestr(name, data)
            z.writestr('manifest.json', json.dumps(manifest))
        self.manager = AgentRepairs(self.root / 'repairs', bundle=self.bundle)
        self.repair = self.manager.create({'purpose': '修复页面识别夹具，验证真实编辑和测试通路'}, {'session_id': 'ses1', 'call_id': 'create1'})
        self.repair_id = self.repair['id']

    def tearDown(self):
        self.temp.cleanup()

    def wait_test(self, call_id='test1'):
        result = self.manager.test({'repair_id': self.repair_id, 'path': 'fixed_runner/test_page_rule.py'}, {'session_id': 'ses1', 'call_id': call_id})
        for _ in range(150):
            if result['state'] not in {'running', 'queued'}:
                return result
            time.sleep(.1)
            result = self.manager.test_status(result['id'], 'ses1')
        self.fail('Repair test did not reach a terminal state')

    def test_real_failure_edit_pass_export_and_revert(self):
        self.assertEqual(self.repair['state'], 'ready')
        first = self.wait_test()
        self.assertEqual(first['state'], 'failed', first['output'])
        read = self.manager.read({'repair_id': self.repair_id, 'path': 'fixed_runner/page_rule.py'}, 'ses1')
        self.manager.edit({'repair_id': self.repair_id, 'path': read['path'], 'expected_sha256': read['sha256'],
                           'content': 'def is_home(label):\n    return label == "HOME"\n'}, 'ses1')
        with self.assertRaises(ValueError):
            self.manager.export(self.repair_id, 'ses1')
        result = self.wait_test('test2')
        self.assertEqual(result['state'], 'passed', result['output'])
        receipt = self.manager.export(self.repair_id, 'ses1')
        self.assertEqual(receipt['revision'], 'fixture-base-commit')
        self.assertEqual(receipt['changed_files'], ['fixed_runner/page_rule.py'])
        self.assertEqual(receipt['artifact_status'], 'candidate_only')
        self.assertEqual(read_bundle(self.bundle)[1], self.contents)
        current = self.manager.read({'repair_id': self.repair_id, 'path': read['path']}, 'ses1')
        self.manager.edit({'repair_id': self.repair_id, 'path': read['path'], 'expected_sha256': current['sha256']}, 'ses1', revert=True)
        self.assertEqual(self.manager.diff(self.repair_id, 'ses1')['changed_files'], [])

    def test_scope_secret_stale_edit_and_restart_history(self):
        for path in ('../secret.py', 'C:/secret.py', 'fixed_runner/runtime/tasks.json', 'fixed_runner/nul.py'):
            with self.assertRaises(ValueError):
                source_path(path)
        with self.assertRaises(ValueError):
            self.manager.read({'repair_id': self.repair_id, 'path': 'fixed_runner/page_rule.py'}, 'other-session')
        with self.assertRaises(ValueError):
            self.manager.edit({'repair_id': self.repair_id, 'path': 'fixed_runner/page_rule.py', 'expected_sha256': 'old', 'content': 'new'}, 'ses1')
        with self.assertRaises(ValueError):
            self.manager.edit({'repair_id': self.repair_id, 'path': 'fixed_runner/new.py', 'expected_sha256': hashlib.sha256(b'').hexdigest(), 'content': 'sk-sp-' + 'x'*40}, 'ses1')
        restarted = AgentRepairs(self.root / 'repairs', bundle=self.bundle)
        self.assertEqual(restarted.get(self.repair_id, 'ses1')['revision'], 'fixture-base-commit')

    def test_executor_start_failure_has_terminal_receipt(self):
        with patch('agent_repairs.threading.Thread.start', side_effect=RuntimeError('fixture')):
            result = self.manager.test({'repair_id': self.repair_id, 'path': 'fixed_runner/test_page_rule.py'}, {'session_id': 'ses1', 'call_id': 'start-failure'})
        self.assertEqual(result['state'], 'failed')
        self.assertIn('启动失败', result['output'])

    def test_cancel_stops_real_test_and_preserves_workspace(self):
        original = self.manager.read({'repair_id': self.repair_id, 'path': 'fixed_runner/test_page_rule.py'}, 'ses1')
        self.manager.edit({'repair_id': self.repair_id, 'path': original['path'], 'expected_sha256': original['sha256'],
            'content': 'import unittest,time\nclass Slow(unittest.TestCase):\n    def test_slow(self):\n        time.sleep(60)\n'}, 'ses1')
        result = self.manager.test({'repair_id': self.repair_id, 'path': original['path']}, {'session_id': 'ses1', 'call_id': 'cancel-test'})
        self.manager.cancel_session('ses1')
        for _ in range(50):
            result = self.manager.test_status(result['id'], 'ses1')
            if result['state'] not in {'running', 'queued'}:
                break
            time.sleep(.1)
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(self.manager.get(self.repair_id, 'ses1')['state'], 'ready')

    def test_child_cannot_read_production_file_or_spawn_command(self):
        outside = self.root / 'private.txt'
        outside.write_text('private-sentinel', encoding='utf-8')
        original = self.manager.read({'repair_id': self.repair_id, 'path': 'fixed_runner/test_page_rule.py'}, 'ses1')
        content = ('import unittest\nfrom pathlib import Path\nclass ScopeTest(unittest.TestCase):\n'
                   '    def test_outside(self):\n        with self.assertRaises(PermissionError):\n'
                   f'            Path({str(outside)!r}).read_text()\n'
                   '    def test_native(self):\n        with self.assertRaises(PermissionError):\n'
                   '            import ctypes\n'
                   '    def test_process(self):\n        import os\n        with self.assertRaises(PermissionError):\n            os.system("echo must-not-run")\n')
        self.manager.edit({'repair_id': self.repair_id, 'path': original['path'], 'expected_sha256': original['sha256'], 'content': content}, 'ses1')
        result = self.wait_test('guard-test')
        self.assertEqual(result['state'], 'passed', result['output'])
        self.assertNotIn('private-sentinel', result['output'])


if __name__ == '__main__':
    unittest.main()
