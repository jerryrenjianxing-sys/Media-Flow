## Why

RiskFlow 后台网页虽然使用 `vinext start`，但未显式设置生产环境。Vinext beta 因此注入 Vite 热更新客户端；热更新 WebSocket 尚未连接时，控制台错误转发调用空连接并形成重复的 `Unhandled Promise Rejection` 遮罩。同时，进程控制器优先登记同可执行文件的临时子进程，停止时可能遗留真正监听 3000 端口的网页进程。

## What Changes

- 网页运行角色强制设置 `NODE_ENV=production`，只提供构建后的稳定静态脚本。
- 网页健康检查拒绝 Vite 开发资源或非生产资源入口，连续失败后沿用既有有界重启。
- 进程控制器优先登记直接启动的进程，只有直接进程不是目标可执行文件时才登记子进程。
- 清理已经确认监听 RiskFlow 3000 端口的旧孤儿 Node 进程，并重启后台宿主加载新规则。
- 非目标：不改变任务队列、设备 Worker、模型、互动策略、数据库或手机状态。

## Capabilities

### Modified Capabilities

- `independent-runtime-host`: 网页角色必须以生产模式运行，健康检查必须识别开发错误页，进程登记不得遗留同角色端口监听器。

## Impact

- 后端运行管理：`runtime_control.py`、`background_host.py`。
- 测试：后台健康探测与进程 PID 选择回归测试。
- 运行状态：仅网页服务和空闲后台宿主受控重启；任务池保持 0 运行、0 等待，设备不执行动作。
