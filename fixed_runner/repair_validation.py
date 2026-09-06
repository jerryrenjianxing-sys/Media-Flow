"""Fixed local-development validation commands; never a model-provided shell.

Runs explicitly authorized first-party project code with scratch runtime roots.
This is not an OS sandbox. Native build tools are required for complete builds.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from repair_dependencies import prepare


def validation_commands(workspace, source, mode, env=None):
    python = source / '.venv/Scripts/python.exe'
    node = source / 'runtime/node/node.exe'
    if not node.is_file():
        node = Path(shutil.which('node') or '')
    if not python.is_file() or not node.is_file():
        raise ValueError('本机完整验证缺少Python或Node运行时，请完成项目环境安装')
    python = prepare(workspace, source, node, env or os.environ.copy())
    console = workspace / 'control_console'
    modules = console / 'node_modules'
    if mode in {'frontend', 'lint', 'build', 'all'}:
        source_modules = source / 'control_console/node_modules'
        if not source_modules.is_dir():
            raise ValueError('缺少本机前端依赖，请完成项目依赖准备')
        # Copy rather than link: dependency changes may never mutate live modules.
        if not modules.exists():
            shutil.copytree(source_modules, modules, symlinks=False)
    specs = shutil.which('openspec.cmd') or shutil.which('openspec')
    if not specs and mode in {'openspec', 'all'}:
        raise ValueError('本机缺少OpenSpec工具，请完成项目环境安装')
    commands = {
        'python': [(workspace, [str(python), 'scripts/test-python.py'])],
        'build': [(console, [str(node), str(modules / 'vinext/dist/cli.js'), 'build'])],
        'frontend': [(console, [str(node), '--test', 'tests/rendered-html.test.mjs', 'tests/agent-state.test.mjs'])],
        'lint': [(console, [str(node), str(modules / 'eslint/bin/eslint.js'), '.', '--ignore-pattern', 'dist', '--ignore-pattern', '.next'])],
        'openspec': [(workspace, [str(node), str(Path(specs).parent / 'node_modules/@fission-ai/openspec/bin/openspec.js'), 'validate', '--all', '--strict'])] if specs else [],
    }
    order = ['openspec', 'python', 'lint', 'build', 'frontend'] if mode == 'all' else ['build', 'frontend'] if mode == 'frontend' else [mode]
    return [item for name in order for item in commands[name]]


def main():
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('workspace', type=Path)
    parser.add_argument('source', type=Path)
    parser.add_argument('mode', choices=['python', 'frontend', 'lint', 'build', 'openspec', 'all'])
    args = parser.parse_args()
    workspace, source = args.workspace.resolve(), args.source.resolve()
    if workspace == source or not (workspace / 'fixed_runner').is_dir():
        raise ValueError('验证只允许独立修复工作区')
    # Exact, local-only regression resources; never bundle production evidence.
    fixtures = ['work/autoglm-handoff-' + name + '.png' for name in
                ('like-verify', 'favorite-verify', 'comment-verify', 'video4')]
    fixtures += ['device_stream_host/vendor/scrcpy-server-v3.3.3']
    for name in fixtures:
        original, target = source / name, workspace / name
        if not original.is_file():
            raise ValueError('完整本机验证缺少已登记测试材料：' + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
    scratch = workspace / '_test_scratch'
    scratch.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ('TOKEN', 'KEY', 'SECRET', 'OPENCODE'))}
    # Do not leave a GIT_CONFIG_COUNT after filtering its KEY_n counterparts.
    for key in list(env):
        if key.startswith(('GIT_CONFIG_', 'GIT_INDEX_')) or key in {'GIT_DIR', 'GIT_WORK_TREE'}:
            env.pop(key, None)
    env.update(MEDIAFLOW_DATA_ROOT=str(scratch), RISKFLOW_DATA_ROOT=str(scratch),
               PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    # Source modules resolve their own workspace; do not override subprocess
    # fixture roots. The canonical Python test entry isolates stores pre-import.
    env.pop('MEDIAFLOW_APP_ROOT', None)
    env.pop('RISKFLOW_APP_ROOT', None)
    for cwd, command in validation_commands(workspace, source, args.mode, env):
        print(json.dumps({'stage': command[-1], 'status': 'running'}), flush=True)
        completed = subprocess.run(command, cwd=cwd, env=env, timeout=600, capture_output=True,
                                   text=True, encoding='utf-8', errors='replace',
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        print((completed.stdout + completed.stderr)[-200000:], flush=True)
        print(json.dumps({'stage': command[-1], 'exit_code': completed.returncode}), flush=True)
        if completed.returncode:
            return completed.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
