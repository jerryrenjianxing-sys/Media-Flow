"""Standard-library loopback HTTP and durable at-most-once write admission."""
import hashlib
import http.client
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
    'repair_read', 'repair_diff', 'repair_test_status', 'repair_update_status',
    'content_plan_list', 'content_plan_get', 'preset_list', 'model_status',
    'notification_list'))


def error(code, message, status='blocked'):
    return {'ok': False, 'status': status, 'reason_code': code, 'user_message': message, 'retryable': False}


class ConnectionFailure(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def automation_url(raw):
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
    return url


def configuration(path=None, connection=None):
    config_path = Path(path or os.environ.get('MEDIAFLOW_SKILL_CONFIG') or Path(__file__).parent.parent/'config.json')
    # An explicit connection replaces legacy address/runtime configuration, not receipts.
    config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.is_file() else {}
    if not isinstance(config, dict):
        raise ValueError('Skill config must be an object')
    url = automation_url(connection['api_url'] if connection else (
        os.environ.get('MEDIAFLOW_API_URL') or config.get('api_url') or 'http://127.0.0.1:48138'))
    receipt_path = Path(os.environ.get('MEDIAFLOW_RECEIPT_DB') or config.get('receipt_db') or
        Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')/'MediaFlow/skill/receipts.db')
    return url, receipt_path


def load_connection(path):
    location = Path(path)
    if not location.is_file():
        raise ConnectionFailure('missing_connection_file', '连接资料文件不存在；请重新复制连接说明或打开已安装的软件，不搜索全盘或猜测路径')
    try:
        if location.stat().st_size > 65536:
            raise ValueError('oversized connection')
        connection = json.loads(location.read_text(encoding='utf-8-sig'))
        if not isinstance(connection, dict) or connection.get('schema') != 1 or connection.get('product') != 'MediaFlow':
            raise ValueError('unsupported connection')
        for key in ('instance_id', 'data_root', 'app_root', 'api_url', 'skill_path', 'client_path', 'python'):
            if not isinstance(connection.get(key), str) or not connection[key].strip():
                raise ValueError('incomplete connection')
        for key in ('data_root', 'app_root', 'skill_path', 'client_path', 'python'):
            if not Path(connection[key]).is_absolute():
                raise ValueError('relative path')
    except (OSError, ValueError, TypeError):
        raise ConnectionFailure('invalid_connection_file', '连接资料损坏或不完整；请重新复制连接说明或打开已安装的软件') from None
    if (not Path(connection['app_root']).is_dir() or not Path(connection['skill_path']).is_file()
            or not Path(connection['client_path']).is_file()):
        raise ConnectionFailure('stale_connection', '连接资料中的程序或Skill路径已失效；请重新复制连接说明，不要重建脚本')
    try:
        automation_url(connection['api_url'])
    except (ValueError, TypeError):
        raise ConnectionFailure('invalid_address', '连接地址不是本机回环HTTP地址；请重新复制当前软件的连接说明') from None
    return connection


def verify_connection(connection):
    """One bounded read validates identity before allowing a business request."""
    url = urllib.parse.urlsplit(automation_url(connection['api_url']))
    deadline = time.monotonic() + 5
    client = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
    try:
        client.connect()
        transport = client.sock
        client.sock.settimeout(max(.001, deadline - time.monotonic()))
        client.request('GET', url.path, headers={'Accept': 'application/json'})
        response = client.getresponse()
        if response.status != 200:
            raise ConnectionFailure('unexpected_service', '地址未返回支持的MediaFlow接口；请重新复制当前软件的连接说明')
        data = bytearray()
        while not response.isclosed():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            # Keep the transport reference when HTTP/1.0 hands it to response.
            transport.settimeout(remaining)
            chunk = response.read1(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 1048576:
                raise ValueError('oversized response')
        info = json.loads(data)
    except TimeoutError:
        raise ConnectionFailure('connection_timeout', '5秒内未完成连接检查；尚不能确定服务未启动，请先询问用户') from None
    except (OSError, http.client.HTTPException):
        raise ConnectionFailure('connection_failed', '暂时无法连接平台，不能据此认定服务未启动；请先询问用户，不自动启动后台') from None
    except (ValueError, TypeError):
        raise ConnectionFailure('unexpected_service', '响应不是支持的MediaFlow接口；请检查连接说明，不重复启动服务') from None
    finally:
        client.close()
    if not isinstance(info, dict) or info.get('product') != 'MediaFlow' or str(info.get('api_version')) != '1' or info.get('status') != 'available':
        raise ConnectionFailure('unexpected_service', '响应不是支持的MediaFlow接口；请重新复制连接说明')
    actual = info.get('connection')
    same_path = lambda left, right: isinstance(left, str) and isinstance(right, str) and (
        os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right)))
    if (not isinstance(actual, dict) or actual.get('schema') != 1 or actual.get('product') != 'MediaFlow'
            or actual.get('instance_id') != connection['instance_id']
            or not same_path(actual.get('data_root'), connection['data_root'])):
        raise ConnectionFailure('wrong_instance', '该地址属于不同MediaFlow实例；未发送业务操作，请重新复制当前软件的连接说明')
    if any(not same_path(actual.get(key), connection[key]) for key in ('app_root', 'skill_path', 'client_path', 'python')):
        raise ConnectionFailure('stale_connection', '程序已升级或连接资料过期；请重新复制连接说明，不猜测新路径')
    version, actual_version = connection.get('version'), actual.get('version')
    if isinstance(version, dict) and any(value and (not isinstance(actual_version, dict) or actual_version.get(key) != value)
            for key, value in version.items() if key in {'version', 'source_revision', 'build_identity'}):
        raise ConnectionFailure('stale_connection', '连接资料版本与运行程序不一致；请重新复制当前软件的连接说明')
    return info


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
            except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
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
