from __future__ import annotations

import sys
import tempfile
import unittest
import json
import time
from unittest.mock import patch
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engagement_inspection import EngagementInspector
from test_engagement_inspection import V3Device, V2Recorder, v3_page, node, v3_policy


CAPTURE = Path(__file__).parent / 'runtime/artifacts/initializations/engagement-v3-calibration/pass-1/runs/20260905-110632-210048-127_0_0_1_16416'


class NavigationRecorder(V2Recorder):
    def screenshot(self, device, name):
        path = CAPTURE / 'incident-engagement_navigation-interaction_entry_not_found.png'
        image = Image.open(path).convert('RGB') if device.state == 'message' and path.exists() else Image.new('RGB', (900, 1600), 'white')
        image.save(self.run_dir / (name + '.png'))
        return image


class CandidateStub:
    def locate(self, image_path, *, semantic_name, page_hint, timeout_seconds=20):
        from control_vision import VisionCandidate
        return VisionCandidate('message', semantic_name, (.18, .32, .40, .36), .99, '可见互动消息文字及聚合入口')


class VisualNavigationRegression(unittest.TestCase):
    def setUp(self):
        scope = patch('engagement_inspection.require_visual_navigation_device')
        scope.start()
        self.addCleanup(scope.stop)

    def test_missing_lock_rejects_before_model_or_click(self):
        device = V3Device([])
        device.pages['message'] = v3_page(node('消息'))
        with tempfile.TemporaryDirectory() as root:
            inspector = EngagementInspector(device, NavigationRecorder(Path(root)), sleep=lambda _: None,
                                             vision_locator=CandidateStub(), navigation_lock=lambda: False)
            result = inspector.inspect({**v3_policy(), 'visual_navigation_enabled': True})
        self.assertIn('navigation_lock_required', result['failure_reason'])
        self.assertFalse(any(state == 'message' and y < 1400 for state, _, y in device.clicks))

    def test_wrong_destination_is_not_replayed(self):
        device = V3Device([v3_page(node('发送消息'))])
        device.pages['message'] = v3_page(node('消息'))
        class Stub(CandidateStub):
            def read_activity(self, *args, **kwargs):
                raise RuntimeError('unified_activity_page_not_recognized')
        with tempfile.TemporaryDirectory() as root:
            inspector = EngagementInspector(device, NavigationRecorder(Path(root)), sleep=lambda _: None,
                                             vision_locator=Stub(), navigation_lock=lambda: True)
            result = inspector.inspect({**v3_policy(), 'visual_navigation_enabled': True})
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['unified_activity']['complete'])
        self.assertEqual(sum(state == 'message' for state, _, _ in device.clicks), 1)

    def test_stale_size_and_forbidden_candidate_rejected(self):
        from control_vision import observe_navigation, verify_navigation_observation, parse_candidate
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'frame.png'
            Image.new('RGB', (900, 1600), 'white').save(path)
            observation = observe_navigation(path, target='interaction_entry', expected_pages={'message'}, fixed_bounds=None, locator=CandidateStub())
            for image in (Image.new('RGB', (1600, 900)), Image.new('RGB', (900, 1600), 'black')):
                with self.assertRaisesRegex(RuntimeError, 'stale'):
                    verify_navigation_observation(observation, image, package='app', expected_package='app', lock_owned=True)
            with self.assertRaisesRegex(RuntimeError, 'not_allowed'):
                observe_navigation(path, target='like', expected_pages={'home'}, fixed_bounds=None, locator=CandidateStub())
        for region in ([0, 0, 1.1, 1], [0, 0, float('nan'), 1], [False, 0, 1, 1]):
            with self.assertRaises(ValueError):
                parse_candidate(json.dumps({'semantic_name': 'message', 'region': region, 'confidence': 1, 'evidence': 'x'}), 'message')

    def test_late_model_response_never_returned(self):
        from control_vision import _bounded_call
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'deadline'):
            _bounded_call(lambda: time.sleep(.1), .01)
        self.assertLess(time.monotonic() - start, .09)

    def test_http_errors_fail_without_retry_or_exposing_body(self):
        from control_vision import VisionCandidateLocator
        from comment_ai import CloudModelError
        from unittest.mock import MagicMock
        import os
        for status, kind in ((401, 'authentication'), (402, 'balance'), (403, 'permanent_rejection'), (429, 'rate_limited')):
            response = MagicMock(status_code=status)
            response.text = 'secret-body-must-not-escape'
            context = MagicMock()
            context.__enter__.return_value = response
            with patch.dict(os.environ, {'PHONE_AGENT_API_KEY': 'test-not-a-key'}), patch('control_vision.encode_image', return_value='x'), patch('control_vision.budgeted_post', return_value=context) as post:
                with self.assertRaises(CloudModelError) as caught:
                    VisionCandidateLocator().locate(Path('unused'), semantic_name='message', page_hint='home')
                self.assertEqual(caught.exception.kind, kind)
                self.assertNotIn('secret-body', str(caught.exception))
                self.assertEqual(post.call_count, 1)

    def test_v3_duplicate_frames_keep_paired_evidence(self):
        device = V3Device([])
        with tempfile.TemporaryDirectory() as root:
            recorder = NavigationRecorder(Path(root))
            inspector = EngagementInspector(device, recorder)
            inspector._evidence_workflow = 'v3'
            inspector._capture_v2('one', '<hierarchy/>')
            inspector._capture_v2('two', '<hierarchy/>')
            for evidence in inspector._v2_evidence:
                self.assertTrue((Path(root) / evidence['image_name']).is_file())
                self.assertTrue((Path(root) / evidence['ui_tree_name']).is_file())

    def test_real_missing_accessibility_entry_through_inspector(self):
        device = V3Device([v3_page(node('互动消息'), node('已读', bounds='[0,600][900,650]'))])
        path = CAPTURE / 'incident-engagement_navigation-interaction_entry_not_found.xml'
        device.pages['message'] = path.read_text(encoding='utf-8') if path.exists() else v3_page(node('消息'))
        with tempfile.TemporaryDirectory() as root:
            recorder = NavigationRecorder(Path(root))
            inspector = EngagementInspector(device, recorder, sleep=lambda _: None)
            # Assign the optional dependency so the pre-fix production entrypoint
            # still runs: failure is the actual missing-entry result, not an import.
            inspector._vision_locator = CandidateStub()
            inspector._navigation_lock = lambda: True
            result = inspector.inspect({**v3_policy(), 'visual_navigation_enabled': True})
            self.assertEqual(result['status'], 'completed', result.get('failure_reason'))
            self.assertTrue(result['unified_activity']['complete'])
            self.assertEqual(sum(state == 'message' for state, _, _ in device.clicks), 1)


if __name__ == '__main__':
    unittest.main()
