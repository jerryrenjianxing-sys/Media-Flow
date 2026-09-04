from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from control_config import DEFAULT_CONFIG, normalized_config
from emulator_onboarding import (
    AUTO_INITIALIZE_FIELD,
    AUTO_RUN_FIELD,
    EmulatorCandidate,
    EmulatorOnboardingCoordinator,
    ONBOARDING_STAGE,
    RUN_STAGE,
    VALIDATION_STAGE,
    discover_root_emulators,
)
from task_store import TaskStore


class EmulatorOnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.temp.name) / "tasks.db")
        self.device_id = "127.0.0.1:16666"
        self.candidate = EmulatorCandidate(
            device_id=self.device_id,
            android_identity="android-new",
            name="MuMu 新实例",
            source="mumu_manager",
        )
        self.config = normalized_config(
            {
                **DEFAULT_CONFIG,
                "device_ids": ["device-existing"],
                AUTO_INITIALIZE_FIELD: True,
                AUTO_RUN_FIELD: True,
                "video_count": 9,
                "round_count": 1,
            }
        )
        self.store.save_profile("default", self.config)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def coordinator(self, candidates=None) -> EmulatorOnboardingCoordinator:
        values = [self.candidate] if candidates is None else candidates
        return EmulatorOnboardingCoordinator(
            self.store, discover=lambda _configured, _known: values
        )

    def make_initialization_ready(self):
        coordinator = self.coordinator()
        first = coordinator.tick(self.config)
        self.assertEqual("initialization_queued", first["devices"][0]["stage"])
        record = self.store.claim_initialization(self.device_id, "worker-test")
        self.assertIsNotNone(record)
        self.store.finish_initialization(
            record.id,
            status="ready",
            stage="complete",
            message="ready",
            result={"status": "passed"},
        )
        return coordinator, record

    def finish_validation(self, *, passed: bool):
        pending = next(
            (
                item
                for item in self.store.list_all()
                if item.device_id == self.device_id and item.status == "pending"
            ),
            None,
        )
        self.assertIsNotNone(pending)
        with patch("task_store.now_iso", return_value=pending.not_before):
            task = self.store.claim_next(self.device_id, "worker-test")
        self.assertIsNotNone(task)
        result = {
            "status": "passed" if passed else "failed",
            "videos_seen": 3,
            "model_valid_decisions": 3 if passed else 2,
            "model_errors": 0 if passed else 1,
            "video_errors": 0,
            "likes": 0,
            "favorites": 0,
            "comments_sent": 0,
        }
        self.store.finish(
            task.id,
            status="completed" if passed else "degraded",
            run_dir=None,
            result=result,
            error=None if passed else "self-check failed",
        )
        return task

    def test_disabled_mode_does_not_discover_or_write(self) -> None:
        calls = []
        coordinator = EmulatorOnboardingCoordinator(
            self.store,
            discover=lambda configured, _known: calls.append(configured) or [],
        )
        summary = coordinator.tick({**self.config, AUTO_INITIALIZE_FIELD: False})
        self.assertFalse(summary["enabled"])
        self.assertEqual([], calls)
        self.assertIsNone(self.store.latest_initialization(self.device_id))

    def test_global_pause_blocks_discovery_and_device_actions(self) -> None:
        calls = []
        self.store.set_paused(True)
        coordinator = EmulatorOnboardingCoordinator(
            self.store,
            discover=lambda configured, _known: calls.append(configured) or [],
        )
        summary = coordinator.tick(self.config)
        self.assertTrue(summary["paused"])
        self.assertEqual([], calls)

    def test_android_identity_alias_is_not_added_or_initialized(self) -> None:
        alias = EmulatorCandidate(
            device_id="127.0.0.1:17777",
            android_identity="same",
            name="MuMu alias",
            source="mumu_manager",
            alias_of="device-existing",
        )
        summary = self.coordinator([alias]).tick(self.config)
        self.assertEqual("alias_existing", summary["devices"][0]["stage"])
        self.assertEqual(
            ["device-existing"], self.store.get_profile("default")["device_ids"]
        )
        self.assertIsNone(self.store.latest_initialization(alias.device_id))

    def test_persisted_identity_detects_alias_when_canonical_is_offline(self) -> None:
        manager = Path(self.temp.name) / "MuMuManager.exe"

        def runner(args, **_kwargs):
            if args[0] == str(manager):
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        '{"0":{"is_android_started":true,'
                        '"adb_host_ip":"127.0.0.1","adb_port":17777,'
                        '"name":"我的"}}'
                    ),
                )
            if args[-1] == "devices":
                return SimpleNamespace(
                    returncode=0,
                    stdout="List of devices attached\n127.0.0.1:17777\tdevice\n",
                )
            if args[-2:] == ["shell", "id"]:
                return SimpleNamespace(returncode=0, stdout="uid=0(root)\n")
            if args[-4:] == ["settings", "get", "secure", "android_id"]:
                return SimpleNamespace(returncode=0, stdout="same-android\n")
            return SimpleNamespace(returncode=0, stdout="")

        candidates = discover_root_emulators(
            ["127.0.0.1:7555"],
            {"same-android": "127.0.0.1:7555"},
            manager_path=manager,
            runner=runner,
        )
        self.assertEqual(1, len(candidates))
        self.assertEqual("127.0.0.1:7555", candidates[0].alias_of)

    def test_new_emulator_is_saved_and_initialization_is_created_once(self) -> None:
        coordinator = self.coordinator()
        first = coordinator.tick(self.config)
        second = coordinator.tick(self.store.get_profile("default"))
        saved = self.store.get_profile("default")
        record = self.store.latest_initialization(self.device_id)
        self.assertEqual("initialization_queued", first["devices"][0]["stage"])
        self.assertEqual("initialization_running", second["devices"][0]["stage"])
        self.assertIn(self.device_id, saved["device_ids"])
        self.assertTrue(record.options["auto_onboarding"])
        self.assertFalse(record.options["write_acceptance"])

    def test_manual_initialization_is_never_auto_promoted(self) -> None:
        self.store.create_initialization(
            self.device_id, options={"write_acceptance": False}
        )
        summary = self.coordinator().tick(self.config)
        self.assertEqual("manual_managed", summary["devices"][0]["stage"])
        self.assertEqual([], self.store.list_all())

    def test_ready_initialization_queues_one_zero_write_validation(self) -> None:
        coordinator, _record = self.make_initialization_ready()
        first = coordinator.tick(self.store.get_profile("default"))
        second = coordinator.tick(self.store.get_profile("default"))
        task = self.store.list_all()[0]
        self.assertEqual("validation_queued", first["devices"][0]["stage"])
        self.assertEqual("validation_running", second["devices"][0]["stage"])
        self.assertEqual(1, len(self.store.list_all()))
        self.assertEqual(3, task.payload["video_count"])
        self.assertTrue(task.payload["preview_only"])
        self.assertEqual(0.0, task.payload["like_probability"])
        self.assertEqual(0.0, task.payload["favorite_probability"])
        self.assertEqual(0.0, task.payload["comment_probability"])
        self.assertEqual(VALIDATION_STAGE, task.payload[ONBOARDING_STAGE])

    def test_failed_validation_blocks_without_replay(self) -> None:
        coordinator, _record = self.make_initialization_ready()
        coordinator.tick(self.store.get_profile("default"))
        self.finish_validation(passed=False)
        first = coordinator.tick(self.store.get_profile("default"))
        second = coordinator.tick(self.store.get_profile("default"))
        self.assertEqual("validation_blocked", first["devices"][0]["stage"])
        self.assertEqual("validation_blocked", second["devices"][0]["stage"])
        self.assertEqual(1, len(self.store.list_all()))

    def test_passed_validation_never_submits_formal_plan_automatically(self) -> None:
        coordinator, _record = self.make_initialization_ready()
        coordinator.tick(self.store.get_profile("default"))
        self.finish_validation(passed=True)
        first = coordinator.tick(self.store.get_profile("default"))
        second = coordinator.tick(self.store.get_profile("default"))
        tasks = self.store.list_all()
        run_tasks = [
            task for task in tasks if task.payload[ONBOARDING_STAGE] == RUN_STAGE
        ]
        self.assertEqual("ready_waiting", first["devices"][0]["stage"])
        self.assertEqual("ready_waiting", second["devices"][0]["stage"])
        self.assertEqual([], run_tasks)
        self.assertTrue(first["devices"][0]["formal_task_requires_workbench"])

    def test_passed_validation_waits_when_auto_run_is_off(self) -> None:
        self.config[AUTO_RUN_FIELD] = False
        self.store.save_profile("default", self.config)
        coordinator, _record = self.make_initialization_ready()
        coordinator.tick(self.store.get_profile("default"))
        self.finish_validation(passed=True)
        summary = coordinator.tick(self.store.get_profile("default"))
        self.assertEqual("ready_waiting", summary["devices"][0]["stage"])
        self.assertEqual(1, len(self.store.list_all()))


if __name__ == "__main__":
    unittest.main()
