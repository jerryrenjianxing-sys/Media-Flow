from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from agent_platform import AgentPlatform
from control_config import PlannedTask
from task_store import TaskStore


class AgentPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'tasks.db')
        self.store.set_paused(True)
        self.snapshot = {'devices': [{'device_id': 'vm-one', 'device_type': 'virtual', 'state': 'device',
                         'profile_verified': True, 'initialization_status': 'legacy'}],
                         'virtualization': {'devices': [{'virtual_device_id': 'vm-uuid', 'provider_instance_id': '0',
                           'adb_endpoint': 'vm-one', 'android_identity': 'android-one'}]}}
        self.launch = Mock()
        self.platform = AgentPlatform(self.root / 'platform', self.store, lambda: self.snapshot,
                                      worker_launcher=self.launch, model_status_reader=lambda: {'model_ready': True})
        self.context = {'session_id': 'ses_one', 'call_id': 'call_one'}
        self.config = {'device_ids': ['vm-one'], 'video_count': 2, 'round_count': 2,
                       'round_interval_minutes': 0, 'content_mode': 'general', 'engagement_inspection_enabled': False}

    def tearDown(self):
        self.temp.cleanup()

    def planned(self):
        return self.platform.tools('plan_tasks', {'config': self.config}, self.context)

    def test_new_agent_inspection_defaults_home_badge_and_accepts_explicit_mode(self):
        self.config.update(engagement_inspection_enabled=True,inspection_every_rounds=1)
        plan=self.planned()
        receipt=self.confirm(plan)
        checks=[self.store.get(id) for id in receipt['task_ids'] if self.store.get(id).task_type=='douyin_engagement_inspection']
        self.assertTrue(checks)
        self.assertTrue(all(task.payload['inspection_workflow_version']=='home_badge' for task in checks))
        self.config['inspection_mode']='home_badge'
        self.context['call_id']='another'
        self.assertIn('plan_id',self.planned())

    def confirm(self, plan):
        return self.platform.confirm(plan['plan_id'], 'ses_one', {'confirmed': True, 'plan_hash': plan['preview']['plan_hash']})

    def test_explicit_legacy_preview_blocks_confirmation_without_standard_profile(self):
        self.config.update(engagement_inspection_enabled=True, inspection_every_rounds=1,
                           inspection_mode='legacy')
        plan = self.planned()
        self.assertEqual(plan['config']['inspection_mode'], 'legacy')
        self.assertFalse(plan['preview']['ready'])
        self.assertTrue(any('标准虚拟机' in reason for reason in plan['preview']['blockers']))
        with self.assertRaises(ValueError):
            self.confirm(plan)
        self.assertEqual(self.store.list(), [])
        self.launch.assert_not_called()

    def test_explicit_legacy_preview_hash_and_confirmed_workflow_agree(self):
        self.store.save_virtual_device({
            'virtual_device_id': 'vm-uuid', 'provider': 'mumu', 'provider_instance_id': '0',
            'name': 'Isolated standard VM', 'state': 'ready', 'recipe': {}, 'provider_snapshot': {},
            'adb_endpoint': 'vm-one', 'android_identity': 'android-one', 'managed': True,
            'standard_status': 'standard', 'profile_status': 'ready', 'presence_status': 'present',
        })
        self.config.update(engagement_inspection_enabled=True, inspection_every_rounds=1,
                           inspection_mode='legacy')
        legacy = self.planned()
        self.assertTrue(legacy['preview']['ready'], legacy['preview']['blockers'])
        self.config['inspection_mode'] = 'home_badge'
        self.context['call_id'] = 'home-comparison'
        home = self.planned()
        self.assertNotEqual(legacy['preview']['plan_hash'], home['preview']['plan_hash'])
        receipt = self.confirm(legacy)
        checks = [self.store.get(task_id) for task_id in receipt['task_ids']
                  if self.store.get(task_id).task_type == 'douyin_engagement_inspection']
        self.assertEqual(len(checks), legacy['preview']['inspection_task_count'])
        self.assertTrue(all(task.payload['inspection_workflow_version'] == 'v3' for task in checks))
        self.assertTrue(all(task.payload['inspection_mode'] == 'legacy' for task in checks))

    def test_plan_confirmation_is_atomic_scoped_idempotent_and_does_not_change_draft(self):
        self.store.save_run_draft({'custom': 'keep'}, expected_revision=0)
        unrelated = self.store.submit('healthcheck', 'other', {})
        plan = self.planned()
        self.assertEqual(plan['state'], 'awaiting_confirmation')
        self.assertEqual(len(self.store.list()), 1)
        result = self.confirm(plan)
        repeated = self.confirm(plan)
        self.assertEqual(result['task_ids'], repeated['task_ids'])
        self.assertEqual(len(self.store.list()), 3)
        self.assertTrue(self.store.is_paused())
        self.assertIsNone(self.store.claim_next('other', 'worker'))
        self.assertEqual(self.store.get(unrelated).status, 'pending')
        claimed = self.store.claim_next('vm-one', 'worker')
        self.assertIn(claimed.id, result['task_ids'])
        self.assertTrue(self.store.agent_task_may_run_paused(claimed.id))
        self.assertEqual(self.store.get_run_draft()['config'], {'custom': 'keep'})
        self.assertEqual(claimed.payload['like_probability'], 0)
        self.assertEqual(claimed.payload['comment_probability'], 0)

    def test_confirmation_required_identity_and_scope_checked(self):
        plan = self.planned()
        with self.assertRaises(ValueError):
            self.platform.confirm(plan['plan_id'], 'ses_other', {})
        with self.assertRaises(ValueError):
            self.platform.confirm(plan['plan_id'], 'ses_one', {'plan_hash': plan['preview']['plan_hash']})
        self.snapshot['virtualization']['devices'][0]['android_identity'] = 'changed'
        with self.assertRaisesRegex(ValueError, '身份'):
            self.confirm(plan)
        self.assertEqual(self.store.list(), [])

    def test_failure_waits_same_device_and_explicit_pause_revokes_exemption(self):
        receipt = self.confirm(self.planned())
        self.store.set_paused(True)
        self.assertFalse(self.store.has_ready('vm-one'))
        self.assertIsNone(self.store.claim_next('vm-one', 'worker'))
        self.store.set_paused(False)
        task = self.store.claim_next('vm-one', 'worker')
        self.store.finish(task.id, status='failed', run_dir=None, error='test failure')
        self.assertFalse(self.store.has_ready('vm-one'))
        self.assertEqual(self.store.get(receipt['task_ids'][1]).status, 'waiting_device')

    def test_stop_session_cancels_only_its_tasks_and_prevents_confirmation(self):
        plan = self.planned()
        receipt = self.confirm(plan)
        claimed = self.store.claim_next('vm-one', 'worker')
        other = self.store.submit('healthcheck', 'other', {})
        self.platform.stop_session('ses_one')
        self.assertTrue(self.store.agent_task_stop_requested(claimed.id))
        self.assertEqual(self.store.get(other).status, 'pending')
        self.assertEqual(self.confirm(plan)['state'], 'cancelled')
        self.assertEqual(self.store.get(receipt['task_ids'][1]).status, 'cancelled')

    def test_batch_insert_rolls_back_and_restart_recovers_receipt(self):
        plan = self.planned()
        receipt = self.confirm(plan)
        restarted = AgentPlatform(self.root / 'platform', self.store, lambda: self.snapshot, worker_launcher=self.launch)
        self.assertEqual(restarted.confirm(plan['plan_id'], 'ses_one', {'confirmed': True, 'plan_hash': plan['preview']['plan_hash']})['task_ids'], receipt['task_ids'])
        items = [PlannedTask('healthcheck', 'new', {}, datetime.now().astimezone().isoformat()),
                 PlannedTask('unknown_type', 'new', {}, datetime.now().astimezone().isoformat())]
        with self.assertRaises(ValueError):
            self.store.submit_agent_batch('invalid', 'ses_one', items, fingerprint='hash', deadline=time.time()+60)
        self.assertIsNone(self.store.agent_batch_receipt('invalid', 'ses_one'))

    def test_missing_parameters_and_read_tools(self):
        result = self.platform.tools('plan_tasks', {'config': {}}, self.context)
        self.assertEqual(result['status'], 'waiting_user')
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.platform.tools('list_devices', {}, self.context)['devices'][0]['provider_instance_id'], '0')
        self.assertEqual(self.platform.tools('list_tasks', {}, self.context)['tasks'], [])

    def test_plan_accepts_inventory_permanent_id_and_freezes_current_endpoint(self):
        self.config['device_ids'] = ['vm-uuid']
        plan = self.planned()
        self.assertEqual(plan['config']['device_ids'], ['vm-one'])
        self.assertEqual(plan['state'], 'awaiting_confirmation')
        self.assertEqual(self.store.list(), [])
        self.snapshot['virtualization']['devices'][0]['adb_endpoint'] = 'changed-port'
        with self.assertRaisesRegex(ValueError, '映射|变化'):
            self.confirm(plan)

    def test_plan_rejects_offline_or_ambiguous_permanent_identity(self):
        self.config['device_ids'] = ['vm-uuid']
        self.snapshot['virtualization']['devices'][0]['adb_endpoint'] = None
        with self.assertRaisesRegex(ValueError, 'ADB'):
            self.planned()
        self.snapshot['virtualization']['devices'][0]['adb_endpoint'] = 'vm-one'
        self.snapshot['virtualization']['devices'].append(dict(self.snapshot['virtualization']['devices'][0]))
        with self.assertRaisesRegex(ValueError, '不唯一'):
            self.planned()

    def test_safety_stop_is_explicit_scoped_and_not_reported_as_vm_offline(self):
        self.store.request_stop(['vm-one', 'other'])
        plan = self.planned()
        self.assertEqual(plan['stopped_device_ids'], ['vm-one'])
        with self.assertRaisesRegex(ValueError, '任务.*安全停止'):
            self.confirm(plan)
        self.assertIn('安全停止', self.platform.get(plan['plan_id'], 'ses_one')['execution']['message'])
        self.assertTrue(self.store.is_stop_requested('vm-one'))
        self.assertEqual(self.store.list(), [])
        result = self.platform.confirm(plan['plan_id'], 'ses_one', {
            'confirmed': True, 'plan_hash': plan['preview']['plan_hash'], 'resume_stopped_devices': True})
        self.assertFalse(self.store.is_stop_requested('vm-one'))
        self.assertTrue(self.store.is_stop_requested('other'))
        self.assertTrue(self.store.is_paused())
        self.assertEqual(len(result['tasks']), 2)

    def test_resume_admission_failure_keeps_all_safety_stops(self):
        self.store.request_stop(['vm-one', 'other'])
        plan = self.planned()
        self.store.save_profile('automation-stop', {'stopped': True})
        with self.assertRaisesRegex(ValueError, '所有自动操作'):
            self.platform.confirm(plan['plan_id'], 'ses_one', {
                'confirmed': True, 'plan_hash': plan['preview']['plan_hash'], 'resume_stopped_devices': True})
        self.assertTrue(self.store.is_stop_requested('vm-one'))
        self.assertEqual(self.store.list(), [])

    def test_paused_batch_explicit_confirm_resumes_only_original_tasks(self):
        plan = self.planned()
        first = self.confirm(plan)
        self.store.set_paused(True)
        self.assertFalse(self.store.has_ready('vm-one'))
        again = self.confirm(plan)
        self.assertEqual(first['task_ids'], again['task_ids'])
        self.assertTrue(self.store.has_ready('vm-one'))
        self.assertTrue(self.store.is_paused())

    def test_launch_failure_receipt_survives_reload_and_recovers_same_batch(self):
        self.launch.side_effect = RuntimeError('worker unavailable')
        plan = self.planned()
        result = self.confirm(plan)
        saved = self.platform.get(plan['plan_id'], 'ses_one')
        self.assertEqual(saved['execution']['reason_code'], 'worker_start_unconfirmed')
        self.assertIn('执行者', saved['execution']['message'])
        self.launch.side_effect = None
        repeated = self.confirm(plan)
        self.assertEqual(repeated['task_ids'], result['task_ids'])
        self.assertEqual(self.platform.get(plan['plan_id'], 'ses_one')['execution']['reason_code'], 'worker_dispatch_checked')

    def test_worker_report_not_running_is_not_vm_offline_or_success(self):
        self.launch.return_value = [{'running': False, 'identity': 'supervisor_pending'}]
        result = self.confirm(self.planned())
        self.assertEqual(result['reason_code'], 'worker_start_unconfirmed')
        self.assertEqual(len(self.store.list()), 2)

    def test_repreview_is_idempotent_and_does_not_submit_or_change_old_plan(self):
        plan = self.planned()
        with self.platform.database() as db:
            db.execute('UPDATE plans SET deadline=0 WHERE id=?', (plan['plan_id'],))
        new = self.platform.repreview(plan['plan_id'], 'ses_one', {'request_id': 'retry1'})
        repeated = self.platform.repreview(plan['plan_id'], 'ses_one', {'request_id': 'retry1'})
        self.assertEqual(new['plan_id'], repeated['plan_id'])
        self.assertNotEqual(new['plan_id'], plan['plan_id'])
        self.assertEqual(self.platform.get(plan['plan_id'], 'ses_one')['state'], 'expired')
        self.assertEqual(self.store.list(), [])

    def test_home_topic_filter_does_not_require_unrelated_search_query(self):
        self.config.update(content_mode='mixed')
        result=self.planned()
        self.assertIn('topic_prompt',result['missing'])
        self.assertNotIn('search_query',result['missing'])
        self.config['topic_prompt']='测试主题'
        self.assertIn('plan_id',self.planned())

    def test_deadline_cancels_but_worker_restart_preserves_new_progress(self):
        plan = self.planned()
        receipt = self.confirm(plan)
        first = self.store.claim_next('vm-one', 'worker')
        self.assertEqual(self.store.recover_interrupted('vm-one'), 1)
        self.assertEqual(self.store.get(first.id).status, 'waiting_device')
        self.assertEqual(self.store.get(receipt['task_ids'][1]).status, 'pending')
        self.assertFalse(self.store.has_ready('vm-one'))
        self.store.control_agent_batch(receipt['batch_id'], 'ses_one', stop=True)
        self.context['call_id'] = 'second_call'
        receipt2 = self.confirm(self.planned())
        with self.store.connection() as db:
            db.execute('UPDATE agent_task_batches SET deadline=0 WHERE id=?', (receipt2['batch_id'],))
        self.assertFalse(self.store.has_ready('vm-one'))
        self.assertTrue(all(self.store.get(task_id).status == 'cancelled' for task_id in receipt2['task_ids']))

    def test_pause_waiter_respects_exact_task_scope(self):
        from worker_runtime import wait_while_paused
        receipt = self.confirm(self.planned())
        wait_while_paused(self.store, .2, device_id='vm-one', task_id=receipt['task_ids'][0])
        self.platform.stop_session('ses_one')
        wait_while_paused(self.store, .2, device_id='vm-one', task_id=receipt['task_ids'][0])

    def test_writes_cannot_be_confirmed_implicitly(self):
        self.config['like_probability'] = .1
        plan = self.planned()
        self.assertTrue(plan['preview']['requires_confirmation'])
        with self.assertRaisesRegex(ValueError, '真实互动'):
            self.confirm(plan)
        self.assertEqual(self.store.list(), [])


if __name__ == '__main__':
    unittest.main()
