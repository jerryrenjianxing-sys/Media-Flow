"""Pi deployment configuration only; upstream owns sessions and chat behavior."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys

from runtime_control import ProcessSpec

UI_VERSION = '0.68.0'
UI_COMMIT = '03680b5066433478a423b59af3a57f1b633df62a'
PI_VERSION = '0.84.4'
LOCK_SHA256 = '6c52c293570be6498a1dd5b7edb39bca0bf2aff02507b2bfe4054f3768b2ca5c'
PROVIDER = 'mediaflow-qwen-token-plan'
MODEL = 'qwen3.8-flash'


def normalize_request_limit(value=10):
    if value == 'unlimited':
        return value
    if isinstance(value,str) and re.fullmatch(r'[1-9]\d*',value):
        value=int(value)
    if type(value) is not int or value < 1 or value > 9007199254740991:
        raise ValueError('MediaFlow request limit is invalid')
    return value


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {}


def _write_new(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
    except FileExistsError:
        pass


def prepare_config(root, old_auth=None):
    """One-way copy into separate native storage. Never overwrite valid credentials."""
    root=Path(root)
    agent=root/'agent'
    _write_new(agent/'settings.json', {
        'defaultProvider': PROVIDER, 'defaultModel': MODEL, 'defaultThinkingLevel':'off',
        'enableInstallTelemetry':False, 'enableAnalytics':False,
        'retry':{'enabled':False, 'provider':{'maxRetries':0, 'timeoutMs':90000}},
        'compaction':{'enabled':False},
    })
    _write_new(agent/'models.json', {'providers':{PROVIDER:{
        'baseUrl':'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
        'api':'openai-completions',
        'models':[{'id':MODEL, 'name':'千问AI平台 · Token Plan / Qwen 3.8 Flash',
                   'reasoning':False, 'input':['text','image'], 'contextWindow':128000,
                   'maxTokens':8192, 'samplingParams':{'enable_thinking':False},
                   'compat':{'supportsDeveloperRole':False,'supportsReasoningEffort':False}}],
    }}})
    auth=agent/'auth.json'
    if not auth.exists() and old_auth:
        candidate=_load(old_auth).get(PROVIDER,{})
        if candidate.get('type')=='api' and isinstance(candidate.get('key'),str) and candidate['key'].strip():
            _write_new(auth,{PROVIDER:{'type':'api_key','key':candidate['key']}})
    (root/'workspace').mkdir(parents=True,exist_ok=True)
    return {'credential_present':bool(_load(auth).get(PROVIDER,{}).get('key')),
            'ui_version':UI_VERSION,'pi_version':PI_VERSION}


def validate_source(source):
    source=Path(source)
    try:
        ui=_load(source/'package.json')
        engine=_load(source/'node_modules/@earendil-works/pi-coding-agent/package.json')
        import hashlib
        digest=hashlib.sha256((source/'package-lock.json').read_bytes()).hexdigest()
        valid=(ui.get('version')==UI_VERSION and engine.get('version')==PI_VERSION and digest==LOCK_SHA256
               and (source/'dist/server/index.js').is_file() and (source/'web/dist/index.html').is_file())
    except (OSError,ValueError,TypeError):
        valid=False
    if not valid:
        raise ValueError('Pi运行资源缺失或版本不符；请重新准备锁定的运行资源，未启动其他Agent。')


def validate_windows_shell(env):
    """Preflight upstream shell discovery so daily startup never downloads a shell."""
    values={key.upper():value for key,value in env.items()}
    candidates=[Path(values[key])/'Git/bin/bash.exe'
                for key in ('PROGRAMFILES','PROGRAMFILES(X86)') if values.get(key)]
    if values.get('USERPROFILE'):
        candidates.append(Path(values['USERPROFILE'])/'.pi-web/bin/bash.exe')
    if not any(path.is_file() for path in candidates):
        raise ValueError('Pi所需Bash运行组件缺失；请先准备Git Bash后重试，未自动下载工具。')


def launch_spec(root, node, source, guard, *, python=None, port=3000, request_limit=10):
    root,node,source,guard=map(Path,(root,node,source,guard))
    request_limit=normalize_request_limit(request_limit)
    client_python=Path(python or sys.executable).with_name('python.exe')
    env={k:v for k,v in os.environ.items() if k.upper() in {
        'SYSTEMROOT','WINDIR','COMSPEC','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','PATHEXT'}}
    # Upstream's Windows shell discovery uses these native installation roots.
    for key in ('ProgramFiles','ProgramFiles(x86)'):
        if os.environ.get(key): env[key]=os.environ[key]
    env.update({
        'PATH':os.pathsep.join([str(node.parent),str(client_python.parent),
                               os.path.join(env.get('SYSTEMROOT','C:/Windows'),'System32'),
                               'C:/Program Files/Git/cmd']),
        'PI_WEB_ENGINE':'pi','PI_WEB_HOST':'127.0.0.1','PI_WEB_PORT':str(port),
        'PI_WEB_CWD':str(root/'workspace'),'PI_WEB_DATA_DIR':str(root/'ui'),
        'PI_CODING_AGENT_DIR':str(root/'agent'),
        'PI_WEB_TERMINAL_IDLE_MS':'0',
        'MEDIAFLOW_API_URL':'http://127.0.0.1:48138', 'MEDIAFLOW_PYTHON':str(client_python),
        'MEDIAFLOW_PI_TRIAL_BUDGET_DIR':str(root/'trial-request-budget'),
        'MEDIAFLOW_PI_REQUEST_LIMIT':str(request_limit),
        'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8',
    })
    guard_url=guard.resolve().as_uri()
    return ProcessSpec(role='pi-agent',command=(str(node),'--import',guard_url,str(source/'dist/server/index.js')),
                       cwd=str(source),log_path=str(root/'pi-host.log'),expected_executable=str(node.resolve()),
                       required_markers=(guard_url,str(source/'dist/server/index.js')),env=env)
