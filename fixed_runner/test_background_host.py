from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from background_host import RuntimeSupervisor, default_role_health_probe


class FakeHttpResponse:
    def __init__(self, body: str, status: int = 200) -> None:
        self.body = body.encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.body


class FakeStore:
    def __init__(self) -> None:
        self.profile = {"device_ids": ["device-1"], "device_id": "device-1"}
        self.stopped: set[str] = set()
        self.running = 0

    def get_profile(self, _name: str):
        return self.profile

    def is_stop_requested(self, device_id: str) -> bool:
        return device_id in self.stopped

    def running_count(self) -> int:
        return self.running

    def is_paused(self) -> bool:
        return False

    def list_managed_virtual_devices(self):
        return []


class FakeControl:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.restarted: list[str] = []
        self.failures: dict[str, int] = {}
        self.changed = True

    def start(self, spec):
        self.started.append(spec.role)
        if self.failures.get(spec.role, 0):
            self.failures[spec.role] -= 1
            raise RuntimeError("simulated failure")
        return {
            "role": spec.role,
            "running": True,
            "pid": 100,
            "changed": self.changed,
        }

    def restart(self, spec):
        self.restarted.append(spec.role)
        return {"role": spec.role, "running": True, "pid": 101, "changed": True}

    def roles(self):
        return ["worker-device-1", "control-api", "control-ui", "incident-analyzer"]

    def stop(self, role: str):
        self.stopped.append(role)
        return {"role": role, "running": False, "changed": True}


class FakeOnboarding:
    def __init__(self) -> None:
        self.calls = 0

    def tick(self, config):
        self.calls += 1
        return {
            "enabled": bool(config.get("auto_onboard_root_emulators")),
            "auto_run": bool(config.get("auto_run_after_onboarding")),
            "paused": False,
            "devices": [{"device_id": "127.0.0.1:16666", "stage": "validation_running"}],
        }


class RuntimeSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FakeStore()
        self.control = FakeControl()
        self.now = 10.0
        self.supervisor = RuntimeSupervisor(
            control=self.control,
            store=self.store,
            clock=lambda: self.now,
            heartbeat_path=self.root / "heartbeat.json",
            stop_request_path=self.root / "stop.json",
            health_probe=lambda _role: True,
            log_event=lambda *_args, **_kwargs: None,
            device_discovery=lambda: {"device-1"},
        )
        self.supervisor.desired_factories = lambda: [
            ("control-api", lambda: SimpleNamespace(role="control-api")),
            *(
                []
                if self.store.is_stop_requested("device-1")
                else [
                    (
                        "worker-device-1",
                        lambda: SimpleNamespace(role="worker-device-1"),
                    )
                ]
            ),
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_tick_ensures_roles_and_writes_heartbeat(self) -> None:
        payload = self.supervisor.tick()
        self.assertEqual(["control-api", "worker-device-1"], self.control.started)
        self.assertEqual("running", payload["state"])
        self.assertTrue((self.root / "heartbeat.json").is_file())

    def test_legacy_root_emulator_onboarding_is_never_executed(self) -> None:
        onboarding = FakeOnboarding()
        self.store.profile["auto_onboard_root_emulators"] = True
        self.store.profile["auto_run_after_onboarding"] = True
        self.supervisor.onboarding = onboarding
        payload = self.supervisor.tick()
        self.assertEqual(0, onboarding.calls)
        self.assertFalse(payload["emulator_onboarding"]["enabled"])
        self.assertEqual([], payload["emulator_onboarding"]["devices"])

    def test_stopped_worker_is_not_desired(self) -> None:
        self.store.stopped.add("device-1")
        self.supervisor.tick()
        self.assertEqual(["control-api"], self.control.started)

    def test_unmanaged_loopback_selection_never_starts_a_worker(self) -> None:
        store = FakeStore()
        store.profile = {
            "device_ids": ["device-1", "127.0.0.1:16416"],
            "device_id": "device-1",
        }
        supervisor = RuntimeSupervisor(
            control=self.control,
            store=store,
            clock=lambda: self.now,
            heartbeat_path=self.root / "loopback-heartbeat.json",
            stop_request_path=self.root / "loopback-stop.json",
            health_probe=lambda _role: True,
            log_event=lambda *_args, **_kwargs: None,
            device_discovery=lambda: {"device-1", "127.0.0.1:16416"},
        )
        roles = [role for role, _factory in supervisor.desired_factories()]
        self.assertIn("worker-device-1", roles)
        self.assertNotIn("worker-127-0-0-1-16416", roles)

    def test_idle_worker_is_stopped_when_no_longer_desired(self) -> None:
        self.control.roles = lambda: ["worker-device-1", "worker-127-0-0-1-16416"]
        stopped = self.supervisor.stop_undesired_workers({"worker-device-1"})
        self.assertEqual(["worker-127-0-0-1-16416"], self.control.stopped)
        self.assertEqual(1, len(stopped))

    def test_failed_role_uses_backoff_before_retry(self) -> None:
        self.control.failures["control-api"] = 1
        first = self.supervisor.ensure_role(SimpleNamespace(role="control-api"))
        second = self.supervisor.ensure_role(SimpleNamespace(role="control-api"))
        self.assertEqual("start_failed", first["identity"])
        self.assertEqual("backoff", second["identity"])
        self.assertEqual(1, self.control.started.count("control-api"))
        self.now += 2.1
        recovered = self.supervisor.ensure_role(SimpleNamespace(role="control-api"))
        self.assertTrue(recovered["running"])
        self.assertEqual(2, self.control.started.count("control-api"))

    def test_factory_failure_does_not_block_other_roles(self) -> None:
        self.supervisor.desired_factories = lambda: [
            ("control-api", lambda: SimpleNamespace(role="control-api")),
            ("incident-analyzer", lambda: (_ for _ in ()).throw(RuntimeError("key"))),
        ]
        payload = self.supervisor.tick()
        self.assertTrue(payload["processes"][0]["running"])
        self.assertEqual("config_failed", payload["processes"][1]["identity"])

    def test_unresponsive_api_is_restarted_after_three_failed_health_probes(self) -> None:
        self.control.changed = False
        self.supervisor.health_probe = lambda _role: False
        spec = SimpleNamespace(role="control-api")

        first = self.supervisor.ensure_role(spec)
        second = self.supervisor.ensure_role(spec)
        recovered = self.supervisor.ensure_role(spec)

        self.assertEqual("health_pending", first["identity"])
        self.assertEqual("health_pending", second["identity"])
        self.assertTrue(recovered["running"])
        self.assertEqual(["control-api"], self.control.restarted)

    def test_ui_health_probe_rejects_vite_development_assets(self) -> None:
        body = '<script type="module" src="/@id/virtual:vite-rsc/entry-browser"></script>'
        with patch(
            "background_host.urllib.request.urlopen",
            return_value=FakeHttpResponse(body),
        ):
            self.assertFalse(default_role_health_probe("control-ui"))

    def test_ui_health_probe_accepts_production_assets(self) -> None:
        body = '<script src="/_next/static/chunks/index-123.js"></script>'
        with patch(
            "background_host.urllib.request.urlopen",
            side_effect=[FakeHttpResponse(body), FakeHttpResponse("client code")],
        ):
            self.assertTrue(default_role_health_probe("control-ui"))

    def test_ui_health_probe_rejects_html_that_references_missing_client_asset(self) -> None:
        body = '<script src="/_next/static/chunks/index-stale.js"></script>'
        with patch(
            "background_host.urllib.request.urlopen",
            side_effect=[FakeHttpResponse(body), OSError("missing asset")],
        ):
            self.assertFalse(default_role_health_probe("control-ui"))

    def test_ui_health_probe_accepts_native_production_and_checks_its_asset(self) -> None:
        body = '<script type="module" src="/_native/assets/index-native.js"></script>'
        with patch('background_host.urllib.request.urlopen',
                   side_effect=[FakeHttpResponse(body), FakeHttpResponse('native code')]) as request:
            self.assertTrue(default_role_health_probe('control-ui'))
            self.assertEqual(request.call_args_list[1].args[0], 'http://127.0.0.1:3000/_native/assets/index-native.js')

    def test_ui_health_probe_rejects_missing_native_asset(self) -> None:
        body = '<script type="module" src="/_native/assets/index-missing.js"></script>'
        with patch('background_host.urllib.request.urlopen',
                   side_effect=[FakeHttpResponse(body), OSError('missing asset')]):
            self.assertFalse(default_role_health_probe('control-ui'))

    def test_shutdown_refuses_while_task_is_running(self) -> None:
        self.store.running = 1
        (self.root / "stop.json").write_text("{}", encoding="utf-8")
        result = self.supervisor.request_shutdown()
        self.assertEqual("stop_refused", result["state"])
        self.assertEqual([], self.control.stopped)
        self.assertFalse((self.root / "stop.json").exists())

    def test_shutdown_stops_workers_before_services(self) -> None:
        result = self.supervisor.request_shutdown()
        self.assertEqual("stopped", result["state"])
        self.assertEqual(
            ["worker-device-1", "incident-analyzer", "control-ui", "device-stream-host", "control-api"],
            self.control.stopped,
        )


if __name__ == "__main__":
    unittest.main()
