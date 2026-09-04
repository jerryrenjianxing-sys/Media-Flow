## Why

RiskFlow 的业务链路已经使用独立 Python 环境和固定执行器，但后台进程仍由启动它的 Codex/PowerShell 进程树间接托管；Codex 更新或退出会让控制台、API、分析器和 Worker 同时消失。现在需要把运行生命周期交给 Windows 当前登录用户自己的后台任务，使它像普通本地软件一样可启动、常驻、自恢复和明确停机。

## What Changes

- 新增单实例运行监督器，持续维护控制 API、网页、只读纠错分析器和已配置设备 Worker。
- 新增当前用户级 Windows 后台任务的安装、卸载、启动、停止、状态与登录后自启动入口；启动后的进程不再依赖 Codex 存活。
- 让现有 `RiskFlow.exe` 和 PowerShell 管理入口优先连接后台任务，同时保留未安装后台任务时的兼容启动路径。
- 对意外退出的无状态服务和 Worker 做有界拉起；中断中的设备任务仍按现有安全规则失败留证，不自动重放或重新提交。
- 增加监督器、后台任务脚本和启动器的离线测试与运行诊断信息。
- 非目标：不重写现有 React/Python 业务，不把 AI 变成设备控制器，不自动重试可能已改变状态的任务，不清理历史数据库或截图，不改成 Session 0 Windows 服务，不在本次引入自动更新。

## Capabilities

### New Capabilities

- `independent-runtime-host`: Windows 当前用户级独立后台宿主、看门狗、安装/启停和可观察状态。

### Modified Capabilities

- `runtime-control`: 一键启动和运行管理从调用者子进程升级为优先交由独立后台宿主管理，并保持幂等、安全停机与身份核验。
- `task-lifecycle`: 后台进程意外退出时，运行中任务必须明确收口且不得因看门狗恢复而自动重放。

## Impact

- 代码：`fixed_runner/runtime_control.py`、新增监督器与 Windows 后台任务脚本、`manage-riskflow.ps1`、`setup-riskflow.ps1`、`launcher/`。
- 运行状态：`fixed_runner/runtime/` 增加监督器状态、日志和停止信号，继续保持 Git 外。
- Windows：为当前用户注册一个 RiskFlow 后台计划任务；不要求系统服务权限，不在未登录桌面会话中控制 USB 设备。
- 兼容性：现有网页、API、SQLite、设备档案、任务载荷和截图目录不变；未安装后台任务时仍可直接启动。
