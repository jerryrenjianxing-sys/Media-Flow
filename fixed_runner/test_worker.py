from pathlib import Path
from contextlib import nullcontext
from datetime import datetime, timedelta
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from douyin_fixed_runner import DOUYIN_PACKAGE  # noqa: E402
from douyin_uia2_runner import GateDecision, Uia2RunRecorder  # noqa: E402
from comment_ai import CloudModelError, CommentConstraintDecision, CommentDecision  # noqa: E402
from task_store import TaskStore  # noqa: E402
from worker import (  # noqa: E402
    DeviceFatalError,
    ModelChannelError,
    _comment_screenshot_evidence,
    _comment_task_type,
    build_parser,
    connect_with_retry,
    device_preflight,
    run_worker,
    routed_action_plan,
    routed_action_probabilities,
    process_current_comment,
    send_comment,
    topic_session,
    wait_while_paused,
    wake_and_unlock,
    write_report,
)
from execution_tasks import (  # noqa: E402
    _input_comment_text,
    _interaction_safety_verification_visible,
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

    def test_search_source_trust_routes_reactions_but_keeps_comment_strict(self) -> None:
        config = {
            "content_mode": "search",
            "search_trust_results": True,
            "like_probability": 0.1,
            "favorite_probability": 0.05,
            "comment_probability": 0.02,
            "matched_like_probability": 1.0,
            "matched_favorite_probability": 0.9,
            "matched_comment_probability": 0.8,
        }
        probabilities, routes = routed_action_plan(config, matched=False, safe=True)
        self.assertEqual(
            probabilities,
            {"like": 1.0, "favorite": 0.9, "comment": 0.0},
        )
        self.assertEqual(routes["like"], "search_source_trusted")
        self.assertEqual(routes["favorite"], "search_source_trusted")
        self.assertEqual(routes["comment"], "topic_mismatch_blocked")

        probabilities, routes = routed_action_plan(config, matched=True, safe=True)
        self.assertEqual(probabilities["comment"], 0.8)
        self.assertEqual(routes["comment"], "topic_matched")

    def test_search_source_trust_never_bypasses_safety_or_legacy_routing(self) -> None:
        config = {
            "content_mode": "search",
            "search_trust_results": True,
            "like_probability": 0.1,
            "favorite_probability": 0.05,
            "comment_probability": 0.02,
            "matched_like_probability": 1.0,
            "matched_favorite_probability": 0.9,
            "matched_comment_probability": 0.8,
        }
        probabilities, routes = routed_action_plan(config, matched=True, safe=False)
        self.assertEqual(probabilities, {"like": 0.0, "favorite": 0.0, "comment": 0.0})
        self.assertEqual(set(routes.values()), {"safety_blocked"})

        legacy = {**config, "search_trust_results": False}
        probabilities, routes = routed_action_plan(legacy, matched=False, safe=True)
        self.assertEqual(probabilities["comment"], 0.0)
        self.assertEqual(routes["like"], "other_safe_content")
        self.assertEqual(routes["comment"], "topic_mismatch_blocked")

    def test_hybrid_routes_search_and_home_with_separate_rules(self) -> None:
        config = {
            "content_mode": "hybrid",
            "search_trust_results": True,
            "like_probability": 0.2,
            "favorite_probability": 0.1,
            "comment_probability": 0.4,
            "matched_like_probability": 0.8,
            "matched_favorite_probability": 0.7,
            "matched_comment_probability": 0.6,
        }
        search_probabilities, search_routes = routed_action_plan(
            config, matched=False, safe=True, feed_phase="search"
        )
        self.assertEqual(
            search_probabilities,
            {"like": 0.8, "favorite": 0.7, "comment": 0.0},
        )
        self.assertEqual(search_routes["like"], "search_source_trusted")
        self.assertEqual(search_routes["comment"], "topic_mismatch_blocked")

        home_probabilities, home_routes = routed_action_plan(
            config, matched=False, safe=True, feed_phase="home"
        )
        self.assertEqual(
            home_probabilities,
            {"like": 0.2, "favorite": 0.1, "comment": 0.0},
        )
        self.assertEqual(home_routes["like"], "home_safe_random")
        self.assertEqual(home_routes["comment"], "topic_mismatch_blocked")

        matched_home_probabilities, matched_home_routes = routed_action_plan(
            config, matched=True, safe=True, feed_phase="home"
        )
        self.assertEqual(
            matched_home_probabilities,
            {"like": 0.2, "favorite": 0.1, "comment": 0.4},
        )
        self.assertEqual(matched_home_routes["like"], "home_safe_random")
        self.assertEqual(matched_home_routes["comment"], "topic_matched")

        unknown_probabilities, unknown_routes = routed_action_plan(
            config, matched=True, safe=True
        )
        self.assertEqual(set(unknown_probabilities.values()), {0.0})
        self.assertEqual(set(unknown_routes.values()), {"feed_phase_unverified"})

    def test_identity_safety_modal_requires_title_and_body_evidence(self) -> None:
        self.assertTrue(
            _interaction_safety_verification_visible(
                '<node text="身份安全验证"/><node text="系统识别你的操作环境存在风险"/>'
            )
        )
        self.assertFalse(
            _interaction_safety_verification_visible(
                '<node text="视频字幕：身份安全验证经验分享"/>'
            )
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
    def test_comment_input_prefers_verified_selector_text(self) -> None:
        class Selector:
            value = ""

            def set_text(self, value):
                self.value = value

            def get_text(self):
                return self.value

        class Device:
            def send_keys(self, *_args, **_kwargs):
                raise AssertionError("IME fallback should not be used")

        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            method = _input_comment_text(
                Device(), Selector(), "工业自动化流程很清楚", recorder, 1
            )
        self.assertEqual(method, "selector_set_text")

    def test_comment_input_falls_back_when_selector_readback_differs(self) -> None:
        class Selector:
            def set_text(self, _value):
                return None

            def get_text(self):
                return ""

        class Device:
            typed = ""

            def send_keys(self, value, clear=True):
                self.typed = value

        device = Device()
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            method = _input_comment_text(
                device, Selector(), "工业自动化流程很清楚", recorder, 1
            )
        self.assertEqual(method, "uiautomator_ime")
        self.assertEqual(device.typed, "工业自动化流程很清楚")

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

    def test_send_comment_finds_dynamic_bottom_input_semantically(self) -> None:
        class DynamicInputDevice(FakeDevice):
            def __init__(self) -> None:
                super().__init__(input_placeholder=None)
                self.input_clicked = False

            def click(self, x: int, y: int) -> None:
                self.input_clicked = True

            def __call__(self, **selector):
                if selector.get("className") == "android.widget.EditText":
                    value = FakeSelector(self.input_clicked)
                    self.selectors.append((selector, value))
                    return value
                return super().__call__(**selector)

            def dump_hierarchy(self, compressed=True, pretty=False) -> str:
                if self.typed:
                    return super().dump_hierarchy(compressed=compressed, pretty=pretty)
                return (
                    '<hierarchy><node package="com.ss.android.ugc.aweme" '
                    'visible-to-user="true" class="android.widget.TextView" '
                    'text="爱评论的人，运气不会差" clickable="true" '
                    'bounds="[40,2190][790,2325]" /></hierarchy>'
                )

        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = DynamicInputDevice()
            self.assertTrue(send_comment(device, recorder, "这个分享很有意思", 1))
            self.assertTrue(device.input_clicked)
            self.assertEqual(device.typed, "这个分享很有意思")

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

    def test_comment_constraint_blocks_before_device_input(self) -> None:
        class CommentRunner:
            profile = type("Profile", (), {"width": 1080, "height": 2400})()
            def tap_control(self, *args): pass
            def close_comment_panel(self, *args): pass

        generated = CommentDecision(
            "comment", "今天的早餐看着不错", "画面清晰", 0.95, False, "{}"
        )
        blocked = CommentConstraintDecision(
            "block",
            "constraint_match",
            "视频主体属于用户排除的日常生活",
            0.96,
            "comment-constraint-v2-2026-08-30",
            "{}",
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("douyin_fixed_runner.comment_panel_visible", return_value=True),
                patch("execution_tasks.generate_comment", return_value=generated),
                patch("execution_tasks.review_comment_constraint", return_value=blocked),
                patch("execution_tasks.send_comment") as send,
            ):
                decision, sent, review = process_current_comment(
                    FakeDevice(),
                    recorder,
                    CommentRunner(),
                    video=1,
                    send=True,
                    comment_policy_enabled=True,
                    comment_policy_prompt="不对日常生活相关内容发表评论",
                )
        self.assertEqual(decision.decision, "comment")
        self.assertFalse(sent)
        self.assertFalse(review["allowed"])
        self.assertEqual(review["category"], "constraint_match")
        send.assert_not_called()

    def test_comment_constraint_error_fails_closed_without_device_input(self) -> None:
        class CommentRunner:
            profile = type("Profile", (), {"width": 1080, "height": 2400})()
            def tap_control(self, *args): pass
            def close_comment_panel(self, *args): pass

        generated = CommentDecision(
            "comment", "这个工艺流程很清楚", "工业画面", 0.95, False, "{}"
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("douyin_fixed_runner.comment_panel_visible", return_value=True),
                patch("execution_tasks.generate_comment", return_value=generated),
                patch(
                    "execution_tasks.review_comment_constraint",
                    side_effect=RuntimeError("model unavailable"),
                ),
                patch("execution_tasks.send_comment") as send,
            ):
                _, sent, review = process_current_comment(
                    FakeDevice(),
                    recorder,
                    CommentRunner(),
                    video=1,
                    send=True,
                    comment_policy_enabled=True,
                    comment_policy_prompt="避免口语化表达",
                )
        self.assertFalse(sent)
        self.assertEqual(review["category"], "policy_uncertain")
        self.assertIn("review_error", review["reason"])
        send.assert_not_called()

    def test_empty_comment_panel_skips_generation_and_closes(self) -> None:
        class EmptyPanelDevice(FakeDevice):
            def dump_hierarchy(self, compressed=True, pretty=False) -> str:
                return (
                    '<hierarchy><node package="com.ss.android.ugc.aweme" '
                    'visible-to-user="true" text="评论 0" bounds="[30,900][300,980]" />'
                    '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
                    'text="期待你的评论" bounds="[180,1300][900,1420]" /></hierarchy>'
                )

        class CommentRunner:
            profile = type("Profile", (), {"width": 1080, "height": 2400})()

            def __init__(self) -> None:
                self.closed = False

            def tap_control(self, *args):
                pass

            def close_comment_panel(self, *args):
                self.closed = True

        runner = CommentRunner()
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("douyin_fixed_runner.comment_panel_visible", return_value=True),
                patch("execution_tasks.generate_comment") as generate,
            ):
                decision, sent, review = process_current_comment(
                    EmptyPanelDevice(), recorder, runner, video=1, send=True
                )

        self.assertEqual(decision.decision, "skip")
        self.assertFalse(sent)
        self.assertIsNone(review)
        self.assertTrue(runner.closed)
        generate.assert_not_called()

    def test_comment_failure_attaches_pre_close_evidence(self) -> None:
        class PanelDevice(FakeDevice):
            panel_open = True

            def screenshot(self, format="pillow") -> Image.Image:
                return Image.new(
                    "RGB", (1080, 2400), "black" if self.panel_open else "white"
                )

        class CommentRunner:
            profile = type("Profile", (), {"width": 1080, "height": 2400})()

            def __init__(self, device) -> None:
                self.device = device

            def tap_control(self, *args):
                pass

            def close_comment_panel(self, *args):
                self.device.panel_open = False

        generated = CommentDecision(
            "comment", "测试评论", "测试", 0.95, False, "{}"
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            device = PanelDevice()
            with (
                patch("douyin_fixed_runner.comment_panel_visible", return_value=True),
                patch("execution_tasks.generate_comment", return_value=generated),
                patch(
                    "execution_tasks.send_comment",
                    side_effect=RuntimeError("Comment input box was not found"),
                ),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    process_current_comment(
                        device,
                        recorder,
                        CommentRunner(device),
                        video=1,
                        send=True,
                    )

            evidence_path = Path(raised.exception.evidence_screenshot_path)
            self.assertTrue(evidence_path.is_file())
            self.assertEqual(Image.open(evidence_path).getpixel((0, 0)), (0, 0, 0))

    def test_verified_send_survives_later_comment_panel_close_failure(self) -> None:
        class CommentRunner:
            profile = type("Profile", (), {"width": 1080, "height": 2400})()

            def tap_control(self, *args): pass

            def close_comment_panel(self, *args):
                raise RuntimeError("Comment panel did not close after bounded recovery")

        generated = CommentDecision(
            "comment", "推导过程很清楚", "基于画面", 0.95, False, "{}"
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("douyin_fixed_runner.comment_panel_visible", return_value=True),
                patch("execution_tasks.generate_comment", return_value=generated),
                patch("execution_tasks.send_comment", return_value=True),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    process_current_comment(
                        FakeDevice(),
                        recorder,
                        CommentRunner(),
                        video=1,
                        send=True,
                    )

        outcome = raised.exception.confirmed_comment_outcome
        self.assertEqual(outcome[0].comment, "推导过程很清楚")
        self.assertTrue(outcome[1])
        self.assertIsNone(outcome[2])

    def test_topic_session_startup_uses_browsable_feed_requirement(self) -> None:
        class BrowsableRunner:
            def __init__(self, *args, **kwargs):
                self.shell_checked = False
                self.calls = 0

            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage):
                raise AssertionError("strict interaction precondition was used")
            def require_main_feed_shell(self, image, stage):
                self.shell_checked = True
            def drain_recovery_events(self): return []
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                self.calls += 1
                decision = (
                    GateDecision(False, ("live",), ("直播中",))
                    if self.calls == 1
                    else GateDecision(True, (), ())
                )
                return Image.new("RGB", (1080, 2400), "black"), decision

        config = {
            "seed": 1, "video_count": 1, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 1, "preview_only": True,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "like_probability": 0, "favorite_probability": 0, "comment_probability": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            topic = Mock(
                matches=False, safe=True, raw_response="{}", reason="safe",
                topic="", evidence=(),
            )
            topic.public_dict.return_value = {
                "matches": False, "safe": True, "reason": "safe", "topic": ""
            }
            with (
                patch("execution_tasks.Uia2DouyinRunner", BrowsableRunner),
                patch("execution_tasks.analyze_topic", return_value=topic),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)

        self.assertEqual(result["known_safe_skips"], 1)
        self.assertEqual(result["unknown_blocked_pages"], 0)

    def test_known_feed_skips_do_not_trip_consecutive_anomaly_limit(self) -> None:
        class KnownSkipRunner:
            def __init__(self, *args, **kwargs): self.calls = 0
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                self.calls += 1
                decision = (
                    GateDecision(False, ("live",), ("直播中",))
                    if self.calls <= 3
                    else GateDecision(True, (), ())
                )
                return Image.new("RGB", (1080, 2400), "black"), decision

        config = {
            "seed": 1, "video_count": 3, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 1, "preview_only": True,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "like_probability": 0, "favorite_probability": 0, "comment_probability": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            topic = Mock(
                matches=False, safe=True, raw_response="{}", reason="safe",
                topic="", evidence=(),
            )
            topic.public_dict.return_value = {
                "matches": False, "safe": True, "reason": "safe", "topic": ""
            }
            with (
                patch("execution_tasks.Uia2DouyinRunner", KnownSkipRunner),
                patch("execution_tasks.analyze_topic", return_value=topic),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["known_safe_skips"], 3)
        self.assertEqual(result["blocked_pages"], 3)

    def test_home_image_notes_reenter_once_then_stop_as_supply_shortage(self) -> None:
        class ImageNoteRunner:
            prepared: list[tuple[str, str]] = []

            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def prepare_feed_phase(self, phase, query, reason):
                self.prepared.append((phase, reason))
                return True
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2340), "black"), GateDecision(
                    False, ("non_video_feed_item",), ("图文", "图片1，按钮")
                )

        ImageNoteRunner.prepared = []
        config = {
            "seed": 1, "video_count": 1, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 1, "preview_only": True,
            "content_mode": "hybrid", "search_query": "人工智能",
            "topic_filter_enabled": True, "topic_prompt": "人工智能",
            "search_segment_min": 1, "search_segment_max": 1,
            "home_segment_min": 1, "home_segment_max": 1,
            "like_probability": 0, "favorite_probability": 0,
            "comment_probability": 0, "matched_like_probability": 0,
            "matched_favorite_probability": 0, "matched_comment_probability": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with patch("execution_tasks.Uia2DouyinRunner", ImageNoteRunner):
                with self.assertRaises(DeviceFatalError) as raised:
                    topic_session(FakeDevice(), recorder, config=config)

        self.assertIn("视频供给不足", str(raised.exception))
        self.assertEqual(len(ImageNoteRunner.prepared), 2)

    def test_home_image_note_never_calls_topic_model(self) -> None:
        class OneImageNoteRunner:
            def __init__(self, *args, **kwargs): self.calls = 0
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                self.calls += 1
                return (
                    Image.new("RGB", (1080, 2340), "black"),
                    GateDecision(False, ("non_video_feed_item",), ())
                    if self.calls == 1 else GateDecision(True, (), ()),
                )

        class SafeTopic:
            matches = False; safe = True; raw_response = "{}"; reason = "safe"; topic = ""; evidence = ()
            def public_dict(self): return {"matches": False, "safe": True, "reason": "safe", "topic": ""}

        config = {
            "seed": 1, "video_count": 1, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 1, "preview_only": True,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "like_probability": 0, "favorite_probability": 0, "comment_probability": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("execution_tasks.Uia2DouyinRunner", OneImageNoteRunner),
                patch("execution_tasks.analyze_topic", return_value=SafeTopic()) as model,
            ):
                result = topic_session(FakeDevice(), recorder, config=config)

        self.assertEqual(result["non_video_feed_items"], 1)
        self.assertEqual(result["videos_seen"], 1)
        self.assertEqual(result["topic_analysis_skipped"], 1)
        model.assert_not_called()


class WorkerSupportTest(unittest.TestCase):
    def test_hybrid_session_alternates_fixed_segments_and_counts_only_valid_videos(self) -> None:
        class HybridRunner:
            prepared: list[str] = []

            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def prepare_feed_phase(self, phase, query, reason):
                self.prepared.append(phase)
                return True
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())

        class SafeTopic:
            matches = False
            raw_response = "{}"
            safe = True
            reason = "unrelated"
            topic = "other"
            evidence = ()
            def public_dict(self):
                return {"matches": False, "safe": True, "reason": self.reason, "topic": self.topic}

        HybridRunner.prepared = []
        config = {
            "seed": 19, "video_count": 20, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": True,
            "content_mode": "hybrid", "search_query": "人工智能",
            "search_trust_results": True, "topic_filter_enabled": True,
            "topic_prompt": "人工智能", "search_segment_min": 7,
            "search_segment_max": 7, "home_segment_min": 5,
            "home_segment_max": 5, "like_probability": 0,
            "favorite_probability": 0, "comment_probability": 0,
            "matched_like_probability": 0, "matched_favorite_probability": 0,
            "matched_comment_probability": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("execution_tasks.Uia2DouyinRunner", HybridRunner),
                patch("execution_tasks.analyze_topic", return_value=SafeTopic()),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)

        self.assertEqual(HybridRunner.prepared, ["search", "home", "search", "home"])
        self.assertEqual(result["videos_seen"], 20)
        self.assertEqual(result["phase_summaries"]["search"]["videos"], 14)
        self.assertEqual(result["phase_summaries"]["home"]["videos"], 6)

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
                patch("execution_tasks.Uia2DouyinRunner", ProbabilityRunner),
                patch("execution_tasks.analyze_topic", return_value=SafeTopic()),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)
        self.assertEqual(result["likes"], 2)
        self.assertEqual(result["favorites"], 2)
        self.assertEqual(result["action_control"], "probability_only")
        self.assertNotIn("action_caps", result)

    def test_search_trust_executes_reactions_but_blocks_unmatched_comment(self) -> None:
        calls = {"like": 0, "favorite": 0, "comment": 0}

        class SearchRunner:
            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def enter_topic_search(self, query): self.query = query
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())
            def like_verified(self, video, frame):
                calls["like"] += 1
                return True
            def favorite_verified(self, video, frame):
                calls["favorite"] += 1
                return True

        class SafeUnmatchedTopic:
            matches = False
            raw_response = "{}"
            safe = True
            reason = "unrelated"
            topic = "测试"
            evidence = ("当前画面与目标主题无关",)
            def public_dict(self):
                return {
                    "matches": False,
                    "relevance": "unrelated",
                    "topic": "测试",
                    "evidence": list(self.evidence),
                    "reason": self.reason,
                    "confidence": 1.0,
                    "safe": True,
                }

        config = {
            "seed": 1, "video_count": 1, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": False,
            "content_mode": "search", "search_query": "人工智能",
            "search_trust_results": True, "topic_filter_enabled": True,
            "topic_prompt": "人工智能", "like_probability": 0,
            "favorite_probability": 0, "comment_probability": 1,
            "matched_like_probability": 1, "matched_favorite_probability": 1,
            "matched_comment_probability": 1, "comment_policy_enabled": False,
            "comment_policy_prompt": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("execution_tasks.Uia2DouyinRunner", SearchRunner),
                patch("execution_tasks.analyze_topic", return_value=SafeUnmatchedTopic()),
                patch(
                    "execution_tasks.process_current_comment",
                    side_effect=lambda *args, **kwargs: calls.__setitem__("comment", calls["comment"] + 1),
                ),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)
            decisions = json.loads(
                (recorder.run_dir / "topic-session-decisions.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(calls, {"like": 1, "favorite": 1, "comment": 0})
        self.assertEqual(result["likes"], 1)
        self.assertEqual(result["favorites"], 1)
        self.assertEqual(result["comments_sent"], 0)
        self.assertEqual(decisions[0]["action_routes"]["comment"], "topic_mismatch_blocked")

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
                patch("execution_tasks.Uia2DouyinRunner", CheckpointRunner),
                patch("execution_tasks.analyze_topic", return_value=SafeTopic()),
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
            with patch("execution_tasks.Uia2DouyinRunner", BlockedRunner):
                with self.assertRaisesRegex(DeviceFatalError, "连续异常页面达到 2 条"):
                    topic_session(FakeDevice(), recorder, config=config, incident_sink=incidents.append)
        self.assertEqual(len(incidents), 2)
        self.assertEqual(incidents[-1]["outcome"], "device_fatal")

    def test_topic_model_403_uses_model_circuit_not_page_anomaly_limit(self) -> None:
        class FeedRunner:
            def __init__(self, *args, **kwargs):
                self.recovery_events = []

            def drain_recovery_events(self): return []
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())
            def main_feed_confirmed(self, image): return True
            def recover_main_feed(self, reason):
                raise AssertionError("model failures must not invoke page recovery")

        config = {
            "seed": 1, "video_count": 4, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 1, "preview_only": True, "topic_confidence": 0.7,
            "topic_filter_enabled": True, "content_mode": "topic",
            "topic_prompt": "人工智能", "engagement_requires_topic": True,
            "comment_requires_topic": True, "like_probability": 0,
            "favorite_probability": 0, "comment_probability": 0,
        }
        incidents = []
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("execution_tasks.Uia2DouyinRunner", FeedRunner),
                patch(
                    "execution_tasks.analyze_topic",
                    side_effect=CloudModelError(
                        "permanent_rejection",
                        "provider Terms Of Service",
                        status_code=403,
                        diagnostics={"provider_name": "example-route"},
                    ),
                ) as analyze,
            ):
                with self.assertRaisesRegex(
                    ModelChannelError, "model_channel_circuit_open"
                ) as raised:
                    topic_session(
                        FakeDevice(), recorder, config=config,
                        incident_sink=incidents.append,
                    )

        self.assertEqual(analyze.call_count, 3)
        self.assertEqual(
            [incident["outcome"] for incident in incidents],
            ["model_failed", "model_failed", "model_circuit_open"],
        )
        self.assertTrue(all(item["stage"] == "topic_model" for item in incidents))
        self.assertTrue(
            all(
                item["context"]["model_error"]["diagnostics"]["provider_name"]
                == "example-route"
                for item in incidents
            )
        )
        self.assertEqual(raised.exception.result["status"], "failed")
        self.assertEqual(raised.exception.result["videos_seen"], 3)

        reprobe_config = dict(config)
        reprobe_config.update(
            {
                "video_count": 1,
                "topic_filter_enabled": False,
                "content_mode": "general",
                "engagement_requires_topic": False,
                "comment_requires_topic": False,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("execution_tasks.Uia2DouyinRunner", FeedRunner),
                patch(
                    "execution_tasks.analyze_topic",
                    side_effect=CloudModelError(
                        "permanent_rejection", "provider rejected", status_code=403
                    ),
                ) as analyze_new_task,
            ):
                result = topic_session(
                    FakeDevice(), recorder, config=reprobe_config
                )
        self.assertEqual(analyze_new_task.call_count, 0)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["videos_seen"], 1)
        self.assertEqual(result["model_attempts"], 0)

    def test_normal_video_resets_consecutive_anomaly_counter(self) -> None:
        class MixedRunner:
            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                allowed = video % 2 == 0
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(allowed, () if allowed else ("unexpected_page",), ())
            def main_feed_confirmed(self, image): return False
            def recover_main_feed(self, reason): return True

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
                patch("execution_tasks.Uia2DouyinRunner", MixedRunner),
                patch("execution_tasks.analyze_topic", return_value=SafeTopic()),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)
        self.assertEqual(result["status"], "passed_with_recovery")
        self.assertEqual(result["blocked_pages"], 3)
        self.assertEqual(result["videos_seen"], 3)

    def test_visual_safety_gate_blocks_all_interactions(self) -> None:
        calls = {"like": 0, "favorite": 0, "comment_gate": 0}

        class UnsafeRunner:
            def __init__(self, *args, **kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                if action == "comment-preview":
                    calls["comment_gate"] += 1
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())
            def like_verified(self, video, frame):
                calls["like"] += 1
                return True
            def favorite_verified(self, video, frame):
                calls["favorite"] += 1
                return True

        class UnsafeTopic:
            matches = False
            relevance = "unrelated"
            topic = "商业推广"
            evidence = ("画面出现购买入口",)
            reason = "广告或商业推广"
            confidence = 1.0
            raw_response = "{}"
            safe = False
            def public_dict(self):
                return {
                    "matches": False,
                    "relevance": "unrelated",
                    "topic": self.topic,
                    "evidence": self.evidence,
                    "reason": self.reason,
                    "confidence": 1.0,
                    "safe": False,
                }

        config = {
            "seed": 1, "video_count": 1, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": False, "topic_confidence": 0.7,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "engagement_requires_topic": False, "comment_requires_topic": False,
            "like_probability": 1, "favorite_probability": 1, "comment_probability": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("execution_tasks.Uia2DouyinRunner", UnsafeRunner),
                patch("execution_tasks.analyze_topic", return_value=UnsafeTopic()),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)

        self.assertEqual(result["visual_safety_blocks"], 1)
        self.assertEqual(result["likes"], 0)
        self.assertEqual(result["favorites"], 0)
        self.assertEqual(result["comments_generated"], 0)
        self.assertEqual(calls, {"like": 0, "favorite": 0, "comment_gate": 0})

    def test_topic_session_records_recoverable_video_error_and_continues(self) -> None:
        class ResilientRunner:
            def __init__(self, *args, **kwargs):
                self.swipes = []
                self.recovery_events = []

            def drain_recovery_events(self):
                return []

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
                self.recovery_events.append(
                    {
                        "rule_id": "douyin-navigation-drift",
                        "rule_version": "1.0.0",
                        "action": "back",
                        "reason": reason,
                        "verified": True,
                    }
                )
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
                patch("execution_tasks.Uia2DouyinRunner", ResilientRunner),
                patch("execution_tasks.analyze_topic", return_value=TopicDecision()),
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
        self.assertEqual(result["videos_seen"], 2)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["stage"], "swipe")
        self.assertEqual(incidents[0]["outcome"], "recovered")
        self.assertEqual(incidents[0]["recovery_action"], "back")
        self.assertTrue(incidents[0]["context"]["verified_recovery"]["verified"])
        self.assertEqual(result["recovery_events"][0]["rule_id"], "douyin-navigation-drift")
        self.assertTrue(artifacts_exist)

    def test_topic_session_keeps_verified_comment_when_panel_cleanup_recovers(self) -> None:
        class CommentRunner:
            def __init__(self, *args, **kwargs):
                self.recovery_events = []

            def drain_recovery_events(self): return []
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())
            def main_feed_confirmed(self, image): return False
            def recover_main_feed(self, reason):
                self.recovery_events.append(
                    {
                        "rule_id": "douyin-navigation-drift",
                        "rule_version": "1.0.0",
                        "action": "back",
                        "reason": reason,
                        "verified": True,
                    }
                )
                return True

        class MatchedTopic:
            matches = True
            safe = True
            raw_response = "{}"

            def public_dict(self):
                return {
                    "matches": True,
                    "relevance": "exact",
                    "topic": "ai-manufacturing-plastics@1.2.0",
                    "evidence": ["画面直接讲解大模型"],
                    "reason": "exact",
                    "confidence": 1.0,
                    "safe": True,
                }

        decision = CommentDecision(
            "comment", "推导过程很清楚", "基于画面", 0.9, False, "{}"
        )
        review = {"video_index": 1, "allowed": True}
        cleanup_error = RuntimeError(
            "Comment panel did not close after bounded recovery"
        )
        cleanup_error.confirmed_comment_outcome = (decision, True, review)
        config = {
            "seed": 1, "video_count": 1, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": False,
            "content_mode": "mixed", "topic_filter_enabled": True,
            "topic_prompt": "AI", "like_probability": 0,
            "favorite_probability": 0, "comment_probability": 0,
            "matched_like_probability": 0, "matched_favorite_probability": 0,
            "matched_comment_probability": 1,
            "comment_policy_enabled": True, "comment_policy_prompt": "专业内容",
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            recorder.screenshot(FakeDevice(), "video-1-comment-sent")
            with (
                patch("execution_tasks.Uia2DouyinRunner", CommentRunner),
                patch("execution_tasks.analyze_topic", return_value=MatchedTopic()),
                patch(
                    "execution_tasks.process_current_comment",
                    side_effect=cleanup_error,
                ),
            ):
                result = topic_session(FakeDevice(), recorder, config=config)

        self.assertEqual(result["status"], "passed_with_recovery")
        self.assertEqual(result["topic_matches"], 1)
        self.assertEqual(result["comments_generated"], 1)
        self.assertEqual(result["comments_sent"], 1)
        self.assertEqual(result["comment_policy_allowed"], 1)
        self.assertEqual(len(result["comment_screenshots"]), 1)
        self.assertEqual(result["video_errors"], 1)

    def test_identity_verification_opens_reaction_circuit_for_rest_of_round(self) -> None:
        calls = {"like": 0, "favorite": 0}

        class VerificationRunner:
            def __init__(self, *args, **kwargs):
                self.recovery_events = []

            def drain_recovery_events(self): return []
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())
            def like_verified(self, video, frame):
                calls["like"] += 1
                raise RuntimeError("Like verification failed; stopping")
            def favorite_verified(self, video, frame):
                calls["favorite"] += 1
                return True
            def main_feed_confirmed(self, image): return False
            def recover_main_feed(self, reason):
                self.recovery_events.append(
                    {
                        "rule_id": "identity-safety-modal",
                        "rule_version": "1.0.0",
                        "action": "back",
                        "reason": reason,
                        "verified": True,
                    }
                )
                return True

        class SafeTopic:
            matches = False
            safe = True
            raw_response = "{}"
            def public_dict(self):
                return {
                    "matches": False,
                    "topic": "测试",
                    "reason": "safe",
                    "confidence": 1.0,
                    "safe": True,
                }

        config = {
            "seed": 1, "video_count": 2, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": True,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "engagement_requires_topic": False, "comment_requires_topic": False,
            "like_probability": 1, "favorite_probability": 1,
            "comment_probability": 0,
        }
        incidents = []
        device = FakeDevice(
            hierarchy_text="身份安全验证 系统识别你的操作环境存在风险，请完成身份验证"
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("execution_tasks.Uia2DouyinRunner", VerificationRunner),
                patch("execution_tasks.analyze_topic", return_value=SafeTopic()),
            ):
                result = topic_session(
                    device, recorder, config=config, incident_sink=incidents.append
                )

        self.assertEqual(calls, {"like": 1, "favorite": 0})
        self.assertEqual(result["videos_seen"], 2)
        self.assertEqual(result["status"], "passed_with_recovery")
        self.assertTrue(result["reaction_circuit_opened"])
        self.assertEqual(result["reaction_actions_disabled"], ["favorite", "like"])
        self.assertEqual(len(incidents), 1)
        self.assertTrue(incidents[0]["context"]["reaction_circuit_opened"])

    def test_identity_verification_opens_comment_circuit_for_rest_of_round(self) -> None:
        calls = {"comment": 0}

        class VerificationRunner:
            def __init__(self, *args, **kwargs): self.recovery_events = []
            def drain_recovery_events(self): return []
            def ensure_app_ready(self): pass
            def ensure_profile(self, image): pass
            def require_main_feed(self, image, stage): pass
            def swipe_next(self, from_video, to_video): pass
            def watch(self, video, dwell): pass
            def capture_gate(self, video, action):
                return Image.new("RGB", (1080, 2400), "black"), GateDecision(True, (), ())
            def main_feed_confirmed(self, image): return False
            def recover_main_feed(self, reason): return True

        class SafeTopic:
            matches = False
            safe = True
            raw_response = "{}"
            def public_dict(self):
                return {
                    "matches": False, "topic": "测试", "reason": "safe",
                    "confidence": 1.0, "safe": True,
                }

        def blocked_comment(*args, **kwargs):
            calls["comment"] += 1
            raise RuntimeError(
                "Comment send could not be verified; task will not retry automatically"
            )

        config = {
            "seed": 1, "video_count": 2, "dwell_min": 0, "dwell_max": 0,
            "max_gate_skips": 3, "preview_only": False,
            "topic_filter_enabled": False, "topic_prompt": "不限主题",
            "engagement_requires_topic": False, "comment_requires_topic": False,
            "like_probability": 0, "favorite_probability": 0,
            "comment_probability": 1,
        }
        incidents = []
        device = FakeDevice(
            hierarchy_text="身份安全验证 系统识别你的操作环境存在风险，请完成身份验证"
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = Uia2RunRecorder(Path(directory), "device-1")
            with (
                patch("execution_tasks.Uia2DouyinRunner", VerificationRunner),
                patch("execution_tasks.analyze_topic", return_value=SafeTopic()),
                patch("execution_tasks.process_current_comment", side_effect=blocked_comment),
            ):
                result = topic_session(
                    device, recorder, config=config, incident_sink=incidents.append
                )

        self.assertEqual(calls["comment"], 1)
        self.assertEqual(result["videos_seen"], 2)
        self.assertTrue(result["comment_circuit_opened"])
        self.assertEqual(result["comment_actions_disabled"], ["comment"])
        self.assertEqual(len(incidents), 1)
        self.assertTrue(incidents[0]["context"]["comment_circuit_opened"])

    def test_wait_while_paused_returns_after_resume(self) -> None:
        store = unittest.mock.Mock()
        store.is_paused.side_effect = [True, True, False]
        with patch("worker_runtime.time.sleep", return_value=None) as sleep:
            wait_while_paused(store, 0.01)
        self.assertEqual(sleep.call_count, 2)

    def test_stop_request_interrupts_pause_wait_without_resuming_pool(self) -> None:
        store = unittest.mock.Mock()
        store.is_paused.return_value = True
        store.is_stop_requested.return_value = True
        with patch("worker_runtime.time.sleep", return_value=None) as sleep:
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
        with patch("worker_runtime.time.sleep", return_value=None):
            self.assertTrue(wake_and_unlock(device))
        self.assertEqual(device.screen_on_calls, 1)
        self.assertEqual(device.unlock_calls, 1)

    def test_parser_accepts_two_video_demo_task(self) -> None:
        args = build_parser().parse_args(
            ["submit", "douyin_two_video_demo", "--device-id", "device-1", "--dwell", "4,7"]
        )
        self.assertEqual(args.task_type, "douyin_two_video_demo")
        self.assertEqual(args.dwell, [4.0, 7.0])

    def test_connect_retries_then_succeeds(self) -> None:
        device = FakeDevice()
        with patch("worker_runtime.u2.connect", side_effect=[RuntimeError("offline"), device]):
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

    def test_worker_preserves_degraded_result_as_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            with (
                patch("worker.DeviceLock", return_value=nullcontext()),
                patch("worker.connect_with_retry", return_value=FakeDevice()),
                patch(
                    "worker.execute_task",
                    return_value={
                        "status": "degraded",
                        "videos_seen": 20,
                        "degraded_reason": {"code": "model_channel_partial"},
                    },
                ),
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
            task = store.get(task_id)
            self.assertEqual(task.status, "degraded")
            self.assertEqual(task.result["videos_seen"], 20)

    def test_unrestored_inspection_preserves_evidence_and_stops_next_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            base = datetime.now().astimezone() - timedelta(minutes=1)
            inspection_id = store.submit(
                "douyin_engagement_inspection",
                "device-1",
                {
                    "submission_id": "submission-worker",
                    "inspection_index": 1,
                    "after_round_index": 5,
                    "inspection_every_rounds": 5,
                    "max_items_per_section": 20,
                },
                not_before=base.isoformat(timespec="microseconds"),
            )
            next_id = store.submit(
                "healthcheck",
                "device-1",
                not_before=(base + timedelta(microseconds=1)).isoformat(
                    timespec="microseconds"
                ),
            )
            failed_result = {
                "status": "failed",
                "restored": False,
                "failure_reason": "home_restore_failed",
                "sections": {"private_messages": {"status": "available"}},
            }
            with (
                patch("worker.DeviceLock", return_value=nullcontext()),
                patch("worker.connect_with_retry", return_value=FakeDevice()),
                patch(
                    "worker.execute_task",
                    side_effect=[failed_result, {"status": "passed"}],
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
            inspection = store.get(inspection_id)
            self.assertEqual(inspection.status, "failed")
            self.assertEqual(inspection.result["sections"], failed_result["sections"])
            self.assertEqual(store.get(next_id).status, "pending")
            self.assertTrue(store.is_stop_requested("device-1"))

    def test_worker_starts_one_version_revalidation_only_for_action_free_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            task_id = store.submit(
                "douyin_engagement_inspection",
                "device-1",
                {
                    "submission_id": "submission-recovery",
                    "inspection_index": 1,
                    "after_round_index": 1,
                    "inspection_every_rounds": 1,
                    "max_items_per_section": 20,
                },
            )
            failed_result = {
                "status": "failed",
                "restored": True,
                "failure_reason": "v2_app_version_changed",
                "failure_class": "recoverable_precondition",
                "recovery_eligible": True,
                "navigation_started": False,
                "expected_app_version": "40.2.0",
                "actual_app_version": "40.3.0",
                "expected_display_signature": "1080x2340x480x0x101",
                "actual_display_signature": "1080x2340x480x0x101",
                "sections": {},
            }
            with (
                patch("worker.DeviceLock", return_value=nullcontext()),
                patch("worker.connect_with_retry", return_value=FakeDevice()),
                patch("worker.execute_task", return_value=failed_result),
                patch(
                    "worker.recover_version_drift",
                    return_value={"status": "queued", "replacement_task_id": None},
                ) as recover,
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
            self.assertEqual(store.get(task_id).status, "failed")
            recover.assert_called_once()
            self.assertEqual(recover.call_args.kwargs["task"].id, task_id)

    def test_worker_preserves_required_model_failure_progress_without_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            healthy = FakeDevice()
            failure = ModelChannelError(
                "model_channel_circuit_open:permanent_rejection",
                {
                    "status": "failed",
                    "videos_seen": 3,
                    "model_attempts": 3,
                    "model_valid_decisions": 0,
                    "failure_reason": {
                        "code": "model_channel_circuit_open:permanent_rejection"
                    },
                },
            )
            with (
                patch("worker.DeviceLock", return_value=nullcontext()),
                patch("worker.connect_with_retry", return_value=healthy) as connect,
                patch("worker.execute_task", side_effect=failure),
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
            task = store.get(task_id)
            self.assertEqual(task.status, "failed")
            self.assertEqual(task.result["videos_seen"], 3)
            self.assertEqual(connect.call_count, 1)
            self.assertEqual(store.list_incidents(), [])

    def test_task_error_is_recorded_without_reconnect_when_device_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            failed_id = store.submit("healthcheck", "device-1")
            completed_id = store.submit("healthcheck", "device-1")
            healthy = FakeDevice()
            def outcome_for_task(_device, task, *_args, **_kwargs):
                # Millisecond timestamps can tie; this test covers recovery,
                # not ordering of two equally eligible queue entries.
                if task.id == failed_id:
                    raise RuntimeError("page changed")
                return {"status": "passed"}
            with (
                patch("worker.DeviceLock", return_value=nullcontext()),
                patch("worker.connect_with_retry", return_value=healthy) as connect,
                patch(
                    "worker.execute_task",
                    side_effect=outcome_for_task,
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
