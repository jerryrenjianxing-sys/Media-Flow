"""Single persisted synthetic health probe shared by all device workers."""
import hashlib
import json
from pathlib import Path
import tempfile
import time


def model_configuration_key():
    from model_providers import selection
    selected = selection()
    # Do not persist credential references or plaintext in the task database.
    fields = {key: selected.get(key) for key in ('provider', 'revision', 'active_contract')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def synthetic_probe():
    from PIL import Image, ImageDraw
    from comment_ai import analyze_topic
    with tempfile.TemporaryDirectory(prefix='mediaflow-model-probe-') as directory:
        path = Path(directory) / 'synthetic.png'
        picture = Image.new('RGB', (320, 200), 'white')
        ImageDraw.Draw(picture).rectangle((30, 30, 120, 160), fill='red')
        picture.save(path)
        decision = analyze_topic(path, '合成测试：白色背景上的红色矩形。命中必须看到红色矩形；没有工厂或人物。')
        if not decision.matches:
            from model_errors import CloudModelError
            raise CloudModelError('invalid_response', '合成健康检查未正确识别红色矩形')
    return True


def service_model_wait(store, wait, *, probe=synthetic_probe, now=None):
    """No device object is accepted: waiting cannot perform Android actions."""
    now = time.time() if now is None else now
    task_id, device = wait['id'], wait['device_id']
    if store.is_stop_requested(device) or store.agent_task_stop_requested(task_id):
        return False
    if store.is_paused() and not store.agent_task_may_run_paused(task_id):
        return False
    if (store.get_profile('automation-stop') or {}).get('stopped') or store.has_active_control_session(device):
        return False
    key = model_configuration_key()
    if wait['status'] == 'waiting_user' and (not wait['model_key'] or wait['model_key'] == key):
        return False
    if wait['status'] not in ('waiting_model', 'waiting_user'):
        return False
    if wait['model_key'] != key:
        with store.connection() as db:
            db.execute("UPDATE task_waits SET model_key=?,reason_code='configuration_changed' WHERE task_id=?", (key, task_id))
            db.execute("UPDATE tasks SET status='waiting_model' WHERE id=? AND status IN ('waiting_model','waiting_user')", (task_id,))
            db.execute('INSERT OR IGNORE INTO model_recovery_probes VALUES(?,0,?,NULL,0,NULL,?)', (key, now, 'configuration_changed'))
    record = store.get_model_probe(key)
    if record and record.get('success_at') and record['success_at'] >= wait['updated_at']:
        # Device order is stable across processes/restarts; no herd resumption.
        # Hash-based fixed stagger doesn't shift when earlier peers resume.
        delay = int(hashlib.sha256(device.encode()).hexdigest()[:4], 16) % 7
        if now >= record['success_at'] + delay:
            return store.resume_waiting_task(task_id, model_key=key, now=now)
        return False
    token = store.claim_model_probe(key, now=now)
    if not token:
        return False
    try:
        probe()
    except Exception as exc:
        kind = getattr(exc, 'kind', 'transient_network')
        permanent = kind in {'authentication', 'balance', 'permanent_rejection', 'invalid_request'}
        store.finish_model_probe(key, token, success=False, reason=kind, permanent=permanent)
    else:
        if model_configuration_key() != key:
            store.finish_model_probe(key, token, success=False, reason='configuration_changed')
        else:
            store.finish_model_probe(key, token, success=True)
    return False
