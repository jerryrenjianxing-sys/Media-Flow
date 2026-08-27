from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from douyin_fixed_runner import PROFILE  # noqa: E402
from douyin_uia2_runner import (  # noqa: E402
    Uia2DouyinRunner,
    classify_mutation_gate,
    find_control_bounds,
    find_search_result_bounds,
    foreground_package,
)


PACKAGE = "com.ss.android.ugc.aweme"


def node(
    *,
    text: str = "",
    description: str = "",
    bounds: str = "[0,0][1080,2200]",
    clickable: str = "false",
) -> str:
    return (
        f'<node package="{PACKAGE}" visible-to-user="true" '
        f'bounds="{bounds}" clickable="{clickable}" text="{text}" '
        f'content-desc="{description}" />'
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


class MutationGateTest(unittest.TestCase):
    def classify(self, *signals: str, include_controls: bool = True):
        return classify_mutation_gate(
            page_xml(*signals, include_controls=include_controls), PACKAGE
        )

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

    def test_verified_search_video_is_accepted_only_inside_search_session(self) -> None:
        search_page = page_xml(include_controls=False).replace(
            node(text="首页", description="首页", bounds="[0,2100][220,2280]", clickable="true"),
            node(description="暂停视频，按钮", bounds="[0,0][1080,2100]", clickable="true"),
        )
        device = Mock()
        device.dump_hierarchy.return_value = search_page
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, Mock(), PROFILE, max_gate_skips=0)
        image = Image.new("RGB", (1080, 2400), "black")
        self.assertFalse(runner.main_feed_confirmed(image))
        runner.allow_search_feed = True
        self.assertTrue(runner.main_feed_confirmed(image))

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
        )
        device.app_current.return_value = {"package": PACKAGE}
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.allow_search_feed = True
        runner.main_feed_confirmed = Mock(return_value=False)

        _, topic_decision = runner.capture_gate(1, "topic-analysis")
        _, like_decision = runner.capture_gate(1, "like")

        self.assertTrue(topic_decision.allowed)
        self.assertFalse(like_decision.allowed)
        self.assertEqual(("visual_main_feed_check_failed",), like_decision.reasons)


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


class TopicSearchRegressionTest(unittest.TestCase):
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
                        node(
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
        runner.main_feed_confirmed = Mock(side_effect=[True, True])

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
        device.dump_hierarchy.side_effect = [minor_mode_page, comment_panel_page]
        recorder = Mock()
        runner = Uia2DouyinRunner(device, recorder, PROFILE, max_gate_skips=0)
        runner.main_feed_confirmed = Mock(return_value=True)
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
            if call.args and call.args[0] == "startup_overlay_recovery"
        ]
        self.assertEqual(overlay_events[0]["overlay"], "minor_mode")


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


if __name__ == "__main__":
    unittest.main()
