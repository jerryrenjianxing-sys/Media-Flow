import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from private_template_payload import append_payload, read_payload, extract_payload, validate_manifest


class SettingsInputTests(unittest.TestCase):
    def test_actual_android15_search_handoff_and_chinese_readback(self):
        from private_vm_template import settings_input_check
        device = Mock()
        screenshot = Mock(size=(900, 1600))
        device.screenshot.return_value = screenshot
        state = {'search': False, 'text': ''}
        landing = '<hierarchy><node text="在设置中搜索" resource-id="com.android.settings:id/search_bar_title" class="android.widget.TextView" package="com.android.settings"/></hierarchy>'
        search = '<hierarchy><node text="搜索…" hint="搜索…" resource-id="android:id/search_src_text" class="android.widget.AutoCompleteTextView" package="com.android.settings.intelligence"/></hierarchy>'
        device.dump_hierarchy.side_effect = lambda **kw: search if state['search'] else landing
        device.app_current.side_effect = lambda: {'package': 'com.android.settings.intelligence' if state['search'] else 'com.android.settings'}
        button, field = Mock(), Mock()
        button.click.side_effect = lambda: state.update(search=True)
        field.set_text.side_effect = lambda value: state.update(text=value)
        field.get_text.side_effect = lambda: state['text'] or '搜索…'
        field.clear_text.side_effect = lambda: state.update(text='')
        device.side_effect = lambda **kw: field if kw.get('resourceId') == 'android:id/search_src_text' else button
        ticks = iter(range(0, 100, 2))
        with tempfile.TemporaryDirectory() as directory, patch('uiautomator2.connect', return_value=device), \
                patch('douyin_fixed_runner.DeviceLock'), patch('private_vm_template.package_installed', return_value=True), \
                patch('private_vm_template.package_version', return_value={'version_name': 'test'}), \
                patch('private_vm_template.adb', side_effect=lambda address,*args: 'Physical density: 320' if args[-1] == 'density' else '15'), \
                patch('private_vm_template.time.sleep'), patch('private_vm_template.time.monotonic', side_effect=lambda: next(ticks)):
            result = settings_input_check('test-only', directory)
        self.assertTrue(result['chinese_input_verified'])
        field.set_text.assert_called_once_with('MediaFlow中文输入验收')
        field.clear_text.assert_called_once()
        button.click.assert_called_once()


