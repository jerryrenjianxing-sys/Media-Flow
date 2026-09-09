from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from douyin_fixed_runner import PROFILE  # noqa: E402
from douyin_uia2_runner import (  # noqa: E402
    GateDecision,
    UIA2_HTTP_TIMEOUT_SECONDS,
    Uia2DouyinRunner,
    comment_input_activation_source_confirmed,
    classify_douyin_page_source,
    classify_mutation_gate,
    find_comment_input_bounds,
    find_control_bounds,
    find_search_result_bounds,
    foreground_package,
)


PACKAGE = "com.ss.android.ugc.aweme"
REAL_PROFILE_RUN = (
    Path(__file__).resolve().parent
    / "runtime"
    / "artifacts"
    / "runs"
    / "20260902-134353-832445-6HJ4C19917021309"
)
FIXTURES = Path(__file__).resolve().parent / "test_fixtures"


def node(
    *,
    text: str = "",
    description: str = "",
    bounds: str = "[0,0][1080,2200]",
    clickable: str = "false",
    resource_id: str = "",
) -> str:
    return (
        f'<node package="{PACKAGE}" visible-to-user="true" '
        f'bounds="{bounds}" clickable="{clickable}" text="{text}" '
        f'content-desc="{description}" resource-id="{resource_id}" />'
    )


def page_xml(*extra_signals: str, include_controls: bool = True) -> str:
    signals = [
        node(description="视频"),
        node(text="首页", description="首页", bounds="[0,2100][220,2280]", clickable="true"),
    ]
    if include_controls:
        signals.extend(
            [
                node(description="未点赞，喜欢12，按钮"),
                node(
                    description="评论3，按钮",
                    bounds="[900,1300][1080,1500]",
                    clickable="true",
                ),
                node(description="未选中，收藏4，按钮"),
            ]
        )
    signals.extend(node(text=value) for value in extra_signals)
    inner = "".join(signals)
    return (
        '<hierarchy><node package="com.ss.android.ugc.aweme" '
        'visible-to-user="true" bounds="[0,0][1080,2200]">'
        f"{inner}</node></hierarchy>"
    )


def search_shell_nodes() -> str:
    return node(
        description="返回",
        bounds="[0,80][120,220]",
        clickable="true",
        resource_id=f"{PACKAGE}:id/back_btn",
    ) + node(
        text="人工智能",
        bounds="[120,80][850,220]",
        resource_id=f"{PACKAGE}:id/et_search_kw",
    )


class MutationGateTest(unittest.TestCase):
    def classify(self, *signals: str, include_controls: bool = True):
        return classify_mutation_gate(
            page_xml(*signals, include_controls=include_controls), PACKAGE
        )

    def test_captured_home_image_notes_are_browsable_but_not_mutation_targets(self) -> None:
        for fixture, size in (
            ("home_image_note_douyin3.xml", (720, 1600)),
            ("home_image_note_douyin4.xml", (1080, 2340)),
        ):
            with self.subTest(fixture=fixture):
                source = (FIXTURES / fixture).read_text(encoding="utf-8")
                self.assertEqual(
                    classify_douyin_page_source(source, PACKAGE, *size),
                    "home_image_note",
                )
                decision = classify_mutation_gate(source, PACKAGE, *size)
                self.assertFalse(decision.allowed)
                self.assertIn("non_video_feed_item", decision.reasons)

    def test_comment_prompt_child_resolves_to_clickable_parent(self) -> None:
        source = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'bounds="[0,0][1080,2340]">'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'class="android.view.View" clickable="true" focusable="true" '
            'bounds="[36,2076][684,2196]">'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'class="android.widget.TextView" clickable="false" '
            'text="发条评论，说说你的感受" bounds="[66,2110][561,2163]" />'
            '</node></node></hierarchy>'
        )
        self.assertEqual(
            find_comment_input_bounds(source, 1080, 2340),
            (36, 2076, 684, 2196),
        )

    def test_comment_input_activation_requires_editor_focus_or_keyboard(self) -> None:
        plain = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" '
            'visible-to-user="true" bounds="[0,0][1080,2340]" /></hierarchy>'
        )
        focused = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" '
            'visible-to-user="true" focused="true" bounds="[36,2076][684,2196]" '
            'class="android.view.View" /></hierarchy>'
        )
        editor = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" '
            'visible-to-user="true" bounds="[0,0][1080,2340]">'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'class="android.widget.EditText" bounds="[36,2076][684,2196]" />'
            '</node></hierarchy>'
        )
        self.assertFalse(comment_input_activation_source_confirmed(plain, 1080, 2340))
        self.assertTrue(comment_input_activation_source_confirmed(focused, 1080, 2340))
        self.assertTrue(comment_input_activation_source_confirmed(editor, 1080, 2340))

    def test_normal_feed_is_allowed(self) -> None:
        self.assertTrue(self.classify("普通视频文案").allowed)

    def test_top_navigation_labels_do_not_block(self) -> None:
        self.assertTrue(self.classify("直播", "团购", "商城", "拍同款").allowed)

    def test_advertising_signal_blocks_mutation(self) -> None:
        decision = self.classify("广告", "立即咨询", "90+人已咨询")
        self.assertFalse(decision.allowed)
        self.assertIn("advertising", decision.reasons)

    def test_live_signal_blocks_mutation(self) -> None:
        decision = self.classify("正在直播，点击进入直播间")
        self.assertFalse(decision.allowed)
        self.assertIn("live", decision.reasons)

    def test_commerce_signal_blocks_mutation(self) -> None:
        decision = self.classify("商品橱窗", "立即购买")
        self.assertFalse(decision.allowed)
        self.assertIn("commerce", decision.reasons)

    def test_effect_signal_blocks_mutation(self) -> None:
        decision = self.classify("收藏特效")
        self.assertFalse(decision.allowed)
        self.assertIn("effect", decision.reasons)

    def test_factory_product_caption_blocks_mutation(self) -> None:
        decision = self.classify("@000后门窗小刘 #系统门窗 #记录我的工作")
        self.assertFalse(decision.allowed)
        self.assertIn("commercial_content", decision.reasons)
    def test_missing_feed_controls_blocks_mutation(self) -> None:
        decision = self.classify("普通文本", include_controls=False)
        self.assertFalse(decision.allowed)
        self.assertTrue(
            any(reason.startswith("missing_feed_controls") for reason in decision.reasons)
        )

    def test_read_only_topic_gate_does_not_require_reaction_controls(self) -> None:
        source = page_xml("暂停视频，按钮", include_controls=False)

        read_only = classify_mutation_gate(
            source,
            PACKAGE,
            require_feed_controls=False,
        )
        mutation = classify_mutation_gate(source, PACKAGE)

        self.assertTrue(read_only.allowed)
        self.assertFalse(mutation.allowed)
        self.assertTrue(
            any(reason.startswith("missing_feed_controls") for reason in mutation.reasons)
        )

    def test_wrong_foreground_package_blocks_mutation(self) -> None:
        decision = classify_mutation_gate(page_xml(), "com.android.settings")
        self.assertFalse(decision.allowed)
        self.assertIn("unexpected_package:com.android.settings", decision.reasons)

    def test_comment_bounds_follow_current_ui_tree(self) -> None:
        self.assertEqual(
            find_control_bounds(page_xml(), "评论"),
            (900, 1300, 1080, 1500),
        )

    def test_close_bounds_do_not_require_button_suffix(self) -> None:
        source = (
            '<hierarchy>'
            + node(
                description="关闭",
                bounds="[948,801][1068,921]",
                clickable="true",
            )
            + "</hierarchy>"
        )
        self.assertEqual(
            find_control_bounds(source, "关闭", require_button_label=False),
            (948, 801, 1068, 921),
        )


