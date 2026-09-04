from __future__ import annotations

import tempfile
import sys
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import runtime_control
from model_runtime_config import OPENROUTER_PRIMARY_MODEL
from runtime_control import ProcessSnapshot, ProcessSpec, RuntimeControl


class FakeInspector:
    def __init__(self) -> None:
        self.processes: dict[int, ProcessSnapshot] = {}

    def snapshot(self, pid: int) -> ProcessSnapshot | None:
        return self.processes.get(pid)


class RuntimeControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inspector = FakeInspector()
        self.next_pid = 100
        self.terminated: list[int] = []

        def launch(spec: ProcessSpec) -> int:
            self.next_pid += 1
            self.inspector.processes[self.next_pid] = ProcessSnapshot(
                self.next_pid,
                spec.expected_executable,
                f"created-{self.next_pid}",
                " ".join(spec.command),
            )
            return self.next_pid

        def terminate(pid: int) -> None:
            self.terminated.append(pid)
            self.inspector.processes.pop(pid, None)

        self.control = RuntimeControl(
            self.root,
            inspector=self.inspector,
            launcher=launch,
            terminator=terminate,
        )
        self.spec = ProcessSpec(
            role="worker-device-1",
            command=(r"C:\project\.venv\python.exe", r"C:\project\worker.py", "device-1"),
            cwd=r"C:\project",
            log_path=str(self.root / "worker.log"),
            expected_executable=r"C:\project\.venv\python.exe",
            required_markers=("worker.py", "device-1"),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_start_is_idempotent_and_tracks_the_real_process(self) -> None:
        first = self.control.start(self.spec)
        second = self.control.start(self.spec)
        self.assertTrue(first["running"])
        self.assertTrue(first["changed"])
        self.assertEqual(first["pid"], second["pid"])
        self.assertFalse(second["changed"])

    def test_launch_prefers_direct_process_over_same_executable_child(self) -> None:
        parent_pid = 200
        child_pid = 201
        executable = str(Path(self.spec.expected_executable).resolve())
        self.inspector.processes[parent_pid] = ProcessSnapshot(
            parent_pid, executable, "parent", "worker.py device-1"
        )
        self.inspector.processes[child_pid] = ProcessSnapshot(
            child_pid, executable, "child", "temporary child"
        )
        self.inspector.child_pids = lambda _pid: [child_pid]
        process = SimpleNamespace(pid=parent_pid, poll=lambda: None)
        with patch.object(runtime_control.subprocess, "Popen", return_value=process):
            tracked_pid = self.control._launch(self.spec)
        self.assertEqual(parent_pid, tracked_pid)

    def test_stop_refuses_reused_pid_with_wrong_executable(self) -> None:
        started = self.control.start(self.spec)
        pid = started["pid"]
        original = self.inspector.processes[pid]
        self.inspector.processes[pid] = ProcessSnapshot(
            pid, r"C:\Windows\notepad.exe", original.creation_token, "notepad.exe"
        )
        with self.assertRaisesRegex(RuntimeError, "身份核验失败"):
            self.control.stop(self.spec.role)
        self.assertEqual([], self.terminated)

    def test_stop_refuses_same_executable_with_reused_creation_token(self) -> None:
        started = self.control.start(self.spec)
        pid = started["pid"]
        original = self.inspector.processes[pid]
        self.inspector.processes[pid] = ProcessSnapshot(
            pid, original.executable, "different-start", original.command_line
        )
        with self.assertRaises(RuntimeError):
            self.control.stop(self.spec.role)
        self.assertEqual([], self.terminated)

    def test_stop_refuses_command_line_for_another_device(self) -> None:
        started = self.control.start(self.spec)
        pid = started["pid"]
        original = self.inspector.processes[pid]
        self.inspector.processes[pid] = ProcessSnapshot(
            pid,
            original.executable,
            original.creation_token,
            r"C:\project\worker.py device-2",
        )
        with self.assertRaises(RuntimeError):
            self.control.stop(self.spec.role)
        self.assertEqual([], self.terminated)

    def test_verified_stop_only_terminates_recorded_process(self) -> None:
        started = self.control.start(self.spec)
        result = self.control.stop(self.spec.role)
        self.assertEqual([started["pid"]], self.terminated)
        self.assertFalse(result["running"])
        self.assertFalse(self.control.status(self.spec.role)["running"])

    def test_distribution_does_not_migrate_a_developer_secret(self) -> None:
        developer_secret = self.root / "legacy.dpapi"
        developer_secret.write_text("developer-only", encoding="utf-8")
        install_secret = self.root / "install" / "openrouter-api-key.dpapi"
        with (
            patch.object(runtime_control, "IS_DISTRIBUTION", True),
            patch.object(runtime_control, "LEGACY_SECRET_PATH", developer_secret),
            patch.object(runtime_control, "SECRET_PATH", install_secret),
        ):
            result = runtime_control.migrate_secret()
        self.assertFalse(result["configured"])
        self.assertFalse(install_secret.exists())

    def test_developer_key_reader_falls_back_to_valid_legacy_blob(self) -> None:
        primary = self.root / "primary.dpapi"
        legacy = self.root / "legacy.dpapi"
        primary.write_text("unreadable", encoding="utf-8")
        legacy.write_text("valid", encoding="utf-8")

        def decrypt(path: Path) -> str:
            if path == primary:
                raise RuntimeError("OpenRouter Key 解密失败")
            return "sk-or-v1-legacy"

        with (
            patch.object(runtime_control, "IS_DISTRIBUTION", False),
            patch.object(runtime_control, "SECRET_PATH", primary),
            patch.object(runtime_control, "LEGACY_SECRET_PATH", legacy),
            patch.object(runtime_control, "_decrypt_openrouter_key", side_effect=decrypt),
        ):
            self.assertEqual("sk-or-v1-legacy", runtime_control.load_openrouter_key())

    def test_distribution_key_reader_never_uses_legacy_blob(self) -> None:
        primary = self.root / "primary.dpapi"
        legacy = self.root / "legacy.dpapi"
        primary.write_text("unreadable", encoding="utf-8")
        legacy.write_text("valid", encoding="utf-8")
        with (
            patch.object(runtime_control, "IS_DISTRIBUTION", True),
            patch.object(runtime_control, "SECRET_PATH", primary),
            patch.object(runtime_control, "LEGACY_SECRET_PATH", legacy),
            patch.object(
                runtime_control,
                "_decrypt_openrouter_key",
                side_effect=RuntimeError("OpenRouter Key 解密失败"),
            ) as decrypt,
        ):
            with self.assertRaises(RuntimeError):
                runtime_control.load_openrouter_key()
        decrypt.assert_called_once_with(primary)

    def test_worker_and_analyzer_share_primary_model_without_fallback(self) -> None:
        with patch.object(runtime_control, "load_openrouter_key", return_value="sk-or-v1-test"):
            worker = runtime_control.worker_spec("device-1")
            analyzer = runtime_control.analyzer_spec()
        for spec in (worker, analyzer):
            self.assertEqual(spec.env["PHONE_AGENT_COMMENT_MODEL"], OPENROUTER_PRIMARY_MODEL)
            self.assertEqual(spec.env["PHONE_AGENT_COMMENT_FALLBACK_MODELS"], "")

    def test_worker_can_start_without_an_openrouter_key(self) -> None:
        with patch.object(
            runtime_control,
            "load_openrouter_key",
            side_effect=RuntimeError("尚未配置 OpenRouter Key"),
        ):
            worker = runtime_control.worker_spec("127.0.0.1:16416")
        self.assertNotIn("PHONE_AGENT_API_KEY", worker.env)

    def test_analyzer_is_not_a_required_role_without_an_openrouter_key(self) -> None:
        with patch.object(
            runtime_control,
            "load_openrouter_key",
            side_effect=RuntimeError("尚未配置 OpenRouter Key"),
        ):
            self.assertIsNone(runtime_control.optional_analyzer_spec())

    def test_ui_runtime_forces_production_mode(self) -> None:
        spec = runtime_control.ui_spec()
        self.assertEqual("production", spec.env["NODE_ENV"])

    def test_stream_runtime_is_loopback_only_and_version_pinned(self) -> None:
        spec = runtime_control.stream_spec()
        self.assertEqual("device-stream-host", spec.role)
        self.assertEqual("127.0.0.1", spec.env["RISKFLOW_STREAM_HOST"])
        self.assertEqual("48139", spec.env["RISKFLOW_STREAM_PORT"])
        self.assertTrue(spec.env["RISKFLOW_SCRCPY_SERVER"].endswith("scrcpy-server-v3.3.3"))

    def test_development_runtime_does_not_require_a_scheduled_task(self) -> None:
        with (
            patch.object(runtime_control, "product_version", return_value={"channel": "development"}),
            patch.object(runtime_control.subprocess, "run") as run,
        ):
            ok, detail = runtime_control.background_task_check()
        self.assertTrue(ok)
        self.assertEqual("not_required_in_development", detail)
        run.assert_not_called()

    def test_release_runtime_requires_the_scheduled_task(self) -> None:
        missing = SimpleNamespace(returncode=1)
        with (
            patch.object(runtime_control, "product_version", return_value={"channel": "release"}),
            patch.object(runtime_control.subprocess, "run", return_value=missing) as run,
        ):
            ok, detail = runtime_control.background_task_check()
        self.assertFalse(ok)
        self.assertEqual("not_registered", detail)
        run.assert_called_once()

    def test_status_does_not_read_or_decrypt_cloud_credentials(self) -> None:
        self.control.start(self.spec)
        with (
            patch.object(runtime_control, "RuntimeControl", return_value=self.control),
            patch.object(runtime_control, "load_openrouter_key", side_effect=AssertionError),
            patch.object(sys, "argv", ["runtime_control.py", "status"]),
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            self.assertEqual(runtime_control.main(), 0)
        result = output.getvalue()
        self.assertIn('"ok": true', result)
        self.assertIn('"role": "worker-device-1"', result)

    def test_refresh_ui_restarts_only_the_running_ui_without_cloud_credentials(self) -> None:
        ui = ProcessSpec(
            role="control-ui",
            command=(r"C:\node.exe", "server.js"),
            cwd=r"C:\project",
            log_path=str(self.root / "ui.log"),
            expected_executable=r"C:\node.exe",
            required_markers=("server.js",),
        )
        self.control.start(ui)
        with (
            patch.object(runtime_control, "RuntimeControl", return_value=self.control),
            patch.object(runtime_control, "ui_spec", return_value=ui),
            patch.object(runtime_control, "load_openrouter_key", side_effect=AssertionError),
            patch.object(sys, "argv", ["runtime_control.py", "refresh-ui"]),
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            self.assertEqual(runtime_control.main(), 0)
        self.assertIn('"role": "control-ui"', output.getvalue())
        self.assertEqual(1, len(self.terminated))


if __name__ == "__main__":
    unittest.main()
