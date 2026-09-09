"""dev.40 regression: active observation followed by false timeout failure.

Timing regression is derived from the audited phone 2/3/4 first-round reaction
traces. Synthetic pixels exercise control logic, not real-device accuracy.
"""
import unittest
from unittest.mock import Mock, patch
from PIL import Image
from douyin_fixed_runner import PROFILE
from douyin_uia2_runner import Uia2DouyinRunner


PACKAGE = 'com.ss.android.ugc.aweme'


def source(active=True):
    state = '已点赞' if active else '未点赞'
    return ('<hierarchy><node package="' + PACKAGE + '" text="首页" />'
            f'<node package="{PACKAGE}" content-desc="{state}，喜欢3，按钮" '
            'bounds="[900,900][1040,1100]" visible-to-user="true" clickable="true" />'
            '</hierarchy>')


class ActionConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.image = Image.new('RGB', (1080, 2400), 'black')
        self.device = Mock()
        self.device.screenshot.return_value = self.image
        self.device.dump_hierarchy.return_value = source()
        self.recorder = Mock()
        self.recorder.screenshot.return_value = self.image
        self.runner = Uia2DouyinRunner(self.device, self.recorder, PROFILE, max_gate_skips=0)
        self.runner.observe_reactions_only = True
        self.runner.control_bounds['like'] = (900, 900, 1040, 1100)
        self.runner.control_states['like'] = False
        self.runner.main_feed_confirmed = Mock(return_value=True)
        self.addCleanup(patch.stopall)
        patch('douyin_uia2_runner.time.monotonic', side_effect=lambda: self.now).start()
        patch('douyin_uia2_runner.time.sleep', side_effect=self.advance).start()
        patch('douyin_uia2_runner.foreground_package', return_value=PACKAGE).start()

    def advance(self, seconds):
        self.now += seconds

    def test_slow_evidence_write_does_not_reverse_observed_success(self):
        def emit(event, **kwargs):
            if event == 'like_state_after':
                self.now += 10
        self.recorder.emit.side_effect = emit
        self.assertTrue(self.runner.like_verified(9, self.image))
        self.assertEqual(self.device.click.call_count, 1)

    def test_missing_current_bounds_never_uses_old_red_region(self):
        self.device.dump_hierarchy.return_value = '<hierarchy/>'
        red = Image.new('RGB', self.image.size, 'red')
        self.recorder.screenshot.return_value = red
        self.device.screenshot.return_value = red
        with self.assertRaisesRegex(RuntimeError, 'Like verification'):
            self.runner.like_verified(9, self.image)
        self.assertEqual(self.device.click.call_count, 1)

    def test_confirmed_inactive_is_distinct_from_unknown(self):
        self.device.dump_hierarchy.return_value = source(False)
        with self.assertRaises(RuntimeError) as caught:
            self.runner.like_verified(9, self.image)
        self.assertEqual(getattr(caught.exception, 'action_outcome', None), 'not_applied')
        self.assertEqual(self.device.click.call_count, 1)


if __name__ == '__main__':
    unittest.main()
