from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from PIL import Image, ImageDraw
from engagement_inspection import EngagementInspector
from task_store import TaskStore

PACKAGE='com.ss.android.ugc.aweme'

def home_xml(x=540, unread='', overlay=False):
    def node(text,bounds,extra=''):
        return f'<node package="{PACKAGE}" text="{text}" bounds="{bounds}" clickable="true" visible-to-user="true" {extra}/>'
    return '<hierarchy>'+node('推荐','[300,70][450,140]')+node('首页','[0,1450][180,1550]','selected="true"')+node('消息'+unread,f'[{x},1450][{x+180},1550]')+node('我','[720,1450][900,1550]')+(node('dialog','[0,1000][900,1600]','class="android.app.Dialog"') if overlay else '')+'</hierarchy>'

def frame(present=False,x=540,dark=False,noise=False):
    image=Image.new('RGB',(900,1600),'#181818' if dark else 'white')
    draw=ImageDraw.Draw(image)
    ink='white' if dark else 'black'
    draw.rectangle((x+58,1500,x+122,1512),fill=ink)
    if present:draw.ellipse((x+102,1460,x+124,1482),fill='#fa294d')
    if noise:draw.rectangle((20,100,850,1350),fill='red')
    return image

class Device:
    def __init__(self,present=False,x=540,dark=False,tree_error=False):
        self.image=frame(present,x,dark); self.source=home_xml(x); self.actions=[]; self.tree_error=tree_error
    def window_size(self):return 900,1600
    def shell(self,*args,**kwargs):raise RuntimeError('use foreground fallback')
    def app_current(self):return {'package':PACKAGE}
    def dump_hierarchy(self,**kwargs):
        if self.tree_error:raise RuntimeError('no tree')
        return self.source
    def screenshot(self,**kwargs):return self.image.copy()
    def click(self,*args):self.actions.append(('click',args)); raise AssertionError('message clicks forbidden')
    def app_stop(self,*args):self.actions.append(('stop',args))
    def app_start(self,*args,**kwargs):self.actions.append(('start',args)); self.source=home_xml()
    def press(self,*args):self.actions.append(('press',args)); raise AssertionError('not needed')

class Recorder:
    def __init__(self,path,fail_image=False):self.run_dir=path; self.events=[]; self.fail_image=fail_image
    def emit(self,event,**kwargs):self.events.append((event,kwargs))
    def screenshot(self,device,name):
        if self.fail_image:raise RuntimeError('image failed')
        image=device.screenshot(); image.save(self.run_dir/(name+'.png')); return image

