import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from agent_permissions import AgentPermissions
from agent_repair_updates import AgentRepairUpdates
from repair_update_worker import run_job


class RepairUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.permissions = AgentPermissions(self.root / 'permissions')
        self.permissions.set('s', 'develop', source='user_settings')
        self.repairs = Mock()
        self.repairs.source_root = self.root
        self.repairs.root = self.root / 'repairs'
        self.diff = {'changed_files': ['fixed_runner/page.py'], 'revision': 'a'*40, 'source_hash': 'hash'}
        self.repairs.diff.return_value = self.diff
        self.repairs.tests.return_value = [{'mode':'all', 'state':'passed', 'source_hash':'hash'}]
        self.u = AgentRepairUpdates(self.root / 'updates', self.repairs, self.permissions, launcher=Mock())
        self.revision = patch('agent_repair_updates.development_revision', return_value='a'*40)
        self.revision.start()

    def tearDown(self):
        self.revision.stop()
        self.tmp.cleanup()

    def approved_job(self):
        self.u.prepare('repair', 's')
        self.u.capture_user_approval('s', 'r', '应用这个修复')
        self.permissions.record('s', 'r', '应用这个修复')
        return self.u.apply('repair', 's')

    def test_only_real_chat_approval_can_launch_once(self):
        self.permissions.record('s', 'r', '读取状态')
        with self.assertRaises(ValueError):
            self.u.apply('repair', 's')
        job = self.approved_job()
        self.assertEqual(self.u.apply('repair', 's')['id'], job['id'])
        self.u.launcher.assert_called_once()

    def test_changed_patch_and_revoked_permission_reject(self):
        self.u.prepare('repair', 's')
        self.u.capture_user_approval('s', 'r', '应用这个修复')
        self.permissions.record('s', 'r', '应用这个修复')
        self.diff['source_hash'] = 'changed'
        with self.assertRaises(ValueError):
            self.u.apply('repair', 's')
        self.diff['source_hash'] = 'hash'
        self.permissions.set('s', 'operate', source='user_settings')
        with self.assertRaises(ValueError):
            self.u.apply('repair', 's')

    def test_test_failure_and_dirty_base_block_before_launch(self):
        self.repairs.tests.return_value = []
        with self.assertRaises(ValueError):
            self.u.prepare('repair', 's')
        self.repairs.tests.return_value = [{'mode':'all','state':'passed','source_hash':'hash'}]
        with patch('agent_repair_updates.development_revision', return_value='different'):
            with self.assertRaises(ValueError):
                self.u.prepare('repair', 's')
        self.u.launcher.assert_not_called()

    def test_success_survives_manager_restart(self):
        job = self.approved_job()
        driver = Mock()
        driver.stage.return_value = 'b'*40
        driver.idle.return_value = True
        run_job(self.u, job, driver)
        restarted = AgentRepairUpdates(self.root/'updates', self.repairs, self.permissions)
        self.assertEqual(restarted.get(job['id'], 's')['status'], 'completed')
        driver.stop.assert_called_once()
        driver.activate.assert_called_once_with('b'*40)
        driver.rollback.assert_not_called()

    def test_failure_rolls_back_and_does_not_report_success(self):
        job = self.approved_job()
        driver = Mock()
        driver.idle.return_value = True
        driver.stage.return_value = 'b'*40
        driver.health.side_effect = ValueError('health failed')
        driver.rollback.return_value = 'c'*40
        run_job(self.u, job, driver)
        result = self.u.get(job['id'], 's')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['stage'], 'rolled_back')
        self.assertEqual(result['rollback_revision'], 'c'*40)
        driver.rollback.assert_called_once_with(True)

    def test_cancel_while_busy_never_stops_or_activates(self):
        job = self.approved_job()
        driver = Mock()
        driver.idle.return_value = False
        run_job(self.u, job, driver, sleep=lambda _: self.u.cancel(job['id'], 's'))
        self.assertEqual(self.u.get(job['id'], 's')['status'], 'cancelled')
        driver.stop.assert_not_called()
        driver.activate.assert_not_called()

    def test_permission_revoked_during_staging_no_update(self):
        job = self.approved_job()
        driver = Mock()
        driver.idle.return_value = True
        driver.check_authorization.side_effect = [None, ValueError('revoked')]
        run_job(self.u, job, driver)
        self.assertEqual(self.u.get(job['id'], 's')['status'], 'failed')
        driver.activate.assert_not_called()

    def test_claim_is_atomic_and_orphan_has_terminal_error(self):
        job = self.approved_job()
        self.assertTrue(self.u.claim(job['id']))
        self.assertFalse(self.u.claim(job['id']))
        with self.u.database() as db:
            db.execute('UPDATE jobs SET lease_until=? WHERE id=?', (time.time()-1, job['id']))
        self.assertEqual(self.u.get(job['id'], 's')['stage'], 'result_unknown')
        self.assertFalse(self.u.claim(job['id']))

    def test_stage_deadline_never_switches(self):
        job = self.approved_job()
        driver = Mock()
        driver.idle.return_value = True
        def expired():
            with self.u.database() as db:
                db.execute('UPDATE jobs SET deadline=? WHERE id=?', (time.time()-1, job['id']))
            return 'b'*40
        driver.stage.side_effect = expired
        run_job(self.u, job, driver)
        self.assertEqual(self.u.get(job['id'], 's')['status'], 'failed')
        driver.stop.assert_not_called()

if __name__ == '__main__':
    unittest.main()