class DeviceLayoutProfileTest(unittest.TestCase):
    def test_transposed_startup_window_uses_verified_portrait_but_rejects_landscape(self):
        from types import SimpleNamespace
        verified = SimpleNamespace(verified=True, width=1080, height=2340)
        device = Mock()
        device.window_size.return_value = (2340, 1080)
        with patch('douyin_uia2_runner.get_device_profile', return_value=verified):
            runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)
        runner.ensure_profile(Image.new('RGB', (1080, 2340)))
        self.assertEqual((runner.profile.width, runner.profile.height), (1080, 2340))
        with self.assertRaises(RuntimeError):
            runner.ensure_profile(Image.new('RGB', (2340, 1080)))

    def test_unverified_or_different_layout_does_not_normalize_window(self):
        from types import SimpleNamespace
        for verified in (None, SimpleNamespace(verified=False, width=1080, height=2340),
                         SimpleNamespace(verified=True, width=720, height=1600)):
            device = Mock()
            device.window_size.return_value = (2340, 1080)
            with patch('douyin_uia2_runner.get_device_profile', return_value=verified):
                runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)
            with self.assertRaises(RuntimeError):
                runner.ensure_profile(Image.new('RGB', (1080, 2340)))

    def test_uiautomator_transport_timeout_is_bounded_for_worker_recovery(self) -> None:
        import uiautomator2 as u2

        self.assertLessEqual(UIA2_HTTP_TIMEOUT_SECONDS, 20.0)
        self.assertEqual(u2.HTTP_TIMEOUT, UIA2_HTTP_TIMEOUT_SECONDS)

    def test_runner_uses_actual_device_window_instead_of_one_global_resolution(self) -> None:
        device = Mock()
        device.window_size.return_value = (720, 1600)
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)
        self.assertEqual((runner.profile.width, runner.profile.height), (720, 1600))

    def test_video_grid_without_home_tab_is_not_mistaken_for_main_feed(self) -> None:
        device = Mock()
        device.dump_hierarchy.return_value = page_xml().replace(
            node(text="首页", description="首页", bounds="[0,2100][220,2280]", clickable="true"),
            node(text="进入兴趣探索模式"),
        )
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)
        self.assertFalse(runner.main_feed_confirmed(Image.new("RGB", (1080, 2400), "black")))

    def test_feed_shell_rejects_exact_recommendation_tab_without_video_controls(self) -> None:
        source = page_xml("推荐", include_controls=False)
        device = Mock()
        device.dump_hierarchy.return_value = source
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)
        image = Image.new("RGB", (1080, 2400), "black")

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertFalse(runner.main_feed_shell_confirmed(image))
            self.assertFalse(runner.main_feed_confirmed(image))

        mutation = classify_mutation_gate(source, PACKAGE)
        self.assertFalse(mutation.allowed)
        self.assertTrue(
            any(reason.startswith("missing_feed_controls") for reason in mutation.reasons)
        )

    def test_personal_profile_is_classified_and_never_accepted_as_feed(self) -> None:
        source = page_xml(
            "编辑主页", "获赞", "互关", "关注", "粉丝", "作品", "日常", "收藏", "喜欢",
            include_controls=False,
        )
        device = Mock()
        device.dump_hierarchy.return_value = source
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)
        image = Image.new("RGB", (1080, 2400), "black")

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertEqual(classify_douyin_page_source(source, PACKAGE), "profile")
            self.assertFalse(runner.main_feed_shell_confirmed(image, source))

    @unittest.skipUnless(
        (REAL_PROFILE_RUN / "topic-session-initial.xml").is_file()
        and (REAL_PROFILE_RUN / "topic-session-initial.png").is_file(),
        "captured personal-profile regression evidence is unavailable",
    )
    def test_captured_personal_profile_regression_is_rejected_before_counting(self) -> None:
        source = (REAL_PROFILE_RUN / "topic-session-initial.xml").read_text(
            encoding="utf-8"
        )
        with Image.open(REAL_PROFILE_RUN / "topic-session-initial.png") as captured:
            image = captured.copy()
        device = Mock()
        device.dump_hierarchy.return_value = source
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertEqual(classify_douyin_page_source(source, PACKAGE, *image.size), "profile")
            self.assertFalse(runner.main_feed_shell_confirmed(image, source))
            self.assertFalse(runner.required_feed_confirmed(image))

    def test_feed_shell_rejects_profile_copy_that_only_contains_recommendation_word(self) -> None:
        source = page_xml(
            "热门抖音视频推荐",
            include_controls=False,
        )
        device = Mock()
        device.dump_hierarchy.return_value = source
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertFalse(
                runner.main_feed_shell_confirmed(
                    Image.new("RGB", (1080, 2400), "black")
                )
            )

    def test_verified_search_video_is_accepted_only_inside_search_session(self) -> None:
        search_page = page_xml(include_controls=False).replace(
            node(text="首页", description="首页", bounds="[0,2100][220,2280]", clickable="true"),
            search_shell_nodes()
            + node(description="暂停视频，按钮", bounds="[0,0][1080,2100]", clickable="true"),
        )
        device = Mock()
        device.dump_hierarchy.return_value = search_page
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)
        image = Image.new("RGB", (1080, 2400), "black")
        self.assertFalse(runner.main_feed_confirmed(image))
        runner.allow_search_feed = True
        runner.feed_phase = "search"
        self.assertTrue(runner.main_feed_confirmed(image))

    def test_verified_search_shell_accepts_complete_controls_without_play_marker(self) -> None:
        search_video = page_xml().replace(
            node(text="首页", description="首页", bounds="[0,2100][220,2280]", clickable="true"),
            search_shell_nodes() + node(text="人工智能科普"),
        )
        device = Mock()
        device.dump_hierarchy.return_value = search_video
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)
        runner.allow_search_feed = True

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertTrue(
                runner.main_feed_shell_confirmed(
                    Image.new("RGB", (1080, 2400), "black"), search_video
                )
            )

    def test_search_topic_capture_can_bypass_visual_template_but_mutation_cannot(self) -> None:
        image = Image.new("RGB", (1080, 2400), "black")
        recorder = Mock()
        recorder.screenshot.return_value = image
        recorder.emit.return_value = None
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.return_value = page_xml(
            "AI职业发展",
            include_controls=False,
        ).replace(
            node(text="首页", description="首页", bounds="[0,2100][220,2280]", clickable="true"),
            search_shell_nodes() + node(description="暂停视频，按钮"),
        )
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.allow_search_feed = True
        runner.search_query = "人工智能"
        runner.main_feed_confirmed = Mock(return_value=False)

        _, topic_decision = runner.capture_gate(1, "topic-analysis")
        _, like_decision = runner.capture_gate(1, "like")

        self.assertTrue(topic_decision.allowed)
        self.assertFalse(like_decision.allowed)
        self.assertTrue(
            any(reason.startswith("missing_feed_controls") for reason in like_decision.reasons)
        )

    def test_topic_analysis_classifies_live_preview_inside_browsable_feed(self) -> None:
        image = Image.new("RGB", (1080, 2400), "black")
        recorder = Mock()
        recorder.screenshot.return_value = image
        source = page_xml("推荐", include_controls=False).replace(
            "</hierarchy>", node(text="直播中") + node(text="点击进入直播间") + "</hierarchy>"
        )
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.return_value = source
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            _, decision = runner.capture_gate(1, "topic-analysis")

        self.assertFalse(decision.allowed)
        self.assertIn("live", decision.reasons)
        self.assertNotIn("visual_main_feed_check_failed", decision.reasons)

    def test_topic_analysis_recovers_known_share_sheet_before_model_gate(self) -> None:
        image = Image.new("RGB", (1080, 2400), "black")
        recorder = Mock()
        recorder.screenshot.side_effect = [image, image]
        recorder.emit.return_value = None
        share_sheet = page_xml("推荐", include_controls=False).replace(
            "</hierarchy>",
            node(text="转发到日常")
            + node(text="不感兴趣")
            + node(text="倍速")
            + "</hierarchy>",
        )
        recovered_feed = page_xml("AI职业发展", include_controls=False)
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.side_effect = [share_sheet, recovered_feed]
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)
        runner.main_feed_confirmed = Mock(return_value=False)
        runner.main_feed_shell_confirmed = Mock(side_effect=[False, True])
        runner.recover_main_feed = Mock(return_value=True)

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            _, decision = runner.capture_gate(3, "topic-analysis")

        self.assertTrue(decision.allowed)
        runner.recover_main_feed.assert_called_once_with("share-sheet-before-topic-3")


