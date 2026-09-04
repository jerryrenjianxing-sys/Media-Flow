from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_store import TaskStore
from virtual_devices import (
    CommandResult,
    MuMuProvider,
    REQUIRED_COMMANDS,
    STANDARD_RECIPE,
    _candidate_manager_paths,
    _path_from_command,
    resolve_mumu_manager,
)
from virtual_device_inventory import VirtualDeviceInventory


class FakeMuMuProvider:
    SETTING_KEYS = MuMuProvider.SETTING_KEYS
    _setting_value = staticmethod(MuMuProvider._setting_value)
    _settings_mismatches = staticmethod(MuMuProvider._settings_mismatches)

    def __init__(self, instances: list[dict] | None = None, endpoint: str = "127.0.0.1:16416") -> None:
        self.instances = instances or []
        self.endpoint = endpoint
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.renamed: list[tuple[str, str]] = []
        self.settings_applied: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.cloned: list[str] = []
        self.clone_result = {"provider_instance_id": "3", "name": "克隆", "state": "stopped"}
        self.import_result = {"provider_instance_id": "4", "name": "恢复", "state": "stopped"}
        self.settings = {
            MuMuProvider.SETTING_KEYS[key]: MuMuProvider._setting_value(key, value)
            for key, value in STANDARD_RECIPE.items()
            if key in MuMuProvider.SETTING_KEYS
        }

    def probe(self, _custom_path=None) -> dict:
        return {"provider": "mumu", "status": "ready", "compatible": True}

    def list_instances(self) -> list[dict]:
        return [dict(item) for item in self.instances]

    def start_and_resolve_adb(self, instance_id: str, *, already_running: bool = False) -> str:
        self.started.append(instance_id)
        return self.endpoint

    def launch(self, instance_id: str) -> None:
        self.started.append(instance_id)
        for instance in self.instances:
            if str(instance.get("provider_instance_id")) == str(instance_id):
                instance["state"] = "running"

    def stop(self, instance_id: str):
        self.stopped.append(instance_id)
        for instance in self.instances:
            if str(instance.get("provider_instance_id")) == str(instance_id):
                instance["state"] = "stopped"
        return None

    def rename(self, instance_id: str, name: str):
        self.renamed.append((instance_id, name))
        return None

    def apply_settings(self, instance_id: str, settings: dict):
        self.settings_applied.append((instance_id, dict(settings)))
        self.settings.update({
            MuMuProvider.SETTING_KEYS[key]: MuMuProvider._setting_value(key, value)
            for key, value in settings.items()
        })
        return dict(self.settings)

    def read_settings(self, _instance_id: str) -> dict:
        return dict(self.settings)

    def clone(self, _instance_id: str) -> dict:
        self.cloned.append(_instance_id)
        return dict(self.clone_result)

    def export_backup(self, _instance_id: str, directory: Path, name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.mumudata"
        path.write_bytes(b"verified-backup")
        return path

    def import_backup(self, _backup_path: Path) -> dict:
        return dict(self.import_result)

    def delete(self, instance_id: str) -> None:
        self.deleted.append(instance_id)
        self.instances = [
            item for item in self.instances
            if str(item.get("provider_instance_id")) != str(instance_id)
        ]


class VirtualDeviceProviderTests(unittest.TestCase):
    def test_parse_instances_includes_stopped_instances(self) -> None:
        provider = MuMuProvider(Path("MuMuManager.exe"))
        payload = provider._json(
            'log\n{"2":{"index":"2","name":"测试机","is_process_started":false,"is_android_started":false}}'
        )
        self.assertEqual(payload["2"]["name"], "测试机")

    def test_missing_custom_directory_never_becomes_executable(self) -> None:
        with patch("virtual_devices._registry_install_candidates", return_value=[]), patch(
            "virtual_devices._running_process_candidates", return_value=[]
        ):
            result = resolve_mumu_manager(r"Z:\does-not-exist")
        self.assertTrue(result is None or result.name.lower() == "mumumanager.exe")
        self.assertFalse(result and str(result).lower().startswith("z:\\does-not-exist"))

    def test_registry_command_path_parser_accepts_quotes_and_arguments(self) -> None:
        self.assertEqual(
            _path_from_command(r'"D:\Apps\MuMu\uninstall.exe" /S'),
            Path(r"D:\Apps\MuMu\uninstall.exe"),
        )

    def test_candidate_expansion_finds_shell_and_nx_main_layouts(self) -> None:
        candidates = _candidate_manager_paths(Path(r"D:\Apps\MuMu"))
        rendered = {str(candidate).lower() for candidate in candidates}
        self.assertIn(str(Path(r"D:\Apps\MuMu\shell\MuMuManager.exe")).lower(), rendered)
        self.assertIn(str(Path(r"D:\Apps\MuMu\nx_main\MuMuManager.exe")).lower(), rendered)

    def test_other_pc_program_files_netease_layout_is_supported(self) -> None:
        candidates = _candidate_manager_paths(Path(r"D:\Program Files\Netease\MuMu"))
        rendered = {str(candidate).lower() for candidate in candidates}
        self.assertIn(
            str(Path(r"D:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe")).lower(),
            rendered,
        )

    def test_unknown_version_is_accepted_after_capability_verification(self) -> None:
        provider = MuMuProvider(Path("MuMuManager.exe"))
        help_text = " ".join(sorted(REQUIRED_COMMANDS))
        with patch.object(provider, "_run") as run:
            run.side_effect = [
                CommandResult(["version"], 0, '{"version":"9.9.9.9"}', ""),
                CommandResult(["--help"], 0, help_text, ""),
            ]
            result = provider.probe()
        self.assertTrue(result["compatible"])
        self.assertEqual(result["compatibility_status"], "capability_verified")

    def test_standard_recipe_is_zero_write_infrastructure_only(self) -> None:
        self.assertEqual(STANDARD_RECIPE["android_version"], "15")
        self.assertTrue(STANDARD_RECIPE["root"])
        self.assertNotIn("account", STANDARD_RECIPE)

    def test_setting_verification_accepts_equivalent_numeric_values(self) -> None:
        mismatches = MuMuProvider._settings_mismatches(
            {
                "performance_mem.custom": "1.750000",
                "resolution_width.custom": "900.000000",
                "root_permission": "true",
            },
            {
                "performance_mem.custom": "1.75",
                "resolution_width.custom": "900",
                "root_permission": "true",
            },
        )
        self.assertEqual(mismatches, [])

    def test_setting_verification_reports_missing_or_wrong_values(self) -> None:
        mismatches = MuMuProvider._settings_mismatches(
            {"root_permission": "false"},
            {"root_permission": "true", "window_auto_rotate": "false"},
        )
        self.assertEqual(len(mismatches), 2)

    def test_apply_settings_uses_allowlist_and_verifies_readback(self) -> None:
        provider = MuMuProvider(Path("MuMuManager.exe"))
        with patch.object(provider, "_run") as run, patch.object(
            provider,
            "read_settings",
            return_value={"performance_cpu.custom": "4", "root_permission": "false"},
        ):
            actual = provider.apply_settings("7", {"cpu": 4, "root": False})
        self.assertEqual(actual["performance_cpu.custom"], "4")
        self.assertEqual(
            run.call_args.args,
            (
                "setting", "-v", "7", "-k", "performance_cpu.custom", "-val", "4",
                "-k", "root_permission", "-val", "false",
            ),
        )
        with self.assertRaisesRegex(ValueError, "不支持"):
            provider.apply_settings("7", {"arbitrary_command": "bad"})

    def test_clone_requires_one_unique_new_instance(self) -> None:
        provider = MuMuProvider(Path("MuMuManager.exe"))
        with patch.object(
            provider,
            "list_instances",
            side_effect=[
                [{"provider_instance_id": "1"}],
                [{"provider_instance_id": "1"}, {"provider_instance_id": "2"}],
            ],
        ), patch.object(provider, "_run") as run:
            created = provider.clone("1")
        self.assertEqual(created["provider_instance_id"], "2")
        self.assertEqual(run.call_args.args, ("clone", "-v", "1", "-n", "1"))

    def test_window_visibility_uses_target_instance_without_manager_launch(self) -> None:
        provider = MuMuProvider(Path("MuMuManager.exe"))
        with patch.object(provider, "_run") as run:
            provider.set_window_visible("7", True)
            provider.set_window_visible("7", False)
        self.assertEqual(
            [call.args for call in run.call_args_list],
            [
                ("control", "-v", "7", "show_window"),
                ("control", "-v", "7", "hide_window"),
            ],
        )

    def test_starting_an_already_running_instance_only_connects_adb(self) -> None:
        provider = MuMuProvider(Path("MuMuManager.exe"))
        with patch.object(provider, "_run") as run, patch.object(
            provider, "resolve_adb_endpoint", return_value="127.0.0.1:16416"
        ):
            endpoint = provider.start_and_resolve_adb("1", already_running=True)
        self.assertEqual(endpoint, "127.0.0.1:16416")
        run.assert_not_called()


class VirtualOperationStoreTests(unittest.TestCase):
    def test_create_operation_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            first, created = store.create_virtual_operation(
                "create", {"name": "测试机"}, idempotency_key="same-request"
            )
            second, created_again = store.create_virtual_operation(
                "create", {"name": "测试机"}, idempotency_key="same-request"
            )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])

    def test_reused_retired_provider_index_gets_a_new_permanent_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "retired-id",
                    "provider": "mumu",
                    "provider_instance_id": "0",
                    "name": "MediaFlow虚拟机1",
                    "state": "retired",
                    "recipe": dict(STANDARD_RECIPE),
                    "provider_snapshot": {},
                    "managed": True,
                    "display_index": 1,
                }
            )
            current = store.save_virtual_device(
                {
                    "virtual_device_id": "new-id",
                    "provider": "mumu",
                    "provider_instance_id": "0",
                    "name": "MediaFlow虚拟机2",
                    "state": "stopped",
                    "recipe": {},
                    "provider_snapshot": {},
                    "managed": True,
                    "display_index": 2,
                }
            )
            records = {item["virtual_device_id"]: item for item in store.list_virtual_devices()}
        self.assertEqual(current["virtual_device_id"], "new-id")
        self.assertEqual(records["new-id"]["provider_instance_id"], "0")
        self.assertTrue(records["retired-id"]["provider_instance_id"].startswith("retired:"))
        self.assertEqual(records["retired-id"]["state"], "retired")

    def test_retired_devices_remain_in_history_but_leave_managed_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "retired-id",
                    "provider": "mumu",
                    "provider_instance_id": "retired:retired-id:0",
                    "name": "MediaFlow虚拟机1",
                    "state": "retired",
                    "recipe": {},
                    "provider_snapshot": {},
                    "managed": True,
                    "display_index": 1,
                }
            )
            managed = store.list_managed_virtual_devices()
            history = store.list_virtual_devices()
        self.assertEqual(managed, [])
        self.assertEqual(history[0]["virtual_device_id"], "retired-id")

    def test_conflicting_active_operation_for_same_instance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.create_virtual_operation(
                "start",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="start-request",
            )
            with self.assertRaisesRegex(ValueError, "已有start操作"):
                store.create_virtual_operation(
                    "stop",
                    {"virtual_device_id": "virtual-1"},
                    idempotency_key="stop-request",
                )

    def test_stop_cancels_waiting_setup_and_can_proceed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            waiting, _ = store.create_virtual_operation(
                "create", {}, idempotency_key="create-waiting"
            )
            store.update_virtual_operation(
                waiting["id"],
                status="waiting_user",
                stage="waiting_app_install",
                progress=70,
                result={"virtual_device_id": "virtual-1"},
            )
            stopping, created = store.create_virtual_operation(
                "stop",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="stop-waiting-setup",
            )
            cancelled = store.get_virtual_operation(waiting["id"])
        self.assertTrue(created)
        self.assertEqual(stopping["status"], "queued")
        self.assertEqual(cancelled["status"], "cancelled")

    def test_show_window_is_allowed_while_setup_waits_for_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            waiting, _ = store.create_virtual_operation(
                "start",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="start-waiting",
            )
            store.update_virtual_operation(
                waiting["id"],
                status="waiting_user",
                stage="waiting_app_install",
                progress=70,
            )
            showing, created = store.create_virtual_operation(
                "show_window",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="show-while-waiting",
            )
            waiting_status = store.get_virtual_operation(waiting["id"])["status"]
        self.assertTrue(created)
        self.assertEqual(showing["status"], "queued")
        self.assertEqual(waiting_status, "waiting_user")

    def test_create_names_are_monotonic_and_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            first, _ = store.create_numbered_virtual_operation(
                "create",
                {"provider_install_id": "install-1"},
                idempotency_key="create-1",
            )
            second, _ = store.create_numbered_virtual_operation(
                "create",
                {"provider_install_id": "install-1"},
                idempotency_key="create-2",
            )
            store.update_virtual_operation(
                second["id"], status="failed", stage="failed", progress=100
            )
            third, _ = store.create_numbered_virtual_operation(
                "create",
                {"provider_install_id": "install-1"},
                idempotency_key="create-3",
            )
        self.assertEqual(first["request"]["name"], "MediaFlow虚拟机1")
        self.assertEqual(second["request"]["name"], "MediaFlow虚拟机2")
        self.assertEqual(third["request"]["name"], "MediaFlow虚拟机3")