class HomeBadgeTests(unittest.TestCase):
    def test_new_default_and_saved_legacy_configs_remain_distinct(self):
        from control_config import DEFAULT_CONFIG, normalized_config, build_scheduled_plan
        from task_preparation import task_requirements
        self.assertEqual(DEFAULT_CONFIG.get('inspection_mode'),'home_badge')
        self.assertEqual(normalized_config({'video_count':20})['inspection_mode'],'legacy')
        config=normalized_config({**DEFAULT_CONFIG,'device_ids':['vm'],'round_count':1,
                                  'engagement_inspection_enabled':True,'inspection_every_rounds':1})
        plan=build_scheduled_plan(config,inspection_profiles={'vm':{'device_kind':'virtual','managed_standard':True}})
        payload=plan.tasks[-1].payload
        self.assertEqual(payload['inspection_workflow_version'],'home_badge')
        self.assertNotIn('inspection_calibration',payload)
        self.assertEqual(task_requirements(payload,inspection=True),['connection','display','application','home_badge'])

    def inspect(self,device,root,**kwargs):
        policy=kwargs.pop('policy',{})
        recorder=Recorder(root,kwargs.pop('fail_image',False)); incidents=[]
        inspector=EngagementInspector(device,recorder,device_id='endpoint',task_id='test',
            incident_sink=incidents.append,sleep=lambda _:None,navigation_lock=lambda:True,**kwargs)
        return inspector.inspect({'inspection_workflow_version':'home_badge',**policy}),recorder,incidents

    def test_home_workflow_reports_only_badge_and_never_enters_messages(self):
        with TemporaryDirectory() as d:
            device=Device(True)
            result,recorder,_=self.inspect(device,Path(d))
            self.assertEqual(result.get('workflow_version'),'home_badge')
            self.assertEqual(result['home_badge']['state'],'present')
            self.assertEqual(result['status'],'completed')
            self.assertEqual(result['sections'],{})
            self.assertEqual(device.actions,[])
            self.assertTrue(list(Path(d).glob('*.png')))
            self.assertTrue(list(Path(d).glob('*.xml.gz')))

    def test_absent_shifted_dark_and_unrelated_red_are_not_messages(self):
        with TemporaryDirectory() as d:
            for x,dark in ((540,False),(490,True)):
                device=Device(False,x,dark);device.image=frame(False,x,dark,noise=True)
                result,_,_=self.inspect(device,Path(d))
                self.assertEqual(result['home_badge']['state'],'absent')
                self.assertEqual(device.actions,[])

    def test_visible_counts_are_optional_and_preserve_plus(self):
        with TemporaryDirectory() as d:
            for text,count,lower in ((' 5',5,False),(' 99+',None,True),('',None,False)):
                device=Device(True); device.source=home_xml(unread=text)
                if text:
                    draw=ImageDraw.Draw(device.image)
                    draw.rounded_rectangle((642,1460,703,1482),radius=10,fill='#fa294d')
                    draw.text((651,1463),text.strip(),fill='white')
                result,_,_=self.inspect(device,Path(d))
                badge=result['home_badge']
                self.assertEqual(badge['state'],'present')
                self.assertEqual(badge['message_count'],count)
                self.assertEqual(badge['count_is_lower_bound'],lower)
                self.assertEqual(badge['badge_text'],text.strip() or None)

    def test_dot_does_not_expose_count_only_present_in_accessibility_description(self):
        with TemporaryDirectory() as d:
            device=Device(True); device.source=home_xml(unread=' 5')
            result,_,_=self.inspect(device,Path(d))
            self.assertEqual(result['home_badge']['state'],'present')
            self.assertIsNone(result['home_badge']['badge_text'])
            self.assertEqual(result['home_badge']['message'],'有消息')

    def test_tight_message_text_bounds_still_include_badge_above_right(self):
        from home_badge import analyze_badge
        image=frame(False)
        ImageDraw.Draw(image).ellipse((682,1450,710,1478),fill='#fa294d')
        xml=home_xml().replace('[540,1450][720,1550]','[598,1495][662,1530]')
        self.assertEqual(analyze_badge(image,xml)['state'],'present')

    def test_message_page_with_recommend_text_is_recovered_not_classified(self):
        with TemporaryDirectory() as d:
            device=Device(True)
            device.source=home_xml().replace('text="推荐"','text="推荐关注"').replace('selected="true"','selected="false"').replace('text="消息"','text="消息" selected="true"')
            result,_,_=self.inspect(device,Path(d))
            self.assertEqual([a[0] for a in device.actions],['stop','start'])
            self.assertEqual(result['home_badge']['state'],'present')

    def test_selected_message_with_number_is_not_home_and_featured_home_is_valid(self):
        from home_badge import is_badge_home
        inspector=SimpleNamespace(_height=1600)
        self.assertFalse(is_badge_home(inspector,home_xml(unread=' 5').replace('text="消息 5"','text="消息 5" selected="true"')))
        self.assertTrue(is_badge_home(inspector,home_xml().replace('推荐','精选')))

    def test_capture_failure_and_overlay_are_unknown_not_absent(self):
        with TemporaryDirectory() as d:
            for fail in (True,False):
                device=Device(False)
                if not fail:device.source=home_xml(overlay=True)
                result,_,incidents=self.inspect(device,Path(d),fail_image=fail)
                self.assertEqual(result['home_badge']['state'],'unknown')
                self.assertNotEqual(result['status'],'completed')
                self.assertTrue(incidents)

    def test_missing_tree_does_not_cold_restart_or_claim_empty(self):
        with TemporaryDirectory() as d:
            device=Device(True,tree_error=True)
            result,_,_=self.inspect(device,Path(d))
            self.assertEqual(result['home_badge']['state'],'unknown')
            self.assertIn('ui_tree',result['home_badge']['evidence_missing'])
            self.assertEqual(device.actions,[])

    def test_existing_navigation_recovery_is_used_once_without_message_click(self):
        with TemporaryDirectory() as d:
            device=Device(True);device.source='<hierarchy />'
            result,_,_=self.inspect(device,Path(d))
            self.assertEqual(result['home_badge']['state'],'present')
            self.assertEqual([a[0] for a in device.actions],['stop','start'])

    def test_persistent_episode_survives_ack_unknown_restart_and_endpoint_change(self):
        with TemporaryDirectory() as d:
            store=TaskStore(Path(d)/'tasks.db')
            self.assertTrue(callable(getattr(store,'record_home_badge_observation',None)), 'persistent home badge operation missing')
            def record(state,device='permanent-1',task='task'):
                return store.record_home_badge_observation(permanent_device_id=device,device_id='endpoint-changed',
                    task_id=task,summary={'conclusion':state,'home_badge':{'state':state},'inspection_id':task},state=state)
            first=record('present')
            self.assertTrue(first['alert_created'])
            store.acknowledge_interaction_alerts([first['alert_id']])
            store=TaskStore(Path(d)/'tasks.db')
            second=record('present',task='second')
            self.assertEqual(second['alert_id'],first['alert_id'])
            self.assertFalse(second['alert_created'])
            record('unknown',task='unknown')
            self.assertFalse(record('present',task='third')['alert_created'])
            self.assertTrue(record('present',device='permanent-2')['alert_created'])
            record('absent',task='clear')
            self.assertTrue(record('present',task='fourth')['alert_created'])

    def test_alert_write_failure_does_not_leave_successful_receipt(self):
        from unittest.mock import patch
        with TemporaryDirectory() as d:
            root=Path(d);store=TaskStore(root/'tasks.db')
            with patch.object(store,'get_virtual_device_for_adb',return_value={'virtual_device_id':'permanent'}), \
                 patch.object(store,'record_home_badge_observation',side_effect=RuntimeError('database write failed')):
                result,_,incidents=self.inspect(Device(True),root,store=store)
            receipt=store.get_interaction_inspection(result['inspection_metadata']['inspection_id'])
            self.assertEqual(receipt['status'],'degraded')
            self.assertEqual(receipt['result_kind'],'incomplete')
            self.assertEqual(result['status'],'degraded')
            self.assertTrue(incidents)
            self.assertTrue((root/'home-badge-receipt.json').is_file())

    def test_existing_visual_observer_can_read_missing_ui_without_navigation(self):
        from unittest.mock import patch
        from control_vision import VisionCandidateLocator
        import json
        answer={'page_type':'home','state':'present','region':[.65,.88,.82,.96],
                'evidence':'首页推荐和底部消息完整可见，99+红色角标','badge_text':'99+'}
        with TemporaryDirectory() as d, patch.object(VisionCandidateLocator,'_request',return_value=json.dumps(answer)) as request:
            device=Device(True,tree_error=True)
            result,_,_=self.inspect(device,Path(d),policy={'visual_navigation_enabled':True})
            self.assertEqual(result['home_badge']['badge_text'],'99+')
            self.assertEqual(result['home_badge']['state'],'present')
            self.assertTrue(result['restored'])
            self.assertIn('ui_tree',result['home_badge']['evidence_missing'])
            self.assertEqual(request.call_count,1)
            self.assertEqual(device.actions,[])

    def test_invalid_visual_region_and_nonhome_are_not_absence(self):
        from unittest.mock import patch
        from control_vision import VisionCandidateLocator
        import json
        for page,region in [('home',[0,0,1,1]),('conversation',[.6,.9,.8,1])]:
            with TemporaryDirectory() as d, patch.object(VisionCandidateLocator,'_request',return_value=json.dumps(
                    {'page_type':page,'state':'absent','region':region,'evidence':'claim'})):
                result,_,_=self.inspect(Device(tree_error=True),Path(d),policy={'visual_navigation_enabled':True})
                self.assertEqual(result['home_badge']['state'],'unknown')

    def test_concurrent_devices_share_only_their_own_episode(self):
        from concurrent.futures import ThreadPoolExecutor
        with TemporaryDirectory() as d:
            store=TaskStore(Path(d)/'tasks.db')
            def run(i):
                return store.record_home_badge_observation(permanent_device_id='vm',device_id='endpoint',
                    task_id=f'task{i}',state='present',summary={'home_badge':{'state':'present'}})
            with ThreadPoolExecutor(max_workers=4) as pool:
                receipts=list(pool.map(run,range(8)))
            self.assertEqual(sum(item['alert_created'] for item in receipts),1)
            self.assertEqual(len({item['alert_id'] for item in receipts}),1)

if __name__=='__main__':unittest.main()
