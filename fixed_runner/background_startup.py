"""Import-free-of-business, run-only diagnostics for the windowless host.

Task Scheduler gives pythonw no standard streams. Keep this module restricted
to the standard library so even an application import failure is observable.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import traceback
import uuid


PHASE_MESSAGES = {
    'configuration': '正在读取后台实例配置。',
    'imports': '正在加载后台组件。',
    'database': '正在检查后台数据存储。',
    'supervisor': '正在初始化后台宿主。',
    'mutex': '正在核对后台单实例状态。',
    'signal': '正在准备后台停止通道。',
    'heartbeat': '正在保存后台首个心跳。',
    'running': '后台宿主已启动，服务仍需完成就绪检查。',
    'duplicate': '已有后台宿主，本次未重复启动。',
}
FAILURE_MESSAGES = {
    'configuration': '后台实例配置读取失败，请检查实例配置及启动诊断日志。',
    'imports': '后台组件加载失败，请检查安装完整性和启动诊断日志。',
    'database': '后台数据存储初始化失败，原有数据未被清理，请查看启动诊断日志。',
    'supervisor': '后台宿主初始化失败，请查看启动诊断日志。',
    'mutex': '后台单实例检查失败，请查看启动诊断日志。',
    'signal': '后台停止通道初始化失败，请查看启动诊断日志。',
    'heartbeat': '后台首个心跳保存失败，请检查数据目录及启动诊断日志。',
    'running': '后台宿主启动后异常退出，请查看启动诊断日志。',
}


def _now():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def _is_run(entry_file, argv):
    if not argv or Path(argv[0]).name.casefold() != Path(entry_file).name.casefold():
        return False
    if '-h' in argv[1:] or '--help' in argv[1:]:
        return False
    skip = False
    for value in argv[1:]:
        if skip:
            skip = False
            continue
        if value in {'--instance-profile', '--interval-seconds', '--timeout-seconds', '--update-operation-id'}:
            skip = True
        elif not value.startswith('-'):
            return value == 'run'
    return False


def _local_app_data():
    value = os.environ.get('LOCALAPPDATA')
    return Path(value) if value else Path.home() / 'AppData/Local'


def _fallback_root(instance_id):
    base = _local_app_data() / 'MediaFlow'
    if instance_id != 'production':
        base = base / 'instances' / instance_id
    return base / 'startup-diagnostics'


def _context(entry_file, argv):
    profile_path = os.environ.get('MEDIAFLOW_INSTANCE_PROFILE')
    selected = []
    for index, value in enumerate(argv[1:], 1):
        if value == '--instance-profile':
            if index + 1 >= len(argv) or not argv[index + 1] or argv[index + 1].startswith('--'):
                raise ValueError('missing_profile_argument')
            selected.append(argv[index + 1])
        elif value.startswith('--instance-profile='):
            selected.append(value.split('=', 1)[1])
            if not selected[-1]:
                raise ValueError('missing_profile_argument')
    if len(selected) > 1:
        raise ValueError('duplicate_profile_argument')
    if selected:
        profile_path = selected[0]
    profile = {}
    if profile_path:
        profile = json.loads(Path(profile_path).read_text(encoding='utf-8-sig'))
        if not isinstance(profile, dict) or profile.get('schema', profile.get('schema_version')) != 1:
            raise ValueError('invalid_profile_schema')
    instance_id = str(os.environ.get('MEDIAFLOW_INSTANCE_ID') or profile.get('id') or
                      profile.get('instance_id') or 'production')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', instance_id):
        raise ValueError('invalid_instance_identity')
    app_root = Path(os.environ.get('MEDIAFLOW_APP_ROOT') or os.environ.get('RISKFLOW_APP_ROOT') or
                    profile.get('app_root') or Path(entry_file).resolve().parent.parent).resolve()
    data_value = os.environ.get('MEDIAFLOW_DATA_ROOT') or os.environ.get('RISKFLOW_DATA_ROOT') or profile.get('data_root')
    if not data_value:
        if instance_id != 'production':
            data_value = _fallback_root(instance_id).parent / 'data'
        else:
            development = os.environ.get('MEDIAFLOW_DEVELOPMENT_SOURCE',
                                         '1' if profile.get('development_source') is True else '0') == '1'
            if not development and (app_root / 'runtime/python/python.exe').is_file():
                local = _local_app_data() / 'MediaFlow'
                try:
                    bootstrap = json.loads((local / 'bootstrap.json').read_text(encoding='utf-8-sig'))
                    data_value = bootstrap.get('data_root')
                except (OSError, ValueError, TypeError):
                    pass
                data_value = data_value or local / 'data'
            else:
                data_value = app_root / 'fixed_runner/runtime'
    return instance_id, Path(data_value).expanduser().resolve()


def _failed_configuration_identity(argv):
    """Malformed isolated profiles must never overwrite production diagnostics."""
    explicit = os.environ.get('MEDIAFLOW_INSTANCE_ID')
    if explicit and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', explicit):
        return explicit
    profile_path = os.environ.get('MEDIAFLOW_INSTANCE_PROFILE')
    for index, value in enumerate(argv[1:], 1):
        if value == '--instance-profile' and index + 1 < len(argv):
            profile_path = argv[index + 1]
        elif value.startswith('--instance-profile='):
            profile_path = value.split('=', 1)[1]
    if profile_path:
        try:
            profile = json.loads(Path(profile_path).read_text(encoding='utf-8-sig'))
            identity = str(profile.get('id') or profile.get('instance_id') or '')
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', identity):
                return identity
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        suffix = hashlib.sha256(str(profile_path).encode('utf-8')).hexdigest()[:12]
        return 'unknown-' + suffix
    return 'production'


class StartupDiagnostics:
    def __init__(self, instance_id, data_root):
        self.data_root = Path(data_root)
        self.path = self.data_root / 'background-startup.json'
        self.log_path = self.data_root / 'background-startup.log'
        self.payload = {
            'instance_id': instance_id, 'pid': os.getpid(), 'attempt_id': uuid.uuid4().hex,
            'started_at': _now(), 'updated_at': _now(), 'state': 'starting',
            'phase': 'imports', 'error_code': None, 'message': PHASE_MESSAGES['imports'],
            'log_path': str(self.log_path),
        }

    def _persist(self, details=None):
        self.payload['updated_at'] = _now()
        self.payload['log_path'] = str(self.log_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + self.payload['attempt_id'] + '.tmp')
        try:
            temporary.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        with self.log_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({**self.payload, **(details or {})}, ensure_ascii=False) + '\n')

    def _save(self, details=None):
        try:
            self._persist(details)
        except OSError:
            fallback = _fallback_root(self.payload['instance_id'])
            self.path = fallback / 'background-startup.json'
            self.log_path = fallback / 'background-startup.log'
            self.payload['diagnostic_storage_fallback'] = True
            try:
                self._persist(details)
            except OSError:
                # Diagnostics must not hide the original startup exception.
                pass

    def phase(self, phase):
        self.payload.update(state='starting', phase=phase, error_code=None,
                            message=PHASE_MESSAGES.get(phase, PHASE_MESSAGES['supervisor']))
        self._save()

    def ready(self, *, duplicate=False):
        phase = 'duplicate' if duplicate else 'running'
        self.payload.update(state='ready', phase=phase, error_code=None, message=PHASE_MESSAGES[phase])
        self._save()

    def fail(self, error):
        phase = self.payload['phase']
        if phase not in FAILURE_MESSAGES:
            phase = 'supervisor'
        self.payload.update(state='failed', phase=phase, error_code='background_startup_' + phase + '_failed',
                            message=FAILURE_MESSAGES[phase])
        # Never persist the raw exception body or source line: either may hold a
        # provider response, credentials, or user content.
        frames = [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                  for frame in traceback.extract_tb(error.__traceback__)[-24:]]
        self._save({'error_type': type(error).__name__, 'frames': frames})

    def install_exception_hook(self):
        def handle(_type, error, _traceback):
            self.fail(error)
            if sys.stderr is not None:
                try:
                    sys.stderr.write(json.dumps({'ok': False, 'code': self.payload['error_code'],
                                                 'message': self.payload['message'],
                                                 'phase': self.payload['phase'],
                                                 'log_path': str(self.log_path)}, ensure_ascii=False) + '\n')
                except (OSError, UnicodeError):
                    pass
        sys.excepthook = handle


def begin_startup(entry_file, argv=None):
    argv = list(sys.argv if argv is None else argv)
    if not _is_run(entry_file, argv):
        return None
    try:
        instance_id, data_root = _context(entry_file, argv)
    except (OSError, ValueError, TypeError, KeyError) as error:
        instance_id = _failed_configuration_identity(argv)
        diagnostics = StartupDiagnostics(instance_id, _fallback_root(instance_id))
        diagnostics.payload['phase'] = 'configuration'
        diagnostics.install_exception_hook()
        diagnostics.fail(error)
        raise ValueError(FAILURE_MESSAGES['configuration']) from None
    diagnostics = StartupDiagnostics(instance_id, data_root)
    diagnostics.install_exception_hook()
    diagnostics.phase('imports')
    return diagnostics
