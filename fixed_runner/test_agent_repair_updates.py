import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from agent_permissions import AgentPermissions
from agent_repair_updates import AgentRepairUpdates
from repair_update_worker import DevelopmentDriver, run_job


class RepairUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.permissions = AgentPermissions(self.root / 'permissions')
        self.permissions.record('s', 'r', '修好这个问题，验证通过就应用')
        self.repairs = Mock()
        self.repairs.source_root = self.root
        self.repairs.root = self.root / 'repairs'
        self.diff = {'changed_files': ['fixed_runner/page.py'], 'revision': 'a'*40, 'source_hash': 'hash'}
        self.repairs.diff.return_value = self.diff
        self.repairs.tests.return_value = [{'mode':'all', 'state':'passed', 'source_hash':'hash'}]
        self.repairs.freeze.side_effect = self.freeze_fixture
        self.u = AgentRepairUpdates(self.root / 'updates', self.repairs, self.permissions, launcher=Mock())
        self.revision = patch('agent_repair_updates.development_revision', return_value='a'*40)
        self.revision.start()

    def tearDown(self):
        self.revision.stop()
        self.tmp.cleanup()

    @staticmethod
    def freeze_fixture(repair_id, session, destination, source_hash):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({'source_hash': source_hash, 'files': {'fixed_runner/page.py': 'VALUE = 2\n'}}), encoding='utf-8')

    def approved_job(self):
        self.u.prepare('repair', 's')
        return self.u.apply('repair', 's')

    def test_real_request_and_prepared_patch_can_launch_once_without_magic_phrase(self):
        with self.assertRaises(ValueError):
            self.u.apply('repair', 's')
        job = self.approved_job()
        self.assertEqual(self.u.apply('repair', 's')['id'], job['id'])
        self.u.launcher.assert_called_once()

    def test_changed_patch_and_cancelled_request_reject(self):
        self.u.prepare('repair', 's')
        self.diff['source_hash'] = 'changed'
        with self.assertRaises(ValueError):
            self.u.apply('repair', 's')
        self.diff['source_hash'] = 'hash'
        self.permissions.cancel_intent('s')
        with self.assertRaises(ValueError):
            self.u.apply('repair', 's')

    def test_new_request_can_apply_prepared_patch_without_restatement(self):
        self.u.prepare('repair', 's')
        self.permissions.record('s', 'next', '可以，按这个结果更新')
        job = self.u.apply('repair', 's')
        self.assertEqual(job['request_id'], 'next')
        self.u.launcher.assert_called_once()

    def test_prepare_requires_user_request_and_rejects_mismatched_repair(self):
        with self.assertRaises(ValueError):
            self.u.prepare('repair', 'unknown')
        self.u.prepare('repair', 's')
        with self.assertRaises(ValueError):
            self.u.prepare('another-repair', 's')
        self.diff['source_hash'] = 'changed'
        self.repairs.tests.return_value = [{'mode':'all','state':'passed','source_hash':'changed'}]
        with self.assertRaises(ValueError):
            self.u.prepare('repair', 's')
        self.u.launcher.assert_not_called()

    def test_unknown_outcome_is_not_relaunched_by_followup_request(self):
        job = self.approved_job()
        self.u.update(job['id'], status='failed', stage='result_unknown', message='unknown')
        self.permissions.record('s', 'followup', '继续处理')
        self.assertEqual(self.u.apply('repair', 's')['id'], job['id'])
        self.u.launcher.assert_called_once()

    def test_known_launch_failure_allows_new_request_retry_only(self):
        self.u.launcher.side_effect = RuntimeError('fixture launch failure')
        first = self.approved_job()
        self.assertEqual(first['stage'], 'launch_failed')
        self.u.launcher.side_effect = None
        self.assertEqual(self.u.apply('repair', 's')['id'], first['id'])
        self.permissions.record('s', 'retry', '重新应用已验证的修复')
        second = self.u.apply('repair', 's')
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(second['stage'], 'waiting_idle')
        self.assertEqual(self.u.apply('repair', 's')['id'], second['id'])
        self.assertEqual(self.u.launcher.call_count, 2)

    def test_known_completed_job_is_returned_without_revalidating_changed_live_base(self):
        job = self.approved_job()
        self.u.update(job['id'], status='completed', stage='completed', message='completed')
        self.permissions.record('s', 'followup', '检查刚才的应用')
        with patch('agent_repair_updates.development_revision', return_value='changed-after-update'):
            self.assertEqual(self.u.apply('repair', 's')['id'], job['id'])

    def test_worker_checks_exact_patch_receipt_instead_of_legacy_level_revision(self):
        job = self.approved_job()
        driver = DevelopmentDriver(self.u, job)
        with self.permissions.database() as db:
            db.execute('INSERT INTO grants VALUES(?,?,?,?,?)', ('s', 'operate', 42, 'user_settings', 123))
        driver.check_authorization()
        with self.u.database() as db:
            db.execute('UPDATE approvals SET hash=? WHERE request_id=?', ('mismatch', 'r'))
        with self.assertRaisesRegex(ValueError, '不匹配'):
            driver.check_authorization()

    def test_snapshot_content_mismatch_blocks_before_creating_git_worktree(self):
        job = self.approved_job()
        snapshot = self.u.root / 'approved' / 'hash.json'
        # Keep the declared source hash intact, but change the bytes to apply.
        snapshot.write_text(json.dumps({'source_hash': 'hash', 'files': {'fixed_runner/page.py': 'VALUE = 999\n'}}), encoding='utf-8')
        driver = DevelopmentDriver(self.u, job)
        driver.command = Mock()
        with patch('repair_update_worker.development_revision', return_value='a'*40):
            with self.assertRaisesRegex(ValueError, '快照'):
                driver.stage()
        driver.command.assert_not_called()

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

    def test_patch_receipt_invalidated_during_staging_no_update(self):
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
