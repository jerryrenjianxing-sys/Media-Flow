"""Disposable desktop/API fixture, never imports the live data store."""
import os
import sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory(prefix='mediaflow-dev42-')
os.environ['MEDIAFLOW_DATA_ROOT'] = temporary.name
os.environ['RISKFLOW_DATA_ROOT'] = temporary.name
sys.path.insert(0, str(ROOT/'fixed_runner'))
from unittest.mock import patch
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import runtime_layout
runtime_layout.SECRET_ROOT = Path(temporary.name)/'secrets'
runtime_layout.STREAM_SECRET_PATH = runtime_layout.SECRET_ROOT/'device-stream-host.secret'
runtime_layout.BOOTSTRAP_CONFIG_PATH = Path(temporary.name)/'bootstrap.json'
runtime_layout.DEVICE_PROFILE_PATH = Path(temporary.name)/'device_profiles.json'
runtime_layout.PLATFORM_PROFILE_PATH = Path(temporary.name)/'platform_profiles.json'
import control_api as api
from task_store import TaskStore
from control_config import DEFAULT_CONFIG

with ExitStack() as stack:
    # Scanning, device signatures, worker launching and paid models are forbidden.
    for name, value in {'device_statuses': [], 'load_device_profiles': {},
                        'load_device_profile_payloads': {}, 'status_runtime_signature': None}.items():
        stack.enter_context(patch.object(api, name, return_value=value))
    stack.enter_context(patch.object(api, 'ensure_workers', side_effect=AssertionError('No worker in QA')))
    stack.enter_context(patch('model_budget._post_request', side_effect=AssertionError('No paid request in QA')))
    class Handler(api.Handler):
        store = TaskStore(Path(temporary.name)/'runtime/tasks.db')
        def log_message(self, *args):
            pass
    Handler.store.save_profile('default', DEFAULT_CONFIG)
    task_id = Handler.store.submit('douyin_topic_session', 'qa-offline', {
        'resilience_version':'v1','submission_id':'qa','round_index':1,'round_count':1,
        'video_count':20,'dwell_min':8,'dwell_max':25,'content_mode':'general','seed':1,'preview_only':True,
        'topic_filter_enabled':False,'like_probability':0,'favorite_probability':0,'comment_probability':0})
    Handler.store.claim_next('qa-offline', 'qa-worker')
    Handler.store.save_task_checkpoint(task_id, {'summary':{'processed_slots':3,'successful_slots':2,'failed_slots':1}})
    Handler.store.wait_task(task_id, 'waiting_device', 'worker_interrupted')
    Handler.store.set_paused(True)
    server = ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler)
    print('Isolated API ready', server.server_port, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        temporary.cleanup()
