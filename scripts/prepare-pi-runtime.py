"""Offline assembly of an already-built, pinned Pi tree. No service is started."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'fixed_runner'))
from pi_runtime import (prepare_config, validate_source, normalize_request_limit,
                        UI_VERSION, PI_VERSION, UI_COMMIT)
from skill_bundle import build_skill_bundle


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def prepare(source,node,root,output,python,old_auth=None,port=3000,request_limit=10):
    source,node,root,output,python=map(lambda p:Path(p).resolve(),(source,node,root,output,python))
    request_limit=normalize_request_limit(request_limit)
    validate_source(source)
    if not node.is_file() or not python.is_file(): raise ValueError('MediaFlow专用Node/Python缺失')
    result=prepare_config(root,old_auth)
    archive=build_skill_bundle(root/'cache/mediaflow-platform.zip')
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(root/'agent/skills')
    shutil.copytree(PROJECT/'fixed_runner/assets/pi/plugins/mediaflow',root/'ui/plugins/mediaflow',dirs_exist_ok=True)
    shutil.copyfile(archive,root/'ui/plugins/mediaflow/mediaflow-platform.zip')
    output.parent.mkdir(parents=True,exist_ok=True)
    config={'root':str(root),'source':str(source),'node':str(node),'python':str(python),
            'guard':str(PROJECT/'scripts/pi-request-budget.mjs'),'port':port,
            'request_limit':request_limit,
            'ui_version':UI_VERSION,'pi_version':PI_VERSION,'upstream_commit':UI_COMMIT,
            'node_sha256':digest(node)}
    pending=output.with_suffix('.tmp')
    pending.write_text(json.dumps(config,indent=2),encoding='utf-8')
    pending.replace(output)
    return {**result,'runtime_config':str(output),'skill_sha256':digest(archive)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','node','root','output','python'): parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--old-auth',type=Path)
    parser.add_argument('--port',type=int,default=3000)
    parser.add_argument('--request-limit',type=normalize_request_limit,default=10)
    args=parser.parse_args()
    print(json.dumps(prepare(**vars(args)),ensure_ascii=False))
