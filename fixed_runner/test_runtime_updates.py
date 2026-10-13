"""Update regressions use temporary stores and simulated processes only."""
import json
import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from control_api import Handler
from runtime_control import ProcessSnapshot, ProcessSpec, RuntimeControl
from runtime_updates import RuntimeUpdateConflict
from task_store import TaskStore


class RuntimeUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = TaskStore(self.root/'tasks.db')

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, request_id='update'):
        return self.store.prepare_runtime_update(request_id, target_version='0.4.1-dev.46', sha512='test-digest')

    def test_pending_waiting_checkpoints_and_flags_survive_cancel_and_restart(self):
        self.store.set_paused(True)
        self.store.save_profile('automation-stop', {'stopped': True})
        pending = self.store.submit('healthcheck', 'test-device')
        waiting = self.store.submit('healthcheck', 'waiting-device', {'resilience_version': 'v1'})
        with self.store.connection() as db:
            db.execute("UPDATE tasks SET status='waiting_device',error='worker_interrupted' WHERE id=?", (waiting,))
            self.store._save_checkpoint(db, waiting, {'processed_slots': 3, 'unknown_actions': 1})
        before = [vars(task) for task in self.store.list_all()]
        operation = self.prepare()
        self.assertEqual('ready', operation['status'])
        self.assertFalse(self.store.has_ready('test-device'))
        self.assertIsNone(self.store.claim_next('test-device', 'worker'))
        self.assertFalse(self.store.resume_waiting_task(waiting))
        reloaded = TaskStore(self.store.path)
        self.assertEqual(operation['operation_id'], reloaded.prepare_runtime_update('update', target_version='0.4.1-dev.46', sha512='test-digest')['operation_id'])
        reloaded.finish_runtime_update(operation['operation_id'], cancelled=True)
        self.assertEqual(before, [vars(task) for task in reloaded.list_all()])
        self.assertEqual({'processed_slots': 3, 'unknown_actions': 1}, reloaded.get_task_checkpoint(waiting))
        self.assertTrue(reloaded.is_paused())
        self.assertEqual({'stopped': True}, reloaded.get_profile('automation-stop'))
        self.assertEqual('pending', reloaded.get(pending).status)

    def test_initialization_busy_and_queued_maintenance_fenced(self):
        active = self.store.create_initialization('test-device')
        queued = self.store.create_initialization('other-device')
        self.store.claim_initialization('test-device', 'worker')
        op = self.prepare()
        self.assertEqual('waiting_idle', op['status'])
        self.assertEqual(active.id, op['blockers'][0]['id'])
        self.assertIsNone(self.store.claim_initialization('other-device', 'worker'))
        with self.store.connection() as db:
            db.execute("UPDATE device_initializations SET status='ready' WHERE id=?", (active.id,))
        self.assertEqual('ready', self.store.runtime_update_status(op['id'])['status'])
        self.assertEqual('queued', self.store.get_initialization(queued.id).status)
        self.store.finish_runtime_update(op['id'])
        self.assertFalse(self.store.runtime_updating())

    def test_manual_and_vm_operations_report_concrete_blockers(self):
        manual = self.store.create_device_view_session(device_id='test-device', virtual_device_id='vm',
                    mode='control', stream_profile='focus', token_hash='a'*64)
        vm, _ = self.store.create_virtual_operation('start', {'virtual_device_id': 'other-vm'}, idempotency_key='vm')
        op = self.prepare()
        self.assertEqual({('manual_control', manual['id']), ('virtual_operation', vm['id'])},
                         {(blocker['kind'], blocker['id']) for blocker in op['blockers']})
        with self.assertRaises(RuntimeUpdateConflict):
            self.store.create_virtual_operation('start', {'virtual_device_id': 'new-vm'}, idempotency_key='new')
        with self.assertRaises(RuntimeUpdateConflict):
            self.store.create_device_view_session(device_id='other-device', virtual_device_id='vm2',
                    mode='control', stream_profile='focus', token_hash='b'*64)
        self.store.close_device_view_session(manual['id'])
        self.store.update_virtual_operation(vm['id'], status='completed', stage='completed', progress=100)
        self.assertEqual('ready', self.store.runtime_update_status(op['id'])['status'])

    def test_transient_probe_and_shutdown_gate_close_admission_race(self):
        task = self.store.submit('healthcheck', 'test-device')
        lease = self.store.begin_runtime_activity('device_probe', '连接检查', device_id='test-device')
        op = self.prepare()
        self.assertEqual('device_probe', op['blockers'][0]['kind'])
        self.assertTrue(self.store.begin_runtime_shutdown(op['id']))
        self.store.end_runtime_activity(lease)
        self.assertEqual([], self.store.begin_runtime_shutdown(op['id']))
        self.store.finish_runtime_update(op['id'], cancelled=True)
        self.assertIsNone(self.store.claim_next('test-device', 'worker'))
        self.store.release_runtime_shutdown()
        self.assertEqual(task, self.store.claim_next('test-device', 'worker').id)

    def test_update_request_id_is_bound_to_payload_and_active_operation(self):
        op = self.prepare()
        with self.assertRaisesRegex(ValueError, 'request_id'):
            self.store.prepare_runtime_update('update', target_version='different')
        with self.assertRaises(RuntimeUpdateConflict) as caught:
            self.prepare('another')
        self.assertEqual(op['id'], caught.exception.operation['id'])
        cancelled = self.store.finish_runtime_update(op['id'], cancelled=True)
        self.assertEqual(cancelled, self.store.finish_runtime_update(op['id'], cancelled=True))

    def test_surviving_model_request_blocks_update_after_its_http_handler_finishes(self):
        ledger = self.root/'model-providers.db'
        with closing(sqlite3.connect(ledger)) as db:
            db.execute('CREATE TABLE calls(id TEXT PRIMARY KEY,pid INTEGER,finished REAL)')
            db.execute('INSERT INTO calls VALUES(?,?,NULL)', ('surviving-model-call', os.getpid()))
            db.execute('INSERT INTO calls VALUES(?,?,NULL)', ('dead-model-call', 0))
            db.commit()
        op = self.prepare()
        self.assertEqual('waiting_idle', op['status'])
        self.assertEqual([('model_call', 'surviving-model-call')], [(row['kind'], row['id']) for row in op['blockers']])
        self.assertTrue(self.store.begin_runtime_shutdown(op['id']))
        with closing(sqlite3.connect(ledger)) as db:
            db.execute("UPDATE calls SET finished=1 WHERE id='surviving-model-call'")
            db.commit()
        self.assertEqual('ready', self.store.runtime_update_status(op['id'])['status'])
        with closing(sqlite3.connect(ledger)) as db:
            self.assertIsNone(db.execute("SELECT finished FROM calls WHERE id='dead-model-call'").fetchone()[0])

    def test_claim_and_prepare_are_serialized_for_business_maintenance_and_control(self):
        for kind in ('task', 'initialization', 'control', 'vm'):
            for attempt in range(4):
                store = TaskStore(self.root/f'{kind}-{attempt}.db')
                if kind == 'task':
                    store.submit('healthcheck', 'test-device')
                elif kind == 'initialization':
                    store.create_initialization('test-device')
                barrier = threading.Barrier(2)
                def claim():
                    barrier.wait()
                    try:
                        if kind == 'task':
                            return store.claim_next('test-device', 'worker')
                        if kind == 'initialization':
                            return store.claim_initialization('test-device', 'worker')
                        if kind == 'control':
                            return store.create_device_view_session(device_id='test-device', virtual_device_id='vm',
                                mode='control', stream_profile='focus', token_hash='a'*64)
                        return store.create_virtual_operation('start', {'virtual_device_id': 'vm'}, idempotency_key='vm')
                    except RuntimeUpdateConflict:
                        return None
                def prepare():
                    barrier.wait()
                    return store.prepare_runtime_update('race')
                with ThreadPoolExecutor(max_workers=2) as pool:
                    claimed, prepared = pool.submit(claim), pool.submit(prepare)
                    result, op = claimed.result(), prepared.result()
                self.assertEqual('waiting_idle' if result else 'ready', op['status'], (kind, op))
                self.assertIsNone(store.claim_next('test-device', 'worker'))

    def test_http_contract_and_conflict_expose_operation_without_device_access(self):
        class IsolatedHandler(Handler):
            store = self.store
        server = ThreadingHTTPServer(('127.0.0.1', 0), IsolatedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(path, body=None):
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}' + path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json'})
            try:
                response = urllib.request.urlopen(request, timeout=3)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response)
        try:
            code, op = call('/api/runtime/update/prepare', {'request_id': 'http', 'target_version': '0.4.1-dev.46', 'sha512': 'digest'})
            self.assertEqual(200, code)
            self.assertEqual('ready', op['status'])
            self.assertEqual(op['operation_id'], op['operation']['operation_id'])
            self.assertEqual(op['operation_id'], call('/api/runtime/update/' + op['operation_id'])[1]['operation_id'])
            self.assertEqual(409, call('/api/run', {})[0])
            self.assertEqual('cancelled', call('/api/runtime/update/' + op['operation_id'] + '/cancel', {})[1]['status'])
            _, completing = call('/api/runtime/update/prepare', {'request_id': 'http-complete', 'target_version': '0.4.1-dev.46'})
            with patch('control_api.product_version', return_value={'version': '0.4.1-dev.45'}):
                self.assertEqual(409, call('/api/runtime/update/' + completing['operation_id'] + '/complete', {})[0])
            self.assertTrue(self.store.runtime_updating())
            with patch('control_api.product_version', return_value={'version': '0.4.1-dev.46'}):
                self.assertEqual('completed', call('/api/runtime/update/' + completing['operation_id'] + '/complete', {})[1]['status'])
            self.assertFalse(self.store.runtime_updating())
            instance = call('/api/runtime/instance')[1]
            self.assertIn('data_root', instance)
            self.assertIn('product_version', instance)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_profile_isolates_paths_ports_and_propagates_environment(self):
        profile = self.root/'instance.json'
        profile.write_text(json.dumps({'schema': 1, 'id': 'acceptance', 'data_root': str(self.root/'data'),
            'app_root': str(self.root/'platform'), 'api_port': 49338, 'ui_port': 31301, 'stream_port': 49339,
            'acceptance': True}), encoding='utf-8')
        environment = dict(os.environ)
        for key in list(environment):
            if key.startswith(('MEDIAFLOW_', 'RISKFLOW_')):
                environment.pop(key)
        environment['MEDIAFLOW_INSTANCE_PROFILE'] = str(profile)
        program = ('import runtime_layout as r,json; print(json.dumps({"id":r.INSTANCE_ID,"data":str(r.DATA_ROOT),'
                   '"secret":str(r.SECRET_ROOT),"device":str(r.DEVICE_PROFILE_PATH),"api":r.API_PORT,'
                   '"ui":r.UI_PORT,"stream":r.STREAM_PORT,"acceptance":r.ACCEPTANCE,"env":r.tool_environment().get("MEDIAFLOW_INSTANCE_ID")}))')
        result = subprocess.run([sys.executable, '-c', program], cwd=Path(__file__).parent,
                                env=environment, text=True, capture_output=True, timeout=5)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(('acceptance', 49338, 31301, 49339, True),
                         (value['id'], value['api'], value['ui'], value['stream'], value['acceptance']))
        self.assertEqual(self.root/'data/secrets', Path(value['secret']))
        self.assertEqual(self.root/'data/device_profiles.json', Path(value['device']))
        self.assertEqual('acceptance', value['env'])
        local = self.root/'local-app-data'
        environment['LOCALAPPDATA'] = str(local)
        bootstrap = local/'MediaFlow/bootstrap.json'
        bootstrap.parent.mkdir(parents=True)
        production = self.root/'configured-production-data'
        bootstrap.write_text(json.dumps({'data_root': str(production)}), encoding='utf-8')
        for protected in (production, self.root/'platform/.secrets', Path(__file__).parent/'runtime'):
            payload = json.loads(profile.read_text(encoding='utf-8'))
            payload['data_root'] = str(protected)
            profile.write_text(json.dumps(payload), encoding='utf-8')
            result = subprocess.run([sys.executable, '-c', program], cwd=Path(__file__).parent,
                                    env=environment, text=True, capture_output=True, timeout=5)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('Acceptance data root cannot use production', result.stderr)

    def test_verified_source_profile_keeps_legacy_layout_and_venv_even_with_stray_bundle(self):
        source = self.root/'source'
        runner = source/'fixed_runner'
        runner.mkdir(parents=True)
        for name in ('runtime_layout.py', 'runtime_control.py', 'model_runtime_config.py',
                     'product_version.py', 'windows_powershell.py'):
            shutil.copyfile(Path(__file__).parent/name, runner/name)
        (source/'PROJECT.md').write_text('source fixture', encoding='utf-8')
        for executable in (source/'.venv/Scripts/python.exe', source/'runtime/python/python.exe'):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b'fixture only; never executed')
        local = self.root/'local-app-data'
        bootstrap = local/'MediaFlow/bootstrap.json'
        bootstrap.parent.mkdir(parents=True)
        bootstrap.write_text(json.dumps({'data_root': str(self.root/'installed-data')}), encoding='utf-8')
        profile = self.root/'source-profile.json'
        payload = {'schema': 1, 'id': 'production', 'app_root': str(source),
                   'development_source': True, 'acceptance': False}
        profile.write_text(json.dumps(payload), encoding='utf-8')
        environment = {key: value for key, value in os.environ.items() if not key.startswith(('MEDIAFLOW_', 'RISKFLOW_'))}
        environment.update(LOCALAPPDATA=str(local), MEDIAFLOW_INSTANCE_PROFILE=str(profile))
        program = ('import runtime_layout as r,runtime_control as c,json; print(json.dumps({'
                   '"source":r.DEVELOPMENT_SOURCE,"distribution":r.IS_DISTRIBUTION,"data":str(r.DATA_ROOT),'
                   '"secret":str(r.SECRET_ROOT),"device":str(r.DEVICE_PROFILE_PATH),'
                   '"python":str(c.PROJECT_PYTHON),"verified_python":str(c.project_python_executable()),'
                   '"env":r.tool_environment()["MEDIAFLOW_DEVELOPMENT_SOURCE"]}))')
        def read():
            return subprocess.run([sys.executable, '-c', program], cwd=runner, env=environment,
                                  capture_output=True, text=True, timeout=5)
        result = read()
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['source'])
        self.assertFalse(value['distribution'])
        self.assertEqual(runner/'runtime', Path(value['data']))
        self.assertEqual(source/'.secrets', Path(value['secret']))
        self.assertEqual(runner/'device_profiles.json', Path(value['device']))
        self.assertEqual(source/'.venv/Scripts/python.exe', Path(value['python']))
        self.assertEqual(Path(value['python']), Path(value['verified_python']))
        self.assertEqual('1', value['env'])
        environment.pop('MEDIAFLOW_INSTANCE_PROFILE')
        environment['MEDIAFLOW_DEVELOPMENT_SOURCE'] = '1'
        self.assertEqual(0, read().returncode, 'explicit source environment must work without profile')
        environment['MEDIAFLOW_INSTANCE_PROFILE'] = str(profile)
        environment.pop('MEDIAFLOW_DEVELOPMENT_SOURCE')
        for marker in (source/'release-manifest.json', source.parent/'app-update.yml'):
            marker.write_text('package fixture', encoding='utf-8')
            result = read()
            self.assertNotEqual(0, result.returncode)
            self.assertIn('verified production source checkout', result.stderr)
            marker.unlink()
        payload.update(id='acceptance-test', acceptance=True, api_port=49338, ui_port=31301,
                       stream_port=49339, data_root=str(self.root/'acceptance-data'))
        profile.write_text(json.dumps(payload), encoding='utf-8')
        self.assertNotEqual(0, read().returncode, 'acceptance must never opt into source credentials')

    def test_acceptance_does_not_discover_devices_or_create_worker_specs(self):
        import background_host
        import control_api
        import runtime_control
        with patch.object(background_host, 'ACCEPTANCE', True), patch.object(control_api, 'ACCEPTANCE', True), \
             patch.object(runtime_control, 'ACCEPTANCE', True), patch('background_host.subprocess.run') as run, \
             patch('control_api.resolve_adb_executable') as resolve, patch('runtime_control.load_openrouter_key') as key:
            self.assertEqual(set(), background_host.online_adb_device_ids())
            self.assertEqual({}, control_api.adb_device_states())
            self.assertIsNone(control_api.status_runtime_signature('test-device'))
            self.assertIsNone(runtime_control.optional_analyzer_spec())
            with self.assertRaises(RuntimeError):
                runtime_control.worker_spec('test-device')
            with patch('model_providers.selection', side_effect=AssertionError('model configuration reached')), \
                 patch('runtime_control.migrate_secret', side_effect=AssertionError('credential migration reached')), \
                 patch('runtime_control.background_task_check', return_value=(True, 'test')):
                checks = runtime_control.doctor()['checks']
            self.assertTrue(next(check['ok'] for check in checks if check['name'] == 'model_access_disabled'))
            run.assert_not_called()
            resolve.assert_not_called()
            key.assert_not_called()

    def test_acceptance_management_gets_never_construct_inventory_or_read_model_credentials(self):
        class IsolatedHandler(Handler):
            store = self.store
        server = ThreadingHTTPServer(('127.0.0.1', 0), IsolatedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        local = self.root/'local-app-data'
        production = local/'MediaFlow/bootstrap.json'
        production.parent.mkdir(parents=True)
        production.write_text(json.dumps({'data_root': str(self.root/'production-data')}), encoding='utf-8')
        try:
            with patch('control_api.ACCEPTANCE', True), patch('runtime_layout.ACCEPTANCE', True), \
                 patch('runtime_layout.INSTANCE_ID', 'acceptance-test'), patch('runtime_layout.DATA_ROOT', self.root/'data'), \
                 patch.dict(os.environ, {'LOCALAPPDATA': str(local)}), \
                 patch('control_api.VirtualDeviceInventory', side_effect=AssertionError('real inventory reached')) as inventory, \
                 patch('control_api.resolve_adb_executable', side_effect=AssertionError('ADB reached')) as adb, \
                 patch('model_providers.database', side_effect=AssertionError('model maintenance reached')) as models, \
                 patch('model_providers._key', side_effect=AssertionError('credential reached')) as key:
                for endpoint in ('/api/virtual-devices/unmanaged', '/api/device-onboarding', '/api/model',
                                 '/api/status', '/api/system/storage', '/api/workbench/draft'):
                    with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}' + endpoint, timeout=5) as response:
                        result = json.load(response)
                    if endpoint == '/api/system/storage':
                        self.assertEqual(str(self.root/'data'), result['data_root'])
                        self.assertFalse(result['legacy_available'])
                        self.assertIn('acceptance-test', result['bootstrap_path'])
                    elif endpoint == '/api/virtual-devices/unmanaged':
                        self.assertEqual([], result['instances'])
                    elif endpoint == '/api/model':
                        self.assertEqual('acceptance_mode', result['reason_code'])
                inventory.assert_not_called()
                adb.assert_not_called()
                models.assert_not_called()
                key.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_update_gate_rejects_inventory_gets_before_device_or_provider_access(self):
        class IsolatedHandler(Handler):
            store = self.store
        server = ThreadingHTTPServer(('127.0.0.1', 0), IsolatedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        operation = self.prepare()
        try:
            with patch('control_api.VirtualDeviceInventory', side_effect=AssertionError('inventory escaped gate')) as inventory, \
                 patch('control_api.device_statuses', side_effect=AssertionError('device probe escaped gate')) as device:
                for endpoint in ('/api/virtual-devices/unmanaged', '/api/device-onboarding'):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}' + endpoint, timeout=3)
                    self.assertEqual(409, caught.exception.code)
                    self.assertEqual(operation['operation_id'], json.load(caught.exception)['operation']['operation_id'])
                inventory.assert_not_called()
                device.assert_not_called()
            self.assertEqual('ready', self.store.runtime_update_status(operation['id'])['status'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_desktop_source_bundle_contains_installer_code_and_excludes_local_data(self):
        from repair_materials import build_bundle
        included = ('desktop/src/main.cjs', 'desktop/installer/setup.nsh', 'desktop/installer/setup.nsi')
        excluded = ('desktop/node_modules/private.cjs', 'desktop/dist/private.cjs',
                    'desktop/work/instance.json', 'fixed_runner/runtime/tasks.json')
        for name in (*included, *excluded):
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture', encoding='utf-8')
        result = build_bundle(self.root, self.root/'source.zip', 'fixture')
        self.assertEqual(set(included), set(result['files']))


class VerifiedStopTests(unittest.TestCase):
    def test_active_legacy_host_cannot_obey_new_gate_and_is_refused_without_stop_request(self):
        import background_host
        executable = Path('python.exe').resolve()
        snapshot = ProcessSnapshot(55, str(executable), 'legacy-creation', 'background_host.py run')
        inspector = SimpleNamespace(snapshot=lambda _: snapshot)
        control = SimpleNamespace(roles=lambda: [])
        with patch('background_host.RuntimeControl', return_value=control), \
             patch('background_host.SystemProcessInspector', return_value=inspector), \
             patch('background_host.read_heartbeat', return_value={'state': 'running', 'pid': 55}), \
             patch('background_host.request_stop', side_effect=AssertionError('legacy stop must not be requested')) as request:
            result = background_host.stop_and_wait(0.01)
        self.assertFalse(result['ok'])
        self.assertFalse(result['host_exited'])
        self.assertEqual('legacy_host_gate_unavailable', result['reason_code'])
        self.assertIn('旧版本正常的后台停止入口', result['error'])
        request.assert_not_called()

    def test_stopped_heartbeat_does_not_prove_host_pid_exit(self):
        import background_host
        executable = Path('python.exe').resolve()
        snapshot = ProcessSnapshot(55, str(executable), 'host-creation', 'background_host.py run')
        inspector = SimpleNamespace(snapshot=lambda _: snapshot)
        control = SimpleNamespace(roles=lambda: [], status=lambda _: self.fail('unexpected role'))
        heartbeat = {'state': 'stopped', 'pid': 55, 'creation_token': 'host-creation'}
        with patch('background_host.RuntimeControl', return_value=control), \
             patch('background_host.SystemProcessInspector', return_value=inspector), \
             patch('background_host.project_python_executable', return_value=executable), \
             patch('background_host.read_heartbeat', return_value=heartbeat), \
             patch('background_host.request_stop', return_value={'ok': True}):
            result = background_host.stop_and_wait(0.01)
        self.assertFalse(result['ok'])
        self.assertFalse(result['host_exited'])
        self.assertEqual('stop_failed', result['state'])

    def test_sync_stop_succeeds_only_after_original_host_exits(self):
        import background_host
        executable = Path('python.exe').resolve()
        snapshot = ProcessSnapshot(55, str(executable), 'host-creation', 'background_host.py run')
        inspector = SimpleNamespace(snapshot=unittest.mock.Mock(side_effect=[snapshot, None]))
        control = SimpleNamespace(roles=lambda: [], status=lambda _: self.fail('unexpected role'))
        heartbeat = {'state': 'stopped', 'pid': 55, 'creation_token': 'host-creation'}
        with patch('background_host.RuntimeControl', return_value=control), \
             patch('background_host.SystemProcessInspector', return_value=inspector), \
             patch('background_host.project_python_executable', return_value=executable), \
             patch('background_host.read_heartbeat', return_value=heartbeat), \
             patch('background_host.request_stop', return_value={'ok': True}):
            result = background_host.stop_and_wait(0.01)
        self.assertTrue(result['ok'])
        self.assertTrue(result['host_exited'])

    def test_sync_stop_failure_has_nonzero_cli_result(self):
        import background_host
        from io import StringIO
        with patch('background_host.stop_and_wait', return_value={'ok': False, 'state': 'stop_failed'}), \
             patch.object(sys, 'argv', ['background_host.py', 'stop', '--timeout-seconds', '1']), \
             patch('sys.stdout', new_callable=StringIO) as output:
            self.assertEqual(1, background_host.main())
        self.assertEqual('stop_failed', json.loads(output.getvalue())['state'])

    def test_terminator_return_does_not_prove_process_exit_and_registry_survives(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = ProcessSnapshot(55, 'python.exe', 'creation', 'worker.py test-device')
            inspector = SimpleNamespace(snapshot=lambda _: snapshot)
            control = RuntimeControl(Path(temporary), inspector=inspector, launcher=lambda _: 55,
                                     terminator=lambda _: None, stop_timeout_seconds=0.01)
            spec = ProcessSpec('worker-test', ('python.exe', 'worker.py'), temporary,
                               str(Path(temporary)/'log'), 'python.exe', ('worker.py',))
            control.start(spec)
            with self.assertRaisesRegex(RuntimeError, '仍存活'):
                control.stop('worker-test')
            self.assertTrue(control._record_path('worker-test').is_file())

    def test_supervisor_reports_stop_failed_for_any_registered_role(self):
        from background_host import RuntimeSupervisor
        with tempfile.TemporaryDirectory() as temporary:
            control = SimpleNamespace(roles=lambda: ['extra-maintenance'],
                stop=lambda role: {'role': role, 'running': role == 'extra-maintenance'})
            supervisor = RuntimeSupervisor(control=control, store=TaskStore(Path(temporary)/'tasks.db'),
                heartbeat_path=Path(temporary)/'heartbeat.json', stop_request_path=Path(temporary)/'stop.json',
                log_event=lambda *_args, **_kwargs: None)
            result = supervisor.request_shutdown()
            self.assertFalse(result['ok'])
            self.assertEqual('stop_failed', result['state'])

    def test_supervisor_refuses_running_initialization_without_stopping_roles(self):
        from background_host import RuntimeSupervisor
        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary)/'tasks.db')
            store.create_initialization('test-device')
            store.claim_initialization('test-device', 'worker')
            control = SimpleNamespace(roles=lambda: [], stop=lambda _: self.fail('must not stop'))
            supervisor = RuntimeSupervisor(control=control, store=store,
                heartbeat_path=Path(temporary)/'heartbeat.json', stop_request_path=Path(temporary)/'stop.json',
                log_event=lambda *_args, **_kwargs: None)
            result = supervisor.request_shutdown()
            self.assertEqual('stop_refused', result['state'])
            self.assertEqual('initialization', result['blockers'][0]['kind'])