class ExistingReactionRegressionTest(unittest.TestCase):
    class Recorder:
        def emit(self, *args, **kwargs) -> None:
            pass

        def screenshot(self, device, name: str) -> Image.Image:
            return Image.new("RGB", (1080, 2400), "red")

    class Device:
        def __init__(self) -> None:
            self.clicks: list[tuple[int, int]] = []

        def click(self, x: int, y: int) -> None:
            self.clicks.append((x, y))

    def test_unliked_semantic_state_overrides_red_video_background(self) -> None:
        device = self.Device()
        runner = Uia2DouyinRunner(device, self.Recorder(), PROFILE, max_gate_skips=0)
        runner.control_bounds["like"] = (900, 900, 1040, 1100)
        runner.control_states = {"like": False}
        red_background = Image.new("RGB", (1080, 2400), "red")

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            changed = runner.like_verified(10, red_background)

        self.assertTrue(changed)
        self.assertEqual(len(device.clicks), 1)

    def test_confirmed_inactive_like_retries_once_and_verifies(self) -> None:
        inactive = Image.new("RGB", (1080, 2400), "black")
        active = inactive.copy()
        active.paste((255, 0, 0), (900, 900, 1040, 1100))
        recorder = Mock()
        recorder.screenshot.side_effect = [inactive, active]
        device = self.Device()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.control_bounds["like"] = (900, 900, 1040, 1100)
        runner.control_states = {"like": False}

        def reacquire(video, action, **kwargs):
            runner.control_states[action] = False
            return inactive, GateDecision(True, (), ())

        runner.capture_gate = Mock(side_effect=reacquire)
        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            changed = runner.like_verified(8, inactive)

        self.assertTrue(changed)
        self.assertEqual(len(device.clicks), 2)
        runner.capture_gate.assert_called_once_with(
            8, "like", evidence_name="like-retry"
        )

    def test_unknown_reaction_state_is_never_replayed(self) -> None:
        inactive = Image.new("RGB", (1080, 2400), "black")
        recorder = Mock()
        recorder.screenshot.return_value = inactive
        device = self.Device()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.control_bounds["like"] = (900, 900, 1040, 1100)
        runner.control_states = {"like": False}

        def reacquire(video, action, **kwargs):
            runner.control_states[action] = None
            return inactive, GateDecision(True, (), ())

        runner.capture_gate = Mock(side_effect=reacquire)
        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Like verification failed"):
                runner.like_verified(8, inactive)

        self.assertEqual(len(device.clicks), 1)

    def test_long_batch_delayed_reaction_uses_observation_not_second_tap(self):
        inactive = Image.new('RGB', (1080,2400), 'black')
        active = inactive.copy()
        active.paste((255,0,0), (900,900,1040,1100))
        recorder = Mock()
        recorder.screenshot.side_effect = [inactive, inactive, active]
        device = self.Device()
        device.screenshot = Mock(side_effect=[inactive, inactive, active])
        device.dump_hierarchy = Mock(return_value='<hierarchy>' + node(
            description='喜欢3，按钮', bounds='[900,900][1040,1100]', clickable='true') + '</hierarchy>')
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.observe_reactions_only = True
        runner.control_bounds['like'] = (900,900,1040,1100)
        runner.control_states['like'] = False
        with patch('douyin_uia2_runner.time.sleep'), patch('douyin_uia2_runner.foreground_package', return_value=PACKAGE), patch.object(runner, 'main_feed_confirmed', return_value=True):
            self.assertTrue(runner.like_verified(1, inactive))
        self.assertEqual(len(device.clicks), 1)

    def test_long_batch_red_on_wrong_page_cannot_confirm_a_like(self):
        inactive = Image.new('RGB', (1080,2400), 'black')
        red = inactive.copy()
        red.paste((255,0,0), (900,900,1040,1100))
        recorder = Mock()
        recorder.screenshot.return_value = red
        device = self.Device()
        device.screenshot = Mock(return_value=red)
        device.dump_hierarchy = Mock(return_value='<hierarchy/>')
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.observe_reactions_only = True
        runner.control_bounds['like'] = (900,900,1040,1100)
        runner.control_states['like'] = False
        with patch('douyin_uia2_runner.time.sleep'), patch.object(runner, 'main_feed_confirmed', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Like verification'):
                runner.like_verified(1, inactive)
        self.assertEqual(len(device.clicks), 1)


class ForegroundPackageRegressionTest(unittest.TestCase):
    class Device:
        def app_current(self):
            return {"package": PACKAGE, "activity": ".splash.SplashActivity"}

        def shell(self, command: str):
            class Response:
                output = (
                    "topResumedActivity=ActivityRecord{123 u0 "
                    "com.hihonor.android.launcher/.drawer.DrawerLauncher t1}"
                )

            return Response()

    def test_system_resumed_activity_overrides_stale_uiautomator_value(self) -> None:
        self.assertEqual(
            foreground_package(self.Device()),
            "com.hihonor.android.launcher",
        )


class MainFeedRegressionTest(unittest.TestCase):
    class Recorder:
        def emit(self, *args, **kwargs) -> None:
            pass

    class Device:
        def shell(self, command: str):
            class Response:
                output = (
                    "topResumedActivity=ActivityRecord{123 u0 "
                    f"{PACKAGE}/.main.MainActivity t1}}"
                )

            return Response()

        def dump_hierarchy(self, **kwargs) -> str:
            return page_xml()

        def app_current(self):
            return {"package": PACKAGE}

    def test_feed_controls_override_bright_bottom_navigation_crop(self) -> None:
        runner = Uia2DouyinRunner(
            self.Device(), self.Recorder(), PROFILE, max_gate_skips=0
        )
        bright_feed = Image.new("RGB", (1264, 2800), "white")
        runner.ensure_profile(bright_feed)

        runner.require_main_feed(bright_feed, "topic session")


class HomeTabRecoveryRegressionTest(unittest.TestCase):
    class Recorder:
        def __init__(self) -> None:
            self.events = []
            self._temporary_directory = tempfile.TemporaryDirectory()
            self.run_dir = Path(self._temporary_directory.name)

        def emit(self, *args, **kwargs) -> None:
            self.events.append((args, kwargs))

        def screenshot(self, device, name: str) -> Image.Image:
            return Image.new("RGB", (1080, 2400), "white")

    class Device:
        def __init__(self) -> None:
            self.on_feed = False
            self.clicks: list[tuple[int, int]] = []

        def click(self, x: int, y: int) -> None:
            self.clicks.append((x, y))
            self.on_feed = True

        def press(self, key: str) -> None:
            pass

        def app_start(self, *args, **kwargs) -> None:
            pass

        def shell(self, command: str):
            class Response:
                output = (
                    "topResumedActivity=ActivityRecord{123 u0 "
                    f"{PACKAGE}/.main.MainActivity t1}}"
                )

            return Response()

        def app_current(self):
            return {"package": PACKAGE}

        def dump_hierarchy(self, **kwargs) -> str:
            if self.on_feed:
                return page_xml()
            return (
                "<hierarchy>"
                + node(text="我", bounds="[850,2100][1080,2280]")
                + node(
                    text="首页",
                    description="首页",
                    bounds="[0,2100][220,2280]",
                    clickable="true",
                )
                + "</hierarchy>"
            )

    def test_profile_page_taps_semantic_home_before_back_or_relaunch(self) -> None:
        device = self.Device()
        runner = Uia2DouyinRunner(
            device, self.Recorder(), PROFILE, max_gate_skips=3
        )

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            recovered = runner.recover_main_feed("profile-page")

        self.assertTrue(recovered)
        self.assertEqual(device.clicks, [(110, 2190)])

    def test_search_stack_unwinds_all_known_layers_before_relaunch(self) -> None:
        class SearchStackDevice:
            def __init__(self) -> None:
                self.layer = 0
                self.back_presses = 0
                self.app_restarts = 0

            def dump_hierarchy(self, **kwargs) -> str:
                return "<hierarchy />"

            def press(self, key: str) -> None:
                if key == "back":
                    self.back_presses += 1
                    self.layer = min(self.layer + 1, 5)

            def app_stop(self, *args, **kwargs) -> None:
                pass

            def app_start(self, *args, **kwargs) -> None:
                self.app_restarts += 1

        device = SearchStackDevice()
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)
        runner._home_feed_shell_confirmed = Mock(
            side_effect=lambda *_args, **_kwargs: device.layer == 5
        )

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            recovered = runner.recover_main_feed("search-stack")

        self.assertTrue(recovered)
        self.assertEqual(device.back_presses, 5)
        self.assertEqual(device.app_restarts, 0)

    def test_recovery_tolerates_one_transient_ui_tree_failure(self) -> None:
        class TransientTreeDevice:
            def __init__(self) -> None:
                self.back_presses = 0
                self.dump_calls = 0

            def dump_hierarchy(self, **kwargs) -> str:
                self.dump_calls += 1
                if self.dump_calls == 2:
                    raise RuntimeError("temporary empty hierarchy")
                return "<hierarchy />"

            def press(self, key: str) -> None:
                if key == "back":
                    self.back_presses += 1

            def app_stop(self, *args, **kwargs) -> None:
                pass

            def app_start(self, *args, **kwargs) -> None:
                pass

        device = TransientTreeDevice()
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)
        runner._home_feed_shell_confirmed = Mock(
            side_effect=lambda *_args, **_kwargs: device.back_presses >= 2
        )

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            recovered = runner.recover_main_feed("transient-tree")

        self.assertTrue(recovered)
        self.assertEqual(device.back_presses, 2)

    def test_required_feed_recovery_rebuilds_search_video_feed(self) -> None:
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.return_value = "<hierarchy />"
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)
        runner.allow_search_feed = True
        runner.search_query = "人工智能"
        runner.feed_phase = "search"
        runner.recover_main_feed = Mock(return_value=True)
        runner.enter_topic_search = Mock()

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            recovered = runner.recover_required_feed("external-app")

        self.assertTrue(recovered)
        runner.enter_topic_search.assert_called_once_with("人工智能")
        self.assertEqual(runner.recovery_events[-1]["action"], "search_reentry")

    def test_required_feed_recovery_retries_one_failed_search_reentry(self) -> None:
        device = Mock()
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)
        runner.allow_search_feed = True
        runner.search_query = "塑料包装"
        runner.feed_phase = "search"
        runner.recover_main_feed = Mock(return_value=True)
        runner.enter_topic_search = Mock(
            side_effect=[RuntimeError("result card still loading"), None]
        )

        recovered = runner.recover_required_feed("search-drift")

        self.assertTrue(recovered)
        self.assertEqual(runner.recover_main_feed.call_count, 2)
        self.assertEqual(runner.enter_topic_search.call_count, 2)
        self.assertEqual(runner.recovery_events[-1]["action"], "search_reentry")


