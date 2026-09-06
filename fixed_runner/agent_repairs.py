"""Editable candidate workspaces with immutable baseline and bounded test receipts."""
from contextlib import contextmanager
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from agent_platform import bounded_diagnostic
from agent_process import ChildJob
from repair_materials import build_bundle, read_bundle, source_path, SECRET, development_revision
from runtime_layout import APP_ROOT, BUNDLED_PYTHON, IS_DISTRIBUTION


def sha(data):
    return hashlib.sha256(data).hexdigest()


class AgentRepairs:
    def __init__(self, root, *, bundle=None, source_root=APP_ROOT):
        self.root = Path(root).resolve()
        self.bundle = Path(bundle or APP_ROOT / 'assets/agent/repair-source.zip')
        self.explicit_bundle = bundle is not None
        self.source_root = Path(source_root)
        self.lock = threading.RLock()
        self.cancellations = {}
        with self.database() as db:
            db.execute("UPDATE tests SET state='interrupted',finished=?,output='后台重启，测试未自动重放；请显式重新检查' WHERE state IN ('queued','running')", (time.time(),))
            db.execute("UPDATE repairs SET state='failed',error='材料准备中断，请新建工作区；原现场保留' WHERE state='creating'")

    @contextmanager
    def database(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'repairs.db', timeout=5)
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE IF NOT EXISTS repairs(id TEXT PRIMARY KEY, session_id TEXT, call_id TEXT UNIQUE,
            purpose TEXT, state TEXT, revision TEXT, manifest TEXT, created REAL, error TEXT);
            CREATE TABLE IF NOT EXISTS edits(id TEXT PRIMARY KEY, repair_id TEXT, path TEXT, before_hash TEXT, after_hash TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS tests(id TEXT PRIMARY KEY, repair_id TEXT, call_id TEXT UNIQUE, mode TEXT, target TEXT,
            source_hash TEXT, state TEXT, started REAL, finished REAL, exit_code INTEGER, output TEXT);''')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, args, context):
        purpose = str(args.get('purpose') or '').strip()
        if not purpose or len(purpose) > 2000 or SECRET.search(purpose.encode()):
            raise ValueError('请描述需要修复的问题，不包含凭证')
        with self.lock, self.database() as db:
            old = db.execute('SELECT id FROM repairs WHERE call_id=?', (context['call_id'],)).fetchone()
            if old:
                return self.get(old['id'], context['session_id'])
            if db.execute("SELECT count(*) FROM repairs WHERE state='ready'").fetchone()[0] >= 10:
                raise ValueError('已有10个修复工作区，请先关闭不再使用的候选')
            repair_id = uuid.uuid4().hex
            db.execute('INSERT INTO repairs VALUES(?,?,?,?,?,?,?,?,NULL)',
                       (repair_id, context['session_id'], context['call_id'], purpose, 'creating', '', '{}', time.time()))
        try:
            bundle = self.bundle
            if not bundle.is_file() or (not IS_DISTRIBUTION and not self.explicit_bundle):
                if IS_DISTRIBUTION:
                    raise ValueError('安装包缺少修复源码，请修复安装；不会下载不明源码')
                bundle = self.root / repair_id / 'development-source.zip'
                build_bundle(self.source_root, bundle, development_revision(self.source_root))
            manifest, files = read_bundle(bundle)
            base = self.root / repair_id
            for name, data in files.items():
                for area in ('baseline', 'workspace'):
                    path = base / area / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            with self.database() as db:
                db.execute("UPDATE repairs SET state='ready',revision=?,manifest=? WHERE id=?",
                           (manifest['source_revision'], json.dumps(manifest, sort_keys=True), repair_id))
        except Exception as exc:
            with self.database() as db:
                db.execute("UPDATE repairs SET state='failed',error=? WHERE id=?", (bounded_diagnostic(exc), repair_id))
        return self.get(repair_id, context['session_id'])

    def get(self, repair_id, session_id):
        with self.database() as db:
            row = db.execute('SELECT * FROM repairs WHERE id=? AND session_id=?', (repair_id, session_id)).fetchone()
        if not row:
            raise ValueError('修复工作区不存在或不属于当前会话')
        return {k: row[k] for k in ('id', 'purpose', 'state', 'revision', 'created', 'error')}

    def list(self, session_id):
        with self.database() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM repairs WHERE session_id=? ORDER BY created DESC LIMIT 30', (session_id,))]
        return [self.get(i, session_id) for i in ids]

    def workspace(self, repair_id, session_id):
        if self.get(repair_id, session_id)['state'] != 'ready':
            raise ValueError('此修复工作区未就绪或已关闭')
        return self.root / repair_id / 'workspace'

    @staticmethod
    def file(root, name):
        path = root / source_path(name)
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('修复路径指向工作区之外')
        return path

    def files(self, repair_id, session_id):
        root = self.workspace(repair_id, session_id)
        from repair_materials import EXCLUDED
        files = []
        for directory, children, names in os.walk(root, followlinks=False):
            children[:] = [name for name in children if name.lower() not in EXCLUDED and name != '_test_scratch' and (not name.startswith('.') or name == '.openai')]
            for name in names:
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                try:
                    source_path(relative)
                except ValueError:
                    continue
                if path.is_file():
                    files.append(relative)
        return {'files': sorted(files)[:5000]}

    def tests(self, repair_id, session_id):
        self.get(repair_id, session_id)
        with self.database() as db:
            return [dict(row) for row in db.execute('SELECT * FROM tests WHERE repair_id=? ORDER BY started DESC LIMIT 20', (repair_id,))]

    def read(self, args, session_id):
        root = self.workspace(args['repair_id'], session_id)
        path = self.file(root, args.get('path'))
        if not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError('文件不存在或过大')
        data = path.read_bytes()
        offset, limit = args.get('offset', 0), args.get('limit', 200)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 300:
            raise ValueError('请指定有效分页范围')
        lines = data.decode('utf-8-sig').splitlines()
        return {'path': args['path'], 'sha256': sha(data), 'lines': lines[offset:offset+limit], 'total_lines': len(lines), 'offset': offset}

    def assert_not_testing(self, db, repair_id):
        active = db.execute("SELECT 1 FROM tests WHERE repair_id=? AND state IN ('queued','running')", (repair_id,)).fetchone()
        if active:
            raise ValueError('测试正在读取此版本，请测试收口后再编辑')

    def edit(self, args, session_id, *, revert=False):
        with self.lock:
            root = self.workspace(args['repair_id'], session_id)
            path = self.file(root, args.get('path'))
            with self.database() as db:
                self.assert_not_testing(db, args['repair_id'])
            old = path.read_bytes() if path.is_file() else b''
            if args.get('expected_sha256') != sha(old):
                raise ValueError('文件已变化，请重新读取后编辑')
            if revert:
                baseline = self.file(self.root / args['repair_id'] / 'baseline', args['path'])
                data = baseline.read_bytes() if baseline.is_file() else None
            else:
                if not isinstance(args.get('content'), str) or len(args['content'].encode()) > 400_000:
                    raise ValueError('请提供不超过400KB的文本内容')
                data = args['content'].encode()
                if SECRET.search(data):
                    raise ValueError('修复文件不能包含凭证')
                total = sum((root / name).stat().st_size for name in self.files(args['repair_id'], session_id)['files'])
                if total - len(old) + len(data) > 100_000_000:
                    raise ValueError('修复工作区超过100MB，请缩小修改范围')
            if data is None:
                if path.is_file():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.edit-tmp')
                temporary.write_bytes(data)
                temporary.replace(path)
            with self.database() as db:
                db.execute('INSERT INTO edits VALUES(?,?,?,?,?,?)', (uuid.uuid4().hex, args['repair_id'], args['path'], sha(old), sha(data or b''), time.time()))
            return {'status': 'completed', 'path': args['path'], 'sha256': sha(data or b''), 'message': '仅修改候选工作区，运行程序没有改变'}

    def diff(self, repair_id, session_id):
        root = self.workspace(repair_id, session_id)
        baseline = self.root / repair_id / 'baseline'
        names = set(self.files(repair_id, session_id)['files']) | {p.relative_to(baseline).as_posix() for p in baseline.rglob('*') if p.is_file()}
        patches, changed, hashes = [], [], {}
        for name in sorted(names):
            new = self.file(root, name)
            old = self.file(baseline, name)
            a, b = old.read_bytes() if old.is_file() else b'', new.read_bytes() if new.is_file() else b''
            hashes[name] = sha(b)
            if a != b:
                changed.append(name)
                patches.extend(difflib.unified_diff(a.decode('utf-8-sig').splitlines(True), b.decode('utf-8-sig').splitlines(True), fromfile='a/'+name, tofile='b/'+name))
        return {'changed_files': changed, 'source_hash': sha(json.dumps(hashes, sort_keys=True).encode()),
                'patch': ''.join(patches), 'revision': self.get(repair_id, session_id)['revision']}

    def remove(self, args, session_id):
        with self.lock:
            root = self.workspace(args['repair_id'], session_id)
            path = self.file(root, args.get('path'))
            with self.database() as db:
                self.assert_not_testing(db, args['repair_id'])
            if not path.is_file() or sha(path.read_bytes()) != args.get('expected_sha256'):
                raise ValueError('候选文件已变化，请重新读取；未删除')
            before = sha(path.read_bytes())
            path.unlink()
            with self.database() as db:
                db.execute('INSERT INTO edits VALUES(?,?,?,?,?,?)', (uuid.uuid4().hex, args['repair_id'], args['path'], before, sha(b''), time.time()))
            return {'status': 'completed', 'message': '仅移除候选文件，基准保留，可用repair_revert恢复'}

    def freeze(self, repair_id, session_id, destination, expected_hash):
        with self.lock:
            diff = self.diff(repair_id, session_id)
            if diff['source_hash'] != expected_hash:
                raise ValueError('修复正在变化，请重新展示当前版本')
            root = self.workspace(repair_id, session_id)
            files = {}
            for name in diff['changed_files']:
                path = self.file(root, name)
                files[name] = path.read_bytes().decode('utf-8') if path.is_file() else None
            payload = {'source_hash': expected_hash, 'files': files}
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            temporary.replace(destination)

    def tool(self, name, args, context):
        if name == 'repair_delete':
            return self.remove(args, context['session_id'])
        session_id = context['session_id']
        if name == 'repair_create':
            return self.create(args, context)
        if name == 'repair_test_status':
            return self.test_status(args.get('test_id'), session_id)
        repair_id = args.get('repair_id')
        if name == 'repair_files':
            return self.files(repair_id, session_id)
        if name == 'repair_read':
            return self.read(args, session_id)
        if name in {'repair_edit', 'repair_revert'}:
            return self.edit(args, session_id, revert=name == 'repair_revert')
        if name == 'repair_test':
            return self.test(args, context)
        if name == 'repair_diff':
            result = self.diff(repair_id, session_id)
            return {**result, 'patch': result['patch'][:30000], 'truncated': len(result['patch']) > 30000}
        if name == 'repair_export':
            return self.export(repair_id, session_id)
        raise ValueError('不支持此修复操作')

    def test(self, args, context):
        repair_id, session_id = args.get('repair_id'), context['session_id']
        with self.lock:
            root = self.workspace(repair_id, session_id)
            target = source_path(args.get('path') or 'fixed_runner/test_agent_permissions.py')
            mode = args.get('mode', 'unit')
            if mode not in {'unit', 'syntax', 'python', 'frontend', 'lint', 'build', 'openspec', 'all'}:
                raise ValueError('请选择受支持的测试或构建类型')
            if mode in {'unit', 'syntax'} and (not target.endswith('.py') or not self.file(root, target).is_file()):
                raise ValueError('请指定Python测试或源码文件')
            if mode not in {'unit', 'syntax'} and IS_DISTRIBUTION:
                raise ValueError('安装版完整构建环境尚未验收；可使用单文件测试和导出，不能声称已应用')
            with self.database() as db:
                old = db.execute('SELECT id FROM tests WHERE call_id=?', (context['call_id'],)).fetchone()
                if old:
                    return self.test_status(old['id'], session_id)
                self.assert_not_testing(db, repair_id)
                test_id = uuid.uuid4().hex
                fingerprint = self.diff(repair_id, session_id)['source_hash']
                db.execute('INSERT INTO tests VALUES(?,?,?,?,?,?,?, ?,NULL,NULL,NULL)',
                           (test_id, repair_id, context['call_id'], mode, target, fingerprint, 'queued', time.time()))
            self.cancellations[test_id] = threading.Event()
            try:
                threading.Thread(target=self._run_test, args=(test_id, root, mode, target), daemon=True, name='repair-test-'+test_id[:8]).start()
            except Exception:
                self.cancellations.pop(test_id, None)
                with self.database() as db:
                    db.execute("UPDATE tests SET state='failed',finished=?,output='测试执行者启动失败，请重新发起测试' WHERE id=?", (time.time(), test_id))
        return self.test_status(test_id, session_id)

    def cancel_session(self, session_id, repair_id=None):
        with self.lock, self.database() as db:
            rows = db.execute("SELECT t.id FROM tests t JOIN repairs r ON r.id=t.repair_id WHERE r.session_id=? AND (? IS NULL OR r.id=?) AND t.state IN ('queued','running')", (session_id, repair_id, repair_id)).fetchall()
            for row in rows:
                event = self.cancellations.get(row['id'])
                if event:
                    event.set()
        return {'status': 'cancelling', 'message': '已请求停止测试，最多等待当前检查点收口；工作区和测试记录保留', 'count': len(rows)}

    def cancel_all(self):
        with self.lock:
            for event in self.cancellations.values():
                event.set()

    def _run_test(self, test_id, root, mode, target):
        log = self.root / ('test-' + test_id + '.log')
        process, job = None, None
        code, state, output = None, 'failed', ''
        try:
            python = BUNDLED_PYTHON if BUNDLED_PYTHON.is_file() else Path(sys.executable)
            scratch = root / '_test_scratch'
            scratch.mkdir(exist_ok=True)
            env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'COMSPEC'}}
            if mode not in {'unit', 'syntax'}:
                env['PATH'] = os.environ.get('PATH', '')
            env.update(TEMP=str(scratch), TMP=str(scratch), PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
            with self.database() as db:
                db.execute("UPDATE tests SET state='running' WHERE id=?", (test_id,))
            with log.open('wb') as stream:
                job = ChildJob()
                command = [str(python), '-I', str(Path(__file__).with_name('repair_test_entry.py')), str(root), mode, target]
                if mode not in {'unit', 'syntax'}:
                    command = [str(python), str(Path(__file__).with_name('repair_validation.py')), str(root), str(self.source_root), mode]
                process = subprocess.Popen(command,
                    cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                job.assign(process)
                deadline = time.monotonic() + (90 if mode in {'unit', 'syntax'} else 900)
                while process.poll() is None:
                    if self.cancellations[test_id].is_set():
                        process.kill()
                        state = 'cancelled'
                        break
                    if time.monotonic() >= deadline or log.stat().st_size > 2_000_000:
                        process.kill()
                        state = 'timeout'
                        break
                    time.sleep(.1)
                code = process.wait(timeout=5)
            if state not in {'timeout', 'cancelled'}:
                state = 'passed' if code == 0 else 'failed'
            with log.open('rb') as stream:
                output = stream.read(40_000).decode('utf-8', errors='replace')
            output = re.sub(r'[A-Za-z]:[\\/][^\s\"\'<>]+', '[工作区路径]', output)
            output = SECRET.sub(b'[credential hidden]', output.encode()).decode()
        except Exception as exc:
            output = bounded_diagnostic(exc)
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if job:
                job.close()
            with self.database() as db:
                db.execute('UPDATE tests SET state=?,finished=?,exit_code=?,output=? WHERE id=?', (state, time.time(), code, output, test_id))
            with self.lock:
                self.cancellations.pop(test_id, None)

    def test_status(self, test_id, session_id):
        with self.database() as db:
            row = db.execute('SELECT t.* FROM tests t JOIN repairs r ON r.id=t.repair_id WHERE t.id=? AND r.session_id=?', (test_id, session_id)).fetchone()
        if not row:
            raise ValueError('测试不存在或不属于当前会话')
        return dict(row)

    def close(self, repair_id, session_id):
        self.get(repair_id, session_id)
        with self.lock, self.database() as db:
            self.assert_not_testing(db, repair_id)
            db.execute("UPDATE repairs SET state='closed' WHERE id=?", (repair_id,))
        return {'status': 'completed', 'message': '候选已关闭，材料和测试记录保留，未改变运行程序'}

    def export(self, repair_id, session_id):
        with self.lock:
            diff = self.diff(repair_id, session_id)
            with self.database() as db:
                self.assert_not_testing(db, repair_id)
                tests = [dict(r) for r in db.execute("SELECT id,mode,target,state,source_hash,exit_code FROM tests WHERE repair_id=? AND state='passed' AND source_hash=?", (repair_id, diff['source_hash']))]
            if not diff['changed_files']:
                raise ValueError('候选没有代码变更')
            if len(diff['patch'].encode()) > 10_000_000:
                raise ValueError('候选补丁超过10MB，请拆分为较小修复')
            if not any(t['mode'] in {'unit', 'python', 'all'} for t in tests):
                raise ValueError('当前候选尚无通过的单元测试，请先运行测试；语法通过不能当成功能修复')
            output = self.root / repair_id / 'candidate.patch'
            output.write_text(diff['patch'], encoding='utf-8')
            receipt = {**{k: diff[k] for k in ('changed_files', 'source_hash', 'revision')}, 'tests': tests,
                       'patch_sha256': sha(output.read_bytes()), 'status': 'completed', 'artifact_status': 'candidate_only',
                       'message': '补丁仅为候选，需合并到唯一发布源、递增版本并验收；未热更新运行软件'}
            (output.parent / 'candidate.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
            return {**receipt, 'download_url': f'/api/agent/sessions/{session_id}/repairs/{repair_id}/patch'}
