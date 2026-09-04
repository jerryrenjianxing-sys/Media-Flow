# MuMu 在线安装与 RiskFlow 自动接管设计

日期：2026-09-03

## 结论

RiskFlow 不应携带或重新分发 MuMu 离线安装包。推荐改为“官方下载安装＋RiskFlow 自动接管”的两段式流程：用户从 RiskFlow 打开 MuMu 官方下载入口并按官方安装向导完成一次 Windows 安装；RiskFlow 后台持续探测注册表、安装目录和管理命令，安装完成后自动验证版本并接管后续虚拟机创建、配置、ADB连接、初始化和画面。

MuMu 官方下载页提供 Windows 下载入口；官方安装说明要求用户打开下载文件并按安装界面完成安装，没有公开承诺第三方可依赖的 Windows 静默安装协议。因此首版不应硬编码当前 CDN 文件地址或猜测安装参数，应把 Windows 安装保留为可见人工步骤。[官方下载安装页](https://www.mumuplayer.com/download/?v=0000) [官方安装说明](https://www.mumuplayer.com/installation/)

MuMu 官方多开说明确认其产品能力包含创建、启停、复制、备份、设置、删除和批量操作；备份格式为 `.mumudata`。这支持 RiskFlow 在MuMu已安装后接管完整实例生命周期，但正式集成仍以本机已经验证的 `MuMuManager.exe` 命令能力和版本兼容探测为准。[官方多开功能说明](https://www.mumuplayer.com/help/win/multi-instance-guide.html)

## 推荐流程

### 1. 引擎未安装

- 设备页显示“MuMu 未安装”，主按钮为“前往官方安装”。
- 点击后打开MuMu官方Windows下载页，同时创建一个只读的 `awaiting_engine_install` 引导记录。
- RiskFlow每5秒检查卸载注册表、常见安装目录和 `MuMuManager.exe`，但不读取浏览器下载目录、不接管安装程序、不绕过UAC。
- 页面提供“我已安装，立即检测”和“选择安装目录”两个兜底入口。

### 2. 用户完成官方安装

- RiskFlow识别安装目录和版本后调用Provider只读探测。
- 必需管理命令通过后状态转为 `engine_ready`；版本不兼容时显示当前版本与受支持范围，不直接修改安装。
- 如果MuMu安装需要重启Windows或启用VT，状态进入明确人工步骤，重启后从探测阶段恢复。

### 3. 自动接管

- 已有MuMu实例先只读列出，绝不自动删除或改配。
- 新建虚拟机由RiskFlow调用本机管理命令创建空白实例、写入标准资源参数并回读验证。
- 启动后解析ADB端点、验证Root和Android身份，并生成RiskFlow永久虚拟设备ID。
- 只有ADB、身份与显示签名一致后才进入应用准备和设备初始化。

### 4. 不再分发标准MuMu模板

用“可重复配置配方”替代随安装器分发的 `.mumudata`：

- 固定Android类型、CPU、内存、分辨率、DPI、帧率、Root、旋转和音量。
- 控制组件由RiskFlow自己的发行包在ADB就绪后安装。
- 抖音不随RiskFlow或MuMu模板分发；设备进入 `waiting_app_install`，由用户通过批准来源安装，或由公司另行提供有授权的应用制品来源。
- 抖音安装后进入 `waiting_login`，由用户登录；随后自动初始化并运行3条零写入自检。

该方案首次准备比克隆模板慢，但避免分发MuMu二进制、GB级模板和模板内第三方应用数据，也降低模板与新MuMu版本不兼容的风险。

### 5. 本机可选加速模板

用户在当前电脑成功准备第一台虚拟机后，可以显式创建“本机基准模板”：

- 仅保存于当前MuMu数据目录，不进入RiskFlow安装包或升级包。
- 创建模板前要求退出账号并清理RiskFlow任务证据；未通过清洁检查不得标记为基准模板。
- 后续虚拟机可在本机从该模板克隆，仍重新核对Android身份、登录状态和初始化档案。

## 状态建议

`engine_missing → awaiting_engine_install → probing → engine_ready → creating → configuring → booting → adb_ready → provisioning → waiting_app_install → waiting_login → initializing → validating → ready`

异常状态使用 `waiting_user / incompatible / degraded / failed`。安装探测和只读对账可以自动重试；创建、克隆、导入、导出、删除等结果不明确时不得自动重放。

## 自动连接的准确含义

“安装完成后自动连接”可以实现，但连接对象分两层：

1. **连接MuMu引擎**：发现注册表和管理程序、验证版本与命令能力，无需用户再填路径。
2. **连接Android实例**：实例启动后从MuMu状态取得ADB地址，调用ADB连接并验证Android身份。停止实例没有ADB，因此只能显示在虚拟设备清单，不能显示为在线手机。

首次安装MuMu、Windows UAC、VT/Hyper-V冲突、抖音安装和账号登录仍是人工边界。完成这些步骤后，RiskFlow可以自动接管其余流程并在重启后重新对账。

## 本机已有实现依据

当前 `fixed_runner/emulator_onboarding.py` 已能从已知路径发现 `MuMuManager.exe`、读取运行实例、连接回环ADB、验证Root与Android身份并去重ADB别名。本机注册表当前可识别MuMu 6.5.7.0安装在 `D:\MuMuPlayer`。新增Provider应扩展这条现有链路，而不是另做一套设备发现。

## 对原方案的修改

- 删除“Suite携带MuMu离线安装程序”。
- 删除“Suite携带版本化 `.mumudata` 公司模板”作为必需路径。
- 总安装器缩减为RiskFlow安装器；设备页承担MuMu官方安装引导和安装后自动探测。
- 标准化从“二进制模板”改成“实例配置配方＋控制组件安装＋应用/登录人工断点”。
- 保留本机基准模板作为可选加速能力，不作为跨电脑发行制品。