class MuMuAsyncArchiveTests(unittest.TestCase):
    def test_rpc_timeout_is_not_a_second_export_command(self):
        from virtual_devices import MuMuProvider, CommandResult
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = MuMuProvider(root / 'MuMuManager.exe')
            def run(*args, **kwargs):
                (root / 'template.mumudata').write_bytes(b'completed archive fixture')
                return CommandResult(list(args), 1, '{"errcode":-502,"errmsg":"mainnx request failed"}', '')
            with patch.object(provider, '_run', side_effect=run) as run_command, \
                    patch('mumu_archive.wait_export', return_value=root/'template.mumudata') as wait:
                result = provider.export_backup('7', root, 'template')
            self.assertEqual(result.name, 'template.mumudata')
            self.assertEqual(run_command.call_count, 1)
            wait.assert_called_once()


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / 'setup.exe'
        self.exe.write_bytes(b'MZ' + b'x' * 100)
        self.vm = self.root / 'template.mumudata'
        self.vm.write_bytes(b'private-test-snapshot')
        self.manifest = dict(format_version=1, source='private_snapshot', template_version='private-test-1',
                             file_name='template.mumudata', size_bytes=self.vm.stat().st_size,
                             sha256=hashlib.sha256(self.vm.read_bytes()).hexdigest(),
                             expanded_size_bytes=100, width=900, height=1600, dpi=320,
                             android_engine='15', app_version='test', private_data_possible=True)

    def test_roundtrip_and_stream_validation(self):
        append_payload(self.exe, self.vm, self.manifest)
        meta = read_payload(self.exe)
        self.assertEqual(meta['offset'], 102)
        output = extract_payload(self.exe, self.root / 'cache')
        self.assertEqual(output.read_bytes(), self.vm.read_bytes())
        self.assertEqual(json.loads((output.parent / 'manifest.json').read_text())['source'], 'private_snapshot')
        with self.assertRaises(ValueError):
            append_payload(self.exe, self.vm, self.manifest)

    def test_corruption_and_insufficient_space_do_not_activate(self):
        append_payload(self.exe, self.vm, self.manifest)
        with self.exe.open('r+b') as stream:
            stream.seek(103)
            stream.write(b'!')
        with self.assertRaises(ValueError):
            extract_payload(self.exe, self.root / 'bad')
        self.assertFalse((self.root / 'bad' / self.manifest['sha256'] / 'manifest.json').exists())
        with patch('private_template_payload.shutil.disk_usage', return_value=Mock(free=0)), self.assertRaises(ValueError):
            extract_payload(self.exe, self.root / 'full')

    def test_manifest_rejects_traversal_bad_size_and_clean_claim(self):
        for changes in ({'file_name': '../escape'}, {'size_bytes': -1}, {'size_bytes': True},
                        {'sha256': 'x'}, {'private_data_possible': False}, {'width': 1920}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_manifest({**self.manifest, **changes})

    def test_64_bit_offset_with_sparse_file(self):
        with self.exe.open('r+b') as stream:
            stream.truncate(2**32 + 1024)
        with self.assertRaisesRegex(ValueError, 'Windows'):
            append_payload(self.exe, self.vm, self.manifest)
        append_payload(self.exe, self.vm, self.manifest, enforce_executable_limit=False)
        self.assertGreater(read_payload(self.exe)['offset'], 2**32)
        self.assertEqual(extract_payload(self.exe, self.root / 'big').read_bytes(), self.vm.read_bytes())


class PrivateImportTests(PayloadTests):
    def setup_service(self):
        from local_vm_template import LocalVmTemplate, PROFILE
        from task_store import TaskStore
        self.store = TaskStore(self.root / 'tasks.db')
        self.store.save_profile(PROFILE, {'status': 'ready', 'template_version': 'old-private', 'untouched': True})
        self.store.set_paused(True)
        self.vmroot = self.root / 'vms'
        self.vmroot.mkdir()
        self.manifest_file = self.root / 'manifest.json'
        self.manifest_file.write_text(json.dumps(self.manifest), encoding='utf-8')
        with patch('local_vm_template.resolve_mumu_manager', return_value=self.root / 'MuMuManager.exe'):
            self.service = LocalVmTemplate(self.store)
        self.service.provider = Mock()
        self.service.provider.probe.return_value = {'compatible': True}
        self.service.provider.list_instances.return_value = []
        self.service.provider.start_and_resolve_adb.return_value = 'new-endpoint'
        def imported(_):
            disk = self.vmroot / 'new-vm'
            disk.mkdir()
            (disk / 'data.vdi').write_bytes(b'private snapshot restored')
            self.service.provider.list_instances.return_value = [{'provider_instance_id': '0', 'state': 'stopped'}]
            return {'provider_instance_id': '0', 'name': 'private-vm', 'state': 'stopped'}
        self.service.provider.import_backup.side_effect = imported
        self.op, _ = self.store.create_virtual_operation('template_prepare', {'virtual_device_id': 'local-template-creation'}, idempotency_key='private')

    def test_import_business_entry_activates_only_after_actual_validator(self):
        from local_vm_template import PROFILE
        self.setup_service()
        with patch('private_vm_template.settings_input_check', return_value={'version_name': 'test', 'chinese_input_verified': True}) as validator:
            result = self.service.create(self.op['id'], 'template', 0, prepare_only=True, private_manifest=self.manifest_file)
        self.assertEqual(result['status'], 'ready')
        validator.assert_called_once()
        current = self.store.get_profile(PROFILE)
        self.assertTrue(current['private_data_possible'])
        self.assertEqual(current['checks']['chinese_input_verified'], True)
        self.assertIsNone(self.store.get_virtual_device(current['virtual_device_id'])['android_identity'])
        with patch('private_vm_template.settings_input_check') as validator:
            self.service.create(self.op['id'], 'template', 0, prepare_only=True, private_manifest=self.manifest_file)
        validator.assert_not_called()
        self.assertEqual(self.service.provider.import_backup.call_count, 1)

    def test_failed_input_retains_old_default_and_unknown_result_never_replays(self):
        from local_vm_template import PROFILE
        self.setup_service()
        with patch('private_vm_template.settings_input_check', side_effect=RuntimeError('input readback failed')):
            with self.assertRaises(RuntimeError):
                self.service.create(self.op['id'], 'template', 0, private_manifest=self.manifest_file)
        self.assertTrue(self.store.get_profile(PROFILE)['untouched'])
        with self.assertRaises(ValueError):
            self.service.create(self.op['id'], 'template', 0, private_manifest=self.manifest_file)
        self.assertEqual(self.service.provider.import_backup.call_count, 1)
        self.assertEqual(len(self.store.list_virtual_devices()), 1)

    def test_insufficient_vm_space_never_imports(self):
        self.setup_service()
        with patch('private_vm_template.shutil.disk_usage', return_value=Mock(free=0)), self.assertRaises(ValueError):
            self.service.create(self.op['id'], 'template', 0, private_manifest=self.manifest_file)
        self.service.provider.import_backup.assert_not_called()

    def test_cancel_before_import_does_not_call_provider(self):
        self.setup_service()
        cancellation = self.root / 'cancel'
        cancellation.touch()
        op, _ = self.store.create_virtual_operation('template_prepare', {'cancel_file': str(cancellation)}, idempotency_key='cancel-test')
        with self.assertRaises(RuntimeError):
            self.service.create(op['id'], 'template', 0, private_manifest=self.manifest_file)
        self.service.provider.import_backup.assert_not_called()

    def test_cli_does_not_import_when_queue_unpaused(self):
        from private_template_cli import execute
        self.setup_service()
        self.store.set_paused(False)
        with self.assertRaisesRegex(ValueError, '暂停'):
            execute(self.manifest_file, self.store)

    def test_cli_failure_has_durable_terminal_receipt(self):
        from private_template_cli import execute
        self.setup_service()
        self.store.update_virtual_operation(self.op['id'], status='cancelled', stage='cancelled', progress=100)
        with patch('local_vm_template.LocalVmTemplate.create', side_effect=RuntimeError('fixture import failed')):
            with self.assertRaisesRegex(RuntimeError, 'fixture import failed'):
                execute(self.manifest_file, self.store)
        self.assertFalse(self.store.list_active_virtual_operations())

    def test_api_restart_preserves_live_installer_but_not_reused_pid(self):
        from virtual_device_inventory import VirtualDeviceInventory
        from runtime_control import ProcessSnapshot
        self.setup_service()
        self.store.update_virtual_operation(self.op['id'], status='cancelled', stage='cancelled', progress=100)
        owner = {'pid': 123, 'executable': 'python.exe', 'creation_token': 'original'}
        op, _ = self.store.create_virtual_operation('template_prepare', {'installer_owner': owner}, idempotency_key='live-installer')
        inventory = VirtualDeviceInventory(self.store)
        with patch('runtime_control.SystemProcessInspector.snapshot', return_value=ProcessSnapshot(**owner)):
            inventory.reconcile_incomplete_operations()
        self.assertEqual(self.store.get_virtual_operation(op['id'])['status'], 'queued')
        with patch('runtime_control.SystemProcessInspector.snapshot', return_value=ProcessSnapshot(**{**owner, 'creation_token': 'other'})):
            inventory.reconcile_incomplete_operations()
        self.assertEqual(self.store.get_virtual_operation(op['id'])['status'], 'waiting_user')
