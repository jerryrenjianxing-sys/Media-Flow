"""Real plan/store regressions for the five-phone user workflow (no devices)."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_platform import AgentPlatform
from control_config import normalized_config, build_scheduled_plan
from execution_tasks import routed_action_plan
from execution_tasks import topic_session, Uia2RunRecorder, CommentDecision
from test_worker import FakeDevice, GateDecision
from PIL import Image
import json
import re
from task_store import TaskStore


class PhysicalHybridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'tasks.db')
        self.store.set_paused(True)
        self.devices = ['phone-' + str(i) for i in range(5)]
        self.snapshot = {'devices': [dict(device_id=d, device_type='physical', state='device',
            model='test-phone', profile_verified=True, initialization_status='legacy') for d in self.devices]}
        self.platform = AgentPlatform(self.root/'plans', self.store, lambda: self.snapshot,
            model_status_reader=lambda: {'model_ready': True}, worker_launcher=lambda ids: [])
        self.raw = dict(device_ids=self.devices, video_count=20, round_count=10,
            round_interval_minutes=1, content_mode='hybrid', search_query='坚果 生产厂家',
            topic_prompt='命中：坚果加工。必须证据：生产线。排除：零售。',
            search_segment_min=10, search_segment_max=10, home_segment_min=10, home_segment_max=10,
            engagement_inspection_enabled=True, inspection_every_rounds=3,
            like_probability=.3, favorite_probability=.2, comment_probability=.1,
            matched_like_probability=.7, matched_favorite_probability=.6, matched_comment_probability=.5,
            preview_only=False, dwell_min=8, dwell_max=25)

    def plan(self):
        return self.platform.plan({'config': self.raw}, {'session_id':'test', 'call_id':'plan'})

    def confirm(self, plan):
        return self.platform.confirm(plan['plan_id'], 'test', dict(confirmed=True,
            confirm_writes=True, plan_hash=plan['preview']['plan_hash']))

    def test_five_physical_phones_use_same_scoped_plan_and_idempotent_batch(self):
        old = self.store.submit('healthcheck', 'other', {})
        self.store.save_run_draft({'keep':'draft'}, expected_revision=0)
        plan = self.plan()
        self.assertEqual(plan['state'], 'awaiting_confirmation')
        self.assertEqual(plan['config']['hybrid_probability_mode'], 'topic')
        self.assertEqual(plan['config']['round_interval_basis'], 'completion')
        receipt = self.confirm(plan)
        self.assertEqual(receipt['task_ids'], self.confirm(plan)['task_ids'])
        tasks = [self.store.get(i) for i in receipt['task_ids']]
        self.assertEqual(len(tasks), 65)
        for device in self.devices:
            own = [t for t in tasks if t.device_id == device]
            self.assertEqual(sum(t.task_type=='douyin_topic_session' for t in own), 10)
            self.assertEqual([t.payload['after_round_index'] for t in own if t.task_type=='douyin_engagement_inspection'], [3,6,9])
        self.assertTrue(self.store.is_paused())
        self.assertIsNone(self.store.claim_next('other','test'))
        self.assertEqual(self.store.get(old).status,'pending')
        self.assertEqual(self.store.get_run_draft()['config'], {'keep':'draft'})

    def test_physical_identity_changed_between_plan_and_start_is_rejected(self):
        plan = self.plan()
        self.snapshot['devices'][0]['model'] = 'another-phone'
        with self.assertRaisesRegex(ValueError, '身份'):
            self.confirm(plan)
        self.assertEqual(self.store.list(), [])

    def test_unlisted_and_ambiguous_devices_are_not_guessed(self):
        for rows in ([], self.snapshot['devices'] + [dict(self.snapshot['devices'][0])]):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                AgentPlatform.resolve_devices({'devices':rows}, [self.devices[0]])

    def test_round_count_batch_has_no_wall_clock_expiry_but_can_still_stop(self):
        self.raw['batch_stop_policy']='round_count'
        plan=self.plan()
        receipt=self.confirm(plan)
        self.assertIsNone(receipt['deadline'])
        task=self.store.claim_next(self.devices[0],'worker')
        with patch('agent_queue.time.time',return_value=4_000_000_000):
            self.assertTrue(self.store.agent_task_may_run_paused(task.id))
            self.assertFalse(self.store.agent_task_stop_requested(task.id))
            self.store.agent_batch_receipt(plan['plan_id'],'test')
            self.assertEqual(self.store.get(receipt['task_ids'][-1]).status,'pending')
            self.store.set_paused(True)
            self.store.resume_agent_batch(plan['plan_id'],'test')
            self.assertEqual(self.confirm(plan)['task_ids'],receipt['task_ids'])
        self.store.control_agent_batch(plan['plan_id'],'test',stop=True)
        self.assertTrue(self.store.agent_task_stop_requested(task.id))

    def test_physical_badge_executes_without_mumu_preparation_or_clicks(self):
        from test_home_badge import Device
        from execution_tasks import execute_task
        config=normalized_config({**self.raw,'inspection_mode':'home_badge'})
        planned=build_scheduled_plan(config)
        check=next(t for t in planned.tasks if t.task_type=='douyin_engagement_inspection')
        task_id=self.store.submit(check.task_type,check.device_id,check.payload)
        recorder=Uia2RunRecorder(self.root/'badge',check.device_id)
        device=Device(True)
        # A real physical profile is not required to use the MuMu 900px canvas.
        width,height=720,1600
        device.image=device.image.resize((width,height))
        device.source=re.sub(r'\[(\d+),(\d+)\]',lambda m:f'[{int(int(m[1])*width/900)},{int(int(m[2])*height/1600)}]',device.source)
        device.window_size=lambda:(width,height)
        from types import SimpleNamespace
        with patch('device_profiles.load_device_profiles',return_value={check.device_id:SimpleNamespace(verified=True)}):
            result=execute_task(device,self.store.get(task_id),recorder,task_store=self.store)
        self.assertEqual(result['home_badge']['state'],'present')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(device.actions,[])

    def test_offline_phone_blocks_whole_plan_without_scope_shrink(self):
        self.snapshot['devices'][0]['state'] = 'offline'
        plan = self.plan()
        self.assertEqual(plan['state'], 'blocked')
        self.assertEqual(plan['config']['device_ids'], self.devices)
        self.assertEqual(self.store.list(), [])

    def test_both_verified_phases_honor_each_topic_probability(self):
        config = {**self.raw, 'hybrid_probability_mode':'topic'}
        for phase in ('search','home'):
            for matched, values in ((True,(.7,.6,.5)), (False,(.3,.2,.1))):
                with self.subTest(phase=phase,matched=matched):
                    probs, _ = routed_action_plan(config,matched=matched,safe=True,feed_phase=phase)
                    self.assertEqual(tuple(probs.values()), values)
                    blocked, _ = routed_action_plan(config,matched=matched,safe=False,feed_phase=phase)
                    self.assertEqual(tuple(blocked.values()), (0,0,0))
        probs,_ = routed_action_plan(config,matched=True,safe=True,feed_phase=None)
        self.assertEqual(tuple(probs.values()), (0,0,0))

    def test_legacy_payload_keeps_old_routes_and_new_flags_are_validated(self):
        legacy = normalized_config(self.raw)
        self.assertEqual(legacy['hybrid_probability_mode'],'legacy')
        self.assertEqual(legacy['round_interval_basis'],'scheduled')
        probs,_ = routed_action_plan(legacy,matched=False,safe=True,feed_phase='home')
        self.assertEqual(tuple(probs.values()),(.3,.2,0))
        for key in ('hybrid_probability_mode','round_interval_basis'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalized_config({**self.raw,key:'invalid'})

    def test_business_loop_comments_on_unmatched_content_without_theme_material(self):
        class Runner:
            def __init__(self,*args,**kwargs): pass
            def ensure_app_ready(self): pass
            def ensure_profile(self,image): pass
            def require_main_feed(self,image,stage): pass
            def prepare_feed_phase(self,phase,query,reason): return True
            def swipe_next(self,*args): pass
            def watch(self,*args): pass
            def capture_gate(self,*args): return Image.new('RGB',(1080,2400),'black'), GateDecision(True,(),())
        class Topic:
            matches=False; safe=True; raw_response='{}'; reason='unrelated'; topic='当前内容'; evidence=()
            def public_dict(self): return dict(matches=False,safe=True,reason=self.reason,topic=self.topic)
        comments=[]
        def comment(*args,**kwargs):
            comments.append(kwargs)
            return CommentDecision('comment','画面展示很清楚','当前画面',.9,False,'{}'),True,None
        config=normalized_config({**self.raw, 'hybrid_probability_mode':'topic', 'video_count':2,
            'dwell_min':1,'dwell_max':1,'search_segment_min':1,'search_segment_max':1,
            'home_segment_min':1,'home_segment_max':1, 'like_probability':0,'favorite_probability':0,
            'comment_probability':1})
        config['content_plan_snapshot']={'theme':{'name':'坚果','comment_template':'坚果主题专用'}}
        recorder=Uia2RunRecorder(self.root/'evidence','test-phone')
        with patch('execution_tasks.Uia2DouyinRunner',Runner), patch('execution_tasks.analyze_topic',return_value=Topic()), \
             patch('execution_tasks.process_current_comment',side_effect=comment):
            result=topic_session(FakeDevice(),recorder,config=config)
        self.assertEqual(result['videos_seen'],2)
        self.assertEqual(result['comments_sent'],2)
        self.assertEqual(len(comments),2)
        self.assertTrue(all(c['content_plan_snapshot'] is None for c in comments))
        decisions=json.loads((recorder.run_dir/'topic-session-decisions.json').read_text(encoding='utf-8'))
        self.assertEqual([d['feed_phase'] for d in decisions],['search','home'])
        self.assertTrue(all(d['probabilities']['comment']==1 for d in decisions))

    def test_actual_completion_gap_survives_restart_and_cannot_skip_rounds(self):
        config = normalized_config({**self.raw,'round_interval_basis':'completion','inspection_mode':'home_badge'})
        base = datetime.now().astimezone()-timedelta(hours=1)
        scheduled = build_scheduled_plan(config,base_time=base,submission_id='gap')
        self.store.set_paused(False)
        for task in scheduled.tasks:
            self.store.submit(task.task_type,task.device_id,task.payload,not_before=task.not_before)
        foreign=self.store.submit('healthcheck',self.devices[0],{'submission_id':'unrelated'},
            not_before=(datetime.now().astimezone()+timedelta(days=1)).isoformat())
        foreign_ready=self.store.get(foreign).not_before
        t0 = datetime.now().astimezone()
        for round_index in (1,2,3):
            current = t0+timedelta(minutes=(round_index-1)*2)
            with patch('task_store.now_iso', return_value=current.isoformat()):
                task = self.store.claim_next(self.devices[0],'worker')
                self.assertEqual(task.payload.get('round_index'),round_index)
                self.store.finish(task.id,status='completed',run_dir=None)
                if round_index == 3:
                    check = self.store.claim_next(self.devices[0],'worker')
                    self.assertEqual(check.task_type,'douyin_engagement_inspection')
                    current+=timedelta(seconds=30)
                    with patch('task_store.now_iso',return_value=current.isoformat()):
                        self.store.finish(check.id,status='completed',run_dir=None)
            restarted = TaskStore(self.root/'tasks.db')
            with patch('task_store.now_iso', return_value=(current+timedelta(seconds=59)).isoformat()):
                self.assertIsNone(restarted.claim_next(self.devices[0],'worker'))
            with patch('task_store.now_iso', return_value=(current+timedelta(seconds=61)).isoformat()):
                self.assertTrue(restarted.has_ready(self.devices[0]))
            self.assertTrue(restarted.has_ready(self.devices[1]))
            self.assertEqual(self.store.get(foreign).not_before,foreign_ready)


if __name__ == '__main__':
    unittest.main()
