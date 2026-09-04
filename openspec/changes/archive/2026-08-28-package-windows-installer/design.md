# Design

## Distribution layout

Velopack 的 `packDir` 由发布脚本生成，内容包括：

- `RiskFlow.exe`：唯一用户入口与 Velopack 生命周期钩子处理器；
- `fixed_runner/`：固定执行器和后台宿主源码；
- `control_console/standalone/`：Vinext standalone 前端；
- `runtime/python/`：可搬迁 CPython 及项目依赖；
- `runtime/node/node.exe`：前端服务运行时；
- `runtime/platform-tools/`：固定 ADB 工具；
- 运行和卸载所需的 ASCII-safe PowerShell 脚本。

开发目录继续使用项目内 `.venv` 和本地运行数据；发布目录由启动器设置 `RISKFLOW_APP_ROOT` 与 `RISKFLOW_DATA_ROOT`。发布数据根固定为 `%LocalAppData%\RiskFlow\data`，不位于 Velopack 会替换的 `current` 目录中。

## Lifecycle

- 普通启动：确保当前用户计划任务存在，发出启动请求，等待 API/页面就绪后打开浏览器。
- 后台模式：启动器在前台任务进程内运行 Python 后台宿主并等待其退出。
- 安装/更新钩子：注册或刷新计划任务，但不提交设备任务。
- 卸载钩子：仅在没有运行任务时请求安全停机并移除计划任务；保留数据目录。

## Safety and rollback

- 打包排除 `.secrets`、`fixed_runner/runtime`、数据库、截图和日志。
- 状态不明的写入任务不自动重放。
- 本机开发入口保留，可删除发布输出回退，不迁移或删除现有项目数据。
- 未签名安装包只用于内部测试；对外正式分发前必须补代码签名和干净机器验收。

