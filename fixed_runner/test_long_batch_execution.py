from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io
from PIL import Image
from comment_ai import CloudModelError
from douyin_uia2_runner import GateDecision, Uia2RunRecorder
from execution_tasks import topic_session
from task_resilience import TaskWaiting


class Device:
    def screenshot(self, **kwargs): return Image.new('RGB', (900, 1600))
    def dump_hierarchy(self, **kwargs): return '<hierarchy />'


class Feed:
    likes = 0
    def __init__(self, *args, **kwargs): self.recovery_events = []
    def ensure_app_ready(self): pass
    def ensure_profile(self, image): pass
    def require_main_feed(self, image, stage): pass
    def swipe_next(self, *args): pass
    def watch(self, *args): pass
    def drain_recovery_events(self): return []
    def main_feed_confirmed(self, image): return True
    def recover_main_feed(self, reason): return True
    def capture_gate(self, *args): return Image.new('RGB', (900,1600)), GateDecision(True, (), ())
    def like_verified(self, *args):
        type(self).likes += 1
        raise RuntimeError('Like verification failed; stopping')


class Topic:
    matches = True
    safe = True
    raw_response = '{}'
    def public_dict(self): return {'matches': True, 'safe': True, 'reason': 'visible production'}


class LongExecutionTest(unittest.TestCase):
    def config(self, count=20):
        return dict(resilience_version='v1', seed=5, video_count=count, dwell_min=0,
                    dwell_max=0, max_gate_skips=3, preview_only=True, content_mode='topic',
                    topic_filter_enabled=True, topic_prompt='factory', like_probability=0,
                    favorite_probability=0, comment_probability=0)

    def run_session(self, outputs, config=None, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        recorder = Uia2RunRecorder(Path(temp.name), 'phone')
        with redirect_stdout(io.StringIO()), patch('execution_tasks.Uia2DouyinRunner', Feed), patch('execution_tasks.analyze_topic', side_effect=outputs) as model:
            result = topic_session(Device(), recorder, config=config or self.config(), **kwargs)
        return result, model

    def test_historical_two_failures_out_of_twenty_finish_degraded_without_95_gate(self):
        outputs = [Topic() for _ in range(20)]
        outputs[8] = CloudModelError('invalid_response', 'field error')
        outputs[11] = CloudModelError('transient_network', 'timeout', retryable=True)
        result, model = self.run_session(outputs)
        self.assertEqual(model.call_count, 20)
        self.assertEqual(result['status'], 'degraded')
        self.assertEqual(result['successful_slots'], 18)
        self.assertEqual(result['failed_slots'], 2)
        self.assertEqual(result['processed_slots'], 20)
        self.assertEqual(result['model_valid_response_rate'], .9)

    def test_scattered_failures_over_hundreds_do_not_open_circuit(self):
        outputs = [CloudModelError('transient_network', 'timeout', retryable=True) if n % 30 == 0 else Topic() for n in range(200)]
        result, _ = self.run_session(outputs, self.config(200))
        self.assertEqual(result['processed_slots'], 200)
        self.assertEqual(result['failed_slots'], 7)

    def test_three_consecutive_temporary_failures_wait_instead_of_finishing(self):
        with self.assertRaises(TaskWaiting) as raised:
            self.run_session([CloudModelError('transient_network', 'timeout', retryable=True)] * 3)
        self.assertEqual(raised.exception.status, 'waiting_model')
        self.assertEqual(raised.exception.result['failed_slots'], 3)
        self.assertEqual(raised.exception.result['processed_slots'], 3)

    def test_auth_waits_user_without_three_identical_rejections(self):
        with self.assertRaises(TaskWaiting) as raised:
            self.run_session([CloudModelError('authentication', 'invalid key', status_code=401)])
        self.assertEqual(raised.exception.status, 'waiting_user')

    def test_unknown_like_disables_like_but_keeps_processing_no_reclick(self):
        Feed.likes = 0
        config = {**self.config(3), 'like_probability': 1}
        result, _ = self.run_session([Topic()] * 3, config)
        self.assertEqual(Feed.likes, 1)
        self.assertEqual(result['processed_slots'], 3)
        self.assertEqual(result['unknown_actions'], 1)
        self.assertEqual(result['reaction_actions_disabled'], ['like'])

    def test_comment_model_failure_before_send_is_not_an_unknown_write(self):
        with patch('execution_tasks.process_current_comment', side_effect=CloudModelError('transient_network', 'timeout', retryable=True)):
            result, _ = self.run_session([Topic()] * 2, {**self.config(2), 'preview_only': False, 'comment_probability': 1})
        self.assertEqual(result['failed_slots'], 2)
        self.assertEqual(result['unknown_actions'], 0)
        self.assertEqual(result['comment_actions_disabled'], [])

    def test_comment_transient_failure_three_videos_waits_after_page_recovery(self):
        with patch('execution_tasks.process_current_comment', side_effect=CloudModelError('transient_network', 'timeout', retryable=True)):
            with self.assertRaises(TaskWaiting) as caught:
                self.run_session([Topic()] * 4, {**self.config(4), 'preview_only': False, 'comment_probability': 1})
        self.assertEqual(caught.exception.status, 'waiting_model')
        self.assertEqual(caught.exception.result['processed_slots'], 3)

    def test_safe_ad_supply_exhaustion_is_bounded_and_not_a_program_fault(self):
        frame = Image.new('RGB', (900, 1600))
        gates = [(frame, GateDecision(False, ('commercial_content',), ()))] * 40 + [(frame, GateDecision(True, (), ()))] * 3
        with patch.object(Feed, 'capture_gate', side_effect=gates):
            result, model = self.run_session([Topic()] * 2, self.config(2))
        self.assertEqual(result['unavailable_slots'], 2)
        self.assertEqual(result['failed_slots'], 0)
        self.assertEqual(result['video_errors'], 0)
        self.assertEqual(model.call_count, 0)

    def test_crash_during_read_resumes_next_slot_and_reports_degraded(self):
        from task_store import TaskStore
        from session_checkpoint import SessionCheckpoint
        import random
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/'tasks.db')
            task = store.get(store.submit('healthcheck', 'phone', {'resilience_version': 'v1'}))
            task = store.claim_next('phone', 'worker')
            cp = SessionCheckpoint(store, task.id)
            cp.save({'processed_slots': 1, 'successful_slots': 0, 'failed_slots': 0, 'videos_seen': 1}, random.Random(5), None, [], in_progress=True)
            store.recover_interrupted('phone')
            self.assertTrue(store.resume_waiting_task(task.id))
            self.assertEqual(store.claim_next('phone', 'worker2').id, task.id)
            result, model = self.run_session([Topic()] * 2, self.config(3), checkpoint=SessionCheckpoint(store, task.id))
            self.assertEqual(model.call_count, 2)
            self.assertEqual(result['processed_slots'], 3)
            self.assertEqual(result['successful_slots'], 2)
            self.assertEqual(result['failed_slots'], 1)
            self.assertEqual(result['status'], 'degraded')

    def test_persisted_hybrid_wait_resumes_home_phase_not_search_or_first_slot(self):
        from task_store import TaskStore
        from session_checkpoint import SessionCheckpoint
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/'tasks.db')
            task_id = store.submit('healthcheck', 'phone', {'resilience_version': 'v1'})
            store.claim_next('phone', 'worker')
            config = {**self.config(6), 'content_mode': 'hybrid', 'search_query': 'factory',
                      'search_segment_min': 3, 'search_segment_max': 3, 'home_segment_min': 3, 'home_segment_max': 3}
            outputs = [Topic()] + [CloudModelError('transient_network', 'timeout', retryable=True)] * 3
            with patch('execution_tasks._prepare_feed_phase', return_value=True):
                with self.assertRaises(TaskWaiting) as caught:
                    self.run_session(outputs, config, checkpoint=SessionCheckpoint(store, task_id))
            store.wait_task(task_id, 'waiting_model', caught.exception.reason, model_key='qwen', now=100, result=caught.exception.result)
            token = store.claim_model_probe('qwen', now=130)
            store.finish_model_probe('qwen', token, success=True, now=131)
            with patch('model_recovery.model_configuration_key', return_value='qwen'):
                self.assertTrue(store.resume_waiting_task(task_id, now=140))
            self.assertEqual(store.claim_next('phone', 'worker2').id, task_id)
            with patch('execution_tasks._prepare_feed_phase', return_value=True) as prepare:
                result, model = self.run_session([Topic()] * 2, config, checkpoint=SessionCheckpoint(store, task_id))
            self.assertEqual(prepare.call_args_list[0].args[1:3], ('home', 'factory'))
            self.assertEqual(model.call_count, 2)
            self.assertEqual((result['successful_slots'], result['failed_slots'], result['processed_slots']), (3, 3, 6))
            self.assertEqual(result['phase_summaries']['search']['videos'], 3)
            self.assertEqual(result['phase_summaries']['home']['videos'], 3)


if __name__ == '__main__': unittest.main()
