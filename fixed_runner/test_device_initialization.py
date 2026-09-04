from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from control_vision import parse_candidate  # noqa: E402
from device_initialization import (  # noqa: E402
    InitializationWaitingForUser,
    _enable_fast_input,
    _install_fast_input_apk,
    _is_u2_input_ime,
    _preferred_original_ime,
    emulator_standard_signature,
    execute_initialization,
)
from platform_adapters import CalibrationResult, MemoryPlatformAdapter  # noqa: E402
from platform_profiles import normalize_bounds, profile_matches_runtime  # noqa: E402
from task_store import TaskStore  # noqa: E402


class FakeDevice:
    serial = "device-1"

    def __init__(self) -> None:
        self.info = {"screenOn": True, "displayRotation": 0}
        self.ime = "original/.Ime"
        self.shell_calls: list[object] = []

    def app_current(self):
        return {"package": "test.memory.platform"}

    def screenshot(self, format="pillow"):
        from PIL import Image
        return Image.new("RGB", (1080, 2400), "black")

    def dump_hierarchy(self, **_kwargs):
        return "<hierarchy/>"

    def window_size(self):
        return 1080, 2400

    def shell(self, command):
        self.shell_calls.append(command)
        values = {
            "wm density": "Physical density: 420",
            "settings get secure navigation_mode": "2",
            "getprop ro.product.manufacturer": "Test",
            "getprop ro.product.model": "Phone",
            "getprop ro.build.version.release": "15",
            "getprop ro.build.version.sdk": "35",
        }
        if isinstance(command, list) and command[:2] == ["ime", "set"]:
            self.ime = command[2]
            return "Input method selected"
        return values.get(" ".join(command) if isinstance(command, list) else command, "")

    def current_ime(self):
        return self.ime

    def is_input_ime_installed(self):
        return True

    def set_input_ime(self, enabled):
        if enabled:
            self.ime = "com.github.uiautomator/.FastInputIME"


class FakeAdapter:
    platform_id = "memory"
    package_name = "test.memory.platform"
    adapter_version = "memory-v1"
    write_calls = 0

    def __init__(self, *_args, **_kwargs):
        pass

    def app_version(self):
        return "1.0"

    def ensure_ready(self):
        return None

    def classify_page(self):
        return "feed"

    def calibrate_navigation(self, _query):
        return CalibrationResult(
            page_type="feed",
            controls={},
            capabilities={"main_feed": True, "search": True, "like": True, "favorite": True, "comment": True, "search_feed": True},
            evidence=[],
            warnings=[],
        )

    def run_write_acceptance(self, _comment):
        type(self).write_calls += 1
        raise RuntimeError("unknown write result")


