## Why

当前 RiskFlow 已能在多台 Android 设备上运行固定流程，但启动脚本仍借用 Mobile Harness 的 Python 环境，Worker PID 记录的是 PowerShell 外壳而非真正持有设备锁的 Python 进程，暂停、停止、取消等待和清空历史也没有清晰区分。这会导致维护时误判“已经停机”、任务状态滞留以及非技术用户无法可靠恢复服务。

## What Changes

- 建立项目独立 Python 虚拟环境、固定依赖和本机自检入口，不再把 Mobile Harness 环境作为正式运行依赖。
- 新增统一运行管理模块和本机脚本，可靠识别本项目的 API、网页与每设备 Worker，支持幂等启动、安全停止、重启和状态检查。
- 将 OpenRouter 加密凭据迁移到项目自己的本机秘密目录，并兼容一次性读取旧路径；任何接口和日志都不返回明文。
- 明确任务生命周期：暂停只阻止领取新任务；“安全停止”在视频步骤之间终止当前任务并进入可识别终态；等待任务可以单独或批量取消；清空历史只在无运行任务时执行。
- 控制台展示上述状态和操作，不再把“暂停”描述成已经停止，也不再依靠陈旧 PID 文件判断任务是否完成。

## Capabilities

### New Capabilities

- `runtime-control`: 定义独立环境、进程身份、自检、启动、停止和重启的统一行为。
- `task-lifecycle`: 定义暂停、安全停止、取消等待、终态和历史清理的可观察语义。

### Modified Capabilities

- `runtime-safety-baseline`: 正式运行链路改为项目独立环境，并要求控制平面只停止经过身份核验的本项目进程。

## Impact

- 影响根目录启动/停机脚本、`fixed_runner/control_api.py`、`fixed_runner/task_store.py`、`fixed_runner/worker.py` 和控制台任务操作。
- 任务数据库会新增可向后兼容的终态与停止请求状态；历史记录和运行证据原样保留。
- 本变更不删除截图、日志、数据库或历史任务，不执行真实点赞、收藏、评论或发布，不修改设备账号和系统安全设置，不让 AI 或 MBH 直接控制设备。

