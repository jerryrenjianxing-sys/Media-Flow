from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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


if __name__ == "__main__":
    unittest.main()
