"""Offline evidence replay through the formal inspector; no store or device I/O."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('image', type=Path)
parser.add_argument('tree', type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
output = args.output.resolve()
if output.exists():
    raise SystemExit('Choose a new output directory; old evidence is never overwritten')
for source in (args.image.resolve(), args.tree.resolve()):
    if source.is_relative_to(output):
        raise SystemExit('Output must be separate from input evidence')
with tempfile.TemporaryDirectory() as isolated:
    os.environ.update(MEDIAFLOW_DATA_ROOT=isolated, RISKFLOW_DATA_ROOT=isolated)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'fixed_runner'))
    from PIL import Image
    from engagement_inspection import EngagementInspector
    class EvidenceDevice:
        def screenshot(self, **kwargs): return Image.open(args.image).copy()
        def dump_hierarchy(self, **kwargs):
            opener = gzip.open if args.tree.suffix == '.gz' else open
            with opener(args.tree, 'rt', encoding='utf-8') as stream: return stream.read()
        def app_current(self): return {'package':'com.ss.android.ugc.aweme'}
        def window_size(self):
            with Image.open(args.image) as image: return image.size
        def __getattr__(self, name):
            raise RuntimeError('Offline evidence only; device operation unavailable: '+name)
    class Recorder:
        run_dir = output
        def screenshot(self, device, name):
            image = device.screenshot(); image.save(output/(name+'.png')); return image
        def emit(self, *args, **kwargs): pass
    output.mkdir(parents=True)
    inspector = EngagementInspector(EvidenceDevice(), Recorder(), device_id='offline-evidence',
        task_id='offline-review', sleep=lambda _:None, navigation_lock=lambda:False)
    result = inspector.inspect({'inspection_workflow_version':'home_badge','visual_navigation_enabled':False})
    receipt = {'review_only':True, 'source_image_sha256':hashlib.sha256(args.image.read_bytes()).hexdigest(),
               'source_tree_sha256':hashlib.sha256(args.tree.read_bytes()).hexdigest(), 'result':result}
    (output/'offline-review.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status':result['status'],'home_badge':result['home_badge']}, ensure_ascii=True))
