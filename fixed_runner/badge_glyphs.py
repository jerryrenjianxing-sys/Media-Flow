"""Conservative, offline glyph matching inside an already located red badge.

Templates are versioned pixel masks, never screenshot fingerprints. Generic
font masks cover 0–9/+; real validation coverage is documented separately.
"""
from functools import lru_cache
import json
from pathlib import Path
from PIL import Image

RULE_VERSION = 'home-badge-glyph-v1'

@lru_cache(maxsize=1)
def _templates():
    return json.loads((Path(__file__).parent/'assets/home_badge_glyphs_v1.json').read_text(encoding='utf-8'))['templates']

def read_glyphs(badge: Image.Image) -> str | None:
    badge=badge.convert('RGB')
    mask=Image.new('L',badge.size)
    # White ink must be enclosed horizontally by the badge, excluding nearby
    # white navigation text and rectangular screenshot backgrounds.
    for y in range(badge.height):
        reds=[x for x in range(badge.width) if (lambda c:c[0]>150 and c[0]-c[1]>65 and c[0]-c[2]>30)(badge.getpixel((x,y)))]
        if not reds:continue
        for x in range(min(reds)+1,max(reds)):
            color=badge.getpixel((x,y))
            if min(color)>180 and max(color)-min(color)<75:
                mask.putpixel((x,y),255)
    segments=[];start=None
    for x in range(mask.width+1):
        on=x<mask.width and mask.crop((x,0,x+1,mask.height)).getbbox()
        if on and start is None:start=x
        if not on and start is not None:
            part=mask.crop((start,0,x,mask.height));box=part.getbbox()
            segments.append(part.crop(box));start=None
    if not 1<=len(segments)<=7:return None
    chars=[]
    for part in segments:
        if part.height<badge.height*.28 or part.height>badge.height*.85 or part.width<2:return None
        sample=list(part.resize((20,28),Image.Resampling.BILINEAR).tobytes())
        scores={}
        for template in _templates():
            aspect=part.width/part.height
            if abs(aspect-template['aspect'])>max(.16,template['aspect']*.3):continue
            expected=[v for row in template['mask'] for v in row]
            # Soft intersection-over-union penalizes both missing and extra ink.
            score=sum(min(a,b) for a,b in zip(sample,expected))/max(1,sum(max(a,b) for a,b in zip(sample,expected)))
            scores[template['char']]=max(score,scores.get(template['char'],0))
        ranked=sorted(scores.items(),key=lambda pair:pair[1],reverse=True)
        if not ranked or ranked[0][1]<.64 or (len(ranked)>1 and ranked[0][1]-ranked[1][1]<.075):return None
        chars.append(ranked[0][0])
    text=''.join(chars)
    import re
    return text if re.fullmatch(r'[1-9]\d{0,5}\+?',text) else None
