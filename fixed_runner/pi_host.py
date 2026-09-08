"""Windows-task-owned Pi host. It never starts or stops the business platform."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import time

from agent_process import ChildJob, DirectoryLease
from pi_runtime import launch_spec, validate_source, validate_windows_shell
from runtime_control import RuntimeControl


def startup_diagnostic(error):
    """Map known local failures; never persist arbitrary exceptions or credentials."""
    code='pi_host_failed'
    message='Pi独立后台未启动；检查运行资源配置及端口后重试。平台后台未被停止。'
    detail=str(error)
    port=re.match(r'本机端口 (\d{1,5}) 已被占用',detail)
    if port:
        code='pi_port_in_use'
        message=f'Pi端口{port.group(1)}已被占用；请检查占用程序后重试，不会结束其他程序。'
    elif detail.startswith('Pi运行资源缺失或版本不符'):
        code='pi_source_mismatch'
        message='Pi运行资源缺失或版本不符；请重新准备锁定版本后启动。'
    elif detail.startswith('Pi所需Bash运行组件缺失'):
        code='pi_shell_missing'
        message='Pi所需Bash组件缺失；请准备Git Bash后重试，未自动下载工具。'
    elif isinstance(error,FileNotFoundError) or detail.startswith('Pi专用运行资源缺失'):
        code='pi_runtime_missing'
        message='Pi专用运行时或配置文件缺失；请先完成运行环境准备。'
    elif detail.startswith('Pi已运行'):
        code='pi_already_running'
        message='Pi已有运行实例；请检查现有后台状态，未重复启动。'
    return {'reason_code':code,'user_message':message,
            'error_type':type(error).__name__,'updated_at':time.time()}


def _run(args):
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    root=Path(config['root'])
    root.mkdir(parents=True,exist_ok=True)
    return _operate(args,config,root)


def main():
    parser=argparse.ArgumentParser(description='Independent MediaFlow Pi Agent')
    parser.add_argument('action',choices=('run','stop','status','request-start'))
    parser.add_argument('--config',type=Path,default=Path(__file__).resolve().parents[1]/'work/pi-runtime/runtime.json')
    args=parser.parse_args()
    try:
        return _run(args)
    except Exception as error:
        record=startup_diagnostic(error)
        args.config.parent.mkdir(parents=True,exist_ok=True)
        args.config.with_name('last-error.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(record,ensure_ascii=False))
        return 1


def _operate(args,config,root):
    stop=root/'independent-stop'
    control=RuntimeControl(registry_root=root/'independent-processes')
    if args.action=='request-start':
        stop.unlink(missing_ok=True)
        return 0
    if args.action=='stop': stop.touch()
    if args.action!='run':
        print(json.dumps(control.stop('pi-agent') if args.action=='stop' else control.status('pi-agent')))
        return 0
    if stop.exists(): return 0
    validate_source(config['source'])
    for field in ('node','guard','python'):
        if not Path(config[field]).is_file(): raise ValueError(f'Pi专用运行资源缺失：{field}')
    spec=launch_spec(root,config['node'],config['source'],config['guard'],
                     python=config['python'],port=config.get('port',3000),
                     request_limit=config.get('request_limit','unlimited'))
    if os.name=='nt': validate_windows_shell(spec.env)
    from native_console_host import require_available_ports
    require_available_ports([config.get('port',3000)])
    lease=DirectoryLease(root/'engine.lock')
    if not lease.acquire(): raise RuntimeError('Pi已运行，未重复启动。')
    job=ChildJob()
    children=[]
    logs=[]
    def launch(spec):
        log=open(spec.log_path,'ab');logs.append(log)
        child=subprocess.Popen(spec.command,cwd=spec.cwd,env=spec.env,stdin=subprocess.DEVNULL,
                               stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        children.append(child);job.assign(child)
        return child.pid
    try:
        control=RuntimeControl(registry_root=root/'independent-processes',launcher=launch)
        if stop.exists(): return 0
        result=control.start(spec)
        if not result.get('running'): raise RuntimeError('Pi启动失败，请检查独立Agent日志。')
        while children and children[0].poll() is None:
            if stop.exists():
                control.stop('pi-agent')
                break
            time.sleep(.5)
        return 0 if stop.exists() else (children[0].poll() or 0)
    finally:
        job.close()
        for child in children:
            if child.poll() is None: child.terminate()
            child.wait(timeout=10)
        for log in logs: log.close()
        lease.close()


if __name__=='__main__': raise SystemExit(main())
