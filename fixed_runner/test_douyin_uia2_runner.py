from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from douyin_fixed_runner import PROFILE  # noqa: E402
from douyin_uia2_runner import (  # noqa: E402
    Uia2DouyinRunner,
    classify_mutation_gate,
    find_control_bounds,
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
    signals = [node(description="视频")]
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
