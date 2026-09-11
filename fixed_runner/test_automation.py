"""Automation admission uses real isolated plans/queue, never a live service."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import urllib.error
import urllib.request

from agent_platform import AgentPlatform
from agent_memory import AgentMemory
from task_store import TaskStore


def content_plan_document(name='产业主题轮换'):
    return {'name': name, 'comment_template': '围绕主题交流', 'common_comment_pool': [],
        'themes': [
            {'id': 'a', 'name': '人工智能', 'topic_prompt': '主要内容必须直接讨论人工智能技术',
             'search_query': '人工智能', 'comment_template': '', 'comment_pool': [], 'enabled': True},
            {'id': 'disabled', 'name': '停用主题', 'topic_prompt': '不参与队列',
             'search_query': '停用', 'comment_template': '', 'comment_pool': [], 'enabled': False},
            {'id': 'b', 'name': '智能制造', 'topic_prompt': '主要内容必须直接讨论智能制造',
             'search_query': '智能制造', 'comment_template': '', 'comment_pool': [], 'enabled': True},
            {'id': 'c', 'name': '塑料包装', 'topic_prompt': '主要内容必须直接讨论塑料包装',
             'search_query': '塑料包装', 'comment_template': '', 'comment_pool': [], 'enabled': True},
        ]}


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'tasks.db')
        self.store.set_paused(True)
        self.snapshot = {'paused': True, 'devices': [{'device_id': 'vm-one', 'device_type': 'virtual',
            'state': 'device', 'profile_verified': True, 'initialization_status': 'legacy'}],
            'virtualization': {'devices': [{'virtual_device_id': 'vm-uuid', 'provider_instance_id': '0',
                'adb_endpoint': 'vm-one', 'android_identity': 'android-one'}]}}
        self.launches = []
        def launch(devices):
            self.launches.append(devices)
            return [{'running': True}]
        self.host = SimpleNamespace(root=self.root, status_reader=lambda: self.snapshot,
            platform=AgentPlatform(self.root/'platform', self.store, lambda: self.snapshot,
                worker_launcher=launch, model_status_reader=lambda: {'model_ready': True}),
            memory=AgentMemory(self.root/'memory'), operations=None, repairs=None,
            repair_updates=None, evidence_root=None)
        self.config = {'device_ids': ['vm-uuid'], 'video_count': 2, 'round_count': 1,
            'content_mode': 'general', 'engagement_inspection_enabled': False}

    def service(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('automation'), 'Automation API is missing')
        from automation import AutomationService
        return AutomationService(self.host)

    def plan(self, service):
        return service.call({'action': 'plan_tasks', 'arguments': {'config': self.config}, 'request_id': 'plan-1'})['result']

    def test_content_plan_actions_are_versioned_durable_and_archive_keeps_history(self):
        service = self.service()
        self.assertEqual(service.call({'action': 'content_plan_list'})['result']['content_plans'], [])
        create = {'action': 'content_plan_save', 'arguments': {'document': content_plan_document()},
                  'request_id': 'content-create'}
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.service().call(create), range(4)))
        first = service.call(create)
        self.assertTrue(first['ok'], first)
        self.assertTrue(all(reply == first or reply['status'] == 'unknown' for reply in replies))
        self.assertEqual(len(self.store.list_content_plans()), 1)
        plan_id = first['result']['content_plan']['plan_id']
        revision_id = first['result']['content_plan']['revision_id']

        changed = content_plan_document('产业主题轮换新版')
        second = self.service().call({'action': 'content_plan_save', 'arguments': {
            'content_plan_id': plan_id, 'document': changed}, 'request_id': 'content-update'})
        self.assertEqual(second['result']['content_plan']['revision_number'], 2)
        old = service.call({'action': 'content_plan_get', 'arguments': {
            'content_plan_id': plan_id, 'revision_id': revision_id}})
        self.assertEqual(old['result']['content_plan']['document']['name'], '产业主题轮换')
        wrong = service.call({'action': 'content_plan_get', 'arguments': {
            'content_plan_id': 'wrong-plan', 'revision_id': revision_id}})
        self.assertFalse(wrong['ok'])
        self.assertEqual(wrong['reason_code'], 'business_rejected')

        archived = service.call({'action': 'content_plan_archive', 'arguments': {
            'content_plan_id': plan_id}, 'request_id': 'content-archive'})
        self.assertTrue(archived['ok'], archived)
        self.assertEqual(service.call({'action': 'content_plan_list'})['result']['content_plans'], [])
        listed = service.call({'action': 'content_plan_list', 'arguments': {'include_archived': True}})
        self.assertEqual(listed['result']['content_plans'][0]['revision_number'], 2)
        historical = self.service().call({'action': 'content_plan_get', 'arguments': {
            'content_plan_id': plan_id, 'revision_id': revision_id}})
        self.assertTrue(historical['result']['content_plan']['archived'])

    def test_content_plan_unknown_after_side_effect_is_persisted_and_never_replayed(self):
        real_save = self.store.save_content_plan
        calls = []
        def uncertain(document, *, plan_id=None):
            calls.append(1)
            real_save(document, plan_id=plan_id)
            raise TimeoutError('result lost after commit')
        self.store.save_content_plan = uncertain
        body = {'action': 'content_plan_save', 'arguments': {'document': content_plan_document()},
                'request_id': 'content-unknown'}
        first = self.service().call(body)
        second = self.service().call(body)
        self.assertEqual(first['status'], 'unknown')
        self.assertFalse(first['retryable'])
        self.assertEqual(second, first)
        self.assertEqual(calls, [1])
        self.assertEqual(len(self.store.list_content_plans()), 1)

    def test_content_plan_preflight_resolves_revision_before_required_theme_fields(self):
        revision = self.store.save_content_plan(content_plan_document())
        planned = self.service().call({'action': 'plan_tasks', 'arguments': {'config': {
            **self.config, 'round_count': 5, 'content_mode': 'search',
            'search_query': '', 'topic_prompt': '不应覆盖内容计划的旧主题',
            'content_plan_id': revision['plan_id'],
            'content_plan_revision_id': revision['revision_id'],
        }}, 'request_id': 'content-plan'})
        self.assertTrue(planned['ok'], planned)
        self.assertNotEqual(planned['status'], 'waiting_user')
        self.assertEqual(planned['result']['config']['search_query'], '人工智能')
        self.assertEqual(planned['result']['config']['topic_prompt'], '主要内容必须直接讨论人工智能技术')
        plan_id = planned['result']['plan_id']
        second_revision = self.store.save_content_plan(
            content_plan_document('编辑后的新版本'), plan_id=revision['plan_id'])
        executed = self.service().call({'action': 'execute_plan', 'arguments': {
            'plan_id': plan_id}, 'request_id': 'content-execute'})
        task_ids = executed['result']['task_ids']
        names = [self.store.get(task_id).payload['content_plan_snapshot']['theme']['name']
                 for task_id in task_ids]
        self.assertEqual(names, ['人工智能', '智能制造', '塑料包装', '人工智能', '智能制造'])
        self.assertTrue(all(self.store.get(task_id).payload['content_plan_snapshot']
                            ['content_plan_revision_id'] == revision['revision_id'] for task_id in task_ids))
        self.assertNotEqual(revision['revision_id'], second_revision['revision_id'])

        mismatch = self.service().call({'action': 'plan_tasks', 'arguments': {'config': {
            **self.config, 'content_mode': 'mixed', 'content_plan_id': 'wrong-plan',
            'content_plan_revision_id': revision['revision_id'],
        }}, 'request_id': 'content-mismatch'})
        self.assertFalse(mismatch['ok'])
        self.assertIn('不匹配', mismatch['user_message'])

    def test_general_preflight_ignores_content_plan_and_keeps_zero_write_defaults(self):
        planned = self.service().call({'action': 'plan_tasks', 'arguments': {'config': {
            **self.config, 'content_plan_id': 'missing-plan',
            'content_plan_revision_id': 'missing-revision', 'like_probability': 0.9,
        }}, 'request_id': 'general-plan'})
        self.assertTrue(planned['ok'], planned)
        self.assertIsNone(planned['result']['config']['content_plan_id'])
        self.assertIsNone(planned['result']['config']['content_plan_revision_id'])
        self.assertEqual(planned['result']['config']['like_probability'], 0.9)

    def test_continuation_through_automation_freezes_rounds_and_survives_resume(self):
        revision = self.store.save_content_plan(content_plan_document())
        config = {**self.config, 'content_mode': 'search', 'round_count': 3,
                  'content_round_start': 3, 'batch_stop_policy': 'round_count',
                  'content_plan_id': revision['plan_id'], 'content_plan_revision_id': revision['revision_id']}
        created = self.service().call({'action': 'plan_tasks', 'arguments': {'config': config}, 'request_id': 'offset-plan'})
        self.assertTrue(created['ok'], created)
        plan = created['result']
        self.assertEqual(plan['config']['search_query'], '塑料包装')
        self.assertEqual(plan['preview']['content_round_start'], 3)
        self.assertEqual(plan['preview']['content_round_end'], 5)
        refreshed = self.service().call({'action': 'repreview_plan', 'arguments': {
            'plan_id': plan['plan_id']}, 'request_id': 'offset-repreview'})
        self.assertTrue(refreshed['ok'], refreshed)
        self.assertEqual(refreshed['result']['config']['content_round_start'], 3)
        body = {'action': 'execute_plan', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'offset-execute'}
        first = self.service().call(body)
        self.assertTrue(first['ok'], first)
        task_ids = first['result']['task_ids']
        self.assertEqual([self.store.get(t).payload['round_index'] for t in task_ids], [3, 4, 5])
        self.assertEqual([self.store.get(t).payload['search_query'] for t in task_ids], ['塑料包装', '人工智能', '智能制造'])
        self.assertEqual(self.service().call(body), first)
        self.service().call({'action': 'pause_batch', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'offset-pause'})
        resumed = self.service().call({**body, 'request_id': 'offset-resume'})
        self.assertEqual(resumed['result']['task_ids'], task_ids)
        self.assertEqual(len(self.store.list_all()), 3)

    def test_invalid_continuation_does_not_submit_or_start_worker(self):
        reply = self.service().call({'action': 'plan_tasks', 'arguments': {'config': {
            **self.config, 'content_round_start': 20, 'round_count': 2}}, 'request_id': 'invalid-offset'})
        self.assertFalse(reply['ok'])
        self.assertEqual(self.store.list_all(), [])
        self.assertEqual(self.launches, [])

    def test_preset_model_and_notification_actions_reuse_existing_services_truthfully(self):
        preset = self.service().call({'action': 'preset_save', 'arguments': {
            'name': '零写入计划', 'config': self.config}, 'request_id': 'preset-save'})
        self.assertTrue(preset['ok'], preset)
        self.assertEqual(self.service().call({'action': 'preset_list'})['result']['presets'][-1]['name'], '零写入计划')

        alert, _ = self.store.record_interaction_alert(task_id='task-one', device_id='vm-one',
            sources=['received_likes'], summary={'headline': '5条'}, fingerprint='fixture')
        notifications = self.service().call({'action': 'notification_list', 'arguments': {'status': 'unread'}})
        self.assertEqual(notifications['result']['notifications'][0]['id'], alert['id'])
        self.assertNotIn('alerts', notifications['result'])
        acknowledged = self.service().call({'action': 'notification_acknowledge', 'arguments': {
            'notification_ids': [alert['id']]}, 'request_id': 'notify-ack'})
        self.assertEqual(acknowledged['result']['acknowledged'], 1)
        self.assertEqual(self.service().call({'action': 'notification_acknowledge', 'arguments': {
            'notification_ids': [alert['id']]}, 'request_id': 'notify-ack'}), acknowledged)

        passed = {'provider_id': 'qwen_token_plan', 'model_test_status': 'passed',
                  'model_ready': True, 'message': 'fixture passed', 'providers': {}}
        failed = {**passed, 'model_test_status': 'failed', 'model_ready': False,
                  'reason_code': 'invalid_response', 'message': 'fixture failed'}
        with patch('model_providers.status', return_value=passed) as status, \
             patch('model_providers.test_candidate', side_effect=[failed, passed]) as test, \
             patch('model_providers.activate', return_value={**passed, 'ok': True}) as activate:
            observed = self.service().call({'action': 'model_status', 'arguments': {
                'provider': 'qwen_token_plan'}})
            self.assertTrue(observed['ok'])
            status.assert_called_once_with('qwen_token_plan')
            rejected_key = self.service().call({'action': 'model_test', 'arguments': {
                'provider': 'qwen_token_plan', 'upload_consent': True, 'api_key': 'must-not-pass'},
                'request_id': 'model-with-key'})
            self.assertFalse(rejected_key['ok'])
            self.assertEqual(test.call_count, 0)
            failure = self.service().call({'action': 'model_test', 'arguments': {
                'provider': 'qwen_token_plan', 'upload_consent': True}, 'request_id': 'model-test-fail'})
            self.assertFalse(failure['ok'])
            self.assertEqual(failure['status'], 'failed')
            self.assertEqual(failure['reason_code'], 'invalid_response')
            success = self.service().call({'action': 'model_test', 'arguments': {
                'provider': 'qwen_token_plan', 'upload_consent': True}, 'request_id': 'model-test-pass'})
            self.assertTrue(success['ok'], success)
            self.assertEqual(test.call_args.kwargs, {'consent': True})
            enabled = self.service().call({'action': 'model_activate', 'arguments': {
                'provider': 'qwen_token_plan'}, 'request_id': 'model-activate'})
            self.assertTrue(enabled['ok'], enabled)
            activate.assert_called_once_with('qwen_token_plan', task_db=self.store.path)

    def test_model_activation_provider_refusal_is_blocked_and_cached(self):
        from model_providers import ProviderError
        state = {'provider_id': 'qwen_token_plan', 'model_test_status': 'passed',
                 'model_ready': True, 'message': 'existing model remains active', 'providers': {}}
        body = {'action': 'model_activate', 'arguments': {'provider': 'qwen_token_plan'},
                'request_id': 'activate-unreadable'}
        with patch('model_providers.status', return_value=state), \
             patch('model_providers.activate', side_effect=ProviderError('credential_unreadable')) as activate:
            first = self.service().call(body)
            second = self.service().call(body)
        self.assertFalse(first['ok'])
        self.assertEqual(first['status'], 'blocked')
        self.assertEqual(first['reason_code'], 'credential_unreadable')
        self.assertIn('重新输入', first['user_message'])
        self.assertEqual(second, first)
        self.assertEqual(activate.call_count, 1)

    def test_typed_model_operation_busy_refusal_is_blocked(self):
        from model_connection import ModelOperationBusyError
        state = {'provider_id': 'openrouter', 'model_test_status': 'passed',
                 'model_ready': True, 'message': 'existing model remains active', 'providers': {}}
        with patch('model_providers.status', return_value=state), \
             patch('model_connection.test_current_model', side_effect=ModelOperationBusyError(
                 '已有模型配置操作正在进行，请稍候查看结果，不要重复提交')):
            response = self.service().call({'action': 'model_test', 'arguments': {
                'provider': 'openrouter'}, 'request_id': 'model-busy'})
        self.assertFalse(response['ok'])
        self.assertEqual(response['status'], 'blocked')
        self.assertEqual(response['reason_code'], 'model_operation_busy')
        self.assertIn('正在进行', response['user_message'])
        self.assertFalse(response['retryable'])

    def test_untyped_model_runtime_failure_remains_durable_unknown(self):
        with patch('model_connection.test_current_model', side_effect=RuntimeError(
                'unexpected failure after transport may have started')) as test:
            body = {'action': 'model_test', 'arguments': {'provider': 'openrouter'},
                    'request_id': 'model-uncertain'}
            first = self.service().call(body)
            second = self.service().call(body)
        self.assertEqual(first['status'], 'unknown')
        self.assertEqual(first['reason_code'], 'result_unknown')
        self.assertFalse(first['retryable'])
        self.assertEqual(second, first)
        self.assertEqual(test.call_count, 1)
        self.assertNotIn('unexpected failure', json.dumps(first))

    def test_task_evidence_separates_requested_count_from_whitelisted_actual_result(self):
        task_id = self.store.submit('healthcheck', 'vm-one', {'video_count': 10, 'round_index': 2})
        pending = self.service().call({'action': 'task_evidence', 'arguments': {'task_id': task_id}})
        self.assertEqual(pending['result']['task']['requested'], {'video_count': 10, 'round_index': 2})
        self.assertIsNone(pending['result']['task']['result_summary'])

        self.store.set_paused(False)
        self.store.claim_next('vm-one', 'fixture-worker')
        self.store.finish(task_id, status='completed', run_dir='C:/private/evidence', result={
            'status': 'passed_with_recovery', 'videos_seen': 8, 'topic_matches': 5,
            'skipped_videos': 2, 'video_errors': 1, 'likes': 3, 'favorites': 2,
            'comments_generated': 2, 'comments_sent': 1, 'model_attempts': 8,
            'model_valid_decisions': 7, 'model_errors': 1, 'stopped_by_user': False,
            'private_decisions': [{'secret': 'must-not-leak'}], 'result_path': 'C:/private/result.json',
        })
        completed = self.service().call({'action': 'task_evidence', 'arguments': {'task_id': task_id}})
        self.assertEqual(completed['result']['task']['result_summary'], {
            'result_status': 'passed_with_recovery', 'videos_seen': 8, 'topic_matches': 5,
            'skipped_videos': 2, 'video_errors': 1, 'likes': 3, 'favorites': 2,
            'comments_generated': 2, 'comments_sent': 1, 'model_attempts': 8,
            'model_valid_decisions': 7, 'model_errors': 1, 'stopped_by_user': False,
        })
        serialized = json.dumps(completed, ensure_ascii=False)
        self.assertNotIn('must-not-leak', serialized)
        self.assertNotIn('C:/private', serialized)

    def test_no_host_session_default_zero_draft_and_scoped_resume(self):
        service = self.service()
        self.store.save_run_draft({'custom': 'untouched'}, expected_revision=0)
        other = self.store.submit('healthcheck', 'other', {})
        plan = self.plan(service)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(plan['config']['like_probability'], 0)
        self.assertEqual(plan['config']['matched_comment_probability'], 0)
        body = {'action': 'execute_plan', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'execute-1'}
        first = service.call(body)
        self.assertEqual(first['result']['batch_id'], plan['plan_id'])
        self.assertNotEqual(first['status'], 'completed')
        self.assertEqual(self.service().call(body), first)
        self.assertEqual(len(self.launches), 1)
        service.call({'action': 'pause_batch', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'pause-1'})
        resumed = service.call({**body, 'request_id': 'resume-1'})
        self.assertEqual(resumed['result']['task_ids'], first['result']['task_ids'])
        self.assertTrue(self.store.is_paused())
        self.assertIsNone(self.store.claim_next('other', 'test'))
        self.assertEqual(self.store.get(other).status, 'pending')
        self.assertEqual(self.store.get_run_draft()['config'], {'custom': 'untouched'})

    def test_concurrent_same_id_and_conflicting_payload(self):
        service = self.service()
        plan = self.plan(service)
        body = {'action': 'execute_plan', 'arguments': {'plan_id': plan['plan_id']}, 'request_id': 'execute-1'}
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.service().call(body), range(4)))
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.launches), 1)
        saved = service.call(body)
        self.assertTrue(any(r == saved for r in replies))
        bad = service.call({**body, 'arguments': {**body['arguments'], 'resume_stopped_devices': True}})
        self.assertEqual(bad['reason_code'], 'request_id_conflict')

    def test_unknown_after_dispatch_is_durable_and_never_replayed(self):
        service = self.service()
        calls = []
        def uncertain(*args, **kwargs):
            calls.append(1)
            raise TimeoutError('sk-abcdefghijklmnopqrstuv')
        self.host.memory.save = uncertain
        body = {'action': 'memory_save', 'arguments': {'title': 'x', 'body': 'y'}, 'request_id': 'uncertain'}
        first = service.call(body)
        self.assertEqual(first['status'], 'unknown')
        self.assertFalse(first['retryable'])
        self.assertNotIn('abcdefghijklmnopqrstuv', json.dumps(first))
        self.assertEqual(self.service().call(body), first)
        self.assertEqual(len(calls), 1)
        lookup = self.service().call({'action': 'request_status', 'arguments': {'request_id': 'uncertain'}})
        self.assertEqual(lookup['result'], first)

    def test_reads_missing_id_and_device_fault_do_not_submit(self):
        service = self.service()
        for action in ['platform_status', 'list_devices', 'list_tasks', 'plan_status', 'memory_list']:
            self.assertTrue(service.call({'action': action})['ok'])
        self.assertFalse(service.call({'action': 'plan_tasks', 'arguments': {'config': self.config}})['ok'])
        self.snapshot['virtualization']['devices'][0]['adb_endpoint'] = None
        failed = service.call({'action': 'plan_tasks', 'arguments': {'config': self.config}, 'request_id': 'offline'})
        self.assertEqual(failed['status'], 'blocked')
        self.assertIn('ADB', failed['user_message'])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.launches, [])

    def test_http_route_json_origin_and_catalog(self):
        from control_api import Handler
        host = self.host
        class TestHandler(Handler):
            def automation_context(self):
                return host
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = 'http://127.0.0.1:%s/api/automation' % server.server_port
        try:
            with urllib.request.urlopen(url) as response:
                catalog = json.load(response)
        except urllib.error.HTTPError as exc:
            self.fail('Automation route must be registered: HTTP %s' % exc.code)
        self.assertEqual(catalog['api_version'], '1')
        self.assertIn('client_runtime', catalog)
        self.assertEqual(catalog['client_runtime']['api_url'], url.removesuffix('/api/automation'))
        self.assertTrue(catalog['client_runtime']['available'])
        self.assertTrue(Path(catalog['client_runtime']['python']).is_file())
        self.assertIn(catalog['client_runtime']['mode'], ('development', 'installed'))
        runtime = self.root/'relocated package'/'runtime'/'python'/'python.exe'
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b'fixture-runtime-identity')
        with patch('runtime_layout.IS_DISTRIBUTION', True), patch('runtime_layout.BUNDLED_PYTHON', runtime):
            installed = json.load(urllib.request.urlopen(url))['client_runtime']
            self.assertEqual(installed['mode'], 'installed')
            self.assertEqual(installed['python'], str(runtime.resolve()))
            runtime.unlink()
            missing = json.load(urllib.request.urlopen(url))['client_runtime']
            self.assertFalse(missing['available'])
            self.assertIsNone(missing['python'])
            self.assertEqual(missing['reason_code'], 'runtime_missing')
        self.assertTrue(any(a['action'] == 'execute_plan' and a['mutates'] for a in catalog['actions']))
        for headers, expected in [({'Origin': 'https://evil.example', 'Content-Type': 'application/json'}, 403),
                                  ({'Content-Type': 'text/plain'}, 415)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, data=b'{}', headers=headers))
            self.assertEqual(error.exception.code, expected)
        request = urllib.request.Request(url, data=json.dumps({'action': 'list_tasks'}).encode(),
            headers={'Content-Type': 'application/json'})
        self.assertEqual(json.load(urllib.request.urlopen(request))['result']['tasks'], [])

    def test_repair_native_workspace_and_hash_bound_updates_without_old_session(self):
        from agent_repairs import AgentRepairs
        from agent_repair_updates import AgentRepairUpdates
        bundle = self.root/'source.zip'
        content = b'VALUE = 1\n'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('fixed_runner/page.py', content)
            archive.writestr('manifest.json', json.dumps({'schema': 1, 'source_revision': 'a'*40,
                'contains_runtime_data': False, 'files': {'fixed_runner/page.py': hashlib.sha256(content).hexdigest()}}))
        repairs = AgentRepairs(self.root/'repairs', bundle=bundle, source_root=self.root)
        self.host.repairs = repairs
        # No legacy permission object exists. A test launcher records queued jobs only.
        launched = []
        self.host.repair_updates = AgentRepairUpdates(self.root/'updates', repairs, None, launcher=launched.append)
        service = self.service()
        created = service.call({'action': 'repair_create', 'arguments': {'purpose': 'fixture'}, 'request_id': 'repair-new'})
        self.assertTrue(created['ok'], created)
        workspace = Path(created['result']['workspace_path'])
        self.assertTrue(workspace.is_dir())
        repair_id = created['result']['id']
        (workspace/'fixed_runner/page.py').write_text('VALUE = 2\n', encoding='utf-8')
        def tested(*args):
            return [{'mode': 'all', 'state': 'passed', 'source_hash': repairs.diff(repair_id, 'skill-local')['source_hash']}]
        with patch('agent_repair_updates.development_revision', return_value='a'*40), patch.object(repairs, 'tests', side_effect=tested):
            prepared = service.call({'action': 'repair_prepare_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'prepare-1'})
            self.assertTrue(prepared['ok'], prepared)
            (workspace/'fixed_runner/page.py').write_text('VALUE = 3\n', encoding='utf-8')
            changed = service.call({'action': 'repair_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'apply-changed'})
            self.assertFalse(changed['ok'])
            self.assertIn('变化', changed['user_message'])
            self.assertEqual(launched, [])
            (workspace/'fixed_runner/page.py').write_text('VALUE = 2\n', encoding='utf-8')
            applied = service.call({'action': 'repair_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'apply-1'})
            self.assertEqual(applied['status'], 'queued', applied)
            self.assertEqual(len(launched), 1)
            self.assertEqual(self.service().call({'action': 'repair_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'apply-1'}), applied)
            self.host.repair_updates.update(applied['operation_id'], status='failed', stage='result_unknown', message='unknown')
            unknown = service.call({'action': 'repair_apply', 'arguments': {'repair_id': repair_id}, 'request_id': 'apply-next'})
            self.assertEqual(unknown['status'], 'unknown')
            self.assertEqual(len(launched), 1)

    def test_blocked_plan_explains_actual_device_condition(self):
        self.snapshot['devices'][0]['state'] = 'offline'
        response = self.service().call({'action': 'plan_tasks', 'arguments': {'config': self.config}, 'request_id': 'blocked'})
        self.assertEqual(response['status'], 'blocked')
        self.assertIn('不可用', response['user_message'])

    def test_non_finite_json_is_rejected_before_receipt_or_dispatch(self):
        response = self.service().call({'action': 'memory_save', 'arguments': {'body': float('nan')}, 'request_id': 'nan'})
        self.assertFalse(response['ok'])
        self.assertEqual(response['reason_code'], 'invalid_arguments')

    def test_virtual_failure_is_not_reported_as_successful_submission(self):
        from agent_operations import AgentOperations
        self.store.save_virtual_device({'virtual_device_id': 'vm1', 'provider': 'mumu',
            'provider_install_id': 'install', 'provider_instance_id': '0', 'name': 'Test VM',
            'state': 'stopped', 'recipe': {}, 'provider_snapshot': {}})
        def dispatch(device, body):
            operation, _ = self.store.create_virtual_operation(body['action'], body,
                idempotency_key=body['idempotency_key'])
            self.store.update_virtual_operation(operation['id'], status='failed', stage='failed',
                progress=100, message='fixture device unavailable', error='device unavailable')
        self.host.operations = AgentOperations(self.root/'commands', self.store, dispatch)
        service = self.service()
        command = service.call({'action': 'plan_virtual_operation', 'arguments': {'virtual_device_id': 'vm1', 'action': 'start'}, 'request_id': 'virtual-plan'})
        result = service.call({'action': 'execute_virtual_operation', 'arguments': {'command_id': command['result']['command_id']}, 'request_id': 'virtual-execute'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('device unavailable', result['user_message'])
        self.assertIsNotNone(result['operation_id'])

    def repair_fixture(self):
        from agent_repairs import AgentRepairs
        files = {'fixed_runner/page.py': b'VALUE = 1\n',
            'fixed_runner/test_page.py': b'import unittest\nfrom page import VALUE\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(VALUE, 2)\n',
            'fixed_runner/test_slow.py': b'import unittest, time\nclass T(unittest.TestCase):\n def test_wait(self): time.sleep(30)\n'}
        bundle = self.root/'fixture.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr('manifest.json', json.dumps({'schema': 1, 'source_revision': 'a'*40,
                'contains_runtime_data': False, 'files': {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}))
        self.host.repairs = AgentRepairs(self.root/'repairs', bundle=bundle, source_root=self.root)
        created = self.service().call({'action': 'repair_create', 'arguments': {'purpose': 'review fixture'}, 'request_id': 'fixture-new'})
        self.assertTrue(created['ok'], created)
        return created['result']['id'], Path(created['result']['workspace_path'])

    def wait_repair_test(self, test_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.service().call({'action': 'repair_test_status', 'arguments': {'test_id': test_id}})
            if result['status'] not in {'queued', 'running'}:
                return result
            time.sleep(.05)
        self.fail('Isolated repair fixture did not terminate')

    def test_real_repair_timeout_receipt_is_not_success(self):
        repair_id, _ = self.repair_fixture()
        # Run an actual isolated child process, advancing only the repair module's
        # deadline clock to avoid a 90-second test. No process/device mock.
        ticks = iter([0, 1000])
        timer = SimpleNamespace(time=time.time, sleep=time.sleep, monotonic=lambda: next(ticks))
        with patch('agent_repairs.time', timer):
            test = self.service().call({'action': 'repair_test', 'arguments': {
                'repair_id': repair_id, 'path': 'fixed_runner/test_slow.py'}, 'request_id': 'timeout-test'})
            result = self.wait_repair_test(test['operation_id'])
        self.assertEqual(result['result']['state'], 'timeout', result)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason_code'], 'timeout')
        # This state is persisted by real restart recovery, not an API mock.
        with self.host.repairs.database() as db:
            db.execute("UPDATE tests SET state='running' WHERE id=?", (test['operation_id'],))
        from agent_repairs import AgentRepairs
        self.host.repairs = AgentRepairs(self.host.repairs.root, bundle=self.root/'fixture.zip', source_root=self.root)
        interrupted = self.service().call({'action': 'repair_test_status', 'arguments': {'test_id': test['operation_id']}})
        self.assertEqual(interrupted['status'], 'interrupted')
        self.assertFalse(interrupted['ok'])

    def test_native_repair_export_returns_verified_candidate_path_without_host_session(self):
        repair_id, workspace = self.repair_fixture()
        (workspace/'fixed_runner/page.py').write_text('VALUE = 2\n', encoding='utf-8')
        test = self.service().call({'action': 'repair_test', 'arguments': {
            'repair_id': repair_id, 'path': 'fixed_runner/test_page.py'}, 'request_id': 'export-test'})
        self.assertEqual(self.wait_repair_test(test['operation_id'])['status'], 'passed')
        result = self.service().call({'action': 'repair_export', 'arguments': {'repair_id': repair_id}, 'request_id': 'export-1'})
        self.assertTrue(result['ok'], result)
        self.assertNotIn('download_url', result['result'])
        artifact = Path(result['result']['artifact_path'])
        self.assertTrue(artifact.is_relative_to(self.host.repairs.root))
        self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), result['result']['patch_sha256'])
        self.assertIn('+VALUE = 2', artifact.read_text(encoding='utf-8'))
        denied = self.service().call({'action': 'repair_export', 'arguments': {'repair_id': '../outside'}, 'request_id': 'escape-export'})
        self.assertFalse(denied['ok'])


class AutomationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hits = []
        self.failures = 0
        self.drop_response = False
        self.truncate_response = False
        outer = self
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.hits.append(body)
                if outer.drop_response:
                    self.close_connection = True
                    return
                if outer.truncate_response:
                    self.send_response(200)
                    self.send_header('Content-Length', '1000')
                    self.end_headers()
                    self.wfile.write(b'{"ok": true')
                    self.wfile.flush()
                    self.close_connection = True
                    return
                failed = len(outer.hits) <= outer.failures
                self.send_response(503 if failed else 200)
                self.end_headers()
                self.wfile.write(json.dumps({'ok': not failed, 'status': 'blocked' if failed else 'observed',
                    'reason_code': 'unavailable' if failed else 'ok', 'result': {'received': body}}).encode())
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_cli(self, action, *args, url=None):
        script = Path(__file__).parent/'assets/agent/skills/mediaflow-platform/scripts/mediaflow.py'
        self.assertTrue(script.is_file(), 'Skill HTTP client is missing')
        env = {**os.environ, 'MEDIAFLOW_API_URL': url or 'http://127.0.0.1:%s' % self.server.server_port,
               'MEDIAFLOW_RECEIPT_DB': str(Path(self.temp.name)/'receipts.db')}
        return subprocess.run([sys.executable, str(script), action, *args], env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=15)

    def test_read_retries_are_bounded_and_return_json(self):
        self.failures = 2
        response = self.run_cli('list_tasks', '--arguments', '{"limit":2}')
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertTrue(json.loads(response.stdout)['ok'])
        self.assertEqual(len(self.hits), 3)
        self.hits.clear()
        self.failures = 10
        self.assertNotEqual(self.run_cli('list_tasks').returncode, 0)
        self.assertEqual(len(self.hits), 3)

    def test_new_business_reads_do_not_require_request_id_and_retry(self):
        self.failures = 2
        response = self.run_cli('content_plan_list')
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(len(self.hits), 3)
        self.assertEqual(self.hits[-1]['action'], 'content_plan_list')

    def test_write_failure_never_retries_even_across_processes(self):
        self.failures = 100
        first = self.run_cli('execute_plan', '--request-id', 'write-1', '--arguments', '{"plan_id":"one"}')
        self.assertNotEqual(first.returncode, 0)
        second = self.run_cli('execute_plan', '--request-id', 'write-1', '--arguments', '{"plan_id":"one"}')
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(len(self.hits), 1)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        conflict = self.run_cli('execute_plan', '--request-id', 'write-1', '--arguments', '{"plan_id":"other"}')
        self.assertEqual(json.loads(conflict.stdout)['reason_code'], 'request_id_conflict')

    def test_non_loopback_and_missing_write_id_fail_before_network(self):
        self.assertNotEqual(self.run_cli('list_tasks', url='http://example.com').returncode, 0)
        self.assertNotEqual(self.run_cli('execute_plan').returncode, 0)
        self.assertEqual(self.hits, [])

    def test_large_arguments_file_and_cached_success(self):
        path = Path(self.temp.name)/'args.json'
        path.write_text('{"title":"test","body":"preference"}', encoding='utf-8')
        first = self.run_cli('memory_save', '--arguments-file', str(path), '--request-id', 'memory-1')
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_cli('memory_save', '--arguments-file', str(path), '--request-id', 'memory-1')
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(self.hits), 1)

    def test_response_lost_after_server_acceptance_does_not_resend(self):
        self.drop_response = True
        first = self.run_cli('execute_plan', '--request-id', 'lost', '--arguments', '{"plan_id":"one"}')
        self.assertEqual(json.loads(first.stdout)['status'], 'unknown')
        second = self.run_cli('execute_plan', '--request-id', 'lost', '--arguments', '{"plan_id":"one"}')
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(self.hits), 1)

    def test_corrupt_client_receipt_fails_closed_with_json_and_redacted_stderr(self):
        (Path(self.temp.name)/'receipts.db').write_bytes(b'corrupt sqlite fixture')
        result = self.run_cli('execute_plan', '--request-id', 'corrupt')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reason_code', json.loads(result.stdout))
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.hits, [])

    def test_truncated_http_body_write_is_durable_unknown_and_read_attempts_are_bounded(self):
        self.truncate_response = True
        first = self.run_cli('execute_plan', '--request-id', 'short-body', '--arguments', '{"plan_id":"one"}')
        self.assertNotEqual(first.returncode, 0)
        self.assertTrue(first.stdout.strip(), first.stderr)
        self.assertEqual(json.loads(first.stdout)['status'], 'unknown')
        self.assertNotIn('Traceback', first.stderr)
        second = self.run_cli('execute_plan', '--request-id', 'short-body', '--arguments', '{"plan_id":"one"}')
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(self.hits), 1)
        self.hits.clear()
        read = self.run_cli('list_tasks')
        self.assertNotEqual(read.returncode, 0)
        self.assertEqual(json.loads(read.stdout)['reason_code'], 'read_failed')
        self.assertNotIn('Traceback', read.stderr)
        self.assertEqual(len(self.hits), 3)