class TopicSearchRegressionTest(unittest.TestCase):
    def test_partial_nodes_allow_authorized_read_only_visual_classification(self):
        image = Image.new('RGB', (1080, 2400), 'black')
        source = '<hierarchy>' + node(text='加载中') + '</hierarchy>'
        device, recorder = Mock(), Mock()
        device.screenshot.return_value = image
        device.dump_hierarchy.return_value = source
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)
        runner.visual_navigation_enabled = True
        runner._visual_feed_kind = Mock(return_value='search_video')
        with patch('douyin_uia2_runner.foreground_package', return_value=PACKAGE):
            self.assertTrue(runner.search_feed_confirmed(source, image, allow_visual_fallback=True))
            self.assertFalse(runner.search_feed_confirmed(source, image, allow_visual_fallback=False))
        runner._visual_feed_kind.assert_called_once()
        saved = recorder.save_observation.call_args.args
        self.assertEqual(saved[0].tobytes(), image.tobytes())
        self.assertEqual(saved[1], source)
        device.click.assert_not_called()

    def test_partial_nodes_do_not_accept_visual_home_as_search(self):
        image = Image.new('RGB', (1080, 2400), 'black')
        device, recorder = Mock(), Mock()
        device.screenshot.return_value = image
        device.dump_hierarchy.return_value = page_xml('普通首页')
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)
        runner.visual_navigation_enabled = True
        runner._visual_feed_kind = Mock(return_value='home')
        with patch('douyin_uia2_runner.foreground_package', return_value=PACKAGE):
            self.assertFalse(runner.search_feed_confirmed(page_xml('普通首页'), image, allow_visual_fallback=True))
        device.click.assert_not_called()

    def test_search_continuation_keeps_context_when_top_search_bar_collapses(self) -> None:
        source = page_xml("塑料袋").replace(
            node(
                text="首页",
                description="首页",
                bounds="[0,2100][220,2280]",
                clickable="true",
            ),
            node(
                text="相关搜索",
                bounds="[108,2035][264,2088]",
                resource_id=f"{PACKAGE}:id/title",
            ),
        )
        device = Mock()
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertTrue(
                runner.search_feed_confirmed(
                    source, Image.new("RGB", (1080, 2400), "black")
                )
            )
            self.assertEqual(
                classify_douyin_page_source(source, PACKAGE),
                "search_feed",
            )

    def test_ordinary_immersive_feed_is_not_a_verified_search_feed(self) -> None:
        source = page_xml("普通主页视频")
        device = Mock()
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertFalse(
                runner.search_feed_confirmed(
                    source, Image.new("RGB", (1080, 2400), "black")
                )
            )

    def test_search_session_accepts_visual_shell_when_app_nodes_are_missing(self) -> None:
        source = (
            '<hierarchy><node package="com.android.systemui" '
            'resource-id="com.android.systemui:id/status_bar" '
            'bounds="[0,0][1080,120]" /></hierarchy>'
        )
        device = Mock()
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)
        image = Image.new("RGB", (1080, 2400), "black")

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertFalse(runner.search_feed_confirmed(source, image))
            self.assertTrue(
                runner.search_feed_confirmed(
                    source, image, allow_visual_fallback=True
                )
            )

    def test_search_visual_shell_does_not_replace_semantic_mutation_gate(self) -> None:
        source = (
            '<hierarchy><node package="com.android.systemui" '
            'resource-id="com.android.systemui:id/status_bar" '
            'bounds="[0,0][1080,120]" /></hierarchy>'
        )
        device = Mock()
        device.dump_hierarchy.return_value = source
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)
        runner.allow_search_feed = True
        runner._search_visual_fallback_active = True
        image = Image.new("RGB", (1080, 2400), "black")

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertTrue(runner.main_feed_shell_confirmed(image, source))
            self.assertFalse(runner.main_feed_confirmed(image))

    def test_search_inline_comment_prompt_is_not_mistaken_for_open_panel(self) -> None:
        source = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" '
            'visible-to-user="true" bounds="[0,0][720,1600]">'
            + node(
                description="返回",
                resource_id=f"{PACKAGE}:id/back_btn",
                bounds="[0,40][90,160]",
            )
            + node(
                text="人工智能",
                resource_id=f"{PACKAGE}:id/et_search_kw",
                bounds="[90,40][600,160]",
            )
            + node(description="视频", bounds="[0,0][720,1502]")
            + node(description="未点赞，喜欢26.7万，按钮")
            + node(description="评论3402，按钮")
            + node(description="已选中，收藏18.1万，按钮")
            + node(text="期待你的评论", bounds="[24,1502][520,1584]")
            + "</node></hierarchy>"
        )
        device = Mock()
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)
        runner.allow_search_feed = True

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertTrue(
                runner.main_feed_shell_confirmed(
                    Image.new("RGB", (720, 1600), "black"), source
                )
            )

    def test_search_visual_fallback_rejects_ambiguous_douyin_tree(self) -> None:
        source = page_xml("个人主页", include_controls=False)
        device = Mock()
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)
        image = Image.new("RGB", (1080, 2400), "black")

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertFalse(runner.search_feed_confirmed(source, image))

    def test_search_results_grid_is_not_an_immersive_search_feed(self) -> None:
        source = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" visible-to-user="true">'
            + node(text="综合")
            + node(text="视频")
            + node(text="用户")
            + node(text="商品")
            + node(description="播放视频，按钮")
            + "</node></hierarchy>"
        )
        device = Mock()
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=3)

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            self.assertFalse(
                runner.search_feed_confirmed(
                    source, Image.new("RGB", (1080, 2400), "white")
                )
            )

    def test_search_drift_recovery_reenters_the_original_query(self) -> None:
        image = Image.new("RGB", (1080, 2400), "black")
        recorder = Mock()
        recorder.screenshot.return_value = image
        recorder.emit.return_value = None
        device = Mock()
        device.window_size.return_value = (1080, 2400)
        device.dump_hierarchy.return_value = page_xml("推荐")
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)
        runner.allow_search_feed = True
        runner.search_query = "人工智能"
        runner.feed_phase = "search"
        runner.recover_main_feed = Mock(return_value=True)
        runner.enter_topic_search = Mock()

        with patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE):
            recovered = runner.recover_required_feed("profile-drift")

        self.assertTrue(recovered)
        runner.enter_topic_search.assert_called_once_with("人工智能")
        self.assertEqual(
            runner.recovery_events[-1]["rule_id"],
            "douyin-search-context-drift",
        )

    def test_search_result_can_be_identified_by_semantic_caption_container(self) -> None:
        source = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'class="android.widget.LinearLayout" bounds="[546,545][1068,1515]" clickable="true">'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/desc" text="人工智能专业介绍" '
            'bounds="[570,1265][1044,1386]" clickable="false" />'
            '</node></hierarchy>'
        )
        self.assertEqual(find_search_result_bounds(source), (546, 545, 1068, 1515))

    def test_captioned_search_card_can_start_below_compact_navigation(self) -> None:
        source = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" visible-to-user="true">'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/ctw" '
            'bounds="[12,380][534,1340]" clickable="true">'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/desc" '
            'text="一期视频带你打通 AI 底层逻辑！" '
            'bounds="[36,1100][510,1215]" clickable="false" />'
            '</node></node></hierarchy>'
        )

        self.assertEqual(
            find_search_result_bounds(source, width=1080, height=2340),
            (12, 380, 534, 1340),
        )

    def test_two_column_result_chooses_clickable_cover_not_row_gap(self) -> None:
        source = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'bounds="[60,759][1019,1418]" clickable="true">'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'class="android.view.View" bounds="[60,759][523,1210]" clickable="true" />'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/desc" text="人工智能工厂应用" '
            'bounds="[60,1210][523,1360]" clickable="false" />'
            '</node></hierarchy>'
        )
        self.assertEqual(find_search_result_bounds(source), (60, 759, 523, 1210))

    def test_search_result_accepts_cover_and_duration_when_caption_id_changes(self) -> None:
        source = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'class="android.widget.LinearLayout" bounds="[8,235][356,883]" clickable="true">'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/cover" class="android.widget.ImageView" '
            'bounds="[8,235][356,699]" clickable="false" />'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'class="android.view.View" bounds="[8,235][356,699]" clickable="true" />'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/5bf" text="32:31" '
            'bounds="[277,653][340,685]" clickable="false" />'
            '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'text="一期视频带你打通 AI 底层逻辑" bounds="[8,699][356,780]" '
            'clickable="false" /></node></hierarchy>'
        )

        self.assertEqual(
            find_search_result_bounds(source, width=720, height=1600),
            (8, 235, 356, 699),
        )

    class Recorder:
        def __init__(self) -> None:
            self.events = []
            self._temporary_directory = tempfile.TemporaryDirectory()
            self.run_dir = Path(self._temporary_directory.name)

        def emit(self, *args, **kwargs) -> None:
            self.events.append((args, kwargs))

        def screenshot(self, device, name: str) -> Image.Image:
            return Image.new("RGB", (1080, 2400), "black")

    class Device:
        def __init__(self) -> None:
            self.clicks = []
            self.typed = []
            self.pressed = []
            self.pages = iter(
                [
                    "<hierarchy>"
                    + node(
                        description="搜索，按钮",
                        bounds="[900,100][1080,260]",
                        clickable="true",
                    )
                    + "</hierarchy>",
                    (
                        '<hierarchy><node package="com.ss.android.ugc.aweme" '
                        'visible-to-user="true" class="android.widget.EditText" '
                        'bounds="[120,100][850,240]" clickable="true" /></hierarchy>'
                    ),
                    (
                        "<hierarchy>"
                        + '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
                        'bounds="[80,500][1000,1300]" clickable="true">'
                        + node(text="视频：人工智能入门")
                        + "</node></hierarchy>"
                    ),
                    (
                        "<hierarchy>"
                        + '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
                        'bounds="[80,500][1000,1300]" clickable="true">'
                        + node(text="视频：人工智能入门")
                        + "</node></hierarchy>"
                    ),
                    page_xml().replace(
                        node(
                            text="首页",
                            description="首页",
                            bounds="[0,2100][220,2280]",
                            clickable="true",
                        ),
                        search_shell_nodes()
                        + node(
                            description="暂停视频，按钮",
                            bounds="[0,0][1080,2100]",
                            clickable="true",
                        ),
                    ),
                ]
            )

        def dump_hierarchy(self, **kwargs) -> str:
            return next(self.pages)

        def click(self, x: int, y: int) -> None:
            self.clicks.append((x, y))

        def send_keys(self, value: str, clear: bool = False) -> None:
            self.typed.append((value, clear))

        def press(self, key: str) -> None:
            self.pressed.append(key)

        def app_current(self) -> dict[str, str]:
            return {"package": PACKAGE}

    def test_search_mode_enters_verified_video_result(self) -> None:
        device = self.Device()
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            runner.enter_topic_search("人工智能")

        self.assertEqual(device.typed, [("人工智能", True)])
        self.assertEqual(device.pressed, ["enter"])
        self.assertEqual(device.clicks[-1], (540, 900))

    def test_search_mode_retries_verified_card_when_grid_does_not_transition(self) -> None:
        device = self.Device()
        result_page = (
            "<hierarchy>"
            + '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
            'bounds="[80,500][1000,1300]" clickable="true">'
            + node(text="视频：人工智能入门")
            + "</node></hierarchy>"
        )
        grid_page = (
            '<hierarchy><node package="com.ss.android.ugc.aweme" visible-to-user="true">'
            + node(text="综合")
            + node(text="视频")
            + node(text="用户")
            + node(text="商品")
            + "</node></hierarchy>"
        )
        video_page = (
            "<hierarchy>"
            + search_shell_nodes()
            + node(description="暂停视频，按钮")
            + "</hierarchy>"
        )
        device.pages = iter(
            [
                "<hierarchy>"
                + node(
                    description="搜索，按钮",
                    bounds="[900,100][1080,260]",
                    clickable="true",
                )
                + "</hierarchy>",
                '<hierarchy><node package="com.ss.android.ugc.aweme" visible-to-user="true" '
                'class="android.widget.EditText" bounds="[120,100][850,240]" '
                'clickable="true" /></hierarchy>',
                result_page,
                result_page,
                grid_page,
                video_page,
            ]
        )
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=3)

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            runner.enter_topic_search("人工智能")

        self.assertEqual(device.clicks[-2:], [(540, 900), (540, 900)])
        retry_events = [
            kwargs
            for args, kwargs in recorder.events
            if args == ("topic_search",) and kwargs.get("action") == "open_video_result_retry"
        ]
        self.assertEqual(len(retry_events), 1)


