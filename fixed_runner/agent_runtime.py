"""Own the embedded OpenCode process; never expose its admin API to the browser."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time

import requests
from runtime_layout import APP_ROOT
from agent_process import DirectoryLease, ChildJob
from agent_dependencies import prepare_dependencies

ENGINE_VERSION = '1.18.29'
ENGINE_SHA256 = '32675fbf8fcb804c6ee4e048ff0926cd64bf54d1b6185bf2c09e93a2662dd69d'
PROVIDER_ID = 'mediaflow-qwen-token-plan'
MODEL_ID = 'qwen3.8-flash'


def available_port():
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        return available.getsockname()[1]


class AgentRuntimeError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def isolated_environment(root: Path, base=None):
    # Do not borrow the developer's personal providers, agents, plugins or keys.
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if not any(s in k.upper() for s in ('TOKEN', 'API_KEY', 'OPENCODE', 'ANTHROPIC', 'OPENAI', 'AWS_', 'GOOGLE_APPLICATION_CREDENTIALS'))}
    for name, folder in [('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'),
                         ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')]:
        path = root / folder
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    env.update(OPENCODE_TEST_HOME=str(root), OPENCODE_DISABLE_AUTOUPDATE='true',
               OPENCODE_DISABLE_PROJECT_CONFIG='true', OPENCODE_DISABLE_MODELS_FETCH='true')
    return env


def build_config(bridge_url: str, bridge_token: str, mcp_command: list[str], *, tool_context=False):
    return {
        '$schema': 'https://opencode.ai/config.json', 'autoupdate': False, 'share': 'disabled',
        'model': f'{PROVIDER_ID}/{MODEL_ID}', 'small_model': f'{PROVIDER_ID}/{MODEL_ID}',
        'default_agent': 'mediaflow',
        'plugin': [(Path(__file__).parent / 'assets/agent/context-plugin.mjs').resolve().as_uri()] if tool_context else [],
        'agent': {'mediaflow': {'mode': 'primary', 'description': 'MediaFlow软件操作与修复助手',
            'steps': 12, 'prompt': (Path(__file__).parent / 'assets/agent/workflow.md').read_text(encoding='utf-8')}},
        'provider': {PROVIDER_ID: {
            'npm': '@ai-sdk/openai-compatible', 'name': '千问AI平台 · Token Plan（实验性）',
            'options': {'baseURL': bridge_url + '/model', 'apiKey': bridge_token},
            'models': {MODEL_ID: {'name': 'Qwen 3.8 Flash', 'tool_call': True,
                'attachment': True, 'modalities': {'input': ['text', 'image'], 'output': ['text']},
                'limit': {'context': 128000, 'output': 8192}}},
        }},
        'permission': {'*': 'deny', 'mediaflow_*': 'allow', 'question': 'allow'},
        'mcp': {'mediaflow': {'type': 'local', 'command': mcp_command, 'enabled': True,
            'environment': {'MEDIAFLOW_AGENT_BRIDGE_URL': bridge_url, 'MEDIAFLOW_AGENT_BRIDGE_TOKEN': bridge_token}}},
    }


class AgentRuntime:
    def __init__(self, root: Path, *, binary: Path | None = None):
        self.root = root.resolve()
        bundled = APP_ROOT / 'runtime/opencode/opencode.exe'
        self.binary = binary or (bundled if bundled.is_file() else APP_ROOT / f'packaging/tools/opencode/{ENGINE_VERSION}/expanded/opencode.exe')
        self._lock = threading.RLock()
        self._process = None
        self._log = None
        self._password = secrets.token_urlsafe(32)
        self._port = None
        self._state = 'stopped'
        self._error = ''
        self._code = ''
        self._lease = DirectoryLease(self.root / 'engine.lock')
        self._job = None

    def status(self):
        # Never wait for the long startup lock when polling progress.
        process = self._process
        if process and process.poll() is not None and self._state == 'ready':
            self._state, self._code, self._error = 'failed', 'engine_exited', '对话引擎已退出，请重新连接；不会重放旧操作'
        return {'state': self._state, 'engine': 'OpenCode', 'engine_version': ENGINE_VERSION,
                'reason_code': self._code, 'message': self._error,
                'retryable': self._state in {'failed', 'stopped'}}

    def start(self, config: dict, *, timeout=55):
        with self._lock:
            if self.status()['state'] == 'ready':
                return self.status()
            # Release only our previous handles; never look up or kill a PID file.
            self.stop()
            self._state, self._error, self._code = 'starting', '', ''
            try:
                if not self._lease.acquire():
                    raise AgentRuntimeError('engine_owned', '同一数据目录已有对话引擎，请使用原服务，不会启动第二份')
                if not self.binary.is_file():
                    raise AgentRuntimeError('engine_missing', '缺少 OpenCode 引擎，请修复安装后重试；原任务台仍可使用')
                with self.binary.open('rb') as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
                if digest != ENGINE_SHA256:
                    raise AgentRuntimeError('engine_checksum', 'OpenCode 引擎校验失败，请修复安装，不会启动未知程序')
                deadline = time.monotonic() + timeout
                if timeout > 0:
                    try:
                        prepare_dependencies(APP_ROOT, self.root, deadline)
                    except Exception:
                        raise AgentRuntimeError('engine_dependencies', '插件运行资源缺失、损坏或准备超时，请修复安装后重试；不会临时下载依赖') from None
                env = isolated_environment(self.root)
                env['OPENCODE_SERVER_PASSWORD'] = self._password
                env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config, ensure_ascii=False)
                workspace = self.root / 'workspace'
                workspace.mkdir(parents=True, exist_ok=True)
                self._port = available_port()
                # A Popen handle (not a stale PID) owns only this child process.
                if self._log:
                    self._log.close()
                self._log = (self.root / 'engine.log').open('a', encoding='utf-8')
                self._job = ChildJob()
                self._process = subprocess.Popen([str(self.binary), 'serve', '--hostname', '127.0.0.1', '--port', str(self._port)],
                    cwd=workspace, env=env, stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self._job.assign(self._process)
                while time.monotonic() < deadline:
                    if self._process.poll() is not None:
                        raise AgentRuntimeError('engine_exited', 'OpenCode 启动失败，请查看引擎诊断后重试')
                    try:
                        health = self.request('GET', '/global/health', timeout=1)
                        if health.get('healthy') and health.get('version') == ENGINE_VERSION:
                            # Health alone is available before plugin/provider initialization.
                            catalog = self.request('GET', '/provider', timeout=max(.1, min(15, deadline-time.monotonic())))
                            if not isinstance(catalog, dict) or not catalog.get('all'):
                                continue
                            self._state = 'ready'
                            return self.status()
                    except (requests.RequestException, AgentRuntimeError):
                        pass
                    time.sleep(.2)
                raise AgentRuntimeError('engine_timeout', 'OpenCode 启动超时，请重试；设备任务未启动')
            except Exception as exc:
                self.stop()
                self._state = 'failed'
                self._code = getattr(exc, 'code', 'engine_start_failed')
                self._error = str(exc) if isinstance(exc, AgentRuntimeError) else '对话引擎启动失败，请查看诊断并重试'
                raise AgentRuntimeError(self._code, self._error) from None

    def request(self, method, path, body=None, *, timeout=15):
        if not path.startswith('/') or path.startswith('//') or '..' in path or '\\' in path:
            raise ValueError('Invalid internal Agent path')
        if not self._port:
            raise AgentRuntimeError('engine_not_started', '请先连接对话引擎')
        with requests.Session() as http:
            http.trust_env = False
            response = http.request(method, f'http://127.0.0.1:{self._port}' + path,
                json=body, auth=('opencode', self._password), timeout=timeout)
            if not response.ok:
                # Raw upstream errors can echo request bodies or credentials.
                raise AgentRuntimeError('engine_request_failed', f'对话引擎请求失败（HTTP {response.status_code}），请查看当前状态后重试')
            return response.json() if response.content else None

    def stop(self):
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            self._process = None
            if self._job:
                self._job.close()
                self._job = None
            if self._log:
                self._log.close()
                self._log = None
            self._state = 'stopped'
            self._lease.close()
