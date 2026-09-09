from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

from control_api import (
    BUILTIN_PRESETS,
    DEFAULT_CONFIG,
    Handler,
    PRESET_FIELDS,
    _public_task,
    _public_inspection_result,
    _public_interaction_alert,
    _public_interaction_inspection,
    _pid_is_running,
    _STATUS_PAYLOAD_CACHE,
    background_supervisor_is_fresh,
    background_onboarding_status,
    build_status_payload,
    cached_status_payload,
    comment_screenshot_path,
    device_screenshot_png,
    device_statuses,
    delete_preset,
    list_presets,
    normalized_config,
    paged_records_payload,
    paged_task_groups_payload,
    save_preset,
    run_submission_payload,
    submit_scheduled_rounds,
    task_detail_payload,
    task_image_path,
    validate_preset_name,
    validate_openrouter_key,
    worker_id_is_running,
    ensure_worker,
    initialization_options_from_body,
    initialization_runtime_status,
    public_initialization,
    worker_status,
    stop_worker,
    write_response_bytes,
)
from control_config import build_scheduled_plan
from task_store import TaskStore
from virtual_devices import STANDARD_RECIPE


class ControlApiTest(unittest.TestCase):
    def test_home_badge_public_result_and_unknown_alert_keep_mode_and_count(self):
        badge={'state':'present','badge_text':'99+','message':'有消息，99+条','target_bounds':[600,1400,700,1500],
               'evidence_missing':[],'source':'local','reason_code':'visible_message_badge','secret':'never-public'}
        value=_public_inspection_result({'workflow_version':'home_badge','status':'completed','home_badge':badge,
                  'inspection_metadata':{'workflow_version':'home_badge','inspection_id':'receipt','result_kind':'alert'}})
        self.assertEqual(value['workflow_version'],'home_badge')
        self.assertEqual(value['home_badge']['badge_text'],'99+')
        self.assertIsNone(value['home_badge']['message_count'])
        self.assertNotIn('secret',value['home_badge'])
        with patch('control_api.load_device_profiles',return_value={}):
            alert=_public_interaction_alert({'id':'alert','device_id':'vm','sources':['home_badge'],
                'summary':{'home_badge':badge,'confirmed':False,'last_checked_at':'now',
                           'last_check_message':'本次未确认','inspection_id':'receipt'}})
        self.assertEqual(alert['sources'],['home_badge'])
        self.assertFalse(alert['summary']['confirmed'])
        self.assertEqual(alert['summary']['last_check_message'],'本次未确认')
        self.assertEqual(alert['summary']['home_badge']['badge_text'],'99+')

    def test_management_and_chat_origins_can_read_api_and_preflight(self) -> None:
        class ProbeHandler(Handler):
            def do_GET(self):
                self._json({'reachable': True})
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), ProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for origin in ('http://127.0.0.1:3000', 'http://127.0.0.1:3001', 'https://unrelated.test', 'http://127.0.0.1:30010'):
                for method in ('GET', 'OPTIONS'):
                    request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/probe', method=method, headers={'Origin': origin})
                    with urllib.request.urlopen(request) as response:
                        self.assertEqual(response.status, 200 if method == 'GET' else 204)
                        expected = origin if origin in ('http://127.0.0.1:3000', 'http://127.0.0.1:3001') else None
                        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_public_v3_inspection_result_keeps_unified_contract(self) -> None:
        payload = _public_inspection_result(
            {
                "status": "completed",
                "workflow_version": "v3",
                "restored": True,
                "unified_activity": {
                    "status": "available",
                    "complete": True,
                    "read_boundary": "first_screen",
                    "scroll_count": 0,
                    "unread_item_count": 1,
                    "categories": ["received_likes"],
                    "reason_code": None,
                },
                "sections": {
                    "received_likes": {
                        "status": "available",
                        "count": 1,
                        "complete": True,
                        "entries": [
                            {
                                "category": "received_likes",
                                "display_name": "测试用户",
                                "content": "测试用户赞了你的作品",
                            }
                        ],
                    }
                },
                "evidence": ["unified_activity:first-screen"],
            }
        )

        self.assertEqual(payload["workflow_version"], "v3")
        self.assertEqual(payload["unified_activity"]["read_boundary"], "first_screen")
        self.assertEqual(payload["unified_activity"]["unread_item_count"], 1)
        self.assertEqual(
            payload["sections"]["received_likes"]["entries"][0]["content"],
            "测试用户赞了你的作品",
        )
        self.assertNotIn("private_messages", payload["sections"])

    def test_status_snapshot_coalesces_duplicate_page_polling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            _STATUS_PAYLOAD_CACHE.clear()
            with patch("control_api.build_status_payload", return_value={"ok": True}) as build:
                first = cached_status_payload(store, normalized_config(DEFAULT_CONFIG))
                second = cached_status_payload(store, normalized_config(DEFAULT_CONFIG))
        self.assertEqual(first, {"ok": True})
        self.assertIs(first, second)
        build.assert_called_once()

    def test_public_interaction_inspection_exposes_ids_and_urls_not_paths(self) -> None:
        payload = _public_interaction_inspection(
            {
                "id": "receipt-1",
                "task_id": "internal-task",
                "device_id": "device-1",
                "workflow_version": "v2",
                "status": "completed",
                "result_kind": "alert",
                "restored": True,
                "summary": {"conclusion": "检测到新互动", "sections": {}},
                "evidence": [{
                    "id": "evidence-1", "label": "消息列表", "section": "private_messages",
                    "captured_at": "2026-09-02T00:00:00+08:00",
                    "image_name": "inspection-v2-message.png",
                    "ui_tree_name": "inspection-v2-message.xml.gz",
                }],
                "run_dir": "C:/secret/path",
                "started_at": "2026-09-02T00:00:00+08:00",
                "finished_at": "2026-09-02T00:01:00+08:00",
            },
            detail=True,
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("internal-task", serialized)
        self.assertNotIn("C:/secret/path", serialized)
        self.assertNotIn("image_name", serialized)
        self.assertIn("/api/interaction-evidence?inspection_id=receipt-1", serialized)

    def test_public_interaction_alert_never_returns_identity_or_fingerprint(self) -> None:
        raw = {
            "id": "alert-1", "task_id": "task-1", "device_id": "device-1",
            "sources": ["profile_visitors"],
            "summary": {
                "source_count": 1,
                "inspection_id": "receipt-1",
                "conclusion": "检测到主页访客变化",
                "evidence_count": 3,
                "sources": {"profile_visitors": {
                    "indicator": "dot", "complete": True,
                    "items": [{"display_name": "本地访客"}],
                }},
            },
            "fingerprint": "secret-fingerprint", "status": "unread",
            "detected_at": "2026-09-01T22:00:00+08:00", "viewed_at": None,
        }
        payload = _public_interaction_alert(raw)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("fingerprint", serialized)
        self.assertNotIn("task-1", serialized)
        self.assertEqual(payload["inspection_id"], "receipt-1")
        self.assertEqual(
            payload["summary"]["sources"]["profile_visitors"]["items"][0]["display_name"],
            "本地访客",
        )
        self.assertNotIn("viewed_at", payload)
        self.assertEqual(payload["sources"], ["profile_visitors"])

    def test_scheduled_plan_freezes_v2_only_for_calibrated_device(self) -> None:
        config = {
            **DEFAULT_CONFIG,
            "inspection_mode": "legacy",
            "device_ids": ["douyin5", "douyin1"],
            "round_count": 6,
            "engagement_inspection_enabled": True,
            "inspection_every_rounds": 5,
        }
        plan = build_scheduled_plan(
            config,
            inspection_profiles={
                "douyin5": {
                    "engagement_inspection_version": "v2",
                    "engagement_app_version": "33.0.0",
                    "engagement_display_signature": "1080x2340x480x0x100",
                    "engagement_calibration": {
                        "profile_version": "douyin-engagement-v2-device5-r1",
                        "device_id": "douyin5",
                        "app_version": "33.0.0",
                        "display_signature": "1080x2340x480x0x100",
                        "passes": 3,
                        "later_passes_semantically_equal": True,
                        "controls": {
                            "aggregate": ["互动消息"],
                            "filter_titles": ["全部消息"],
                            "received_likes": ["赞与收藏"],
                            "received_comments": ["收到的评论"],
                            "received_danmaku": ["收到的弹幕"],
                            "visitor": ["新访客", "主页访客"],
                        },
                    },
                }
            },
        )
        inspections = [task for task in plan.tasks if task.task_type == "douyin_engagement_inspection"]
        by_device = {task.device_id: task.payload for task in inspections}
        self.assertEqual(by_device["douyin5"]["inspection_workflow_version"], "v2")
        self.assertEqual(
            by_device["douyin5"]["inspection_calibration"]["profile_version"],
            "douyin-engagement-v2-device5-r1",
        )
        self.assertEqual(by_device["douyin1"]["inspection_workflow_version"], "v1")
        five_queue = [task for task in plan.tasks if task.device_id == "douyin5"]
        self.assertEqual([task.task_type for task in five_queue], ["douyin_topic_session"] * 5 + ["douyin_engagement_inspection", "douyin_topic_session"])

    def test_scheduled_plan_rejects_unstable_v2_calibration(self) -> None:
        config = {
            **DEFAULT_CONFIG,
            "inspection_mode": "legacy",
            "device_ids": ["douyin1"],
            "round_count": 5,
            "engagement_inspection_enabled": True,
            "inspection_every_rounds": 5,
        }
        with self.assertRaisesRegex(ValueError, "stable three-pass calibration"):
            build_scheduled_plan(
                config,
                inspection_profiles={
                    "douyin1": {
                        "engagement_inspection_version": "v2",
                        "engagement_app_version": "40.2.0",
                        "engagement_display_signature": "1080x2340x480x0x101",
                        "engagement_calibration": {
                            "profile_version": "unstable",
                            "device_id": "douyin1",
                            "app_version": "40.2.0",
                            "display_signature": "1080x2340x480x0x101",
                            "passes": 3,
                            "later_passes_semantically_equal": False,
                            "controls": {},
                        },
                    }
                },
            )
    def test_scheduled_plan_freezes_v3_for_standard_virtual_device(self) -> None:
        config = {
            **DEFAULT_CONFIG,
            "inspection_mode": "legacy",
            "device_ids": ["127.0.0.1:16416"],
            "round_count": 5,
            "engagement_inspection_enabled": True,
            "inspection_every_rounds": 5,
        }
        calibration = {
            "profile_version": "mediaflow-engagement-v3-r1",
            "device_id": "127.0.0.1:16416",
            "app_version": "35.8.0",
            "display_signature": "900x1600x320x0x100",
            "passes": 3,
            "later_passes_semantically_equal": True,
            "controls": {"aggregate": ["互动消息"]},
        }
        plan = build_scheduled_plan(
            config,
            inspection_profiles={
                "127.0.0.1:16416": {
                    "device_kind": "virtual",
                    "managed_standard": True,
                    "engagement_inspection_version": "v3",
                    "engagement_app_version": "35.8.0",
                    "engagement_display_signature": "900x1600x320x0x100",
                    "engagement_calibration": calibration,
                }
            },
        )
        inspection = [
            task for task in plan.tasks
            if task.task_type == "douyin_engagement_inspection"
        ][0]
        self.assertEqual(inspection.payload["inspection_workflow_version"], "v3")
        self.assertEqual(
            inspection.payload["inspection_calibration"]["profile_version"],
            "mediaflow-engagement-v3-r1",
        )

    def test_scheduled_plan_does_not_fall_back_for_standard_virtual_device(self) -> None:
        config = {
            **DEFAULT_CONFIG,
            "inspection_mode": "legacy",
            "device_ids": ["127.0.0.1:16416"],
            "round_count": 5,
            "engagement_inspection_enabled": True,
            "inspection_every_rounds": 5,
        }
        with self.assertRaisesRegex(ValueError, "requires a stable v3 calibration"):
            build_scheduled_plan(
                config,
                inspection_profiles={
                    "127.0.0.1:16416": {
                        "device_kind": "virtual",
                        "managed_standard": True,
                    }
                },
            )

    def test_background_onboarding_status_reads_supervisor_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            heartbeat = Path(directory) / "heartbeat.json"
            heartbeat.write_text(
                json.dumps(
                    {
                        "emulator_onboarding": {
                            "enabled": True,
                            "auto_run": True,
                            "paused": False,
                            "devices": [{"stage": "validation_running"}],
                        }
                    }
                ),
                encoding="utf-8",
            )
            summary = background_onboarding_status(heartbeat)
        self.assertTrue(summary["enabled"])
        self.assertEqual("validation_running", summary["devices"][0]["stage"])

    def test_initialization_options_are_zero_write_by_default(self) -> None:
        self.assertEqual(
            initialization_options_from_body({}),
            {"write_acceptance": False, "search_query": "人工智能"},
        )

    def test_initialization_write_acceptance_requires_boolean_and_confirmation(self) -> None:
        with self.assertRaisesRegex(ValueError, "格式无效"):
            initialization_options_from_body({"write_acceptance": "false"})
        with self.assertRaisesRegex(ValueError, "明确确认"):
            initialization_options_from_body({"write_acceptance": True})
        self.assertTrue(
            initialization_options_from_body(
                {
                    "write_acceptance": True,
                    "confirmation": "ENABLE_WRITE_ACCEPTANCE",
                    "search_query": " AI ",
                }
            )["write_acceptance"]
        )

    def test_waiting_user_payload_keeps_human_message_without_internal_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            record = store.create_initialization("device-1")
            store.claim_initialization("device-1", "worker-1")
            store.finish_initialization(
                record.id,
                status="waiting_user",
                stage="waiting_user",
                message="请在手机上允许安装后继续",
                error="InitializationWaitingForUser: internal detail",
            )
            payload = public_initialization(store.get_initialization(record.id))
        self.assertEqual(payload["message"], "请在手机上允许安装后继续")
        self.assertIsNone(payload["error"])

    def test_legacy_recheck_presentation_uses_stored_options_without_changing_them(self):
        from control_api import _compact_initialization_payload, _virtual_device_guidance
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            for index, (options, legacy) in enumerate([
                ({"inspection_recheck": True}, True),
                ({"inspection_recheck": True, "inspection_mode": "legacy"}, True),
                ({"inspection_recheck": True, "inspection_mode": "home_badge"}, False),
                ({}, False),
            ]):
                record = store.create_initialization(f"device-{index}", options=options)
                for status in ("queued", "running", "waiting_user", "failed", "cancelled", "ready"):
                    record = replace(record, status=status, message="请完成登录或验证")
                    for payload in (public_initialization(record), _compact_initialization_payload(record)):
                        self.assertEqual(payload.get("legacy_inspection_recheck"), legacy)
                    guidance = _virtual_device_guidance(
                        {"state": "running", "standard_status": "standard"}, connected=True,
                        active_operation=None, initialization=record, model_status={})
                    if legacy:
                        self.assertNotIn("continue_initialization", guidance["available_actions"])
                        if status in {"queued", "running", "waiting_user"}:
                            self.assertIn("cancel_initialization", guidance["available_actions"])
                    elif status == "waiting_user":
                        self.assertIn("continue_initialization", guidance["available_actions"])
                self.assertEqual(store.get_initialization(record.id).options, options)
                store.claim_initialization(record.device_id, "offline-worker")
                store.finish_initialization(record.id, status="waiting_user", stage="waiting_user", message="等待处理")
                resumed = store.continue_initialization(record.device_id)
                self.assertEqual(resumed.id, record.id)
                self.assertEqual(resumed.status, "queued")
                self.assertEqual(resumed.options, options)

    def test_ready_initialization_becomes_stale_when_runtime_signature_changes(self) -> None:
        profile = {
            "status": "ready",
            "app_version": "33.0.0",
            "display_signature": "1080x2340x480x0xgesture",
            "adapter_version": "douyin-adapter-v1",
        }
        matching = {
            "app_version": "33.0.0",
            "display": {
                "width": 1080,
                "height": 2340,
                "density": 480,
                "orientation": 0,
                "navigation_mode": "gesture",
            },
        }
        self.assertEqual(
            initialization_runtime_status("ready", profile, matching), "ready"
        )
        changed = {**matching, "app_version": "34.0.0"}
        self.assertEqual(
            initialization_runtime_status("ready", profile, changed), "stale"
        )
        self.assertEqual(
            initialization_runtime_status("waiting_user", profile, changed),
            "waiting_user",
        )

    def test_device_screenshot_returns_png_for_online_authorized_device(self) -> None:
        png = b"\x89PNG\r\n\x1a\nimage"
        with (
            patch("control_api.adb_device_states", return_value={"phone-1": "device"}),
            patch("control_api.resolve_adb_executable", return_value="adb.exe"),
            patch("control_api.subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=0, stdout=png)
            payload = device_screenshot_png("phone-1")

        self.assertEqual(payload, png)
        self.assertEqual(
            run.call_args.args[0],
            ["adb.exe", "-s", "phone-1", "exec-out", "screencap", "-p"],
        )

    def test_device_screenshot_rejects_offline_or_unknown_device(self) -> None:
        with patch(
            "control_api.adb_device_states",
            return_value={"offline-phone": "offline"},
        ):
            with self.assertRaises(KeyError):
                device_screenshot_png("offline-phone")
            with self.assertRaises(KeyError):
                device_screenshot_png("unknown-phone")

    def test_device_screenshot_rejects_invalid_or_failed_output(self) -> None:
        with (
            patch("control_api.adb_device_states", return_value={"phone-1": "device"}),
            patch("control_api.resolve_adb_executable", return_value="adb.exe"),
            patch("control_api.subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=1, stdout=b"")
            with self.assertRaisesRegex(RuntimeError, "screenshot"):
                device_screenshot_png("phone-1")
            run.return_value = MagicMock(returncode=0, stdout=b"not-a-png")
            with self.assertRaisesRegex(RuntimeError, "screenshot"):
                device_screenshot_png("phone-1")

    def test_client_disconnect_during_response_write_is_not_an_api_error(self) -> None:
        writer = MagicMock()
        writer.write.side_effect = ConnectionAbortedError(10053, "client closed")

        self.assertFalse(write_response_bytes(writer, b"image"))

    def test_current_python_process_is_recognized_as_a_live_worker(self) -> None:
        self.assertTrue(_pid_is_running(os.getpid()))
        self.assertTrue(worker_id_is_running(f"test-host-{os.getpid()}"))
        self.assertFalse(worker_id_is_running("malformed-worker"))

    def test_worker_status_uses_verified_runtime_identity(self) -> None:
        with patch("control_api.RuntimeControl.status") as status:
            status.return_value = {
                "role": "worker-device-1",
                "running": False,
                "pid": 12345,
                "identity": "command_mismatch",
            }
            result = worker_status("device-1")
        self.assertFalse(result["running"])
        self.assertEqual(result["identity"], "command_mismatch")

    def test_worker_start_defers_dpapi_identity_mismatch_to_fresh_supervisor(self) -> None:
        with (
            patch("control_api.worker_status", return_value={"running": False}),
            patch("control_api.PROJECT_PYTHON", Path(__file__)),
            patch(
                "control_api.worker_spec",
                side_effect=RuntimeError("OpenRouter Key 解密失败 (PowerShell exit 1)"),
            ),
            patch("control_api.background_supervisor_is_fresh", return_value=True),
        ):
            result = ensure_worker("device-1")

        self.assertFalse(result["running"])
        self.assertEqual(result["identity"], "supervisor_pending")

    def test_worker_start_does_not_hide_dpapi_failure_without_fresh_supervisor(self) -> None:
        with (
            patch("control_api.worker_status", return_value={"running": False}),
            patch("control_api.PROJECT_PYTHON", Path(__file__)),
            patch(
                "control_api.worker_spec",
                side_effect=RuntimeError("OpenRouter Key 解密失败 (PowerShell exit 1)"),
            ),
            patch("control_api.background_supervisor_is_fresh", return_value=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "OpenRouter Key 解密失败"):
                ensure_worker("device-1")

    def test_explicit_worker_stop_sets_supervisor_stop_state(self) -> None:
        store = MagicMock()
        store.running_count.return_value = 0
        with patch("control_api.RuntimeControl.stop") as stop:
            stop.return_value = {"role": "worker-device-1", "running": False}
            result = stop_worker(store, "device-1")
        store.request_stop.assert_called_once_with(["device-1"])
        self.assertFalse(result["running"])

    def test_failed_worker_stop_rolls_back_supervisor_stop_state(self) -> None:
        store = MagicMock()
        store.running_count.return_value = 0
        with patch("control_api.RuntimeControl.stop", side_effect=RuntimeError("identity")):
            with self.assertRaises(RuntimeError):
                stop_worker(store, "device-1")
        store.clear_stop_requests.assert_called_once_with(["device-1"])

    def test_default_config_is_valid(self) -> None:
        config = normalized_config(DEFAULT_CONFIG)
        self.assertTrue(config["preview_only"])
        self.assertFalse(config["topic_filter_enabled"])
        self.assertFalse(config["engagement_requires_topic"])
        self.assertFalse(config["comment_requires_topic"])
        self.assertEqual(config["video_count"], 20)
        self.assertEqual(config["like_probability"], 0.2)
        self.assertEqual(config["favorite_probability"], 0.1)
        self.assertEqual(config["comment_probability"], 0.05)
        self.assertEqual(config["max_comments"], config["video_count"])
        self.assertGreater(config["dwell_max"], config["dwell_min"])
        self.assertEqual(config["round_count"], 1)
        self.assertFalse(config["comment_policy_enabled"])
        self.assertIn("日常生活", config["comment_policy_prompt"])
        self.assertFalse(config["engagement_inspection_enabled"])
        self.assertEqual(config["inspection_every_rounds"], 5)

    def test_engagement_inspection_config_is_explicit_and_bounded(self) -> None:
        legacy = {
            key: value
            for key, value in DEFAULT_CONFIG.items()
            if key not in {"engagement_inspection_enabled", "inspection_every_rounds"}
        }
        self.assertFalse(normalized_config(legacy)["engagement_inspection_enabled"])
        enabled = normalized_config(
            {
                **DEFAULT_CONFIG,
                "engagement_inspection_enabled": True,
                "inspection_every_rounds": 5,
            }
        )
        self.assertTrue(enabled["engagement_inspection_enabled"])
        self.assertEqual(enabled["inspection_every_rounds"], 5)
        for invalid in (0, -1, 21):
            with self.assertRaisesRegex(ValueError, "1 到 20"):
                normalized_config(
                    {**DEFAULT_CONFIG, "inspection_every_rounds": invalid}
                )

    def test_public_task_exposes_scheduled_start_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            scheduled = "2026-08-31T06:52:00+08:00"
            task_id = store.submit(
                "healthcheck", "device-1", not_before=scheduled
            )

            payload = _public_task(store.get(task_id))

        self.assertEqual(payload["not_before"], scheduled)

    def test_comment_policy_requires_text_only_when_enabled(self) -> None:
        disabled = normalized_config(
            {**DEFAULT_CONFIG, "comment_policy_enabled": False, "comment_policy_prompt": ""}
        )
        self.assertFalse(disabled["comment_policy_enabled"])
        with self.assertRaisesRegex(ValueError, "请输入约束内容"):
            normalized_config(
                {**DEFAULT_CONFIG, "comment_policy_enabled": True, "comment_policy_prompt": "  "}
            )

    def test_comment_policy_is_normalized_and_saved_in_presets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            saved = save_preset(
                store,
                "不评日常",
                {
                    **DEFAULT_CONFIG,
                    "comment_policy_enabled": True,
                    "comment_policy_prompt": "  不对日常生活相关内容\n发表评论  ",
                },
            )
        self.assertTrue(saved["config"]["comment_policy_enabled"])
        self.assertEqual(
            saved["config"]["comment_policy_prompt"],
            "不对日常生活相关内容 发表评论",
        )
        self.assertIn("comment_policy_enabled", PRESET_FIELDS)
        self.assertIn("comment_policy_prompt", PRESET_FIELDS)

    def test_empty_run_payload_can_reuse_saved_multi_device_config(self) -> None:
        saved = {**DEFAULT_CONFIG, "device_ids": ["device-1", "device-2"]}
        self.assertEqual(normalized_config(saved)["device_ids"], ["device-1", "device-2"])

    def test_profile_round_trip_uses_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_profile("default", DEFAULT_CONFIG)
            self.assertEqual(store.get_profile("default"), DEFAULT_CONFIG)

    def test_hybrid_profile_round_trip_does_not_create_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "content_mode": "hybrid",
                    "topic_prompt": "智能制造",
                    "search_query": "智能制造",
                    "search_segment_min": 7,
                    "search_segment_max": 14,
                    "home_segment_min": 5,
                    "home_segment_max": 10,
                }
            )
            before = store.task_status_counts()
            store.save_profile("default", config)
            loaded = normalized_config(store.get_profile("default") or {})
            after = store.task_status_counts()
        self.assertEqual(before, after)
        self.assertEqual(loaded["content_mode"], "hybrid")
        self.assertEqual(
            (
                loaded["search_segment_min"],
                loaded["search_segment_max"],
                loaded["home_segment_min"],
                loaded["home_segment_max"],
            ),
            (7, 14, 5, 10),
        )

    def test_builtin_presets_are_read_only_and_complete(self) -> None:
        self.assertEqual(
            list(BUILTIN_PRESETS),
            ["保守预演", "均衡测试", "长时稳定性", "主题搜索测试", "搜索＋主页交替测试"],
        )
        hybrid = BUILTIN_PRESETS["搜索＋主页交替测试"]
        self.assertEqual(hybrid["content_mode"], "hybrid")
        self.assertEqual(
            (
                hybrid["search_segment_min"],
                hybrid["search_segment_max"],
                hybrid["home_segment_min"],
                hybrid["home_segment_max"],
            ),
            (7, 14, 5, 10),
        )
        with self.assertRaisesRegex(ValueError, "内置预设不能覆盖"):
            validate_preset_name("均衡测试")
        with self.assertRaisesRegex(ValueError, "请输入预设名称"):
            validate_preset_name("   ")

    def test_custom_preset_round_trip_isolated_from_runtime_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_profile("default", DEFAULT_CONFIG)
            source = {
                **DEFAULT_CONFIG,
                "video_count": 37,
                "device_id": "private-device",
                "device_ids": ["private-device"],
                "seed": 99,
                "preview_only": False,
            }

            created = save_preset(store, "我的预设", source)
            updated = save_preset(store, "我的预设", {**source, "video_count": 41})
            listed = list_presets(store)

            self.assertEqual(created["config"]["video_count"], 37)
            self.assertEqual(updated["config"]["video_count"], 41)
            self.assertEqual(set(updated["config"]), set(PRESET_FIELDS))
            self.assertNotIn("device_ids", updated["config"])
            self.assertNotIn("seed", updated["config"])
            self.assertNotIn("preview_only", updated["config"])
            self.assertNotIn("engagement_inspection_enabled", updated["config"])
            self.assertNotIn("inspection_every_rounds", updated["config"])
            self.assertEqual(len([item for item in listed if item["name"] == "我的预设"]), 1)
            self.assertEqual(store.get_profile("default"), DEFAULT_CONFIG)
            self.assertTrue(delete_preset(store, "我的预设"))
            self.assertFalse(delete_preset(store, "我的预设"))

    def test_preset_rejects_non_object_config_and_long_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            with self.assertRaisesRegex(ValueError, "参数格式无效"):
                save_preset(store, "测试", "not-an-object")
            with self.assertRaisesRegex(ValueError, "最多 40"):
                save_preset(store, "太" * 41, DEFAULT_CONFIG)

    def test_probability_out_of_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "like_probability"):
            normalized_config({**DEFAULT_CONFIG, "like_probability": 1.2})

    def test_unlimited_topic_mode_does_not_require_topic_text(self) -> None:
        config = normalized_config(
            {**DEFAULT_CONFIG, "topic_filter_enabled": False, "topic_prompt": ""}
        )
        self.assertFalse(config["topic_filter_enabled"])

    def test_legacy_topic_switches_migrate_to_mixed_mode(self) -> None:
        config = normalized_config(
            {
                **DEFAULT_CONFIG,
                "content_mode": "mixed",
                "engagement_requires_topic": True,
                "comment_requires_topic": False,
                "topic_prompt": "宠物日常",
            }
        )
        self.assertEqual(config["content_mode"], "mixed")
        self.assertTrue(config["topic_filter_enabled"])
        self.assertFalse(config["engagement_requires_topic"])
        self.assertFalse(config["comment_requires_topic"])
        self.assertFalse(config["like_only_on_match"])

    def test_search_trust_is_explicit_and_missing_values_remain_strict(self) -> None:
        legacy = normalized_config(
            {
                **DEFAULT_CONFIG,
                "content_mode": "search",
                "search_query": "人工智能",
                "topic_prompt": "人工智能",
                "search_trust_results": False,
            }
        )
        enabled = normalized_config({**legacy, "search_trust_results": True})
        missing = {key: value for key, value in legacy.items() if key != "search_trust_results"}

        self.assertFalse(normalized_config(missing)["search_trust_results"])
        self.assertTrue(enabled["search_trust_results"])
        self.assertFalse(
            normalized_config({**enabled, "content_mode": "mixed", "search_trust_results": False})[
                "search_trust_results"
            ]
        )

    def test_builtin_search_preset_is_isolated_from_runtime_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            presets = {item["name"]: item for item in list_presets(store)}
            search = presets["主题搜索测试"]

        self.assertTrue(search["builtin"])
        self.assertEqual(search["config"]["content_mode"], "search")
        self.assertTrue(search["config"]["search_trust_results"])
        self.assertNotIn("device_ids", search["config"])
        self.assertNotIn("seed", search["config"])
        self.assertNotIn("preview_only", search["config"])

    def test_long_run_limits_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "round_count"):
            normalized_config({**DEFAULT_CONFIG, "round_count": 21})
        with self.assertRaisesRegex(ValueError, "video_count"):
            normalized_config({**DEFAULT_CONFIG, "video_count": 201})

    def test_legacy_action_caps_are_ignored_and_normalized_to_video_count(self) -> None:
        config = normalized_config(
            {
                **DEFAULT_CONFIG,
                "video_count": 37,
                "max_likes": 0,
                "max_favorites": 1,
                "max_comments": 9999,
            }
        )
        self.assertEqual(config["max_likes"], 37)
        self.assertEqual(config["max_favorites"], 37)
        self.assertEqual(config["max_comments"], 37)
        self.assertNotIn("max_likes", PRESET_FIELDS)
        self.assertNotIn("max_favorites", PRESET_FIELDS)
        self.assertNotIn("max_comments", PRESET_FIELDS)

    def test_legacy_anomaly_threshold_is_migrated_into_safe_range(self) -> None:
        self.assertEqual(normalized_config({**DEFAULT_CONFIG, "max_gate_skips": 0})["max_gate_skips"], 1)
        self.assertEqual(normalized_config({**DEFAULT_CONFIG, "max_gate_skips": 99})["max_gate_skips"], 50)

    def test_scheduled_rounds_have_distinct_seeds_and_times(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_id": "device-1",
                    "device_ids": ["device-1"],
                    "round_count": 3,
                    "round_interval_minutes": 15,
                }
            )
            task_ids = submit_scheduled_rounds(store, config)
            tasks = [store.get(task_id) for task_id in task_ids]
            self.assertEqual(len(tasks), 3)
            self.assertEqual([task.payload["seed"] for task in tasks], [20260821, 20260822, 20260823])
            scheduled = [datetime.fromisoformat(task.not_before) for task in tasks]
            self.assertEqual(
                [round((value - scheduled[0]).total_seconds() / 60) for value in scheduled],
                [0, 15, 30],
            )
            self.assertEqual(
                len({task.payload["submission_id"] for task in tasks}), 1
            )

    def test_submission_plan_inserts_inspection_after_each_complete_group(self) -> None:
        base_time = datetime.now().astimezone()
        config = normalized_config(
            {
                **DEFAULT_CONFIG,
                "device_ids": ["device-1"],
                "round_count": 12,
                "round_interval_minutes": 0,
                "engagement_inspection_enabled": True,
                "inspection_every_rounds": 5,
            }
        )
        plan = build_scheduled_plan(
            config, base_time=base_time, submission_id="submission-1"
        )
        labels = [
            (
                f"round-{item.payload['round_index']}"
                if item.task_type == "douyin_topic_session"
                else f"inspection-{item.payload['inspection_index']}"
            )
            for item in plan.tasks
        ]
        self.assertEqual(
            labels,
            [
                "round-1", "round-2", "round-3", "round-4", "round-5",
                "inspection-1", "round-6", "round-7", "round-8", "round-9",
                "round-10", "inspection-2", "round-11", "round-12",
            ],
        )
        self.assertEqual(plan.video_task_count, 12)
        self.assertEqual(plan.inspection_task_count, 2)
        self.assertEqual(
            [item.not_before for item in plan.tasks],
            sorted(item.not_before for item in plan.tasks),
        )

    def test_submission_plan_handles_exact_partial_disabled_and_multi_device(self) -> None:
        def plan_for(round_count: int, enabled: bool, devices: list[str]):
            return build_scheduled_plan(
                normalized_config(
                    {
                        **DEFAULT_CONFIG,
                        "device_ids": devices,
                        "round_count": round_count,
                        "engagement_inspection_enabled": enabled,
                        "inspection_every_rounds": 5,
                    }
                ),
                submission_id="submission-2",
            )

        exact = plan_for(5, True, ["device-1"])
        partial = plan_for(4, True, ["device-1"])
        disabled = plan_for(12, False, ["device-1"])
        multi = plan_for(12, True, ["device-1", "device-2", "device-3", "device-4"])
        self.assertEqual(exact.inspection_task_count, 1)
        self.assertEqual(exact.tasks[-1].task_type, "douyin_engagement_inspection")
        self.assertEqual(partial.inspection_task_count, 0)
        self.assertEqual(disabled.inspection_task_count, 0)
        self.assertEqual(multi.video_task_count, 48)
        self.assertEqual(multi.inspection_task_count, 8)
        self.assertEqual(
            [item["inspection_task_count"] for item in multi.device_plans],
            [2, 2, 2, 2],
        )

    def test_run_submission_response_keeps_legacy_ids_and_adds_split_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            receipt = submit_scheduled_rounds(
                store,
                normalized_config(
                    {
                        **DEFAULT_CONFIG,
                        "device_ids": ["d1", "d2", "d3", "d4"],
                        "round_count": 12,
                        "engagement_inspection_enabled": True,
                        "inspection_every_rounds": 5,
                    }
                ),
            )
            payload = run_submission_payload(
                receipt, [{"device_id": value} for value in ("d1", "d2", "d3", "d4")]
            )
        self.assertEqual(payload["count"], 56)
        self.assertEqual(len(payload["task_ids"]), 56)
        self.assertEqual(payload["video_task_count"], 48)
        self.assertEqual(payload["inspection_task_count"], 8)
        self.assertEqual(len(payload["device_plans"]), 4)

    def test_inspection_plan_is_orthogonal_to_all_content_modes(self) -> None:
        for mode in ("general", "mixed", "search", "hybrid"):
            raw = {
                **DEFAULT_CONFIG,
                "device_ids": ["device-1"],
                "round_count": 6,
                "engagement_inspection_enabled": True,
                "inspection_every_rounds": 5,
                "content_mode": mode,
                "topic": "工业智能",
                "search_query": "机器视觉",
                "seed": 9001,
            }
            plan = build_scheduled_plan(
                normalized_config(raw), submission_id=f"submission-{mode}"
            )
            video_before = plan.tasks[4]
            inspection = plan.tasks[5]
            video_after = plan.tasks[6]
            self.assertEqual(inspection.task_type, "douyin_engagement_inspection")
            self.assertNotIn("round_index", inspection.payload)
            self.assertEqual(video_before.payload["topic"], "工业智能")
            self.assertEqual(video_after.payload["search_query"], "机器视觉")
            self.assertEqual(video_before.payload["seed"], 9005)
            self.assertEqual(video_after.payload["seed"], 9006)

    def test_submitted_inspection_degraded_does_not_cancel_next_round(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            receipt = submit_scheduled_rounds(
                store,
                normalized_config(
                    {
                        **DEFAULT_CONFIG,
                        "device_ids": ["device-1"],
                        "round_count": 6,
                        "round_interval_minutes": 0,
                        "engagement_inspection_enabled": True,
                        "inspection_every_rounds": 5,
                    }
                ),
            )
            self.assertEqual(receipt.video_task_count, 6)
            self.assertEqual(receipt.inspection_task_count, 1)
            claimed_types = []
            for expected_round in range(1, 6):
                task = store.claim_next("device-1", "worker-1")
                self.assertIsNotNone(task)
                assert task is not None
                claimed_types.append(task.task_type)
                self.assertEqual(task.payload["round_index"], expected_round)
                store.finish(task.id, status="completed", run_dir=None, result={})
            inspection = store.claim_next("device-1", "worker-1")
            self.assertIsNotNone(inspection)
            assert inspection is not None
            self.assertEqual(inspection.task_type, "douyin_engagement_inspection")
            store.finish(
                inspection.id,
                status="degraded",
                run_dir=None,
                result={"status": "degraded"},
                error="visitor_unavailable",
            )
            sixth = store.claim_next("device-1", "worker-1")
            self.assertIsNotNone(sixth)
            assert sixth is not None
            self.assertEqual(sixth.payload["round_index"], 6)

    def test_scheduled_rounds_are_distributed_across_devices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_ids": ["device-1", "device-2"],
                    "round_count": 2,
                    "round_interval_minutes": 5,
                }
            )
            task_ids = submit_scheduled_rounds(store, config)
            tasks = [store.get(task_id) for task_id in task_ids]
            self.assertEqual(len(tasks), 4)
            self.assertEqual(
                [task.device_id for task in tasks],
                ["device-1", "device-1", "device-2", "device-2"],
            )
            self.assertEqual(
                [task.payload["seed"] for task in tasks],
                [20260821, 20260822, 20260823, 20260824],
            )
            groups = paged_task_groups_payload(store, 10, 0)
            self.assertEqual(groups["total"], 2)
            self.assertEqual(
                sorted(group["rounds_total"] for group in groups["items"]),
                [2, 2],
            )
            selected = paged_task_groups_payload(store, 5, 0, task_ids[0])
            self.assertEqual(selected['total'], 1)
            self.assertEqual([task['id'] for task in selected['items'][0]['tasks']], [task_ids[0]])
            with self.assertRaises(KeyError):
                paged_task_groups_payload(store, 5, 0, 'missing-task')

    def test_task_group_reports_partial_failure_without_changing_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_ids": ["device-1"],
                    "round_count": 2,
                    "round_interval_minutes": 0,
                }
            )
            first_id, second_id = submit_scheduled_rounds(store, config)
            # Planned timestamps use microseconds, while the real queue clock
            # uses milliseconds. This result aggregation test must not rely on
            # the host clock advancing between submit and claim.
            ready_at = store.get(second_id).not_before
            with patch("task_store.now_iso", return_value=ready_at):
                first = store.claim_next("device-1", "worker-1")
            self.assertEqual(first.id, first_id)
            store.finish(
                first_id,
                status="completed",
                run_dir=None,
                result={
                    "videos_seen": 3,
                    "non_video_feed_items": 2,
                    "feed_phase_reentries": 1,
                    "likes": 0,
                    "favorites": 0,
                },
            )
            with patch("task_store.now_iso", return_value=ready_at):
                second = store.claim_next("device-1", "worker-1")
            self.assertEqual(second.id, second_id)
            store.finish(
                second_id,
                status="failed",
                run_dir=None,
                error="sample failure",
            )

            payload = paged_task_groups_payload(store, 5, 0)

        group = payload["items"][0]
        self.assertEqual(group["status"], "partial_failed")
        self.assertEqual(group["completed_rounds"], 1)
        self.assertEqual(group["failed_rounds"], 1)
        self.assertEqual(group["videos_seen"], 3)
        self.assertEqual(group["non_video_feed_items"], 2)
        self.assertEqual(group["feed_phase_reentries"], 1)
        self.assertEqual(len(group["tasks"]), 2)

    def test_task_group_keeps_degraded_rounds_out_of_completed_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_ids": ["device-1"],
                    "round_count": 2,
                    "round_interval_minutes": 0,
                }
            )
            first_id, second_id = submit_scheduled_rounds(store, config)
            ready_at = store.get(second_id).not_before
            with patch("task_store.now_iso", return_value=ready_at):
                self.assertEqual(store.claim_next("device-1", "worker-1").id, first_id)
            store.finish(
                first_id,
                status="completed",
                run_dir=None,
                result={
                    "videos_seen": 20,
                    "model_attempts": 20,
                    "model_valid_decisions": 20,
                    "model_errors": 0,
                },
            )
            with patch("task_store.now_iso", return_value=ready_at):
                self.assertEqual(store.claim_next("device-1", "worker-1").id, second_id)
            store.finish(
                second_id,
                status="degraded",
                run_dir=None,
                result={
                    "status": "degraded",
                    "videos_seen": 20,
                    "model_attempts": 20,
                    "model_valid_decisions": 18,
                    "model_errors": 2,
                    "degraded_reason": {"code": "model_channel_partial"},
                },
                error="model_channel_partial",
            )
            group = paged_task_groups_payload(store, 5, 0)["items"][0]

        self.assertEqual(group["status"], "partial_degraded")
        self.assertEqual(group["completed_rounds"], 1)
        self.assertEqual(group["degraded_rounds"], 1)
        self.assertEqual(group["videos_seen"], 40)
        self.assertEqual(group["model_attempts"], 40)
        self.assertEqual(group["model_valid_decisions"], 38)
        self.assertEqual(group["model_valid_response_rate"], 0.95)

    def test_group_keeps_inspection_status_and_metrics_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            receipt = submit_scheduled_rounds(
                store,
                normalized_config(
                    {
                        **DEFAULT_CONFIG,
                        "device_ids": ["device-1"],
                        "round_count": 5,
                        "round_interval_minutes": 0,
                        "engagement_inspection_enabled": True,
                        "inspection_every_rounds": 5,
                    }
                ),
            )
            for _index in range(5):
                task = store.claim_next("device-1", "worker-1")
                self.assertIsNotNone(task)
                assert task is not None
                store.finish(
                    task.id,
                    status="completed",
                    run_dir=None,
                    result={"videos_seen": 2, "likes": 0},
                )
            inspection = store.claim_next("device-1", "worker-1")
            self.assertIsNotNone(inspection)
            assert inspection is not None
            store.finish(
                inspection.id,
                status="degraded",
                run_dir=None,
                result={
                    "status": "degraded",
                    "restored": True,
                    "likes": 999,
                    "sections": {
                        "profile_visitors": {
                            "status": "unavailable",
                            "reason": "visitor_history_disabled",
                            "entries": [],
                        }
                    },
                },
                error="visitor_history_disabled",
            )
            group = paged_task_groups_payload(store, 5, 0)["items"][0]

        self.assertEqual(len(receipt), 6)
        self.assertEqual(group["status"], "completed")
        self.assertEqual(group["rounds_total"], 5)
        self.assertEqual(group["completed_rounds"], 5)
        self.assertEqual(group["inspection_total"], 1)
        self.assertEqual(group["degraded_inspections"], 1)
        self.assertEqual(group["inspection_status"], "degraded")
        self.assertEqual(group["likes"], 0)
        self.assertEqual(len(group["tasks"]), 5)
        self.assertEqual(len(group["inspections"]), 1)

    def test_inspection_detail_is_bounded_and_drops_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit(
                "douyin_engagement_inspection",
                "device-1",
                {
                    "submission_id": "submission-sensitive",
                    "inspection_index": 1,
                    "after_round_index": 5,
                    "inspection_every_rounds": 5,
                    "max_items_per_section": 20,
                },
            )
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="degraded",
                run_dir=r"C:\private\run",
                result={
                    "status": "degraded",
                    "task_type": "douyin_engagement_inspection",
                    "restored": True,
                    "failure_reason": r"C:\private\failure.xml",
                    "side_effect_notice": "打开列表可能改变未读角标",
                    "evidence": ["section:profile_visitors:unavailable", r"C:\secret.xml"],
                    "sections": {
                        "private_messages": {
                            "status": "available",
                            "count": 25,
                            "entries": [
                                {
                                    "display_name": r"C:\secret\name.txt" if index == 0 else f"user-{index}",
                                    "preview": "x" * 300,
                                }
                                for index in range(25)
                            ],
                        }
                    },
                },
                error="visitor_unavailable",
            )
            detail = task_detail_payload(store, [task_id])["tasks"][0]

        result = detail["result"]
        self.assertEqual(detail["round_index"], None)
        self.assertEqual(detail["inspection_index"], 1)
        self.assertEqual(len(result["sections"]["private_messages"]["entries"]), 20)
        self.assertTrue(result["sections"]["private_messages"]["truncated"])
        self.assertEqual(
            result["sections"]["private_messages"]["entries"][0]["display_name"], ""
        )
        self.assertNotIn("C:\\", json.dumps(detail, ensure_ascii=False))
        self.assertEqual(detail["images"], [])

    def test_inspection_is_visible_while_pending_running_and_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit(
                "douyin_engagement_inspection",
                "device-1",
                {
                    "submission_id": "submission-visible",
                    "inspection_index": 1,
                    "after_round_index": 5,
                    "inspection_every_rounds": 5,
                    "max_items_per_section": 20,
                },
            )
            pending = paged_task_groups_payload(store, 5, 0)["items"][0]
            store.claim_next("device-1", "worker-1")
            running = paged_task_groups_payload(store, 5, 0)["items"][0]
            store.finish(
                task_id,
                status="completed",
                run_dir=None,
                result={
                    "status": "completed",
                    "restored": True,
                    "sections": {},
                },
            )
            terminal = paged_task_groups_payload(store, 5, 0)["items"][0]

        self.assertEqual(pending["inspection_status"], "pending")
        self.assertEqual(running["inspection_status"], "running")
        self.assertEqual(terminal["inspection_status"], "completed")
        self.assertEqual(terminal["inspection_total"], 1)

    def test_isolated_http_api_queues_and_cancels_without_starting_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "isolated-api.db")
            store.set_paused(True)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            def request(path: str, body: dict | None = None) -> dict:
                payload = None if body is None else json.dumps(body).encode("utf-8")
                raw = urllib.request.Request(
                    f"{base_url}{path}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST" if body is not None else "GET",
                )
                with urllib.request.urlopen(raw, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))

            try:
                with (
                    patch.object(Handler, "store", store),
                    patch(
                        "control_api.device_statuses",
                        return_value=[
                            {"device_id": "isolated-device", "state": "device"}
                        ],
                    ),
                    patch(
                        "control_api.ensure_workers",
                        return_value=[
                            {
                                "device_id": "isolated-device",
                                "state": "suppressed_for_api_test",
                            }
                        ],
                    ) as ensure,
                ):
                    store.save_profile(
                        "device-preferences", {"physical_devices_enabled": True}
                    )
                    thread.start()
                    submitted = request(
                        "/api/run",
                        {
                            **DEFAULT_CONFIG,
                            "device_id": "isolated-device",
                            "device_ids": ["isolated-device"],
                            "round_count": 6,
                            "round_interval_minutes": 0,
                            "engagement_inspection_enabled": True,
                            "inspection_every_rounds": 5,
                        },
                    )
                    groups = request("/api/records/task-groups?limit=5&offset=0")
                    cancelled = request(
                        "/api/tasks/cancel-pending",
                        {"confirmation": "CANCEL_PENDING_TASKS"},
                    )
                    history = request("/api/records/task-groups?limit=5&offset=0")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(submitted["video_task_count"], 6)
        self.assertEqual(submitted["inspection_task_count"], 1)
        self.assertEqual(groups["items"][0]["rounds_total"], 6)
        self.assertEqual(groups["items"][0]["inspection_total"], 1)
        self.assertEqual(cancelled["cancelled"], 7)
        self.assertEqual(history["items"][0]["cancelled_rounds"], 6)
        self.assertEqual(history["items"][0]["inspections"][0]["status"], "cancelled")
        ensure.assert_called_once_with(["isolated-device"])

    def test_task_detail_returns_bounded_images_and_incidents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            run_dir = artifacts / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "topic-session-initial.png").write_bytes(b"png")
            (run_dir / "video-2-topic-analysis-before.png").write_bytes(b"png")
            outside = root / "outside.png"
            outside.write_bytes(b"outside")
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="completed",
                run_dir=str(run_dir),
                result={"videos_seen": 2},
            )
            store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=2,
                stage="topic_gate",
                error_type="RuntimeError",
                error_message="sample",
                outcome="recovered",
                recovery_action="back",
                screenshot_path=str(run_dir / "video-2-topic-analysis-before.png"),
            )
            with patch("control_api.DEFAULT_ARTIFACTS", artifacts):
                detail = task_detail_payload(store, [task_id])
                resolved = task_image_path(
                    store, task_id, "topic-session-initial.png"
                )
                with self.assertRaises(KeyError):
                    task_image_path(store, task_id, "../outside.png")

        self.assertEqual(resolved.name, "topic-session-initial.png")
        self.assertEqual(len(detail["tasks"]), 1)
        self.assertEqual(len(detail["tasks"][0]["images"]), 2)
        self.assertEqual(len(detail["tasks"][0]["incidents"]), 1)

    def test_public_incident_records_expose_capabilities_not_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "incident.png"
            tree = root / "incident.xml"
            image.write_bytes(b"png")
            tree.write_text("<hierarchy />", encoding="utf-8")
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=None,
                stage="engagement_navigation",
                error_type="RuntimeError",
                error_message="main_feed_not_ready",
                outcome="device_fatal",
                recovery_action="restore_home_then_revalidate",
                screenshot_path=str(image),
                ui_tree_path=str(tree),
            )
            item = paged_records_payload(store, "incidents", 10, 0)["items"][0]
        self.assertTrue(item["has_screenshot"])
        self.assertTrue(item["has_ui_tree"])
        self.assertNotIn("screenshot_path", item)
        self.assertNotIn("ui_tree_path", item)
        self.assertNotIn(str(root), json.dumps(item, ensure_ascii=False))

    def test_failed_historical_inspection_is_explicit_about_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit(
                "douyin_engagement_inspection",
                "device-1",
                {
                    "submission_id": "submission-1",
                    "max_items_per_section": 20,
                    "inspection_workflow_version": "v1",
                    "parent_task_id": "round-1",
                    "inspection_index": 1,
                    "after_round_index": 1,
                    "inspection_every_rounds": 1,
                },
            )
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="failed",
                run_dir=None,
                result={"status": "failed", "failure_reason": "main_feed_not_ready"},
                error="main_feed_not_ready",
            )
            detail = task_detail_payload(store, [task_id])["tasks"][0]
        self.assertEqual(detail["incidents"], [])
        self.assertEqual(detail["incident_evidence_status"], "not_captured_historical")

    def test_task_detail_only_labels_registered_comment_screenshot_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            run_dir = artifacts / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            registered = run_dir / "video-2-comment-sent.png"
            unregistered = run_dir / "video-3-comment-sent.png"
            registered.write_bytes(b"registered")
            unregistered.write_bytes(b"unregistered")
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="completed",
                run_dir=str(run_dir),
                result={
                    "comment_screenshots": [
                        {"video_index": 2, "screenshot_path": str(registered)}
                    ]
                },
            )
            with patch("control_api.DEFAULT_ARTIFACTS", artifacts):
                detail = task_detail_payload(store, [task_id])

        image_names = [item["name"] for item in detail["tasks"][0]["images"]]
        self.assertIn(registered.name, image_names)
        self.assertNotIn(unregistered.name, image_names)

    def test_task_detail_groups_only_verified_action_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            run_dir = artifacts / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            names = [
                "video-1-topic-analysis-before.png",
                "video-2-topic-analysis-before.png",
                "video-1-like-after.png",
                "video-2-like-after.png",
                "video-3-favorite-after.png",
                "video-4-comment-sent.png",
                "video-5-comment-sent.png",
                "video-6-incident-recovery.png",
            ]
            for name in names:
                (run_dir / name).write_bytes(b"png")
            events = [
                {"event": "valid_video_processed", "video": 1, "feed_phase": "search"},
                {"event": "valid_video_processed", "video": 2, "feed_phase": "search"},
                {"event": "valid_video_processed", "video": 3, "feed_phase": "home"},
                {"event": "like_state_after", "video": 1, "active": True, "feed_phase": "search"},
                {"event": "like_state_after", "video": 2, "active": False},
                {"event": "favorite_state_after", "video": 3, "active": True, "feed_phase": "home"},
            ]
            (run_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events) + "\nnot-json\n",
                encoding="utf-8",
            )
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="completed",
                run_dir=str(run_dir),
                result={
                    "comment_screenshots": [
                        {
                            "video_index": 4,
                            "screenshot_path": str(run_dir / "video-4-comment-sent.png"),
                        }
                    ]
                },
            )
            store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=6,
                stage="recovery",
                error_type="RuntimeError",
                error_message="sample",
                outcome="recovered",
                recovery_action="back_to_feed",
                screenshot_path=str(run_dir / "video-6-incident-recovery.png"),
            )

            with patch("control_api.DEFAULT_ARTIFACTS", artifacts):
                groups = task_detail_payload(store, [task_id])["tasks"][0][
                    "evidence_groups"
                ]

        self.assertEqual(
            [item["name"] for item in groups["video"]],
            [
                "video-1-topic-analysis-before.png",
                "video-2-topic-analysis-before.png",
            ],
        )
        self.assertEqual(
            [item["name"] for item in groups["like"]],
            ["video-1-like-after.png"],
        )
        self.assertEqual(
            [item["name"] for item in groups["favorite"]],
            ["video-3-favorite-after.png"],
        )
        self.assertEqual(groups["like"][0]["feed_phase"], "search")
        self.assertEqual(groups["favorite"][0]["feed_phase"], "home")
        self.assertEqual(
            [item["name"] for item in groups["comment"]],
            ["video-4-comment-sent.png"],
        )
        self.assertEqual(
            [item["name"] for item in groups["correction"]],
            ["video-6-incident-recovery.png"],
        )
        self.assertNotIn("video-2-like-after.png", str(groups))
        self.assertNotIn("video-5-comment-sent.png", str(groups))
        self.assertNotIn(str(root), str(groups))

    def test_task_detail_exposes_only_explicit_action_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            run_dir = artifacts / "runs" / "run-1"
            legacy_dir = artifacts / "runs" / "legacy"
            run_dir.mkdir(parents=True)
            legacy_dir.mkdir(parents=True)
            events = [
                {
                    "event": "topic_ai_decision",
                    "video": 1,
                    "feed_phase": "search",
                    "action_routes": {
                        "like": "search_source_trusted",
                        "favorite": "search_source_trusted",
                        "comment": "topic_mismatch_blocked",
                    },
                },
                {
                    "event": "topic_ai_decision",
                    "video": 2,
                    "feed_phase": "home",
                    "action_routes": {
                        "like": "search_source_trusted",
                        "favorite": "search_source_trusted",
                        "comment": "topic_matched",
                    },
                },
            ]
            (run_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events), encoding="utf-8"
            )
            (legacy_dir / "events.jsonl").write_text(
                json.dumps({"event": "topic_ai_decision", "video": 1}),
                encoding="utf-8",
            )
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(task_id, status="completed", run_dir=str(run_dir), result={})
            legacy_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(legacy_id, status="completed", run_dir=str(legacy_dir), result={})
            with patch("control_api.DEFAULT_ARTIFACTS", artifacts):
                detail = task_detail_payload(store, [task_id, legacy_id])["tasks"]

        by_id = {item["id"]: item for item in detail}
        self.assertEqual(
            by_id[task_id]["action_routing"]["like"],
            [
                {"feed_phase": "home", "route": "search_source_trusted", "count": 1},
                {"feed_phase": "search", "route": "search_source_trusted", "count": 1},
            ],
        )
        self.assertEqual(
            {item["route"] for item in by_id[task_id]["action_routing"]["comment"]},
            {"topic_matched", "topic_mismatch_blocked"},
        )
        self.assertEqual(by_id[legacy_id]["action_routing"]["like"], [])

    def test_openrouter_key_format_is_validated_without_echoing_value(self) -> None:
        valid = "sk-or-v1-" + "a" * 64
        self.assertEqual(validate_openrouter_key(valid), valid)
        with self.assertRaisesRegex(ValueError, "OpenRouter"):
            validate_openrouter_key("not-a-key")

    def test_device_inventory_does_not_materialize_draft_only_ids(self) -> None:
        with patch("control_api.adb_device_states", return_value={"real-device": "device"}), patch(
            "control_api.enrich_device_statuses", side_effect=lambda rows: rows
        ):
            devices = device_statuses(["old-draft-device"])
        self.assertEqual(devices, [{"device_id": "real-device", "state": "device"}])

    def test_status_payload_includes_recent_correction_incidents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=2,
                stage="comment",
                error_type="RuntimeError",
                error_message="comment input missing",
                outcome="recovered",
                recovery_action="back_to_feed",
            )
            with (
                patch(
                    "control_api.device_statuses",
                    return_value=[{"device_id": "device-1", "state": "device"}],
                ),
                patch(
                    "control_api.worker_status",
                    return_value={"device_id": "device-1", "running": True, "pid": 1},
                ),
            ):
                payload = build_status_payload(
                    store,
                    normalized_config({**DEFAULT_CONFIG, "device_ids": ["device-1"]}),
                )
        self.assertEqual(payload["incident_summary"]["recovered"], 1)
        self.assertEqual(payload["incidents"][0]["video_index"], 2)
        self.assertEqual(payload["incidents"][0]["analysis_status"], "queued")

    def test_status_payload_supports_an_empty_device_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            with patch("control_api.device_statuses", return_value=[]):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        self.assertIsNone(payload["device"])
        self.assertEqual(payload["devices"], [])
        self.assertIsNone(payload["worker"])
        self.assertEqual(payload["workers"], [])
        self.assertEqual(payload["active_tasks"], [])

    def test_status_payload_keeps_stopped_virtual_inventory_out_of_adb_devices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "virtual-stopped",
                    "provider": "mumu",
                    "provider_instance_id": "2",
                    "name": "已关闭的测试机",
                    "state": "stopped",
                    "recipe": {},
                    "provider_snapshot": {},
                    "adb_endpoint": None,
                    "last_adb_endpoint": "127.0.0.1:16448",
                    "profile_status": "ready",
                    "presence_status": "present",
                }
            )
            with patch("control_api.device_statuses", return_value=[]):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        self.assertEqual(payload["devices"], [])
        self.assertEqual(payload["virtualization"]["device_count"], 1)
        self.assertEqual(payload["virtualization"]["stopped_count"], 1)
        self.assertEqual(payload["virtualization"]["ready_count"], 0)
        self.assertEqual(
            payload["virtualization"]["devices"][0]["last_adb_endpoint"],
            "127.0.0.1:16448",
        )

    def test_status_payload_hides_unmanaged_mumu_from_both_device_inventories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "legacy-discovery",
                    "provider": "mumu",
                    "provider_instance_id": "0",
                    "name": "MuMu安卓设备",
                    "state": "running",
                    "recipe": {},
                    "provider_snapshot": {},
                    "adb_endpoint": "127.0.0.1:16384",
                    "last_adb_endpoint": "127.0.0.1:16384",
                    "discovery_source": "provider_discovery",
                    "managed": False,
                }
            )
            with patch(
                "control_api.device_statuses",
                return_value=[
                    {"device_id": "127.0.0.1:16384", "state": "device"},
                    {"device_id": "physical-1", "state": "device"},
                ],
            ), patch("control_api.worker_status", return_value={"running": False}):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        self.assertEqual([item["device_id"] for item in payload["devices"]], [])
        self.assertFalse(payload["device_preferences"]["physical_devices_enabled"])
        self.assertEqual(payload["virtualization"]["devices"], [])

    def test_running_vm_without_active_operation_is_retryable_not_starting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "managed-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "MediaFlow虚拟机1",
                    "state": "running",
                    "recipe": {},
                    "provider_snapshot": {"is_process_started": True},
                    "adb_endpoint": None,
                    "discovery_source": "mediaflow_created",
                    "managed": True,
                    "display_index": 1,
                    "presence_status": "present",
                    "standard_status": "standard",
                    "recipe": dict(STANDARD_RECIPE),
                }
            )
            with patch("control_api.device_statuses", return_value=[]):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        device = payload["virtualization"]["devices"][0]
        self.assertIsNone(device["active_operation"])
        self.assertEqual(device["connection_status"], "adb_unavailable")
        self.assertTrue(device["can_start"])
        self.assertEqual(payload["virtualization"]["starting_count"], 0)

    def test_connected_nonstandard_vm_is_manageable_but_not_task_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "managed-nonstandard",
                    "provider": "mumu",
                    "provider_instance_id": "4",
                    "name": "MediaFlow虚拟机4",
                    "state": "running",
                    "recipe": dict(STANDARD_RECIPE),
                    "provider_snapshot": {"is_process_started": True},
                    "adb_endpoint": "127.0.0.1:16512",
                    "last_adb_endpoint": "127.0.0.1:16512",
                    "discovery_source": "mediaflow_created",
                    "managed": True,
                    "display_index": 4,
                    "presence_status": "present",
                    "standard_status": "nonstandard",
                    "standard_message": "分辨率不是900×1600",
                }
            )
            with (
                patch(
                    "control_api.device_statuses",
                    return_value=[
                        {"device_id": "127.0.0.1:16512", "state": "device"}
                    ],
                ),
                patch("control_api.openrouter_key_status", return_value={}),
            ):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        self.assertEqual(
            [item["device_id"] for item in payload["devices"]],
            ["127.0.0.1:16512"],
        )
        self.assertFalse(payload["devices"][0]["task_eligibility"]["browse"])
        virtual_device = payload["virtualization"]["devices"][0]
        self.assertEqual(virtual_device["management_status"], "managed_nonstandard")
        self.assertFalse(virtual_device["task_ready"])
        self.assertEqual(
            virtual_device["connected_device"]["device_id"],
            "127.0.0.1:16512",
        )
        self.assertIn("repair_standard", virtual_device["available_actions"])

    def test_virtual_device_issue_explains_missing_app_without_hiding_adb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_virtual_device(
                {
                    "virtual_device_id": "managed-1",
                    "provider": "mumu",
                    "provider_instance_id": "1",
                    "name": "MediaFlow虚拟机1",
                    "state": "waiting_app",
                    "recipe": {},
                    "provider_snapshot": {"is_process_started": True},
                    "adb_endpoint": "127.0.0.1:16416",
                    "last_adb_endpoint": "127.0.0.1:16416",
                    "discovery_source": "mediaflow_created",
                    "managed": True,
                    "display_index": 1,
                    "presence_status": "present",
                    "profile_status": "requires_verification",
                    "standard_status": "standard",
                }
            )
            operation, _ = store.create_virtual_operation(
                "create", {}, idempotency_key="create-waiting"
            )
            store.update_virtual_operation(
                operation["id"],
                status="waiting_user",
                stage="waiting_app_install",
                progress=70,
                result={
                    "virtual_device_id": "managed-1",
                    "adb_endpoint": "127.0.0.1:16416",
                },
                error="请安装抖音",
            )
            with (
                patch(
                    "control_api.device_statuses",
                    return_value=[
                        {"device_id": "127.0.0.1:16416", "state": "device"}
                    ],
                ),
                patch(
                    "control_api.worker_status", return_value={"running": False}
                ),
                patch("control_api.status_runtime_signature", return_value=None),
                patch("control_api.latest_device_platform_profile", return_value=None),
            ):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        device = payload["virtualization"]["devices"][0]
        self.assertEqual(device["connection_status"], "connected")
        self.assertEqual(device["onboarding_status"], "waiting_app_install")
        self.assertEqual(device["reason_code"], "douyin_not_installed")
        self.assertIn("安装抖音", device["user_message"])
        self.assertIn("open_screen", device["available_actions"])
        self.assertIn("continue_onboarding", device["available_actions"])
        self.assertTrue(device["diagnostic_id"])
        self.assertTrue(device["readiness_steps"])

    def test_status_payload_reports_runtime_stale_profile_without_rewriting_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            store.save_profile(
                "device-preferences", {"physical_devices_enabled": True}
            )
            record = store.create_initialization("device-1")
            store.claim_initialization("device-1", "worker-1")
            store.finish_initialization(
                record.id,
                status="ready",
                stage="ready",
                message="已就绪",
            )
            profile = {
                "status": "ready",
                "app_version": "33.0.0",
                "display_signature": "1080x2340x480x0xgesture",
                "adapter_version": "douyin-adapter-v1",
            }
            runtime = {
                "app_version": "34.0.0",
                "display": {
                    "width": 1080,
                    "height": 2340,
                    "density": 480,
                    "orientation": 0,
                    "navigation_mode": "gesture",
                },
            }
            with (
                patch(
                    "control_api.device_statuses",
                    return_value=[{"device_id": "device-1", "state": "device"}],
                ),
                patch(
                    "control_api.worker_status",
                    return_value={"device_id": "device-1", "running": True, "pid": 1},
                ),
                patch(
                    "control_api.latest_device_platform_profile", return_value=profile
                ),
                patch("control_api.status_runtime_signature", return_value=runtime),
            ):
                payload = build_status_payload(
                    store,
                    normalized_config({**DEFAULT_CONFIG, "device_ids": ["device-1"]}),
                )
            stored_status = store.get_initialization(record.id).status
        self.assertEqual(payload["devices"][0]["initialization_status"], "stale")
        self.assertEqual(payload["devices"][0]["initialization"]["status"], "stale")
        self.assertEqual(payload["initialization_summary"]["ready"], 0)
        self.assertEqual(payload["initialization_summary"]["stale"], 1)
        self.assertEqual(stored_status, "ready")

    def test_status_payload_is_bounded_to_five_tasks_and_incidents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            for index in range(7):
                task_id = store.submit("healthcheck", f"device-{index}")
                store.record_incident(
                    task_id=task_id,
                    device_id=f"device-{index}",
                    video_index=index,
                    stage="test",
                    error_type="RuntimeError",
                    error_message=f"incident-{index}",
                    outcome="skipped",
                    recovery_action="continue",
                )
            with (
                patch("control_api.device_statuses", return_value=[{"device_id": "device-0", "state": "device"}]),
                patch("control_api.worker_status", return_value={"device_id": "device-0", "running": False, "pid": None}),
            ):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        self.assertEqual(len(payload["tasks"]), 5)
        self.assertEqual(len(payload["task_groups"]), 5)
        self.assertEqual(payload["task_group_total"], 7)
        self.assertEqual(len(payload["incidents"]), 5)
        self.assertEqual(payload["task_summary"]["pending"], 7)
        self.assertEqual(payload["incident_summary"]["total"], 7)

    def test_status_payload_never_scans_the_complete_task_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            for index in range(12):
                store.submit("healthcheck", f"device-{index}")
            with (
                patch.object(
                    store,
                    "list_all",
                    side_effect=AssertionError("status must not scan complete history"),
                ),
                patch("control_api.device_statuses", return_value=[]),
            ):
                payload = build_status_payload(store, normalized_config(DEFAULT_CONFIG))
        self.assertLessEqual(len(payload["tasks"]), 5)
        self.assertLessEqual(len(payload["task_groups"]), 5)
        self.assertEqual(payload["task_group_total"], 12)

    def test_status_payload_prefers_each_devices_running_task_for_live_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            device_ids = [f"device-{index}" for index in range(5)]
            for device_id in device_ids:
                for round_index in range(1, 4):
                    store.submit(
                        "healthcheck",
                        device_id,
                        {"round_index": round_index, "round_count": 3},
                    )
                store.claim_next(device_id, f"worker-{device_id}")
            with (
                patch(
                    "control_api.device_statuses",
                    return_value=[
                        {"device_id": device_id, "state": "device"}
                        for device_id in device_ids
                    ],
                ),
                patch(
                    "control_api.worker_status",
                    side_effect=lambda device_id: {
                        "device_id": device_id,
                        "running": True,
                        "pid": 1,
                    },
                ),
                patch("control_api.worker_id_is_running", return_value=True),
            ):
                payload = build_status_payload(
                    store,
                    normalized_config({**DEFAULT_CONFIG, "device_ids": device_ids}),
                )

        self.assertEqual(payload["task_summary"]["running"], 5)
        self.assertEqual(len(payload["active_tasks"]), 5)
        self.assertEqual(
            {task["device_id"] for task in payload["active_tasks"]}, set(device_ids)
        )
        self.assertTrue(
            all(task["status"] == "running" for task in payload["active_tasks"])
        )

    def test_status_payload_reads_live_valid_video_phase_and_recovery_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            run_dir = artifacts / "runs" / "live-1"
            run_dir.mkdir(parents=True)
            events = [
                {"event": "feed_phase_started", "phase": "search", "target": 9, "processed": 0},
                {"event": "non_video_feed_item", "video": 1, "feed_phase": "search", "consecutive": 1},
                {"event": "feed_phase_reentered", "feed_phase": "search", "reason": "non_video_supply"},
                {"event": "valid_video_processed", "video": 2, "videos_seen": 1, "feed_phase": "search", "phase_processed": 1, "phase_target": 9},
                {"event": "like_state_after", "video": 2, "active": True},
                {"event": "favorite_state_after", "video": 2, "active": True},
                {"event": "comment_send_verification", "video": 2, "verified": True},
                {"event": "video_incident", "error_type": "FeedContextDriftError", "outcome": "recovered"},
                {"event": "page_observation", "phase": "visual_recognition", "message": "untrusted-text"},
            ]
            (run_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events), encoding="utf-8"
            )
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.attach_run_dir(task_id, str(run_dir))
            with (
                patch("control_api.DEFAULT_ARTIFACTS", artifacts),
                patch("control_api.device_statuses", return_value=[{"device_id": "device-1", "state": "device"}]),
                patch("control_api.worker_status", return_value={"device_id": "device-1", "running": True, "pid": 1}),
                patch("control_api.worker_id_is_running", return_value=True),
            ):
                payload = build_status_payload(
                    store,
                    normalized_config({**DEFAULT_CONFIG, "device_ids": ["device-1"]}),
                )
        progress = payload["active_tasks"][0]["result"]
        self.assertEqual(progress["current_feed_phase"], "search")
        self.assertEqual(progress["videos_seen"], 1)
        self.assertEqual(progress["feed_items_seen"], 2)
        self.assertEqual(progress["likes"], 1)
        self.assertEqual(progress["favorites"], 1)
        self.assertEqual(progress["comments_sent"], 1)
        self.assertEqual(progress["successful_recoveries"], 1)
        self.assertEqual(progress["page_drifts"], 1)
        self.assertEqual(progress["non_video_feed_items"], 1)
        self.assertEqual(progress["feed_phase_reentries"], 1)
        self.assertEqual(progress["navigation_message"], "视觉识别（最多20秒）")

    def test_status_payload_closes_task_whose_worker_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "host-98765")
            with (
                patch(
                    "control_api.device_statuses",
                    return_value=[{"device_id": "device-1", "state": "device"}],
                ),
                patch(
                    "control_api.worker_status",
                    return_value={"device_id": "device-1", "running": False, "pid": None},
                ),
                patch("control_api.worker_id_is_running", return_value=False),
            ):
                payload = build_status_payload(
                    store,
                    normalized_config({**DEFAULT_CONFIG, "device_ids": ["device-1"]}),
                )

        task = next(item for item in payload["tasks"] if item["id"] == task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIsNotNone(task["finished_at"])
        self.assertEqual(payload["task_summary"]["running"], 0)
        self.assertEqual(payload["task_summary"]["failed"], 1)

    def test_comment_screenshot_path_accepts_registered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            image = artifacts / "runs" / "run-1" / "video-2-comment-sent.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="completed",
                run_dir=str(image.parent),
                result={
                    "comment_screenshots": [
                        {"video_index": 2, "screenshot_path": str(image)}
                    ]
                },
            )
            with patch("control_api.DEFAULT_ARTIFACTS", artifacts):
                resolved = comment_screenshot_path(store, task_id, 2)
        self.assertEqual(resolved, image.resolve())

    def test_comment_screenshot_path_rejects_unregistered_or_outside_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(b"png")
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="completed",
                run_dir=str(root),
                result={
                    "comment_screenshots": [
                        {"video_index": 1, "screenshot_path": str(outside)}
                    ]
                },
            )
            with patch("control_api.DEFAULT_ARTIFACTS", artifacts):
                with self.assertRaises(KeyError):
                    comment_screenshot_path(store, task_id, 1)
                with self.assertRaises(KeyError):
                    comment_screenshot_path(store, task_id, 9)

    def test_workbench_http_contract_saves_previews_and_freezes_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "workbench-api.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            def request(path: str, body: dict | None = None, method: str | None = None) -> tuple[int, dict]:
                payload = None if body is None else json.dumps(body).encode("utf-8")
                raw = urllib.request.Request(
                    f"{base_url}{path}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method=method or ("POST" if body is not None else "GET"),
                )
                try:
                    with urllib.request.urlopen(raw, timeout=5) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as error:
                    return error.code, json.loads(error.read().decode("utf-8"))

            status_payload = {
                "paused": False,
                "devices": [{
                    "device_id": "isolated-device",
                    "friendly_name": "隔离设备",
                    "model": "test",
                    "state": "device",
                    "profile_verified": True,
                    "initialization_status": "ready",
                }],
                "tasks": [],
                "task_summary": {"pending": 0, "running": 0},
                "stop_requested_device_ids": [],
            }
            try:
                with (
                    patch.object(Handler, "store", store),
                    patch("control_api.build_status_payload", return_value=status_payload),
                    patch("control_api.ensure_workers", return_value=[{"device_id": "isolated-device", "state": "suppressed_for_api_test"}]),
                ):
                    thread.start()
                    _, original = request("/api/workbench/draft")
                    config = {
                        **original["draft"]["config"],
                        "device_id": "isolated-device",
                        "device_ids": ["isolated-device"],
                        "round_count": 1,
                        "video_count": 3,
                        "like_probability": 0,
                        "favorite_probability": 0,
                        "comment_probability": 0,
                        "preview_only": True,
                    }
                    saved_status, saved = request(
                        "/api/workbench/draft",
                        {"revision": original["draft"]["revision"], "config": config},
                        "PUT",
                    )
                    preview_status, preview = request(
                        "/api/workbench/preview",
                        {"revision": saved["draft"]["revision"]},
                    )
                    submit_status, submitted = request(
                        "/api/workbench/submit",
                        {
                            "revision": saved["draft"]["revision"],
                            "plan_hash": preview["preview"]["plan_hash"],
                            "confirm_writes": False,
                        },
                    )
                    conflict_status, conflict = request(
                        "/api/workbench/draft",
                        {"revision": original["draft"]["revision"], "config": {**config, "video_count": 4}},
                        "PUT",
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(saved_status, 200)
        self.assertEqual(preview_status, 200)
        self.assertTrue(preview["preview"]["ready"])
        self.assertFalse(preview["preview"]["requires_confirmation"])
        self.assertEqual(submit_status, 202, submitted)
        self.assertEqual(len(submitted["task_ids"]), 1)
        self.assertEqual(conflict_status, 409)
        self.assertEqual(conflict["draft"]["revision"], saved["draft"]["revision"])

    def test_workbench_http_preview_returns_a_blocker_when_inventory_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "empty-workbench-api.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            def request(path: str, body: dict | None = None) -> tuple[int, dict]:
                raw = urllib.request.Request(
                    f"{base_url}{path}",
                    data=None if body is None else json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="GET" if body is None else "POST",
                )
                with urllib.request.urlopen(raw, timeout=5) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))

            try:
                with (
                    patch.object(Handler, "store", store),
                    patch("control_api.device_statuses", return_value=[]),
                    patch("control_api.RuntimeControl.status", return_value={"running": False}),
                    patch("control_api.background_onboarding_status", return_value={}),
                ):
                    thread.start()
                    _, draft = request("/api/workbench/draft")
                    status, preview = request(
                        "/api/workbench/preview",
                        {"revision": draft["draft"]["revision"]},
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(status, 200)
        self.assertFalse(preview["preview"]["ready"])
        self.assertTrue(preview["preview"]["blockers"])


if __name__ == "__main__":
    unittest.main()