class AppReadyRegressionTest(unittest.TestCase):
    def test_waits_through_splash_and_closes_login_modal(self) -> None:
        device = Mock()
        device.screenshot.return_value = Image.new("RGB", (900, 1600), "black")
        login_page = (
            "<hierarchy>"
            + node(text="登录后，体验完整功能")
            + node(
                description="关闭",
                bounds="[800,700][880,780]",
                clickable="true",
            )
            + "</hierarchy>"
        )
        device.dump_hierarchy.side_effect = [login_page, page_xml(), page_xml()]
        recorder = Mock()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.main_feed_shell_confirmed = Mock(side_effect=[True, True])

        with (
            patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE),
            patch("douyin_uia2_runner.time.sleep", return_value=None),
        ):
            runner.ensure_app_ready()

        device.click.assert_called_once_with(840, 740)
        recorder.emit.assert_called_once()

    def test_closes_minor_mode_overlay_then_recovers_comment_panel(self) -> None:
        device = Mock()
        device.screenshot.return_value = Image.new("RGB", (900, 1600), "black")
        minor_mode_page = (
            "<hierarchy>"
            + node(text="未成年人模式")
            + node(text="开启未成年人模式")
            + node(
                description="关闭",
                bounds="[800,120][880,200]",
                clickable="true",
            )
            + "</hierarchy>"
        )
        comment_panel_page = (
            "<hierarchy>"
            + node(text="分享你此刻的想法")
            + node(
                description="关闭",
                bounds="[800,700][880,780]",
                clickable="true",
            )
            + "</hierarchy>"
        )
        device.dump_hierarchy.side_effect = [
            minor_mode_page,
            comment_panel_page,
            comment_panel_page,
        ]
        recorder = Mock()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.main_feed_shell_confirmed = Mock(return_value=False)
        runner.recover_main_feed = Mock(return_value=True)

        with (
            patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE),
            patch("douyin_uia2_runner.time.sleep", return_value=None),
        ):
            runner.ensure_app_ready()

        device.click.assert_called_once_with(840, 160)
        runner.recover_main_feed.assert_called_once_with("app-ready-comment-panel")
        overlay_events = [
            call.kwargs
            for call in recorder.emit.call_args_list
            if call.args and call.args[0] == "verified_recovery"
        ]
        self.assertEqual(overlay_events[0]["rule_id"], "douyin-minor-mode-overlay")
        self.assertEqual(overlay_events[0]["action"], "close_button")

    def test_minor_mode_uses_do_not_remind_when_close_is_missing(self) -> None:
        device = Mock()
        device.screenshot.return_value = Image.new("RGB", (1080, 2400), "black")
        minor_mode_page = (
            "<hierarchy>"
            + node(text="未成年人模式")
            + node(text="开启未成年人模式")
            + node(
                text="不再提醒",
                bounds="[48,2016][984,2148]",
                clickable="true",
            )
            + "</hierarchy>"
        )
        device.dump_hierarchy.side_effect = [minor_mode_page, page_xml()]
        recorder = Mock()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.main_feed_shell_confirmed = Mock(return_value=True)

        with (
            patch("douyin_uia2_runner.foreground_package", return_value=PACKAGE),
            patch("douyin_uia2_runner.time.sleep", return_value=None),
        ):
            runner.ensure_app_ready()

        device.click.assert_called_once_with(516, 2082)
        self.assertEqual(runner.recovery_events[0]["action"], "do_not_remind")

    def test_overlay_action_without_feed_verification_is_not_recovered(self) -> None:
        device = Mock()
        device.screenshot.return_value = Image.new("RGB", (1080, 2340), "black")
        minor_mode_page = (
            "<hierarchy>"
            + node(text="未成年人模式")
            + node(text="开启未成年人模式")
            + node(
                description="关闭",
                bounds="[960,1427][984,1511]",
                clickable="true",
            )
            + "</hierarchy>"
        )
        device.dump_hierarchy.return_value = minor_mode_page
        recorder = Mock()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.main_feed_shell_confirmed = Mock(return_value=False)

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            recovered = runner.try_verified_overlay_recovery(
                minor_mode_page, "test-overlay"
            )

        self.assertFalse(recovered)
        self.assertEqual(runner.recovery_events, [])

    def test_restart_waits_through_splash_until_feed_shell_is_ready(self) -> None:
        device = Mock()
        image = Image.new("RGB", (720, 1600), "black")
        device.screenshot.return_value = image
        device.dump_hierarchy.side_effect = ["<hierarchy />", "<hierarchy />", page_xml("推荐", include_controls=False)]
        recorder = Mock()
        recorder.screenshot.return_value = image
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner._home_feed_shell_confirmed = Mock(side_effect=[False, False, True])

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            ready = runner.wait_for_main_feed_shell(
                "slow-start", attempts=3, delay_s=0.01
            )

        self.assertTrue(ready)
        self.assertEqual(runner._home_feed_shell_confirmed.call_count, 3)
        recorder.screenshot.assert_called_once_with(
            device, "feed-recovery-slow-start-app-start-ready"
        )


