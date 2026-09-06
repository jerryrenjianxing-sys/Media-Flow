"""Standard-library loopback HTTP and durable at-most-once write admission."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

READ_ACTIONS = frozenset(('platform_status', 'list_devices', 'list_tasks', 'plan_status',
    'task_evidence', 'incident_evidence', 'request_status', 'virtual_operation_status',
    'memory_list', 'memory_history', 'repair_list', 'repair_status', 'repair_files',
    'repair_read', 'repair_diff', 'repair_test_status', 'repair_update_status'))


def error(code, message, status='blocked'):
    return {'ok': False, 'status': status, 'reason_code': code, 'user_message': message, 'retryable': False}


def configuration(path=None):
    config_path = Path(path or os.environ.get('MEDIAFLOW_SKILL_CONFIG') or Path(__file__).parent.parent/'config.json')
    config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.is_file() else {}
    raw = os.environ.get('MEDIAFLOW_API_URL') or config.get('api_url') or 'http://127.0.0.1:48138'
    parsed = urllib.parse.urlsplit(raw)
    host = parsed.hostname
    try:
        local = host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if (not local or parsed.scheme != 'http' or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {'', '/', '/api/automation'}):
        raise ValueError('MediaFlow地址必须是本机回环HTTP地址')
    # Avoid DNS/proxy resolution taking a nominally local hostname elsewhere.
    if host == 'localhost':
        raw = 'http://127.0.0.1' + (':' + str(parsed.port) if parsed.port else '')
    url = raw.rstrip('/')
    if not url.endswith('/api/automation'):
        url += '/api/automation'
    receipt_path = Path(os.environ.get('MEDIAFLOW_RECEIPT_DB') or config.get('receipt_db') or
        Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')/'MediaFlow/skill/receipts.db')
    return url, receipt_path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(url, body, timeout):
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except (ValueError, UnicodeError):
            payload = error('http_error', '本机接口返回HTTP错误；请查询原操作回执')
        return exc.code, payload


def invoke(url, receipt_path, body, timeout=30):
    write = body['action'] not in READ_ACTIONS
    request_id = body.get('request_id')
    if write and not request_id:
        return error('request_id_required', '写请求需要稳定唯一request_id')
    db = None
    unknown = error('result_unknown', '请求结果未确认；使用request_status查询原request_id，并查询原计划/操作；不要重发写入', 'unknown')
    try:
        if write:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(receipt_path, timeout=15)
            db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT)')
            fingerprint = hashlib.sha256(json.dumps([url, body], ensure_ascii=False, sort_keys=True,
                allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            with db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT fingerprint,result FROM receipts WHERE id=?', (request_id,)).fetchone()
                if old:
                    if old[0] != fingerprint:
                        return error('request_id_conflict', '请求编号已绑定不同参数；未发送')
                    return json.loads(old[1]) if old[1] else unknown
                db.execute('INSERT INTO receipts VALUES(?,?,NULL)', (request_id, fingerprint))
        result = unknown
        for attempt in range(1 if write else 3):
            try:
                status, result = send(url, body, timeout)
                if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
                    result = unknown if write else error('invalid_response', '接口响应不是有效业务回执')
                    if write:
                        break
                elif status < 500 and status != 429:
                    break
                elif write:
                    # Server errors can occur after dispatch. Preserve this uncertainty locally.
                    result = unknown
                    break
            except (OSError, ValueError, urllib.error.URLError):
                result = unknown if write else error('read_failed', '本机接口读取失败；检查服务地址和状态')
            if not write and attempt < 2:
                time.sleep(0.1 * (attempt + 1))
        if write:
            with db:
                db.execute('UPDATE receipts SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), request_id))
        return result
    finally:
        if db is not None:
            db.close()
