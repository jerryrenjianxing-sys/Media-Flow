# RiskFlow 脱离 Codex 常驻与 Windows 软件封装研究

日期：2026-08-28  
范围：RiskFlow 当前的本地网页控制台、Python API/Worker/纠错分析器、ADB 真机控制和当前 Windows 用户级 DPAPI 凭据。  
来源边界：仅使用 Microsoft 官方文档、候选项目官方文档和官方 GitHub 仓库。

## 结论

RiskFlow **已经具备先做“独立运行版”的条件，但还不具备直接发布一个无需准备环境的完整安装包的条件**。

推荐拆成两层：

1. **现在先落地运行层**：由 Windows 任务计划程序以当前登录用户的 `InteractiveToken` 启动一个 RiskFlow 守护进程；守护进程负责启动并监测 API、网页、纠错分析器和各设备 Worker。Codex 只用于开发，不再是进程父级。
2. **稳定后再落地分发层**：把不可变程序文件和所需运行时整理成发布目录，用 Velopack 生成每用户 `Setup.exe`、桌面/开始菜单快捷方式和后续更新包。

不推荐当前直接使用 Windows Service/WinSW。原因不是 WinSW 不稳定，而是 Windows 服务运行在 Session 0，RiskFlow 又依赖当前用户会话、用户级 DPAPI 密钥、本地浏览器入口和 ADB 用户环境。Microsoft 明确说明，从 Windows Vista 起服务不能直接与用户交互，Session 0 专用于服务且不支持交互式用户进程；WinSW 自己的排障文档也建议需要 UI 交互时使用后台应用并由 Task Scheduler 自动启动。[Microsoft: Interactive Services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services) [Microsoft: Session 0 Isolation](https://learn.microsoft.com/en-us/windows/win32/services/service-changes-for-windows-vista) [WinSW troubleshooting](https://github.com/winsw/winsw/blob/v3/docs/troubleshooting.md)

## 为什么“当前用户计划任务”最符合 RiskFlow

Microsoft 的任务计划程序支持 `TASK_LOGON_INTERACTIVE_TOKEN`：用户必须已经登录，任务在现有交互会话中运行。[Principal.LogonType](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype) 这恰好满足当前项目的两个硬条件：

- RiskFlow 的密钥使用 Windows 用户级 DPAPI。Microsoft 说明，默认情况下通常只有同一台电脑上具有相同登录凭据的用户才能解密。[CryptUnprotectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata)
- 控制台和设备工具属于当前用户环境；以交互用户身份运行可以继续使用该用户的 PATH、ADB 配置、浏览器和本地文件权限。

推荐任务属性：

- 触发：用户登录时；同时允许启动器手动触发。
- 身份：当前用户，`LogonType=Interactive`，普通权限；只有确实需要管理员能力的单独安装步骤才提权。
- 实例策略：只允许一个守护进程实例。
- 失败策略：任务计划程序只负责重启“守护进程”；子进程由 RiskFlow 守护进程管理。
- 停止：先让 RiskFlow 完成安全停机，再结束计划任务，不能粗暴结束所有 Python/Node 进程。

这会有一个明确边界：**用户未登录时 RiskFlow 不运行**。对当前“插在桌面电脑上的授权测试手机 + 本地网页控制台”是合理边界；如果以后要求无人登录也能运行，就必须把交互 UI、ADB 控制、凭据和后台服务重新分层，而不是简单改成 WinSW。

## 守护进程必须承担什么

任务计划程序只能解决“谁启动 RiskFlow”，不能替代应用自己的生命周期管理。RiskFlow 守护进程应承担：

- 启动并健康检查本地 API、控制台、纠错分析器和每台在线设备 Worker；
- 子进程异常退出时只重启相应进程，不因 Codex 更新或退出一起消失；
- 保留现有 PID、启动时间、可执行文件身份核验，拒绝误杀其他 Python/Node 进程；
- 守护进程或 Worker 中断后，把结果不明的状态改变任务安全收口为失败，不自动重放点赞、收藏、评论等动作；
- 写独立的守护日志和最后一次健康状态，便于启动器展示“正在启动、已就绪、部分异常”；
- `Stop/Restart` 与看门狗协调，避免用户主动停机后被立即拉起。

## 候选开源方案对比

| 方案 | 官方定位与许可 | 对 RiskFlow 的适用性 | 结论 |
|---|---|---|---|
| Windows Task Scheduler | Windows 原生能力；`InteractiveToken` 只在现有登录会话运行。不是额外开源依赖。[Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-logontype-principaltype-element) | 保持当前用户、DPAPI、ADB 和浏览器环境；无需常驻 Codex；可登录启动和手动启动 | **当前运行层首选** |
| [WinSW](https://github.com/winsw/winsw) | 把任意可执行程序包装为 Windows Service，MIT；安装/管理服务通常需要管理员权限 | 对纯后台服务很好，但服务处于 Session 0，用户环境、UI 和凭据边界不符合当前 RiskFlow；项目自身也建议需要 UI 时考虑 Task Scheduler | **当前不采用**；未来拆出真正无界面后台服务时再评估 |
| [Velopack](https://github.com/velopack/velopack) | 语言无关的安装与自动更新框架，MIT；Windows 可生成每用户 `Setup.exe` 和 MSI | 可把已有 `.exe` 启动器及完整发布目录做成常规安装软件；每用户安装默认在 `%LocalAppData%`、不需管理员权限，并建立稳定入口和快捷方式。[Windows packaging](https://docs.velopack.io/packaging/operating-systems/windows) [Installers](https://docs.velopack.io/packaging/installer) | **分发层首选**，应在运行目录整理完成后接入 |
| [Tauri](https://github.com/tauri-apps/tauri) | Web 前端 + Rust 后端的桌面壳，MIT/Apache-2.0；Windows 使用 WebView2，可生成 NSIS `.exe`/WiX `.msi`，[官方 README](https://github.com/tauri-apps/tauri/blob/dev/README.md) | 能把控制台放进独立窗口，但不会自动解决 Python、ADB、Worker 常驻和任务恢复；需要增加 Rust 工具链及桌面壳维护 | **暂不采用**；只有明确要求原生窗口、托盘、通知时再做独立 change |
| [Electron](https://github.com/electron/electron) | Chromium + Node.js 的跨平台桌面框架，MIT，[官方 README](https://github.com/electron/electron/blob/main/README.md) | 可复用 React/JS，但会额外携带 Chromium；也不会解决 Python/ADB 后台架构。官方要求持续跟进 Electron/Chromium 安全更新并正确隔离渲染进程。[Security](https://github.com/electron/electron/blob/main/docs/tutorial/security.md) | **不推荐**，本项目没有必要再带一套 Chromium |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | 把 Python 应用及解释器和依赖打成目录或可执行文件；GPL-2.0-or-later + bootloader 特例，允许分发非自由/商业程序。[README](https://github.com/pyinstaller/pyinstaller/blob/develop/README.rst) [许可](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt) | 可以减少目标电脑安装 Python 的要求，但 RiskFlow 有多个 Python 入口、动态设备 Worker、第三方二进制和 Node 前端，需专门冻结回归 | **可选的第二阶段构建工具**；先做 `onedir`，不建议一开始追求单文件 |

## 当前离“普通 Windows 软件”还差什么

### 已经具备

- 已有根目录 `.exe` 启动器和一键启停入口；
- 有独立 `.venv`、固定前端构建、doctor、自身 PID/身份核验与安全停机逻辑；
- API、网页、Worker、纠错分析器边界已经清楚；
- 运行数据库、日志、截图和密钥已经被识别为不可随代码清理的本机状态。

### 完整安装包之前必须补齐

1. **独立守护进程与计划任务安装/卸载**：先解决 Codex 退出或更新导致整组进程消失的问题。
2. **发布目录闭包**：当前安装脚本仍从系统 Python 创建虚拟环境、用系统 npm 安装并构建前端，控制台运行时还通过 PATH 查找 `node.exe`。真正的安装包必须携带或明确安装这些运行时，同时固定 ADB 路径。
3. **可变数据移出安装目录**：Velopack 更新时会完整替换 `current` 目录。[Windows layout](https://docs.velopack.io/packaging/operating-systems/windows) 因此 SQLite、截图、日志、设备档案、密钥和自定义预设不能继续放在会被替换的程序目录，应迁移到 `%LocalAppData%\RiskFlow\runtime`（或等价固定数据目录）。
4. **安装/更新钩子**：安装后注册计划任务；更新前安全停机；更新后重新注册或验证任务并启动；卸载默认保留数据，单独提供明确的数据清理选项。
5. **签名与发布回归**：安装器、启动器和更新包需要稳定版本号、图标、发布清单、第三方许可清单，正式分发前再配置代码签名。

## 建议实施顺序

### 阶段 A：独立运行版（现在做）

- 新增单实例守护进程；
- 新增当前用户计划任务的安装、启动、停止、卸载脚本；
- 让现有 `RiskFlow.exe` 触发计划任务并在健康检查通过后打开页面；
- 验证 Codex 关闭/更新后 RiskFlow 仍在；主动终止一个无设备写入的子服务，验证守护进程只恢复该服务；
- 保留“不自动重放状态改变任务”的现有安全语义。

验收后，用户感知已经接近普通软件：双击图标即可打开，关闭 Codex 不影响，后台异常会自动拉起。

### 阶段 B：便携发布目录

- 固定 Python、Node、ADB 和所有静态资产的相对路径；
- 把可变数据迁移到 LocalAppData；
- 优先做可检查、可增量更新的多文件目录，不急于做“一个巨大 EXE”；
- 在一台没有开发环境的干净 Windows 用户账户做安装、启动、设备发现、更新和卸载测试。

### 阶段 C：正式安装包

- 使用 Velopack 生成每用户 `Setup.exe`；
- 以现有 `.NET` 启动器作为稳定入口；
- 需要时加入自动更新、桌面快捷方式、开始菜单和“应用与功能”卸载；
- 只有在产品明确需要原生窗口/托盘后，才评估 Tauri；不为了“看起来像软件”重写现有网页。

## 最终决策

当前最小且正确的组合是：

```text
RiskFlow.exe
    -> 触发当前用户计划任务
        -> RiskFlow Supervisor
            -> API / Web UI / Analyzer / Device Workers

后续发布目录
    -> Velopack Setup.exe
```

这条路线复用 Windows 原生任务计划程序和 Velopack，保留现有 React/Python/ADB 架构，不引入不必要的 Tauri/Electron 重写，也避免把依赖用户会话的系统错误地塞进 Session 0 服务。
