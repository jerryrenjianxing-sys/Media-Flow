"""Real sanitized pixels catch missing bitmap reading at both public entry points."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from PIL import Image, ImageDraw
from home_badge import analyze_badge
from test_home_badge import Device, Recorder
from engagement_inspection import EngagementInspector

def original_three():
    image=Image.new('RGB',(720,1600),'#1e1e1e')
    # Only the original, lossless badge pixels are retained in source control.
    with Image.open(Path(__file__).parent/'testdata/home_badge/real-three.png') as crop:
        image.paste(crop,(527,1431))
    ImageDraw.Draw(image).rectangle((480,1470,525,1480),fill='white')
    source='<hierarchy><node text="推荐" bounds="[250,80][330,120]"/><node text="首页" selected="true" bounds="[0,1440][144,1520]"/><node text="消息" bounds="[480,1440][528,1490]"/></hierarchy>'
    return image,source

class RealBadgeTests(unittest.TestCase):
    def test_gray_three_uses_recognized_glyph_even_without_bright_white_ink(self):
        image,source=original_three()
        for y in range(1431,1467):
            for x in range(527,563):
                color=image.getpixel((x,y))
                if min(color)>180 and max(color)-min(color)<75:
                    image.putpixel((x,y),(200,200,200))
        result=analyze_badge(image,source)
        self.assertEqual(result['message_count'],3)
        self.assertEqual(result['quantity_status'],'recognized')
        self.assertEqual(result['quantity_source'],'local_glyph')

    def test_gray_unreadable_ink_is_not_a_pure_dot(self):
        for brightness in (100,200):
            with self.subTest(brightness=brightness):
                image,source=original_three()
                draw=ImageDraw.Draw(image)
                draw.rectangle((527,1431,562,1466),fill='#fa294d')
                draw.line((536,1440,553,1456),fill=(brightness,)*3,width=3)
                draw.line((536,1456,553,1440),fill=(brightness,)*3,width=3)
                result=analyze_badge(image,source)
                self.assertEqual(result['quantity_status'],'unreadable')
                self.assertEqual(result['state'],'present')
                self.assertIsNone(result['badge_text'])

    def test_original_three_pixels_have_exact_count_without_ui_number(self):
        image,source=original_three()
        result=analyze_badge(image,source)
        self.assertEqual(result['message_count'],3)
        self.assertEqual(result['badge_text'],'3')
        self.assertEqual(result['quantity_status'],'recognized')
        self.assertEqual(result['quantity_source'],'local_glyph')
        self.assertEqual(result['badge_bounds'],[527,1431,563,1467])

    def test_formal_inspector_reads_three_and_saves_raw_crop(self):
        with TemporaryDirectory() as directory:
            device=Device();device.image,device.source=original_three()
            device.window_size=lambda:(720,1600)
            recorder=Recorder(Path(directory))
            inspector=EngagementInspector(device,recorder,device_id='offline',task_id='offline',sleep=lambda _:None,navigation_lock=lambda:True)
            result=inspector.inspect({'inspection_workflow_version':'home_badge'})
            self.assertEqual(result['home_badge']['message_count'],3)
            crop=next(e for e in result['evidence'] if e.get('name')=='badge_crop')
            with Image.open(Path(directory)/crop['image_name']) as saved:
                self.assertEqual(saved.size,(36,36))
            self.assertEqual(device.actions,[])

    def test_conflicting_accessibility_count_preserves_badge_without_guess(self):
        image,source=original_three()
        result=analyze_badge(image,source.replace('text="消息"','text="消息 8"'))
        self.assertEqual(result['quantity_status'],'conflict')
        self.assertEqual(result['state'],'present')
        self.assertIsNone(result['message_count'])

    def test_unreadable_white_text_preserves_present_without_guessing_dot(self):
        image,source=original_three()
        draw=ImageDraw.Draw(image)
        draw.rectangle((527,1431,562,1466),fill='#fa294d')
        draw.line((536,1440,553,1456),fill='white',width=3)
        draw.line((536,1456,553,1440),fill='white',width=3)
        result=analyze_badge(image,source)
        self.assertEqual(result['quantity_status'],'unreadable')
        self.assertEqual(result['state'],'present')
        self.assertIsNone(result['badge_text'])

    def test_multiple_candidate_badges_do_not_pick_arbitrary_count(self):
        image,source=original_three()
        ImageDraw.Draw(image).ellipse((574,1432,590,1448),fill='#fa294d')
        result=analyze_badge(image,source)
        self.assertEqual(result['quantity_status'],'conflict')
        self.assertIsNone(result['badge_text'])

    def test_public_quantity_contract_and_raw_crop_evidence_link(self):
        from control_api import _public_home_badge, _public_interaction_inspection
        badge=_public_home_badge(dict(state='present',badge_text='3',quantity_status='recognized',
            quantity_source='local_glyph',badge_bounds=[527,1431,563,1467],rule_version='home-badge-glyph-v1',private='secret'))
        self.assertEqual(badge.get('quantity_status'),'recognized')
        self.assertEqual(badge['quantity_source'],'local_glyph')
        self.assertEqual(badge['badge_bounds'],[527,1431,563,1467])
        self.assertNotIn('private',badge)
        detail=_public_interaction_inspection(dict(id='offline',evidence=[dict(id='offline-badge',name='badge_crop',image_name='inspection-home_badge-crop.png')]),detail=True)
        self.assertEqual(detail['evidence'][0]['image_url'],'/api/interaction-evidence?inspection_id=offline&evidence_id=offline-badge&kind=image')

    def test_public_quantity_rejects_untrusted_enum_and_invalid_rectangle(self):
        from control_api import _public_home_badge
        badge=_public_home_badge(dict(state='unknown',quantity_status='secret',quantity_source='secret',badge_bounds=[4,8,1,2],rule_version={'secret':'value'}))
        self.assertEqual(badge.get('quantity_status'),'unreadable')
        self.assertIsNone(badge['quantity_source'])
        self.assertIsNone(badge['badge_bounds'])
        self.assertIsNone(badge['rule_version'])

    def test_local_multi_digit_and_plus_preserve_display_without_ui_count(self):
        for text,count in [('99+',None),('10',10),('58',58),('76',76),('90',90)]:
            with self.subTest(text=text):
                image,source=original_three()
                ImageDraw.Draw(image).rectangle((527,1431,565,1468),fill='#1e1e1e')
                with Image.open(Path(__file__).parent/f'testdata/home_badge/synthetic-{text.replace("+","plus")}.png') as crop:
                    image.paste(crop,(527,1431))
                result=analyze_badge(image,source)
                self.assertEqual(result['badge_text'],text)
                self.assertEqual(result['message_count'],count)
                self.assertEqual(result['quantity_source'],'local_glyph')
                self.assertEqual(result['count_is_lower_bound'],text.endswith('+'))

    def test_scaled_compressed_three_is_correct_or_conservatively_unreadable(self):
        import io,re
        for factor in (.75,1.0,1.5):
            for quality in (75,95):
                with self.subTest(factor=factor,quality=quality):
                    image,source=original_three()
                    image=image.resize((int(720*factor),int(1600*factor)),Image.Resampling.LANCZOS)
                    source=re.sub(r'\d+',lambda m:str(int(int(m[0])*factor)),source)
                    data=io.BytesIO();image.save(data,format='JPEG',quality=quality);data.seek(0)
                    with Image.open(data) as compressed:
                        result=analyze_badge(compressed,source)
                    self.assertEqual(result['state'],'present')
                    if factor>=1:
                        self.assertEqual(result['message_count'],3)
                    else:
                        # 13px ink after downscale+JPEG loses stroke pixels.
                        # Rejection is intentional; do not relax separation.
                        self.assertIsNone(result['message_count'])
                        self.assertEqual(result['quantity_status'],'unreadable')

    def test_optional_visual_fallback_resolves_unreadable_but_not_conflict(self):
        from unittest.mock import patch
        from control_vision import VisionCandidateLocator
        for conflict in (False,True):
            with TemporaryDirectory() as directory:
                device=Device();device.image,device.source=original_three();device.window_size=lambda:(720,1600)
                if conflict:
                    device.source=device.source.replace('text="消息"','text="消息 8"')
                else:
                    draw=ImageDraw.Draw(device.image)
                    draw.rectangle((537,1435,552,1462),fill='#fa294d')
                    draw.line((538,1440,551,1456),fill='white',width=3)
                observer=VisionCandidateLocator()
                with patch.object(observer,'read_home_badge',return_value=dict(page_type='home',state='present',region=[.72,.89,.80,.93],badge_text='3')) as read:
                    inspector=EngagementInspector(device,Recorder(Path(directory)),device_id='offline',task_id='offline',sleep=lambda _:None,navigation_lock=lambda:True,vision_locator=observer)
                    result=inspector.inspect({'inspection_workflow_version':'home_badge','visual_navigation_enabled':True})
                if conflict:
                    self.assertEqual(result['home_badge']['quantity_status'],'conflict')
                    self.assertEqual(read.call_count,0)
                else:
                    self.assertEqual(result['home_badge']['message_count'],3)
                    self.assertEqual(result['home_badge']['quantity_source'],'vision')
                self.assertEqual(device.actions,[])
