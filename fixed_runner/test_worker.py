from pathlib import Path
from contextlib import nullcontext
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from douyin_fixed_runner import DOUYIN_PACKAGE  # noqa: E402
from douyin_uia2_runner import GateDecision, Uia2RunRecorder  # noqa: E402
from task_store import TaskStore  # noqa: E402
from worker import (  # noqa: E402
    DeviceFatalError,
    _comment_screenshot_evidence,
    _comment_task_type,
    build_parser,
    connect_with_retry,
    device_preflight,
    run_worker,
    routed_action_probabilities,
    send_comment,
    topic_session,
    wait_while_paused,
    wake_and_unlock,
    write_report,
)


class TopicProbabilityRoutingTest(unittest.TestCase):
    def test_matched_and_general_probabilities_are_independent(self) -> None:
        config = {
            "like_probability": 0.1,
            "favorite_probability": 0.05,
            "comment_probability": 0.02,
            "matched_like_probability": 1.0,
            "matched_favorite_probability": 0.9,
            "matched_comment_probability": 0.8,
        }
        self.assertEqual(
            routed_action_probabilities(config, False),
            {"like": 0.1, "favorite": 0.05, "comment": 0.02},
        )
        self.assertEqual(
            routed_action_probabilities(config, True),
            {"like": 1.0, "favorite": 0.9, "comment": 0.8},
        )


class FakeSelector:
    def __init__(self, exists: bool) -> None:
        self._exists = exists
        self.clicked = False

    def exists(self, timeout=0) -> bool:
        return self._exists

    def click(self) -> None:
        self.clicked = True


class FakeDevice:
    def __init__(
        self,
        duplicate: bool = False,
        input_placeholder: str | None = "有什么想法",
        resource_input: bool = False,
        send_text: bool = True,
        send_resource: bool = False,
        hierarchy_text: str | None = None,
    ) -> None:
        self.duplicate = duplicate
        self.input_placeholder = input_placeholder
        self.resource_input = resource_input
        self.send_text = send_text
        self.send_resource = send_resource
        self.hierarchy_text = hierarchy_text
        self.typed = ""
        self.selectors: list[tuple[dict, FakeSelector]] = []

    def __call__(self, **selector):
        if (
            self.resource_input
            and selector.get("resourceId") == "com.ss.android.ugc.aweme:id/eyz"
        ):
            exists = True
        elif (
            "textContains" in selector
            and selector.get("textContains") == self.input_placeholder
        ):
            exists = True
        elif (
            self.send_resource
            and selector.get("resourceId") == "com.ss.android.ugc.aweme:id/e27"
        ):
            exists = True
        elif self.send_text and selector.get("text") == "发送":
            exists = True
        else:
            exists = self.duplicate
        value = FakeSelector(exists)
        self.selectors.append((selector, value))
        return value

    def send_keys(self, value: str, clear: bool = True) -> None:
        self.typed = value

    def screenshot(self, format="pillow") -> Image.Image:
        return Image.new("RGB", (1080, 2400), "black")

    def app_current(self):
        return {"package": DOUYIN_PACKAGE}

    def dump_hierarchy(self, compressed=True, pretty=False) -> str:
        visible_text = self.typed if self.hierarchy_text is None else self.hierarchy_text
        return (
            '<hierarchy><node package="com.ss.android.ugc.aweme" '
            'visible-to-user="true" bounds="[0,0][1080,2200]" '
            f'text="{visible_text}" content-desc="" /></hierarchy>'
        )