class DeviceInitializationTest(unittest.TestCase):
    def test_emulator_standard_signature_covers_runtime_dimensions(self):
        signature = emulator_standard_signature(
            {
                "android_version": "15",
                "sdk": "35",
                "build_fingerprint": "mumu/test/image",
                "display": {
                    "width": 900,
                    "height": 1600,
                    "density": 320,
                    "navigation_mode": "gesture",
                },
            },
            app_version="40.3.0",
            input_mode="uiautomator_selector",
            adapter_version="douyin-adapter-v1",
        )
        self.assertEqual(signature["resolution"], [900, 1600])
        self.assertEqual(signature["density"], 320)
        self.assertEqual(signature["douyin_version"], "40.3.0")
        self.assertEqual(signature["input_mode"], "uiautomator_selector")
        self.assertEqual(len(signature["fingerprint"]), 20)

    def test_current_and_legacy_u2_input_components_are_supported(self):
        self.assertTrue(_is_u2_input_ime("com.github.uiautomator/.AdbKeyboard"))
        self.assertTrue(_is_u2_input_ime("com.github.uiautomator/.FastInputIME"))
        self.assertFalse(_is_u2_input_ime("original/.Ime"))

    def test_root_device_can_use_verified_selector_input_fallback(self):
        device = FakeDevice()
        device.is_input_ime_installed = lambda: False
        device.set_input_ime = lambda _enabled: (_ for _ in ()).throw(
            RuntimeError("ime set disabled")
        )
        original_shell = device.shell

        def rooted_shell(command):
            if command == ["id"]:
                return "uid=0(root) gid=0(root)"
            if command == ["pm", "path", "com.github.uiautomator"]:
                return "package:/data/app/com.github.uiautomator/base.apk"
            return original_shell(command)

        device.shell = rooted_shell
        with patch(
            "device_initialization._install_fast_input_apk",
            return_value={"adb_result": "Success"},
        ):
            result = _enable_fast_input(
                device, "device-1", original_ime="original/.Ime"
            )
        self.assertEqual(result["input_mode"], "uiautomator_selector")
        self.assertFalse(result["chinese_input_verified"])

    def test_installed_input_package_is_not_reinstalled_when_coloros_hides_ime_list(self):
        device = FakeDevice()
        device.is_input_ime_installed = lambda: False
        device.set_input_ime = lambda _enabled: (_ for _ in ()).throw(
            RuntimeError("Security exception: WRITE_SECURE_SETTINGS")
        )
        original_shell = device.shell

        def coloros_shell(command):
            if command == ["pm", "path", "com.github.uiautomator"]:
                return "package:/data/app/com.github.uiautomator/base.apk"
            if command == ["id"]:
                return "uid=2000(shell) gid=2000(shell)"
            return original_shell(command)

        device.shell = coloros_shell
        with patch(
            "device_initialization._install_fast_input_apk",
            side_effect=AssertionError("installed package must not be reinstalled"),
        ):
            with self.assertRaisesRegex(
                InitializationWaitingForUser, "输入组件已安装.*禁止 ADB 切换输入法"
            ):
                _enable_fast_input(
                    device, "device-1", original_ime="original/.Ime"
                )

    def test_coloros_security_error_is_not_saved_as_original_input_method(self):
        device = FakeDevice()
        device.ime = "com.github.uiautomator/.AdbKeyboard"
        original_shell = device.shell

        def coloros_shell(command):
            if command == ["ime", "list", "-s"]:
                return (
                    "Security exception: uid 2000 does not have "
                    "android.permission.WRITE_SECURE_SETTINGS."
                )
            return original_shell(command)

        device.shell = coloros_shell
        self.assertEqual(_preferred_original_ime(device), "")

    def test_memory_adapter_proves_generic_workflow_surface(self):
        adapter = MemoryPlatformAdapter()
        adapter.ensure_ready()
        adapter.calibrate_navigation("topic")
        self.assertEqual(adapter.package_name, "test.memory.platform")
        self.assertEqual(adapter.calls, ["ensure_ready", "calibrate:topic"])

    def test_low_confidence_ai_coordinate_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_candidate(
                '{"page_type":"feed","semantic_name":"search","region":[0.1,0.1,0.2,0.2],"confidence":0.72,"evidence":"icon"}',
                "search",
            )

    def test_normalized_bounds_and_runtime_signature_staleness(self):
        self.assertEqual(normalize_bounds((100, 200, 300, 400), 1000, 2000), [0.1, 0.1, 0.3, 0.2])
        profile = {"status": "ready", "app_version": "1", "display_signature": "1000x2000x420x0xgesture", "adapter_version": "douyin-adapter-v1"}
        display = {"width": 1000, "height": 2000, "density": 420, "orientation": 0, "navigation_mode": "gesture"}
        self.assertTrue(profile_matches_runtime(profile, app_version="1", display=display))
        changed = {**display, "width": 1080}
        self.assertFalse(profile_matches_runtime(profile, app_version="1", display=changed))

    def test_fast_input_install_timeout_becomes_user_checkpoint(self):
        import subprocess

        with patch(
            "device_initialization.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["adb"], 45),
        ), patch("device_initialization._adb_executable", return_value="adb.exe"):
            with self.assertRaisesRegex(
                InitializationWaitingForUser, "安装等待超时"
            ):
                _install_fast_input_apk("device-1")

    def test_unknown_write_result_is_failed_once_and_ime_is_restored(self):
        FakeAdapter.write_calls = 0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            record = store.create_initialization(
                "device-1", options={"write_acceptance": True}
            )
            claimed = store.claim_initialization("device-1", "worker-1")
            assert claimed is not None
            device = FakeDevice()
            with patch("device_initialization.upsert_device_profile"), patch("device_initialization.save_platform_profile"):
                result = execute_initialization(
                    device,
                    claimed,
                    store,
                    artifacts_root=root / "artifacts",
                    adapter_factory=FakeAdapter,
                    vision_locator=None,
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(FakeAdapter.write_calls, 1)
            self.assertEqual(store.get_initialization(record.id).status, "failed")
            self.assertEqual(device.current_ime(), "original/.Ime")


if __name__ == "__main__":
    unittest.main()
