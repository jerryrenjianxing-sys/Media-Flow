"""Real git/file activation in disposable projects; no running service/device."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from repair_update_worker import DevelopmentDriver
from repair_dependencies import DependencySwitch
from repair_materials import development_revision


@unittest.skipUnless(shutil.which('git'), 'git required')
class IsolatedUpdateIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'project'
        self.source.mkdir()
        self.git('init')
        self.git('config', 'user.name', 'MediaFlow Test')
        self.git('config', 'user.email', 'fixture@example.invalid')
        for name, text in {'.gitignore':'/work/\n/control_console/dist/\n',
                'fixed_runner/rule.py':'VALUE = 1\n', 'packaging/version.json':'{"version":"0.4.1","development_iteration":22}',
                'history.txt':'immutable user history'}.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-m', 'fixture baseline')
        self.base = self.git('rev-parse', 'HEAD')
        self.candidate = self.root / 'candidate'
        (self.candidate / 'fixed_runner').mkdir(parents=True)
        (self.candidate / 'fixed_runner/rule.py').write_text('VALUE = 2\n')

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.excludesfile=', *args], cwd=self.source,
            text=True, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()

    def test_actual_commit_activation_and_inverse_commit_preserve_data(self):
        from agent_repairs import AgentRepairs
        updates = Mock()
        updates.root = self.root / 'updates'
        (updates.root / 'approved').mkdir(parents=True)
        (updates.root / 'approved/hash.json').write_text(json.dumps({'source_hash':'hash', 'files':{'fixed_runner/rule.py':'VALUE = 2\n'}}))
        updates.repairs.source_root = self.source
        updates.repairs.diff.return_value = {'changed_files':['fixed_runner/rule.py']}
        updates.repairs.workspace.return_value = self.candidate
        updates.repairs.file = AgentRepairs.file
        job = {'id':'fixture', 'base':self.base, 'repair':'repair', 'session':'session', 'hash':'hash',
               'snapshot_hash': hashlib.sha256((updates.root / 'approved/hash.json').read_bytes()).hexdigest()}
        class FixtureDriver(DevelopmentDriver):
            def command(inner, args, **kwargs):
                if any(str(x).endswith('repair_validation.py') for x in args):
                    # Full project validation is tested separately. This fixture
                    # runs a real assertion and builds an isolated output.
                    result = super(FixtureDriver, inner).command(
                        [__import__('sys').executable, '-c', 'from fixed_runner.rule import VALUE; assert VALUE == 2'],
                        cwd=inner.stage_root)
                    dist = inner.stage_root / 'control_console/dist'
                    dist.mkdir(parents=True)
                    (dist / 'index.html').write_text('candidate')
                    return result
                return super(FixtureDriver, inner).command(args, **kwargs)
            def stop(inner): pass
            def start(inner): pass
            def health(inner, revision):
                assert development_revision(inner.source) == revision
        old_dist = self.source / 'control_console/dist'
        old_dist.mkdir(parents=True)
        (old_dist / 'index.html').write_text('old')
        driver = FixtureDriver(updates, job)
        revision = driver.stage()
        self.assertEqual((self.source/'fixed_runner/rule.py').read_text(), 'VALUE = 1\n')
        driver.activate(revision)
        self.assertEqual((self.source/'fixed_runner/rule.py').read_text(), 'VALUE = 2\n')
        self.assertEqual(json.loads((self.source/'packaging/version.json').read_text())['development_iteration'], 23)
        rollback = driver.rollback(True)
        self.assertNotEqual(rollback, revision)
        self.assertEqual((self.source/'fixed_runner/rule.py').read_text(), 'VALUE = 1\n')
        self.assertEqual((old_dist/'index.html').read_text(), 'old')
        self.assertEqual((self.source/'history.txt').read_text(), 'immutable user history')

    def test_dependency_switch_can_restore_exact_previous_directory(self):
        new = self.root/'dependencies'
        for root, value in ((self.source,'old'), (new,'new')):
            (root/'control_console/node_modules').mkdir(parents=True, exist_ok=True)
            (root/'control_console/package.json').write_text(value)
            (root/'control_console/node_modules/value.txt').write_text(value)
        switch = DependencySwitch(self.source, new, self.root/'backup')
        switch.activate()
        self.assertEqual((self.source/'control_console/node_modules/value.txt').read_text(), 'new')
        switch.rollback()
        self.assertEqual((self.source/'control_console/node_modules/value.txt').read_text(), 'old')

if __name__ == '__main__':
    unittest.main()
