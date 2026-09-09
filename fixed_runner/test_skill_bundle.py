"""Portable MediaFlow Skill bundle and extracted CLI integration tests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import urllib.request

from agent_memory import AgentMemory
from agent_platform import AgentPlatform
from task_store import TaskStore


class SkillBundleTests(unittest.TestCase):
    def test_missing_setup_reference_blocks_both_delivery_formats(self):
        from skill_bundle import SKILL_SOURCE, build_skill_bundle, build_skill_markdown
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)/'source'
            shutil.copytree(SKILL_SOURCE, source)
            (source/'references/setup.md').unlink()
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                build_skill_bundle(Path(temporary)/'result.zip', source=source)
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                build_skill_markdown(source=source)
            self.assertFalse((Path(temporary)/'result.zip').exists())

    def test_builder_creates_deterministic_allowlisted_archive(self):
        spec = importlib.util.find_spec('skill_bundle')
        self.assertIsNotNone(spec, 'skill_bundle module is missing')
        if spec is None:
            return
        from skill_bundle import build_skill_bundle

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build_skill_bundle(root/'first.zip')
            second = build_skill_bundle(root/'second.zip')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), sorted(archive.namelist()))
                self.assertEqual(set(archive.namelist()), {
                    'mediaflow-platform/NOTICE.md',
                    'mediaflow-platform/README.md',
                    'mediaflow-platform/SKILL.md',
                    'mediaflow-platform/config.example.json',
                    'mediaflow-platform/examples/content-plan-nut-factory.json',
                    'mediaflow-platform/examples/execute-arguments.json',
                    'mediaflow-platform/examples/plan-arguments.json',
                    'mediaflow-platform/examples/preset-zero-write.json',
                    'mediaflow-platform/examples/request-status-arguments.json',
                    'mediaflow-platform/references/api.md',
                    'mediaflow-platform/references/content-guide.md',
                    'mediaflow-platform/references/setup.md',
                    'mediaflow-platform/references/troubleshooting.md',
                    'mediaflow-platform/references/workflows.md',
                    'mediaflow-platform/scripts/_common.py',
                    'mediaflow-platform/scripts/mediaflow.py',
                    'mediaflow-platform/scripts/mediaflow.ps1',
                })
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0)
                                    for info in archive.infolist()))

    def test_builder_ignores_private_state_and_rejects_symlinks_and_machine_paths(self):
        spec = importlib.util.find_spec('skill_bundle')
        self.assertIsNotNone(spec, 'skill_bundle module is missing')
        if spec is None:
            return
        from skill_bundle import SKILL_SOURCE, build_skill_bundle

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'
            shutil.copytree(SKILL_SOURCE, source)
            (source/'config.json').write_text('{"api_url":"http://127.0.0.1:1"}', encoding='utf-8')
            (source/'receipts.db').write_bytes(b'private receipt')
            (source/'runtime').mkdir()
            (source/'runtime/auth.json').write_text('{"token":"secret"}', encoding='utf-8')
            archive_path = build_skill_bundle(root/'safe.zip', source=source)
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
            self.assertFalse(any(name.endswith('/config.json') or 'receipts' in name or '/runtime/' in name
                                 for name in names))

            readme = source/'README.md'
            original = readme.read_text(encoding='utf-8')
            readme.write_text(original + '\nhttp://127.0.0.1:48138/api/automation\n'
                'references/api.md\n./state/receipts.db\n', encoding='utf-8')
            build_skill_bundle(root/'safe-paths.zip', source=source)
            for index, machine_path in enumerate((
                    r'D:\private\device.json',
                    r'\\fileserver\share\auth.json',
                    '/home/alice/private/device.json',
                    '/Users/alice/private/device.json')):
                with self.subTest(machine_path=machine_path):
                    readme.write_text(original + '\n' + machine_path + '\n', encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'machine-specific absolute path'):
                        build_skill_bundle(root/('unsafe-path-%s.zip' % index), source=source)
            readme.write_text(original, encoding='utf-8')

            secret = root/'secret.txt'
            secret.write_text('not distributable', encoding='utf-8')
            notice = source/'NOTICE.md'
            notice_text = notice.read_text(encoding='utf-8')
            notice.unlink()
            try:
                notice.symlink_to(secret)
            except OSError:
                notice.write_text(notice_text, encoding='utf-8')
                with patch.object(Path, 'is_symlink', autospec=True,
                                  side_effect=lambda candidate: candidate == notice):
                    with self.assertRaisesRegex(ValueError, 'symbolic link'):
                        build_skill_bundle(root/'unsafe-link.zip', source=source)
                return
            with self.assertRaisesRegex(ValueError, 'symbolic link'):
                build_skill_bundle(root/'unsafe-link.zip', source=source)


class ExtractedSkillCliTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.find_spec('skill_bundle')
        self.assertIsNotNone(spec, 'skill_bundle module is missing')
        if spec is None:
            return
        from skill_bundle import build_skill_bundle

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = TaskStore(self.root/'tasks.db')
        self.store.set_paused(True)
        self.launches = []
        snapshot = {'paused': True, 'devices': [{'device_id': 'vm-one', 'device_type': 'virtual',
            'state': 'device', 'profile_verified': True, 'initialization_status': 'legacy'}],
            'virtualization': {'devices': [{'virtual_device_id': 'vm-uuid', 'provider_instance_id': '0',
                'adb_endpoint': 'vm-one', 'android_identity': 'android-one'}]}}
        self.host = SimpleNamespace(root=self.root/'service', status_reader=lambda: snapshot,
            platform=AgentPlatform(self.root/'platform', self.store, lambda: snapshot,
                worker_launcher=self.launch, model_status_reader=lambda: {'model_ready': True}),
            memory=AgentMemory(self.root/'memory'), operations=None, repairs=None,
            repair_updates=None, evidence_root=None)
        self.hits = []
        outer = self

        class Endpoint(BaseHTTPRequestHandler):
            store = outer.store

            def _headers(self, *args, **kwargs):
                from control_api import Handler
                return Handler._headers(self, *args, **kwargs)

            def do_GET(self):
                if self.path.startswith(('/api/records/task-detail', '/api/interaction-inspections', '/api/interaction-evidence')):
                    from control_api import Handler
                    return Handler.do_GET(self)
                from automation import handle_automation_http
                handle_automation_http(self, 'GET', self.path)

            def automation_context(self):
                return outer.host

            def _json(self, body, status=200):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                from automation import AutomationService
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.hits.append(body)
                response = AutomationService(outer.host).call(body)
                if body.get('request_id') == 'unknown-plan':
                    self.close_connection = True
                    return
                status = 409 if response['reason_code'] == 'request_id_conflict' else (200 if response['ok'] else 400)
                if response['status'] == 'unknown':
                    status = 202
                payload = json.dumps(response, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        archive = build_skill_bundle(self.root/'mediaflow-platform.zip')
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(self.root/'extracted')
        self.skill = self.root/'extracted/mediaflow-platform'
        self.config = {'config': {'device_ids': ['vm-uuid'], 'video_count': 2, 'round_count': 1,
            'content_mode': 'general', 'engagement_inspection_enabled': False}}

    def launch(self, devices):
        self.launches.append(devices)
        return [{'running': True}]

    def run_cli(self, action, *arguments, request_id=None, session_id='portable-session'):
        command = [sys.executable, str(self.skill/'scripts/mediaflow.py'), action, *arguments,
                   '--session-id', session_id]
        if request_id:
            command.extend(['--request-id', request_id])
        env = {**os.environ,
            'MEDIAFLOW_API_URL': 'http://127.0.0.1:%s' % self.server.server_port,
            'MEDIAFLOW_RECEIPT_DB': str(self.root/'client-receipts.db')}
        return subprocess.run(command, env=env, capture_output=True, text=True,
            encoding='utf-8', timeout=15)

    def test_extracted_cli_plan_execute_status_repeat_unknown_and_resume(self):
        plan_call = self.run_cli('plan_tasks', '--arguments', json.dumps(self.config), request_id='plan-one')
        self.assertEqual(plan_call.returncode, 0, plan_call.stderr)
        plan_id = json.loads(plan_call.stdout)['plan_id']
        repeated_plan = self.run_cli('plan_tasks', '--arguments', json.dumps(self.config), request_id='plan-one')
        self.assertEqual(repeated_plan.stdout, plan_call.stdout)

        execute_args = json.dumps({'plan_id': plan_id})
        executed = self.run_cli('execute_plan', '--arguments', execute_args, request_id='execute-one')
        self.assertEqual(executed.returncode, 0, executed.stderr)
        execution = json.loads(executed.stdout)
        repeated = self.run_cli('execute_plan', '--arguments', execute_args, request_id='execute-one')
        self.assertEqual(repeated.stdout, executed.stdout)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.list()[0].payload['video_count'], 2)
        self.assertEqual(len(self.launches), 1)

        status = self.run_cli('plan_status', '--arguments', execute_args)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)['plan_id'], plan_id)

        paused = self.run_cli('pause_batch', '--arguments', execute_args, request_id='pause-one')
        self.assertEqual(paused.returncode, 0, paused.stderr)
        resume_args = json.dumps({'plan_id': plan_id, 'resume_stopped_devices': True})
        resumed = self.run_cli('execute_plan', '--arguments', resume_args, request_id='resume-one')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)['result']['task_ids'], execution['result']['task_ids'])
        resumed_again = self.run_cli('execute_plan', '--arguments', resume_args, request_id='resume-one')
        self.assertEqual(resumed_again.stdout, resumed.stdout)
        self.assertEqual(len(self.store.list()), 1)

        unknown = self.run_cli('plan_tasks', '--arguments', json.dumps(self.config), request_id='unknown-plan')
        self.assertNotEqual(unknown.returncode, 0)
        self.assertEqual(json.loads(unknown.stdout)['status'], 'unknown')
        unknown_again = self.run_cli('plan_tasks', '--arguments', json.dumps(self.config), request_id='unknown-plan')
        self.assertEqual(unknown_again.stdout, unknown.stdout)
        self.assertEqual(sum(hit.get('request_id') == 'unknown-plan' for hit in self.hits), 1)
        receipt = self.run_cli('request_status', '--arguments', '{"request_id":"unknown-plan"}')
        self.assertEqual(receipt.returncode, 0, receipt.stderr)
        self.assertTrue(json.loads(receipt.stdout)['result']['ok'])

    def test_packaged_business_examples_save_and_drive_real_isolated_services(self):
        content_example = self.skill/'examples/content-plan-nut-factory.json'
        preset_example = self.skill/'examples/preset-zero-write.json'

        saved = self.run_cli('content_plan_save', '--arguments-file', str(content_example),
                             request_id='content-example-save')
        self.assertEqual(saved.returncode, 0, saved.stderr)
        revision = json.loads(saved.stdout)['result']['content_plan']
        themes = revision['document']['themes']
        self.assertEqual([theme['search_query'] for theme in themes], [
            '坚果工厂 生产线 源头厂家',
            '每日坚果 OEM代工',
            '干果炒货 自动化车间',
            '坚果分装 生产包装线',
        ])

        listed = self.run_cli('content_plan_list')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)['result']['content_plans'][0]['plan_id'],
                         revision['plan_id'])

        planned_config = {'config': {**self.config['config'], 'round_count': 4, 'content_mode': 'search',
            'search_trust_results': False, 'content_plan_id': revision['plan_id'],
            'content_plan_revision_id': revision['revision_id']}}
        planned = self.run_cli('plan_tasks', '--arguments', json.dumps(planned_config),
                               request_id='content-example-plan')
        self.assertEqual(planned.returncode, 0, planned.stdout or planned.stderr)
        plan_id = json.loads(planned.stdout)['plan_id']
        executed = self.run_cli('execute_plan', '--arguments', json.dumps({'plan_id': plan_id}),
                                request_id='content-example-execute')
        self.assertEqual(executed.returncode, 0, executed.stderr)
        task_ids = json.loads(executed.stdout)['result']['task_ids']
        snapshots = [self.store.get(task_id).payload['content_plan_snapshot']
                     for task_id in task_ids]
        self.assertEqual([item['theme']['search_query'] for item in snapshots],
                         [theme['search_query'] for theme in themes])

        preset = self.run_cli('preset_save', '--arguments-file', str(preset_example),
                              request_id='preset-example-save')
        self.assertEqual(preset.returncode, 0, preset.stderr)
        saved_preset = json.loads(preset.stdout)['result']['preset']
        self.assertEqual(saved_preset['name'], '严格生产搜索（零互动）')
        self.assertFalse(saved_preset['config']['search_trust_results'])
        self.assertEqual([saved_preset['config'][field] for field in (
            'like_probability', 'favorite_probability', 'comment_probability',
            'matched_like_probability', 'matched_favorite_probability',
            'matched_comment_probability')], [0] * 6)

    def test_both_bundles_preview_home_inspection_without_execution_and_keep_null_results(self):
        from skill_bundle import build_skill_markdown
        entries = re.findall(r'### `([^`]+)`\n\n(`{4,})[^\n]*\n(.*?)\n\2\n',
                             build_skill_markdown(), re.S)
        restored = self.root/'copied'
        for name, _, body in entries:
            target = restored/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding='utf-8')
        zipped = self.skill
        for label, skill in [('zip', zipped), ('markdown', restored/'mediaflow-platform')]:
            with self.subTest(format=label):
                self.skill = skill
                config = json.loads((skill/'examples/plan-arguments.json').read_text(encoding='utf-8'))
                config['config'].update(device_ids=['vm-uuid'], engagement_inspection_enabled=True,
                                         inspection_mode='home_badge', inspection_every_rounds=1)
                call = self.run_cli('plan_tasks', '--arguments', json.dumps(config),
                                    request_id='inspection-preview-' + label)
                self.assertEqual(call.returncode, 0, call.stdout or call.stderr)
                plan = json.loads(call.stdout)['result']
                self.assertEqual(plan['config']['inspection_mode'], 'home_badge')
                self.assertEqual([plan['config'][field] for field in (
                    'like_probability', 'favorite_probability', 'comment_probability',
                    'matched_like_probability', 'matched_favorite_probability',
                    'matched_comment_probability')], [0] * 6)
                self.assertEqual(self.store.list(), [])
                self.assertEqual(self.launches, [])

        task_id = self.store.submit('douyin_engagement_inspection', 'vm-one',
                                    {'inspection_mode': 'home_badge', 'inspection_workflow_version': 'home_badge',
                                     'submission_id': 'fixture-null', 'inspection_index': 1,
                                     'after_round_index': 1, 'inspection_every_rounds': 1})
        for status in ('pending', 'running'):
            if status == 'running':
                self.store.set_paused(False)
                self.store.claim_next('vm-one', 'fixture-worker')
            call = self.run_cli('task_evidence', '--arguments', json.dumps({'task_id': task_id}))
            self.assertEqual(call.returncode, 0, call.stdout or call.stderr)
            task = json.loads(call.stdout)['result']['task']
            self.assertEqual(task['status'], status)
            self.assertIsNone(task['result_summary'])
        self.assertEqual(self.launches, [])

    def test_documented_read_only_task_to_inspection_route_preserves_quantity_evidence(self):
        from engagement_inspection import EngagementInspector
        from test_home_badge import Device, Recorder
        from skill_bundle import SKILL_SOURCE
        evidence_root = self.root/'evidence'
        evidence_root.mkdir()
        device = Device(True)
        inspector = EngagementInspector(device, Recorder(evidence_root), device_id='vm-one',
            task_id='offline-evidence', sleep=lambda _: None, navigation_lock=lambda: True)
        produced = inspector.inspect({'inspection_workflow_version': 'home_badge'})
        self.assertEqual(produced['status'], 'completed')
        self.assertEqual(device.actions, [])
        documented = (SKILL_SOURCE/'references/api.md').read_text(encoding='utf-8')
        self.assertIn('`section=home_badge`', documented)
        self.assertIn('`label=消息角标原始裁剪`', documented)
        def get_json(path):
            with urllib.request.urlopen('http://127.0.0.1:%s%s' %
                                        (self.server.server_port, path), timeout=10) as response:
                return json.load(response)

        self.store.set_paused(False)
        cases = [('recognized', '3', 3, 'present'), ('recognized', '99+', None, 'present'),
                 ('dot', None, None, 'present'), ('unreadable', None, None, 'present'),
                 ('conflict', None, None, 'present'), ('none', None, None, 'absent')]
        for index, (quantity_status, text, count, state) in enumerate(cases):
            with self.subTest(quantity_status=quantity_status, text=text):
                task_id = self.store.submit('douyin_engagement_inspection', 'vm-one',
                                            {'inspection_mode': 'home_badge', 'inspection_workflow_version': 'home_badge',
                                             'submission_id': 'fixture-%s' % index, 'inspection_index': 1,
                                             'after_round_index': 1, 'inspection_every_rounds': 1})
                self.store.claim_next('vm-one', 'fixture-worker')
                inspection_id = 'saved-inspection-%s' % index
                badge = {'state': state, 'badge_text': text, 'message_count': count,
                         'quantity_status': quantity_status, 'quantity_source': 'local_glyph',
                         'badge_bounds': [10, 10, 30, 30], 'rule_version': 'fixture-v1'}
                self.store.record_interaction_inspection(
                    inspection_id=inspection_id, task_id=task_id, device_id='vm-one',
                    workflow_version='home_badge', status='completed',
                    result_kind='clear' if state == 'absent' else 'alert', restored=True,
                    summary={'home_badge': badge}, evidence=produced['evidence'], run_dir=str(evidence_root),
                    started_at='2026-09-09T00:00:00Z', finished_at='2026-09-09T00:00:01Z')
                self.store.finish(task_id, status='completed', run_dir=str(self.root/'evidence'),
                    result={'workflow_version': 'home_badge', 'home_badge': badge,
                            'inspection_metadata': {'inspection_id': inspection_id}})
                listed = json.loads(self.run_cli('list_tasks').stdout)['result']['tasks']
                self.assertIn(task_id, [task['task_id'] for task in listed])
                task = get_json('/api/records/task-detail?ids=' + task_id)['tasks'][0]
                actual_id = task['result']['inspection_metadata']['inspection_id']
                self.assertEqual(actual_id, inspection_id)
                detail = get_json('/api/interaction-inspections/' + actual_id)['inspection']
                self.assertEqual(detail['summary']['home_badge'], badge)
                self.assertEqual(task['result']['home_badge']['message_count'], count)
                crops = [item for item in detail['evidence'] if item['section'] == 'home_badge'
                         and item['label'] == '消息角标原始裁剪']
                self.assertEqual(len(crops), 1)
                crop = crops[0]
                original = next(item for item in detail['evidence'] if item['id'] != crop['id'])
                self.assertNotEqual(original['image_url'], crop['image_url'])
                self.assertNotIn('name', crop)
                with patch('control_api.DEFAULT_ARTIFACTS', self.root):
                    for item in (original, crop):
                        with urllib.request.urlopen('http://127.0.0.1:%s%s' %
                                (self.server.server_port, item['image_url']), timeout=10) as response:
                            self.assertEqual(response.status, 200)
                            self.assertTrue(response.read().startswith(b'\x89PNG'))
                self.assertNotIn(str(self.root), json.dumps(detail))
        self.assertEqual(self.launches, [])
        self.assertTrue(all(hit['action'] == 'list_tasks' for hit in self.hits))

    def test_powershell_launcher_prefers_environment_then_configured_runtime(self):
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not powershell:
            self.skipTest('PowerShell is unavailable')
        launcher = self.skill/'scripts/mediaflow.ps1'
        base_env = {**os.environ,
            'MEDIAFLOW_API_URL': 'http://127.0.0.1:%s' % self.server.server_port,
            'MEDIAFLOW_RECEIPT_DB': str(self.root/'launcher-receipts.db')}
        config_path = self.skill/'config.json'
        config_path.write_text(json.dumps({'api_url': base_env['MEDIAFLOW_API_URL'],
            'python': 'definitely-not-a-runtime'}), encoding='utf-8')
        explicit = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(launcher), 'list_tasks'], env={**base_env, 'MEDIAFLOW_PYTHON': sys.executable},
            capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)

        config_path.write_text(json.dumps({'api_url': base_env['MEDIAFLOW_API_URL'],
            'python': sys.executable}), encoding='utf-8')
        configured_env = {key: value for key, value in base_env.items() if key != 'MEDIAFLOW_PYTHON'}
        configured_env['MEDIAFLOW_SKILL_CONFIG'] = str(config_path)
        configured = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(launcher), 'list_tasks'], env=configured_env, capture_output=True, text=True,
            encoding='utf-8', timeout=15)
        self.assertEqual(configured.returncode, 0, configured.stderr)

    def test_powershell_bootstraps_platform_python_without_path_or_user_config(self):
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not powershell:
            self.skipTest('PowerShell is unavailable')
        env = {key: value for key, value in os.environ.items()
               if key not in ('MEDIAFLOW_PYTHON', 'MEDIAFLOW_SKILL_CONFIG')}
        env.update(PATH='', MEDIAFLOW_API_URL='http://127.0.0.1:%s' % self.server.server_port)
        result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(self.skill/'scripts/mediaflow.ps1'), 'list_tasks'], env=env,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result']['tasks'], [])
        self.assertFalse((self.skill/'config.json').exists())
        self.assertEqual(self.launches, [])

    def test_bootstrap_rejects_nonlocal_url_without_sending_a_task(self):
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not powershell:
            self.skipTest('PowerShell is unavailable')
        env = {key: value for key, value in os.environ.items()
               if key not in ('MEDIAFLOW_PYTHON', 'MEDIAFLOW_SKILL_CONFIG')}
        env.update(PATH='', MEDIAFLOW_API_URL='https://example.invalid')
        result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(self.skill/'scripts/mediaflow.ps1'), 'list_tasks'], env=env,
            capture_output=True, text=True, errors='replace', timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.hits, [])

    def test_copied_example_and_explicit_config_bootstrap_without_system_python(self):
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not powershell:
            self.skipTest('PowerShell is unavailable')
        env = {key: value for key, value in os.environ.items()
               if key not in ('MEDIAFLOW_PYTHON', 'MEDIAFLOW_SKILL_CONFIG', 'MEDIAFLOW_API_URL')}
        env['PATH'] = ''
        config = json.loads((self.skill/'config.example.json').read_text(encoding='utf-8-sig'))
        config['api_url'] = 'http://127.0.0.1:%s' % self.server.server_port
        for name, args in [('config.json', []), ('selected.json', ['--config'])]:
            with self.subTest(config=name):
                path = self.skill/name
                path.write_text(json.dumps(config), encoding='utf-8')
                if args:
                    (self.skill/'config.json').unlink()
                result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    str(self.skill/'scripts/mediaflow.ps1'), 'list_tasks', *(args + [str(path)] if args else [])],
                    env=env, capture_output=True, text=True, errors='replace', timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['result']['tasks'], [])

    def test_unreachable_platform_does_not_fall_back_to_system_python(self):
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not powershell:
            self.skipTest('PowerShell is unavailable')
        env = {key: value for key, value in os.environ.items()
               if key not in ('MEDIAFLOW_PYTHON', 'MEDIAFLOW_SKILL_CONFIG')}
        env.update(PATH='', MEDIAFLOW_API_URL='http://127.0.0.1:0')
        result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(self.skill/'scripts/mediaflow.ps1'), 'list_tasks'], env=env,
            capture_output=True, text=True, errors='replace', timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Ask the user before starting MediaFlow', result.stderr)
        self.assertEqual(self.hits, [])

    @unittest.skipUnless(os.name == 'nt', 'Windows portable runtime')
    def test_relocated_installed_runtime_executes_extracted_skill_without_developer_paths(self):
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not powershell:
            self.skipTest('PowerShell is unavailable')
        source = Path(sys.base_prefix)
        runtime = self.root/'installed software'/'runtime'/'python'
        runtime.mkdir(parents=True)
        for file in [source/'python.exe', *source.glob('*.dll')]:
            shutil.copy2(file, runtime/file.name)
        shutil.copytree(source/'Lib', runtime/'Lib', ignore=shutil.ignore_patterns(
            'site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'tkinter', 'ensurepip'))
        shutil.copytree(source/'DLLs', runtime/'DLLs')
        env = {key: value for key, value in os.environ.items() if key.upper() not in
               ('MEDIAFLOW_PYTHON', 'MEDIAFLOW_SKILL_CONFIG', 'PYTHONHOME', 'PYTHONPATH')}
        env.update(PATH='', MEDIAFLOW_API_URL='http://127.0.0.1:%s' % self.server.server_port)
        with patch('runtime_layout.IS_DISTRIBUTION', True), patch('runtime_layout.BUNDLED_PYTHON', runtime/'python.exe'):
            result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                str(self.skill/'scripts/mediaflow.ps1'), 'list_tasks'], env=env,
                capture_output=True, text=True, errors='replace', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result']['tasks'], [])
        self.assertEqual(self.launches, [])


if __name__ == '__main__':
    unittest.main()