class CommentSendTest(unittest.TestCase):
    def test_comment_task_type_distinguishes_send_from_preview(self) -> None:
        self.assertEqual(_comment_task_type(False), "douyin_comment_preview")
        self.assertEqual(_comment_task_type(True), "douyin_comment")

    def test_send_comment_types_clicks_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = FakeDevice()
            self.assertTrue(send_comment(device, recorder, "雨中的舞台很有力量", 1))
            self.assertEqual(device.typed, "雨中的舞台很有力量")
            self.assertTrue(
                (recorder.run_dir / "video-1-comment-sent.png").is_file()
            )
            send_selectors = [
                value for selector, value in device.selectors if selector.get("text") == "发送"
            ]
            self.assertTrue(send_selectors[0].clicked)

    def test_comment_screenshot_evidence_requires_successful_send(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            self.assertEqual(_comment_screenshot_evidence(recorder, 2, False), [])
            evidence = _comment_screenshot_evidence(recorder, 2, True)
        self.assertEqual(evidence[0]["video_index"], 2)
        self.assertTrue(evidence[0]["screenshot_path"].endswith("video-2-comment-sent.png"))

    def test_send_comment_accepts_current_empty_panel_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = FakeDevice(input_placeholder="分享你此刻的想法")
            self.assertTrue(send_comment(device, recorder, "这个视角很有意思", 1))
            self.assertEqual(device.typed, "这个视角很有意思")

    def test_send_comment_uses_stable_editor_resource_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = FakeDevice(input_placeholder=None, resource_input=True)
            self.assertTrue(send_comment(device, recorder, "花养得真好", 1))
            self.assertEqual(device.typed, "花养得真好")

    def test_send_comment_uses_clickable_send_container_resource_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = FakeDevice(send_text=False, send_resource=True)
            self.assertTrue(send_comment(device, recorder, "这个分享很实用", 1))
            send_selectors = [
                value
                for selector, value in device.selectors
                if selector.get("resourceId") == "com.ss.android.ugc.aweme:id/e27"
            ]
            self.assertTrue(send_selectors[0].clicked)

    def test_send_comment_verification_ignores_emoji_split_from_ui_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = FakeDevice(hierarchy_text="花朵色彩明艳，看着心情真好。")
            self.assertTrue(
                send_comment(device, recorder, "花朵色彩明艳，看着心情真好。🌻", 1)
            )

    def test_duplicate_comment_is_blocked_before_typing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = FakeDevice(duplicate=True)
            with self.assertRaises(RuntimeError):
                send_comment(device, recorder, "雨中的舞台很有力量", 1)
            self.assertEqual(device.typed, "")


class WorkerSupportTest(unittest.TestCase):
    def test_topic_session_actions_are_probability_only_even_with_zero_legacy_caps(self) -> None:
        class ProbabilityRunner:
            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())
            def like_verified(self, video, frame): return True
            def favorite_verified(self, video, frame): return True

        class SafeTopic:
            matches = True
            confidence = 1.0
            raw_response = "{}"
            safe = True
            def public_dict(self):
                return {"matches": True, "topic": "测试", "reason": "matched", "confidence": 1.0, "safe": True}

        config = {
            "seed": 1, "video_count": 2, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": True, "topic_confidence": 0.7,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "engagement_requires_topic": False, "comment_requires_topic": False,
            "max_likes": 0, "max_favorites": 0, "max_comments": 0,
            "like_probability": 1, "favorite_probability": 1,
            "comment_probability": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("worker.Uia2DouyinRunner", ProbabilityRunner),
                patch("worker.analyze_topic", return_value=SafeTopic()),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)
        self.assertEqual(result["likes"], 2)
        self.assertEqual(result["favorites"], 2)
        self.assertEqual(result["action_control"], "probability_only")
        self.assertNotIn("action_caps", result)

    def test_topic_session_stops_at_next_video_checkpoint(self) -> None:
        class CheckpointRunner:
            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())

        class SafeTopic:
            matches = False
            raw_response = "{}"
            safe = True
            def public_dict(self):
                return {"matches": False, "topic": "", "reason": "unrelated", "confidence": 1.0, "safe": True}

        config = {
            "seed": 1, "video_count": 5, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": True,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "like_probability": 0, "favorite_probability": 0, "comment_probability": 0,
        }
        checks = iter([False, True])
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("worker.Uia2DouyinRunner", CheckpointRunner),
                patch("worker.analyze_topic", return_value=SafeTopic()),
            ):
                result = topic_session(
                    FakeDevice(),
                    recorder,
                    config=config,
                    stop_checker=lambda: next(checks),
                )
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["stopped_by_user"])
        self.assertEqual(result["videos_seen"], 1)

    def test_worker_finishes_claimed_topic_task_as_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            payload = {
                "seed": 1, "video_count": 2, "round_count": 1,
                "round_interval_minutes": 0, "dwell_min": 0, "dwell_max": 0,
                "max_gate_skips": 3, "preview_only": True,
                "topic_filter_enabled": False, "topic_prompt": "不限主题",
                "engagement_requires_topic": False, "comment_requires_topic": False,
                "like_probability": 0, "favorite_probability": 0, "comment_probability": 0,
                "matched_like_probability": 0, "matched_favorite_probability": 0,
                "matched_comment_probability": 0,
            }
            task_id = store.submit("douyin_topic_session", "device-1", payload)
            healthy = FakeDevice()

            def stop_during_task(*args, **kwargs):
                store.request_stop(["device-1"])
                return {"status": "stopped", "stopped_by_user": True, "videos_seen": 1}

            with (
                patch("worker.DeviceLock", return_value=nullcontext()),
                patch("worker.connect_with_retry", return_value=healthy),
                patch("worker.execute_task", side_effect=stop_during_task),
            ):
                run_worker(
                    store=store,
                    device_id="device-1",
                    artifacts_root=root / "artifacts",
                    poll_seconds=0.01,
                    max_tasks=2,
                    connect_attempts=1,
                    reconnect_delay_seconds=0,
                    offline_wait_seconds=0,
                )
            task = store.get(task_id)
            self.assertEqual(task.status, "stopped")
            self.assertEqual(task.error, "stopped_by_user")

    def test_topic_session_stops_after_consecutive_anomaly_threshold(self) -> None:
        class BlockedRunner:
            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(False, ("unexpected_page",), ())
            def main_feed_confirmed(self, image): return True
            def recover_main_feed(self, reason): return True

        config = {
            "seed": 1, "video_count": 3, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 2, "preview_only": True, "topic_confidence": 0.7,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "engagement_requires_topic": False, "comment_requires_topic": False,
            "like_probability": 0, "favorite_probability": 0, "comment_probability": 0,
        }
        incidents = []
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with patch("worker.Uia2DouyinRunner", BlockedRunner):
                with self.assertRaisesRegex(DeviceFatalError, "连续异常页面达到 2 条"):
                    topic_session(FakeDevice(), recorder, config=config, incident_sink=incidents.append)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["outcome"], "device_fatal")

    def test_normal_video_resets_consecutive_anomaly_counter(self) -> None:
        class MixedRunner:
            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                allowed = video == 2
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(allowed, () if allowed else ("unexpected_page",), ())

        class SafeTopic:
            matches = True
            confidence = 1.0
            raw_response = "{}"
            safe = True
            def public_dict(self):
                return {"matches": True, "topic": "测试", "reason": "matched", "confidence": 1.0, "safe": True}

        config = {
            "seed": 1, "video_count": 3, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 2, "preview_only": True, "topic_confidence": 0.7,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "engagement_requires_topic": False, "comment_requires_topic": False,
            "like_probability": 0, "favorite_probability": 0, "comment_probability": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("worker.Uia2DouyinRunner", MixedRunner),
                patch("worker.analyze_topic", return_value=SafeTopic()),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["blocked_pages"], 2)
        self.assertEqual(result["videos_seen"], 3)

    def test_topic_session_records_recoverable_video_error_and_continues(self) -> None:
        class ResilientRunner:
            def __init__(self, *args, **kwargs):
                self.swipes = []

            def ensure_app_ready(self):
                return None

            def ensure_profile(self, image):
                return None

            def require_main_feed(self, image, stage):
                return None

            def swipe_next(self, from_video, to_video):
                self.swipes.append(to_video)
                if to_video == 1:
                    raise RuntimeError("Comment panel did not close after bounded recovery")

            def watch(self, video, dwell):
                return None

            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())

            def main_feed_confirmed(self, image):
                return False

            def recover_main_feed(self, reason):
                return True

        class TopicDecision:
            matches = False
            confidence = 0.9
            raw_response = "{}"

            def public_dict(self):
                return {"matches": False, "topic": "", "reason": "not matched", "confidence": 0.9, "safe": True}

        config = {
            "seed": 1, "video_count": 2, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": True, "topic_confidence": 0.7,
            "topic_filter_enabled": True, "topic_prompt": "测试", "max_likes": 0,
            "max_favorites": 0, "max_comments": 0, "like_probability": 0,
            "favorite_probability": 0, "comment_probability": 0,
            "like_only_on_match": True,
        }
        incidents = []
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = FakeDevice()
            device.dump_hierarchy = unittest.mock.Mock(return_value="<hierarchy />")
            with (
                patch("worker.Uia2DouyinRunner", ResilientRunner),
                patch("worker.analyze_topic", return_value=TopicDecision()),
            ):
                result = topic_session(
                    device,
                    recorder,
                    config=config,
                    incident_sink=incidents.append,
                )
            artifacts_exist = (
                Path(incidents[0]["screenshot_path"]).exists()
                and Path(incidents[0]["ui_tree_path"]).exists()
            )

        self.assertEqual(result["status"], "passed_with_recovery")
        self.assertEqual(result["video_errors"], 1)
        self.assertEqual(result["recovered_videos"], 1)
        self.assertEqual(result["videos_seen"], 1)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["stage"], "swipe")
        self.assertEqual(incidents[0]["outcome"], "recovered")
        self.assertTrue(artifacts_exist)

    def test_wait_while_paused_returns_after_resume(self) -> None:
        store = unittest.mock.Mock()
        store.is_paused.side_effect = [True, True, False]
        with patch("worker.time.sleep", return_value=None) as sleep:
            wait_while_paused(store, 0.01)
        self.assertEqual(sleep.call_count, 2)

    def test_stop_request_interrupts_pause_wait_without_resuming_pool(self) -> None:
        store = unittest.mock.Mock()
        store.is_paused.return_value = True
        store.is_stop_requested.return_value = True
        with patch("worker.time.sleep", return_value=None) as sleep:
            wait_while_paused(store, 0.01, device_id="device-1")
        sleep.assert_not_called()

    def test_wake_and_unlock_turns_on_non_secure_device(self) -> None:
        class SleepingDevice:
            screen_on_calls = 0
            unlock_calls = 0

            @property
            def info(self):
                return {"screenOn": self.screen_on_calls > 0}

            def screen_on(self):
                self.screen_on_calls += 1

            def unlock(self):
                self.unlock_calls += 1

        device = SleepingDevice()
        with patch("worker.time.sleep", return_value=None):
            self.assertTrue(wake_and_unlock(device))
        self.assertEqual(device.screen_on_calls, 1)
        self.assertEqual(device.unlock_calls, 1)

    def test_parser_accepts_two_video_demo_task(self) -> None:
        args = build_parser().parse_args(
            ["submit", "douyin_two_video_demo", "--dwell", "4,7"]
        )
        self.assertEqual(args.task_type, "douyin_two_video_demo")
        self.assertEqual(args.dwell, [4.0, 7.0])

    def test_connect_retries_then_succeeds(self) -> None:
        device = FakeDevice()
        with patch("worker.u2.connect", side_effect=[RuntimeError("offline"), device]):
            connected = connect_with_retry("device-1", attempts=2, delay_seconds=0)
        self.assertIs(connected, device)

    def test_device_preflight_fails_closed(self) -> None:
        device = FakeDevice()
        device.app_current = unittest.mock.Mock(side_effect=RuntimeError("usb gone"))
        self.assertFalse(device_preflight(device))

    def test_worker_reconnects_before_claiming_ready_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            stale = FakeDevice()
            stale.app_current = unittest.mock.Mock(side_effect=RuntimeError("offline"))
            healthy = FakeDevice()
            with (
                patch("worker.DeviceLock", return_value=nullcontext()),
                patch("worker.connect_with_retry", side_effect=[stale, healthy]) as connect,
                patch("worker.execute_task", return_value={"status": "passed"}),
            ):
                run_worker(
                    store=store,
                    device_id="device-1",
                    artifacts_root=root / "artifacts",
                    poll_seconds=0.01,
                    max_tasks=1,
                    connect_attempts=1,
                    reconnect_delay_seconds=0,
                    offline_wait_seconds=0,
                )
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(store.get(task_id).status, "completed")

    def test_task_error_is_recorded_without_reconnect_when_device_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            failed_id = store.submit("healthcheck", "device-1")
            completed_id = store.submit("healthcheck", "device-1")
            healthy = FakeDevice()
            with (
                patch("worker.DeviceLock", return_value=nullcontext()),
                patch("worker.connect_with_retry", return_value=healthy) as connect,
                patch(
                    "worker.execute_task",
                    side_effect=[RuntimeError("page changed"), {"status": "passed"}],
                ),
            ):
                run_worker(
                    store=store,
                    device_id="device-1",
                    artifacts_root=root / "artifacts",
                    poll_seconds=0.01,
                    max_tasks=2,
                    connect_attempts=1,
                    reconnect_delay_seconds=0,
                    offline_wait_seconds=0,
                )
            self.assertEqual(connect.call_count, 1)
            self.assertEqual(store.get(failed_id).status, "failed")
            self.assertEqual(store.get(completed_id).status, "completed")
            incidents = store.list_incidents()
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0].stage, "task")
            self.assertEqual(incidents[0].outcome, "skipped")

    def test_report_is_written_from_task_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            store.claim_next("device-1", "worker-1")
            store.finish(
                task_id,
                status="completed",
                run_dir="run/path",
                result={"status": "passed", "wall_s": 3.25},
            )
            output = root / "report.md"
            stats = write_report(store, output)
            report = output.read_text(encoding="utf-8")
            self.assertEqual(stats["by_status"], {"completed": 1})
            self.assertIn("| completed | 1 |", report)
            self.assertIn("平均 3.25 秒", report)


if __name__ == "__main__":
    unittest.main()
