## Why

RiskFlow 现有固定执行器已经能按设备档案复用抖音流程，但新设备的控制组件准备、输入能力验证和平台控件校准仍依赖人工命令或 MBH 调试，无法由安装版独立完成。需要把这些步骤收敛为可恢复、可审计的“初始化流程”，同时把平台语义与设备差异分开，避免按机型或分辨率复制完整脚本。

## What Changes

- 新增独立设备初始化记录与状态机，复用本地 SQLite、设备独占锁和证据目录，但不混入普通社媒任务记录。
- 初始化负责 ADB 前检、设备探查、uiautomator2/FastInputIME 准备、截图/UI 树/中文输入验证、抖音安全导航校准和平台档案生成。
- 新增平台适配器 seam；首版提供抖音 Adapter，通用流程只调用页面、搜索、互动和恢复等语义动作。
- 控件解析固定为 UI 树语义优先、已验证规则次之、云端视觉候选兜底；AI 仅返回结构化候选，固定程序复验后才能保存或执行。
- 设备页增加初始化状态、进度、断点续跑、重新校准、报告、取消及显式完整写入验收开关。
- Windows 安装版继续内置 ADB、uiautomator2 和输入法资源；正式初始化不要求 Mobilerun Portal、MBH 或 Codex。
- 现有设备档案和任务保持兼容；旧档案继续可用，显示特征或 App 版本变化只禁用过期坐标并提示快速复验。
- 非目标：不启用开发者选项、不处理首次 ADB 授权、不安装或登录抖音、不绕过厂商安全确认、不自动处理身份验证、不自动删除写入验收评论、不接入第二个真实平台。

## Capabilities

### New Capabilities

- `device-initialization`: 定义独立初始化状态、控制组件准备、人工断点、平台校准、证据和报告。
- `platform-adapters`: 定义通用流程与平台差异的 Adapter seam、控件解析优先级和抖音首版能力。

### Modified Capabilities

- `device-profiles`: 增加控制能力与平台档案、失效和旧档案兼容语义。
- `device-discovery`: 在线设备需要展示初始化摘要并提供初始化入口。
- `execution-architecture`: 固定执行器通过平台 Adapter 执行动作，AI视觉结果仍只作为候选。
- `runtime-safety-baseline`: 初始化默认无写入，完整写入验收显式授权且未知结果不重放。
- `windows-distribution`: 独立安装版包含初始化所需资源且不依赖 MBH/Codex。

## Impact

- 后端：新增初始化存储、执行器、平台适配接口和设备接口；扩展设备状态载荷。
- 控制台：设备页增加初始化操作、状态和报告交互。
- 数据：在现有 SQLite 中增加初始化专用表，平台档案保存在运行数据目录；不迁移或删除历史任务、截图和设备档案。
- 设备：初始化会安装/校验 uiautomator2 输入组件并进行无写入导航；只有用户显式开启最终验收时才进行一次受控写入。
- 发布：验证 uiautomator2 自带的 `u2.jar` 与 FastInputIME APK 随便携 Python 进入发行包，Mobilerun Portal保持可选。