class CommentPanelRecoveryRegressionTest(unittest.TestCase):
    class Recorder:
        def __init__(self) -> None:
            self.events: list[tuple[tuple, dict]] = []

        def emit(self, *args, **kwargs) -> None:
            self.events.append((args, kwargs))

        def screenshot(self, device, name: str) -> Image.Image:
            return Image.new("RGB", (1080, 2400), "black")

    class Device:
        def __init__(self) -> None:
            self.panel_open = True
            self.back_presses = 0

        def click(self, x: int, y: int) -> None:
            pass

        def press(self, key: str) -> None:
            if key == "back":
                self.back_presses += 1
                self.panel_open = False

        def shell(self, command: str):
            class Response:
                output = (
                    "topResumedActivity=ActivityRecord{123 u0 "
                    f"{PACKAGE}/.main.MainActivity t1}}"
                )

            return Response()

        def app_current(self):
            return {"package": PACKAGE}

        def dump_hierarchy(self, **kwargs) -> str:
            if not self.panel_open:
                return page_xml()
            return (
                "<hierarchy>"
                + node(
                    description="关闭",
                    bounds="[948,801][1068,921]",
                    clickable="true",
                )
                + node(text="分享你此刻的想法")
                + "</hierarchy>"
            )

    def test_close_comment_panel_falls_back_to_back_and_verifies_feed(self) -> None:
        device = self.Device()
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            runner.close_comment_panel(1, "closed")

        self.assertEqual(device.back_presses, 1)
        self.assertTrue(
            any(args and args[0] == "comment_close_recovery" for args, _ in recorder.events)
        )

    def test_close_comment_panel_accepts_verified_search_shell_without_back(self) -> None:
        device = self.Device()
        search_video = page_xml().replace(
            node(text="首页", description="首页", bounds="[0,2100][220,2280]", clickable="true"),
            search_shell_nodes() + node(text="人工智能科普"),
        )

        def close_panel(_x: int, _y: int) -> None:
            device.panel_open = False

        device.click = close_panel
        original_dump = device.dump_hierarchy
        device.dump_hierarchy = lambda **kwargs: (
            search_video if not device.panel_open else original_dump(**kwargs)
        )
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.allow_search_feed = True

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            runner.close_comment_panel(1, "closed")

        self.assertEqual(device.back_presses, 0)

    def test_closed_panel_on_search_page_rebuilds_required_feed(self) -> None:
        device = self.Device()
        search_landing = (
            "<hierarchy>"
            + search_shell_nodes()
            + node(text="历史记录")
            + node(text="猜你想搜")
            + "</hierarchy>"
        )

        def close_panel(_x: int, _y: int) -> None:
            device.panel_open = False

        device.click = close_panel
        original_dump = device.dump_hierarchy
        device.dump_hierarchy = lambda **kwargs: (
            search_landing if not device.panel_open else original_dump(**kwargs)
        )
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.allow_search_feed = True
        runner.search_query = "人工智能"
        runner.feed_phase = "search"
        runner.recover_required_feed = Mock(return_value=True)

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            runner.close_comment_panel(1, "closed")

        runner.recover_required_feed.assert_called_once_with(
            "comment-panel-close-drift-1"
        )
        self.assertEqual(device.back_presses, 0)

    def test_close_comment_panel_ignores_unrelated_close_control_in_search_feed(self) -> None:
        device = self.Device()
        search_video = page_xml().replace(
            node(text="首页", description="首页", bounds="[0,2100][220,2280]", clickable="true"),
            search_shell_nodes()
            + node(description="关闭", bounds="[900,80][1060,240]", clickable="true"),
        )

        def close_panel(_x: int, _y: int) -> None:
            device.panel_open = False

        device.click = close_panel
        original_dump = device.dump_hierarchy
        device.dump_hierarchy = lambda **kwargs: (
            search_video if not device.panel_open else original_dump(**kwargs)
        )
        recorder = self.Recorder()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.allow_search_feed = True

        with patch("douyin_uia2_runner.time.sleep", return_value=None):
            runner.close_comment_panel(1, "closed")

        self.assertEqual(device.back_presses, 0)


if __name__ == "__main__":
    unittest.main()