class VirtualDeviceInventoryTests(unittest.TestCase):
    @staticmethod
    def _save_stopped(store: TaskStore, *, device_id: str = "virtual-1", instance_id: str = "1") -> dict:
        return store.save_virtual_device(
            {
                "virtual_device_id": device_id,
                "provider": "mumu",
                "provider_instance_id": instance_id,
                "name": "MediaFlow虚拟机1",
                "state": "stopped",
                "recipe": dict(STANDARD_RECIPE),
                "provider_snapshot": {},
                "discovery_source": "mediaflow_created",
                "managed": True,
                "display_index": 1,
                "provider_install_id": "install-1",
                "profile_status": "ready",
            }
        )

    def test_reconcile_does_not_persist_unmanaged_provider_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "2", "name": "客户虚拟机", "state": "stopped"}]
            )
            inventory = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            )
            result = inventory.reconcile()
            self.assertEqual(result["devices"], [])
            self.assertEqual(store.list_managed_virtual_devices(), [])
            self.assertEqual(result["unmanaged_instances"][0]["provider_instance_id"], "2")

    def test_reconcile_keeps_an_explicitly_managed_stopped_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-2",
                    "provider": "mumu",
                    "provider_instance_id": "2",
                    "name": "MediaFlow虚拟机1",
                    "state": "stopped",
                    "recipe": {},
                    "provider_snapshot": {},
                    "discovery_source": "mediaflow_created",
                    "managed": True,
                    "display_index": 1,
                }
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "2", "name": "MediaFlow虚拟机1", "state": "stopped"}]
            )
            inventory = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            )
            first = inventory.reconcile()["devices"][0]
            second = inventory.reconcile()["devices"][0]
        self.assertEqual(first["virtual_device_id"], second["virtual_device_id"])
        self.assertEqual(second["state"], "stopped")
        self.assertTrue(second["managed"])

    def test_reconcile_accepts_mumu_android_15_point_zero_as_standard_android_15(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            stored = self._save_stopped(store)
            store.save_virtual_device({**stored, "recipe": {}})
            provider = FakeMuMuProvider(
                [
                    {
                        "provider_instance_id": "1",
                        "name": "MediaFlow虚拟机1",
                        "state": "stopped",
                        "android_version": "15.0",
                    }
                ]
            )
            reconciled = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            ).reconcile()["devices"][0]
        self.assertEqual(reconciled["standard_status"], "standard")
        self.assertIsNone(reconciled["standard_message"])

    def test_reconcile_never_replaces_the_allocated_name_with_provider_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-0",
                    "provider": "mumu",
                    "provider_instance_id": "0",
                    "name": "MuMu安卓设备",
                    "state": "stopped",
                    "recipe": {},
                    "provider_snapshot": {},
                    "discovery_source": "mediaflow_created",
                    "managed": True,
                    "display_index": 1,
                }
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "0", "name": "MuMu安卓设备", "state": "stopped"}]
            )
            reconciled = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            ).reconcile()["devices"][0]
        self.assertEqual(reconciled["name"], "MediaFlow虚拟机1")
        self.assertEqual(reconciled["provider_snapshot"]["name"], "MuMu安卓设备")

    def test_explicit_adoption_numbers_renames_and_requires_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "2", "name": "MuMu安卓设备", "state": "stopped"}]
            )
            inventory = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            )
            operation, _ = store.create_numbered_virtual_operation(
                "adopt",
                {
                    "provider": "mumu",
                    "provider_install_id": "install-1",
                    "provider_instance_id": "2",
                },
                idempotency_key="adopt-2",
            )
            adopted = inventory.adopt(
                "2",
                name=operation["request"]["name"],
                display_index=operation["request"]["display_index"],
            )
        self.assertEqual(provider.renamed, [("2", "MediaFlow虚拟机1")])
        self.assertTrue(adopted["managed"])
        self.assertEqual(adopted["discovery_source"], "mediaflow_adopted")
        self.assertEqual(adopted["profile_status"], "requires_verification")
        self.assertIsNone(adopted["android_identity"])

    def test_unmanaged_legacy_record_cannot_be_operated_or_streamed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "legacy-unmanaged",
                    "provider": "mumu",
                    "provider_instance_id": "9",
                    "name": "MuMu安卓设备",
                    "state": "running",
                    "recipe": {},
                    "provider_snapshot": {},
                    "adb_endpoint": "127.0.0.1:16999",
                    "discovery_source": "provider_discovery",
                    "managed": False,
                }
            )
            with self.assertRaises(KeyError):
                store.get_virtual_device("legacy-unmanaged")
            by_adb = store.get_virtual_device_for_adb("127.0.0.1:16999")
        self.assertIsNone(by_adb)

    def test_stopped_instance_clears_current_endpoint_but_keeps_last_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "旧实例",
                    "state": "ready",
                    "recipe": {},
                    "provider_snapshot": {},
                    "adb_endpoint": "127.0.0.1:16416",
                    "last_adb_endpoint": "127.0.0.1:16416",
                    "profile_status": "ready",
                }
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "1", "name": "旧实例", "state": "stopped"}]
            )
            result = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            ).reconcile()["devices"][0]
        self.assertIsNone(result["adb_endpoint"])
        self.assertEqual(result["last_adb_endpoint"], "127.0.0.1:16416")
        self.assertEqual(result["profile_status"], "ready")

    def test_reconcile_running_instance_rebinds_verified_last_endpoint(self) -> None:
        """An online managed VM must not disappear after a later setup failure."""
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "MediaFlow虚拟机1",
                    "state": "running",
                    "recipe": dict(STANDARD_RECIPE),
                    "provider_snapshot": {},
                    "adb_endpoint": None,
                    "last_adb_endpoint": "127.0.0.1:16416",
                    "android_identity": "android-one",
                    "profile_status": "requires_verification",
                    "presence_status": "present",
                    "managed": True,
                    "display_index": 1,
                }
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "1", "name": "MediaFlow虚拟机1", "state": "running"}]
            )
            result = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
                identity_reader=lambda _endpoint: "android-one",
            ).reconcile(online_adb_ids={"127.0.0.1:16416"})["devices"][0]
        self.assertEqual(result["adb_endpoint"], "127.0.0.1:16416")
        self.assertEqual(result["last_adb_endpoint"], "127.0.0.1:16416")
        self.assertEqual(result["state"], "adb_ready")
        self.assertIsNone(result["last_error"])

    def test_reconcile_never_rebinds_last_endpoint_to_a_different_android(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "MediaFlow虚拟机1",
                    "state": "running",
                    "recipe": {},
                    "provider_snapshot": {},
                    "adb_endpoint": None,
                    "last_adb_endpoint": "127.0.0.1:16416",
                    "android_identity": "old-android",
                    "profile_status": "ready",
                    "presence_status": "present",
                    "managed": True,
                    "display_index": 1,
                }
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "1", "name": "MediaFlow虚拟机1", "state": "running"}]
            )
            result = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
                identity_reader=lambda _endpoint: "different-android",
            ).reconcile(online_adb_ids={"127.0.0.1:16416"})["devices"][0]
        self.assertIsNone(result["adb_endpoint"])
        self.assertEqual(result["presence_status"], "identity_conflict")
        self.assertEqual(result["profile_status"], "requires_verification")

    def test_start_rebinds_adb_without_changing_permanent_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "可启动实例",
                    "state": "stopped",
                    "recipe": {},
                    "provider_snapshot": {},
                    "android_identity": "android-one",
                    "profile_status": "ready",
                }
            )
            operation, _ = store.create_virtual_operation(
                "start",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="start-once",
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "1", "name": "可启动实例", "state": "stopped"}],
                endpoint="127.0.0.1:16512",
            )
            connected = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
                identity_reader=lambda _endpoint: "android-one",
            ).start("virtual-1", operation["id"])
        self.assertEqual(provider.started, ["1"])
        self.assertEqual(connected["virtual_device_id"], "virtual-1")
        self.assertEqual(connected["adb_endpoint"], "127.0.0.1:16512")
        self.assertEqual(connected["last_adb_endpoint"], "127.0.0.1:16512")

    def test_start_identity_conflict_never_activates_old_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "冲突实例",
                    "state": "stopped",
                    "recipe": {},
                    "provider_snapshot": {},
                    "android_identity": "old-android",
                    "profile_status": "ready",
                }
            )
            operation, _ = store.create_virtual_operation(
                "start",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="conflict-once",
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "1", "name": "冲突实例", "state": "stopped"}]
            )
            conflicted = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
                identity_reader=lambda _endpoint: "new-android",
            ).start("virtual-1", operation["id"])
            updated_operation = store.get_virtual_operation(operation["id"])
        self.assertEqual(conflicted["state"], "degraded")
        self.assertEqual(conflicted["presence_status"], "identity_conflict")
        self.assertIsNone(conflicted["adb_endpoint"])
        self.assertEqual(updated_operation["status"], "waiting_user")

    def test_first_start_without_android_identity_waits_for_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "MediaFlow虚拟机1",
                    "state": "stopped",
                    "recipe": {},
                    "provider_snapshot": {},
                    "profile_status": "requires_verification",
                }
            )
            operation, _ = store.create_virtual_operation(
                "start",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="missing-identity-once",
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "1", "name": "MediaFlow虚拟机1", "state": "stopped"}]
            )
            conflicted = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
                identity_reader=lambda _endpoint: None,
                identity_attempts=1,
                identity_interval_seconds=0,
            ).start("virtual-1", operation["id"])
            updated_operation = store.get_virtual_operation(operation["id"])
        self.assertEqual(conflicted["state"], "degraded")
        self.assertEqual(conflicted["presence_status"], "identity_conflict")
        self.assertIsNone(conflicted["adb_endpoint"])
        self.assertEqual(updated_operation["status"], "waiting_user")
        self.assertIn("Android身份", updated_operation["error"])

    def test_operation_message_tracks_the_current_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            operation, _ = store.create_virtual_operation(
                "start",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="stage-message-once",
            )
            updated = store.update_virtual_operation(
                operation["id"],
                status="running",
                stage="waiting_android",
                progress=40,
            )
        self.assertEqual(updated["message"], "正在等待Android启动")

    def test_restart_stop_phase_keeps_single_operation_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "待重启实例",
                    "state": "ready",
                    "recipe": {},
                    "provider_snapshot": {},
                    "adb_endpoint": "127.0.0.1:16416",
                    "last_adb_endpoint": "127.0.0.1:16416",
                    "profile_status": "ready",
                }
            )
            operation, _ = store.create_virtual_operation(
                "restart",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="restart-once",
            )
            provider = FakeMuMuProvider()
            stopped = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            ).stop("virtual-1", operation["id"], finalize_operation=False)
            updated_operation = store.get_virtual_operation(operation["id"])
        self.assertEqual(provider.stopped, ["1"])
        self.assertEqual(stopped["state"], "stopped")
        self.assertEqual(updated_operation["status"], "running")
        self.assertEqual(updated_operation["stage"], "restarting")

    def test_startup_reconcile_closes_stale_start_without_replaying_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "MediaFlow虚拟机1",
                    "state": "starting",
                    "recipe": {},
                    "provider_snapshot": {},
                    "discovery_source": "mediaflow_created",
                    "managed": True,
                    "display_index": 1,
                    "profile_status": "ready",
                }
            )
            operation, _ = store.create_virtual_operation(
                "start",
                {"virtual_device_id": "virtual-1"},
                idempotency_key="stale-start",
            )
            store.update_virtual_operation(
                operation["id"], status="running", stage="waiting_android", progress=35
            )
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "1", "name": "MediaFlow虚拟机1", "state": "stopped"}]
            )
            inventory = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            )
            inventory.reconcile_incomplete_operations()
            updated = store.get_virtual_operation(operation["id"])
        self.assertEqual(updated["status"], "failed")
        self.assertEqual(updated["stage"], "interrupted_before_start")
        self.assertTrue(updated["retryable"])
        self.assertEqual(provider.started, [])

    def test_operation_records_deadline_and_terminal_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            operation, _ = store.create_virtual_operation(
                "start", {"virtual_device_id": "virtual-1"}, idempotency_key="timed-start"
            )
            self.assertEqual(operation["message"], "操作已排队")
            self.assertIsNotNone(operation["deadline_at"])
            self.assertIsNone(operation["started_at"])
            running = store.update_virtual_operation(
                operation["id"], status="running", stage="starting", progress=10
            )
            self.assertIsNotNone(running["started_at"])
            failed = store.update_virtual_operation(
                operation["id"],
                status="failed",
                stage="timeout",
                progress=100,
                error="启动超时",
                message="可以重试",
                retryable=True,
            )
        self.assertIsNotNone(failed["finished_at"])
        self.assertTrue(failed["retryable"])
        self.assertEqual(failed["message"], "可以重试")

    def test_terminal_operation_cannot_be_overwritten_by_late_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            operation, _ = store.create_virtual_operation(
                "start", {"virtual_device_id": "virtual-1"}, idempotency_key="terminal-race"
            )
            store.update_virtual_operation(
                operation["id"], status="running", stage="starting", progress=20
            )
            completed = store.update_virtual_operation(
                operation["id"], status="completed", stage="ready", progress=100
            )
            late_failure = store.update_virtual_operation(
                operation["id"],
                status="failed",
                stage="late_timeout",
                progress=100,
                error="晚到的超时线程",
            )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(late_failure["status"], "completed")
        self.assertEqual(late_failure["stage"], "ready")
        self.assertIsNone(late_failure["error"])

    def test_operation_cannot_return_to_queue_after_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            operation, _ = store.create_virtual_operation(
                "start", {"virtual_device_id": "virtual-1"}, idempotency_key="invalid-transition"
            )
            store.update_virtual_operation(
                operation["id"], status="running", stage="starting", progress=20
            )
            with self.assertRaisesRegex(ValueError, "不能从 running 变为 queued"):
                store.update_virtual_operation(
                    operation["id"], status="queued", stage="queued", progress=0
                )

    def test_restart_does_not_replay_or_guess_clone_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self._save_stopped(store)
            operation, _ = store.create_virtual_operation(
                "clone", {"virtual_device_id": "virtual-1"}, idempotency_key="stale-clone"
            )
            store.update_virtual_operation(
                operation["id"], status="running", stage="cloning", progress=40
            )
            provider = FakeMuMuProvider()
            inventory = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            )
            inventory.reconcile_incomplete_operations()
            updated = store.get_virtual_operation(operation["id"])
        self.assertEqual(updated["status"], "waiting_user")
        self.assertEqual(updated["stage"], "unknown_result_after_restart")
        self.assertFalse(updated["retryable"])
        self.assertEqual(provider.cloned, [])

    def test_locked_display_settings_are_rejected_but_performance_is_editable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self._save_stopped(store)
            operation, _ = store.create_virtual_operation(
                "settings", {"virtual_device_id": "virtual-1"}, idempotency_key="settings-1"
            )
            provider = FakeMuMuProvider()
            inventory = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            )
            with self.assertRaisesRegex(ValueError, "MediaFlow"):
                inventory.apply_settings("virtual-1", operation["id"], {"width": 1080})
            updated = inventory.apply_settings("virtual-1", operation["id"], {"cpu": 4})
        self.assertEqual(provider.settings_applied, [("1", {"cpu": 4})])
        self.assertEqual(updated["profile_status"], "ready")

    def test_pool_plan_supplements_only_missing_standard_devices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self._save_stopped(store)
            provider = FakeMuMuProvider(
                [{"provider_instance_id": "1", "name": "MediaFlow虚拟机1", "state": "stopped"}]
            )
            plan = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            ).pool_plan("supplement", 3)
        self.assertEqual(plan["standard_count"], 1)
        self.assertEqual(plan["create_count"], 2)
        self.assertEqual(plan["deletion_items"], [])

    def test_pool_reset_lists_every_provider_instance_and_requires_exact_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self._save_stopped(store)
            provider = FakeMuMuProvider([
                {"provider_instance_id": "0", "name": "MuMu安卓设备", "state": "stopped"},
                {"provider_instance_id": "1", "name": "MediaFlow虚拟机1", "state": "stopped"},
            ])
            plan = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            ).pool_plan("reset", 2)
        self.assertEqual(len(plan["deletion_items"]), 2)
        self.assertEqual(plan["confirmation_phrase"], "删除全部并重建2台")
        self.assertTrue(plan["no_backup"])

    def test_backup_persists_sha256_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self._save_stopped(store)
            operation, _ = store.create_virtual_operation(
                "backup", {"virtual_device_id": "virtual-1"}, idempotency_key="backup-1"
            )
            provider = FakeMuMuProvider()
            backup_root = Path(directory) / "backups"
            with patch("virtual_device_inventory.BACKUP_ROOT", backup_root), patch(
                "virtual_device_inventory.shutil.disk_usage",
                return_value=type("Usage", (), {"free": 10 * 1024**3})(),
            ):
                backup = VirtualDeviceInventory(
                    store,
                    manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                    provider_factory=lambda _manager: provider,
                ).backup("virtual-1", operation["id"])
        self.assertEqual(backup["size_bytes"], len(b"verified-backup"))
        self.assertEqual(len(backup["sha256"]), 64)

    def test_clone_creates_a_new_unverified_managed_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self._save_stopped(store)
            operation, _ = store.create_numbered_virtual_operation(
                "clone",
                {
                    "virtual_device_id": "virtual-1",
                    "provider_install_id": "install-1",
                },
                idempotency_key="clone-1",
            )
            provider = FakeMuMuProvider()
            cloned = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            ).clone(
                "virtual-1",
                operation["id"],
                name=operation["request"]["name"],
                display_index=operation["request"]["display_index"],
            )
        self.assertEqual(cloned["provider_instance_id"], "3")
        self.assertNotEqual(cloned["virtual_device_id"], "virtual-1")
        self.assertEqual(cloned["profile_status"], "requires_verification")

    def test_delete_requires_exact_name_and_retires_only_after_provider_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            self._save_stopped(store)
            operation, _ = store.create_virtual_operation(
                "delete", {"virtual_device_id": "virtual-1"}, idempotency_key="delete-1"
            )
            provider = FakeMuMuProvider()
            inventory = VirtualDeviceInventory(
                store,
                manager_resolver=lambda _path: Path(directory) / "MuMuManager.exe",
                provider_factory=lambda _manager: provider,
            )
            with self.assertRaisesRegex(ValueError, "名称不一致"):
                inventory.delete(
                    "virtual-1", operation["id"], confirmation_name="wrong"
                )
            retired = inventory.delete(
                "virtual-1", operation["id"], confirmation_name="MediaFlow虚拟机1"
            )
        self.assertEqual(provider.deleted, ["1"])
        self.assertEqual(retired["state"], "retired")


if __name__ == "__main__":
    unittest.main()
