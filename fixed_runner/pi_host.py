"""Windows-task-owned Pi host. It never starts or stops the business platform."""
import argparse
import json
from pathlib import Path
import subprocess
import time

from agent_process import ChildJob, DirectoryLease
from pi_runtime import launch_spec, validate_source
from runtime_control import RuntimeControl


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
        record={'reason_code':'pi_runtime_missing' if isinstance(error,FileNotFoundError) else 'pi_host_failed',
                'user_message':'Pi独立后台未启动；检查运行资源配置及端口后重试。平台后台未被停止。',
                'error_type':type(error).__name__,'updated_at':time.time()}
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
        result=control.start(launch_spec(root,config['node'],config['source'],config['guard'],
                                        python=config['python'],port=config.get('port',3000)))
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
