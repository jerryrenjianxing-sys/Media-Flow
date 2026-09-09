import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from comment_ai import CloudModelError, CommentDecision
from douyin_uia2_runner import Uia2RunRecorder
from execution_tasks import process_current_comment, topic_session
from task_resilience import TaskWaiting
from test_long_batch_execution import Device, Feed, Topic


class CommentFeed(Feed):
    def tap_control(self, *args): pass
    def close_comment_panel(self, *args): pass


class CommentEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.recorder = Uia2RunRecorder(Path(directory), 'offline')
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch('execution_tasks.time.sleep'))
        self.stack.enter_context(patch('douyin_fixed_runner.comment_panel_visible', return_value=True))

    def events(self, name):
        return [event for line in self.recorder.log_path.read_text(encoding='utf-8').splitlines()
                if (event := json.loads(line))['event'] == name]

    def comment(self, video):
        return process_current_comment(Device(), self.recorder, CommentFeed(), video=video, send=False)

    def error(self):
        return CloudModelError('invalid_response', 'Model fields are invalid', attempts=2,
                               diagnostics={'stage': 'schema', 'elapsed_ms': 45678, 'attempt': 2, 'category': 'field'})

    def session(self, topic_error=None):
        config = dict(resilience_version='v1', seed=5, video_count=1, dwell_min=0,
                      dwell_max=0, max_gate_skips=3, preview_only=False, content_mode='topic',
                      topic_filter_enabled=True, topic_prompt='factory', like_probability=0,
                      favorite_probability=0, comment_probability=1)
        self.incidents = []
        with patch('execution_tasks.Uia2DouyinRunner', CommentFeed), patch('execution_tasks.analyze_topic', return_value=Topic(), side_effect=topic_error):
            return topic_session(Device(), self.recorder, config=config, incident_sink=self.incidents.append)

    def test_two_videos_and_repeated_video_keep_immutable_comment_evidence(self):
        decisions = [CommentDecision('skip', '', text, .9, False, text) for text in ('first', 'second', 'third')]
        with patch('execution_tasks.generate_comment', side_effect=decisions):
            for video in (1, 2, 2):
                self.comment(video)
        events = self.events('comment_ai_decision')
        self.assertEqual([event.get('video_index') for event in events], [1, 2, 2])
        self.assertEqual(len({event['raw_response_path'] for event in events}), 3)
        for event, text in zip(events, ('first', 'second', 'third')):
            self.assertEqual(Path(event['raw_response_path']).read_text(encoding='utf-8'), text)
            saved = json.loads(Path(event['decision_path']).read_text(encoding='utf-8'))
            self.assertEqual(saved['reason'], text)
            self.assertEqual(saved['video_index'], event['video_index'])

    def test_second_invalid_response_has_current_input_and_no_stale_raw_attribution(self):
        with patch('execution_tasks.generate_comment', side_effect=[CommentDecision('skip', '', 'first', .9, False, 'first raw'), self.error()]):
            self.comment(1)
            with self.assertRaises(CloudModelError): self.comment(2)
        failure = self.events('comment_failure_evidence')[0]
        self.assertEqual(failure.get('video_index'), 2)
        self.assertIsNone(failure['raw_response_path'])
        self.assertIsNone(failure['decision_path'])
        self.assertTrue(Path(failure['input_screenshot_path']).is_file())
        self.assertIn('video-2-', failure['input_screenshot_path'])
        self.assertEqual(failure['model_error'], self.error().public_dict())
        saved = json.loads(Path(failure['failure_path']).read_text(encoding='utf-8'))
        self.assertEqual(saved['model_error'], failure['model_error'])
        self.assertNotIn('first raw', json.dumps(saved))
        self.assertEqual(Path(self.events('comment_ai_decision')[0]['raw_response_path']).read_text(encoding='utf-8'), 'first raw')

    def test_model_diagnostics_reach_comment_incident_and_count_once(self):
        with patch('execution_tasks.generate_comment', side_effect=self.error()):
            result = self.session()
        self.assertEqual(self.incidents[0]['context'].get('model_error'), self.error().public_dict())
        self.assertEqual(result['model_errors'], 1)
        self.assertEqual(result['model_error_counts'], {'invalid_response': 1})
        self.assertEqual(result['unknown_actions'], 0)

    def test_model_cause_survives_cleanup_and_feed_recovery_failure(self):
        with patch('execution_tasks.generate_comment', side_effect=self.error()), \
             patch.object(CommentFeed, 'close_comment_panel', side_effect=RuntimeError('cleanup failed')), \
             patch.object(CommentFeed, 'main_feed_confirmed', return_value=False), \
             patch.object(CommentFeed, 'recover_main_feed', return_value=False):
            with self.assertRaises(TaskWaiting) as caught: self.session()
        self.assertEqual(caught.exception.status, 'waiting_device')
        self.assertEqual(caught.exception.result['model_errors'], 1)
        self.assertEqual(caught.exception.result['model_error_counts'], {'invalid_response': 1})
        self.assertEqual(self.incidents[0]['context']['model_error'], self.error().public_dict())
        self.assertEqual(self.incidents[0]['outcome'], 'device_fatal')

    def test_topic_error_is_not_counted_twice(self):
        result = self.session(topic_error=self.error())
        self.assertEqual(result['model_errors'], 1)
        self.assertEqual(result['model_error_counts'], {'invalid_response': 1})
        self.assertEqual(len(self.incidents), 1)

    def test_ordinary_comment_error_still_records_incident_without_model_count(self):
        with patch('execution_tasks.generate_comment', side_effect=RuntimeError('ordinary failure')):
            result = self.session()
        self.assertEqual(result['model_errors'], 0)
        self.assertEqual(self.incidents[0]['error_type'], 'RuntimeError')
        self.assertEqual(self.incidents[0]['outcome'], 'skipped')

    def test_model_evidence_does_not_publish_raw_upstream_error_or_key(self):
        error = self.error()
        error.args = ('private upstream body Bearer sk-private123456789',)
        error.diagnostics.update(raw_body='private upstream body', api_key='sk-private123456789')
        with patch('execution_tasks.generate_comment', side_effect=error):
            self.session()
        public = self.recorder.log_path.read_text(encoding='utf-8') + json.dumps(self.incidents)
        self.assertNotIn('private upstream body', public)
        self.assertNotIn('sk-private123456789', public)

    def test_failure_metadata_write_error_does_not_replace_model_cause(self):
        write_text = Path.write_text
        def write(path, *args, **kwargs):
            if path.name.endswith('-failure.json'):
                raise OSError('disk unavailable')
            return write_text(path, *args, **kwargs)
        with patch('execution_tasks.generate_comment', side_effect=self.error()), patch.object(Path, 'write_text', write):
            result = self.session()
        self.assertEqual(result['model_errors'], 1)
        self.assertEqual(self.incidents[0]['context'].get('model_error'), self.error().public_dict())
        failure = self.events('comment_failure_evidence')[0]
        self.assertIsNone(failure['failure_path'])
        self.assertEqual(failure['evidence_write_error'], 'OSError')


if __name__ == '__main__': unittest.main()
