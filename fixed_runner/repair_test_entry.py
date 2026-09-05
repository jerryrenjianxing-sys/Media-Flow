"""Trusted child entry for Python-only repair checks, not a general terminal.

Audit checks are process-level Python guards, not an OS/container sandbox.
Native extensions, subprocesses and network are unavailable to repair tests.
Tests requiring those capabilities must be run in the release test environment.
"""
import ast
import json
import os
from pathlib import Path
import sys
import unittest


def main():
    workspace, mode, target = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
    test_path = (workspace / target).resolve()
    if not test_path.is_relative_to(workspace) or test_path.suffix != '.py':
        raise ValueError('Invalid repair test path')
    readonly = [Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()]
    # Runtime layout redirects every standard data root into the repair scratch.
    scratch = workspace / '_test_scratch'
    scratch.mkdir(exist_ok=True)
    os.environ.update(MEDIAFLOW_APP_ROOT=str(workspace), MEDIAFLOW_DATA_ROOT=str(scratch),
                      LOCALAPPDATA=str(scratch), USERPROFILE=str(scratch), TEMP=str(scratch), TMP=str(scratch))
    os.chdir(workspace)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(workspace / 'fixed_runner'))
    sys.path.insert(0, str(test_path.parent))

    def audit(event, args):
        if event.startswith(('subprocess.', 'socket.', 'ctypes.', 'winreg.')) or event in {'os.system', 'os.startfile', 'os.exec', 'os.spawn', 'os.posix_spawn'}:
            raise PermissionError('Repair tests cannot access network, native commands or registry')
        if event == 'import' and args[0] in {'ctypes', '_ctypes', 'winreg', '_winapi'}:
            raise PermissionError('Native interfaces require separate release validation')
        if event == 'open' and not isinstance(args[0], int):
            path = Path(os.fsdecode(args[0])).resolve()
            flags = args[2] or 0
            write = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if write and not path.is_relative_to(scratch):
                raise PermissionError('Repair tests may only write test scratch')
            if not path.is_relative_to(workspace) and not any(path.is_relative_to(root) for root in readonly):
                raise PermissionError('Repair test file is outside source/runtime roots')
        if event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.rename', 'os.link', 'os.symlink', 'os.chmod', 'os.truncate'}:
            paths = args[:2] if event in {'os.rename', 'os.link', 'os.symlink'} else args[:1]
            for value in paths:
                if isinstance(value, (str, bytes)) and not Path(os.fsdecode(value)).resolve().is_relative_to(scratch):
                    raise PermissionError('Repair tests may only mutate scratch')
    sys.addaudithook(audit)
    if mode == 'syntax':
        ast.parse(test_path.read_text(encoding='utf-8-sig'), filename=target)
        print(json.dumps({'ok': True, 'check': 'python_syntax', 'target': target}))
        return 0
    if mode != 'unit' or not test_path.name.startswith('test_'):
        raise ValueError('Select syntax or a Python unit-test file')
    suite = unittest.defaultTestLoader.discover(str(test_path.parent), pattern=test_path.name)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return int(not result.wasSuccessful() or result.testsRun == 0)


if __name__ == '__main__':
    raise SystemExit(main())
