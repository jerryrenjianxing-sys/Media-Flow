import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from local_vm_template import LocalVmTemplate, PROFILE, disk_seal
from task_store import TaskStore
from template_apks import package_paths, install_bundle


class TemplateTests(unittest.TestCase):
    def test_fresh_creation_can_reuse_missing_number_without_inheriting_identity(self):
        old = {"virtual_device_id": "old", "provider": "mumu", "provider_instance_id": "3",
               "name": "old account VM", "state": "stopped", "presence_status": "missing",
               "android_identity": "old-android", "recipe": {"old": True}}
        self.store.save_virtual_device(old)
        op, _ = self.store.create_virtual_operation("template_prepare", {}, idempotency_key="fresh")
        self.store.update_virtual_operation(op["id"], status="running", stage="template_creating", progress=20)
        new = {"virtual_device_id": "new", "provider": "mumu", "provider_instance_id": "3",
               "name": "clean template", "state": "stopped", "recipe": {"is_template": True}}
        self.store.save_virtual_device(new, fresh_creation={"operation_id": op["id"], "before_ids": ["0", "1", "2"], "created_ids": ["3"]})
        self.assertIsNone(self.store.get_virtual_device("new")["android_identity"])
        previous = next(item for item in self.store.list_virtual_devices() if item["virtual_device_id"] == "old")
        self.assertEqual(previous["android_identity"], "old-android")
        self.assertEqual(previous["recipe"], {"old": True})
        self.assertEqual(previous["state"], "retired")

    def test_creation_proof_does_not_replace_present_or_ambiguous_instance(self):
        self.store.save_virtual_device({"virtual_device_id": "old", "provider": "mumu", "provider_instance_id": "3", "name": "existing"})
        op, _ = self.store.create_virtual_operation("template_prepare", {}, idempotency_key="proof")
        self.store.update_virtual_operation(op["id"], status="running", stage="template_creating", progress=20)
        new = {"virtual_device_id": "new", "provider": "mumu", "provider_instance_id": "3", "name": "new"}
        for before, created in ((["1"], ["3"]), (["3"], ["3"]), (["1"], ["3", "4"])):
            with self.assertRaises(ValueError):
                self.store.save_virtual_device(new, fresh_creation={"operation_id": op["id"], "before_ids": before, "created_ids": created})
        self.assertEqual(self.store.get_virtual_device("old")["provider_instance_id"], "3")

    def test_clean_android_absent_app_is_not_adb_failure(self):
        import template_apks
        with patch("template_apks.adb", return_value="") as command:
            self.assertFalse(template_apks.package_installed("new-vm"))
        command.assert_called_once_with("new-vm", "shell", "pm", "list", "packages", template_apks.PACKAGE)

    def test_package_presence_exact_match_and_transport_errors(self):
        import template_apks
        with patch("template_apks.adb", return_value="package:com.ss.android.ugc.aweme.lite"):
            self.assertFalse(template_apks.package_installed("new-vm"))
        with patch("template_apks.adb", return_value="package:com.ss.android.ugc.aweme"):
            self.assertTrue(template_apks.package_installed("new-vm"))
        with patch("template_apks.adb", side_effect=RuntimeError("device offline")), self.assertRaises(RuntimeError):
            template_apks.package_installed("new-vm")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root/"tasks.db")

    def test_apk_extraction_rejects_private_data_and_accepts_splits(self):
        with patch("template_apks.adb", return_value="package:/data/app/pkg/base.apk\npackage:/data/app/pkg/split_config.apk"):
            self.assertEqual(len(package_paths("vm")), 2)
        for bad in ("package:/data/user/0/pkg/login.apk", "package:/data/app/../secret.apk", "package:/data/app/base.apk\npackage:/data/app/base.apk"):
            with patch("template_apks.adb", return_value=bad), self.assertRaises(ValueError):
                package_paths("vm")

    def test_altered_apk_never_reaches_install(self):
        bundle = self.root/"apks"
        bundle.mkdir()
        (bundle/"part-0.apk").write_bytes(b"tampered")
        with patch("template_apks.adb") as adb, self.assertRaises((ValueError, RuntimeError)):
            install_bundle("new-vm", bundle, {"files": [{"name": "part-0.apk", "sha256": "wrong"}]})
        adb.assert_not_called()

    def template(self):
        storage = self.root/"vm-disk"
        storage.mkdir()
        (storage/"data.vdi").write_bytes(b"clean test disk")
        self.store.save_virtual_device({"virtual_device_id": "template", "provider": "mumu", "provider_instance_id": "0",
            "name": "MediaFlow本机模板", "state": "stopped", "recipe": {"is_template": True}, "provider_snapshot": {}, "managed": True})
        state = {"status": "ready", "virtual_device_id": "template", "storage": str(storage), "disk_seal": disk_seal(storage), "template_version": "test-v1"}
        self.store.save_profile(PROFILE, state)
        operation, _ = self.store.create_virtual_operation("template_create", {"virtual_device_id": "local-template-creation"}, idempotency_key="test")
        with patch("local_vm_template.resolve_mumu_manager", return_value=self.root/"MuMuManager.exe"):
            service = LocalVmTemplate(self.store)
        service.provider = Mock()
        service.provider.list_instances.return_value = [{"provider_instance_id": "0", "state": "stopped"}]
        service.inventory = Mock()
        return service, operation, storage

    def test_template_is_not_business_inventory(self):
        self.template()
        self.assertEqual(self.store.list_managed_virtual_devices(), [])
        self.assertEqual(len(self.store.list_virtual_devices()), 1)

    def test_changed_template_is_not_cloned(self):
        service, operation, storage = self.template()
        (storage/"data.vdi").write_bytes(b"changed account disk")
        with self.assertRaisesRegex(ValueError, "模板已被"):
            service.create(operation["id"], "MediaFlow虚拟机1", 1)
        service.inventory.clone.assert_not_called()

    def test_unknown_result_does_not_rebuild_on_retry(self):
        service, operation, _ = self.template()
        self.store.save_profile(PROFILE, {"status": "preparing"})
        with patch.object(service, "_build") as build, self.assertRaisesRegex(ValueError, "不会自动重放"):
            service.create(operation["id"], "MediaFlow虚拟机1", 1)
        build.assert_not_called()

    def test_all_stop_blocks_template_before_clone(self):
        service, operation, _ = self.template()
        self.store.save_profile("automation-stop", {"stopped": True})
        with self.assertRaisesRegex(RuntimeError, "所有自动操作"):
            service.create(operation["id"], "MediaFlow虚拟机1", 1)
        service.inventory.clone.assert_not_called()

    def test_cancellation_keeps_lease_until_executor_checkpoint(self):
        from local_vm_template import TemplateCancelled
        service, operation, _ = self.template()
        self.store.update_virtual_operation(operation["id"], status="running", stage="template_verifying", progress=20)
        result = self.store.request_template_cancellation(operation["id"])
        self.assertEqual(result["status"], "running")
        with self.assertRaises(TemplateCancelled):
            service.create(operation["id"], "MediaFlow虚拟机1", 1)
        service.inventory.clone.assert_not_called()
