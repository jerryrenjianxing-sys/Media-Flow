# RiskFlow macOS 版与 iPhone 真机控制可行性评估

研究日期：2026-09-02（Asia/Shanghai）  
研究对象：当前 RiskFlow 正式链路“React/Vinext 控制台 → Python 本地 API → SQLite WAL 队列 → 每设备 Worker/独占锁 → uiautomator2/ADB → Android”。  
资料边界：项目当前代码和文档；外部事实只采用 Apple、Appium、WebDriverAgent、libimobiledevice 等项目的一手文档、仓库和发布记录。版本与系统支持是研究日快照，引入时必须锁版本并用实际 Mac/iPhone 复验。

## 结论先行

**可以出 macOS 版，也可以在 Mac 上控制实体 iPhone；但它不是把 Windows 安装包“重新编译一下”，而是保留 RiskFlow 的产品控制面，新增一条 iOS 真机执行链。**

推荐目标链路：

```text
RiskFlow macOS 控制台（沿用 React/Vinext）
        ↓ HTTP localhost
Python 本地 API + SQLite WAL 队列（沿用）
        ↓ 每台 iPhone 一个 Worker / 独占锁（沿用安全语义）
iOS DeviceBackend + iOS 平台 Adapter（新增）
        ↓ W3C WebDriver
Appium 3 + XCUITest Driver
        ↓
已签名的 WebDriverAgent + Apple XCTest/XCUITest
        ↓ USB 为首版基线；无线为后续可选
实体 iPhone
```

需要更换的是**设备技术栈和 macOS 运行/分发层**：

- `uiautomator2 / adbutils / adb.exe / Android APK 初始化` 要换成 `Appium XCUITest Driver / WebDriverAgent / Xcode 签名与配对`；
- C# WinForms 启动器、PowerShell、计划任务、DPAPI、Velopack Windows 包要换成 macOS `.app`、`launchd`、Keychain、Developer ID 签名与公证；
- 抖音 Android 的 package、resource-id、坐标档案、输入法和页面识别不能直接带到 iOS，必须为目标 iOS App 重做定位和动作后验证。

不需要推倒重来的部分：网页任务台、本地 API 的大部分业务接口、SQLite 队列、任务状态、每设备独占、预览/二次确认、结果不明不自动重放、证据留存、模型只判断不直接持有设备这些核心设计都应保留。

**最合理的路线不是先做完整 Mac 商品版，而是先在一台 Mac + 一台已授权 iPhone 上完成“USB、只读、单设备”技术样机。** 只有截图、UI 树、启动目标 App、查找元素、滑动和动作后复核都稳定，再进入写入动作、多设备和商品化打包。

## 一、当前项目中哪些能复用

当前正式架构和安全边界记录在 [PROJECT.md](../PROJECT.md)、[fixed_runner/README.md](../fixed_runner/README.md) 和主规格中。代码核对显示：

- [task_store.py](../fixed_runner/task_store.py) 的 SQLite 队列、任务/初始化生命周期、事故记录和停止请求不依赖 ADB 的业务语义，可以继续使用；
- [control_api.py](../fixed_runner/control_api.py) 的任务、结果、审批、证据等接口大部分可沿用，但设备发现、实时签名、截图和初始化接口目前直接调用 `adb.exe`，需要按平台路由；
- [worker.py](../fixed_runner/worker.py) 已经具备每设备 Worker、设备锁、中断任务失败收口和未知状态不自动重放，但它当前直接导入 `Uia2RunRecorder`、Android 预检、Android 初始化和 uiautomator2 风格设备方法，不能只替换一个连接函数；
- [platform_adapters.py](../fixed_runner/platform_adapters.py) 已有很小的语义 Adapter 接口，这是正确方向，但仍缺设备级 `screenshot/source/tap/swipe/type/app lifecycle/health` 抽象；
- [douyin_uia2_runner.py](../fixed_runner/douyin_uia2_runner.py)、[device_initialization.py](../fixed_runner/device_initialization.py) 和 Android 设备档案包含 uiautomator2、ADB、APK、FastInputIME、Android activity/resource-id/坐标等强耦合，iOS 版应另建实现，不应在文件里堆大量 `if ios`；
- [launcher](../launcher) 和 [packaging](../packaging) 明确是 WinForms、PowerShell、Windows 计划任务、Velopack 和内置 `adb.exe` 路线，macOS 版需要独立发行目标。

建议先引入一个比 `PlatformAdapter` 更底层的设备协议：

```text
DeviceBackend
├─ identity / health / connection_type
├─ screenshot / page_source
├─ find / tap / swipe / type_text
├─ launch_app / activate_app / terminate_app
└─ session / reconnect / close

AndroidUia2Backend（保留现有实现）
IosAppiumBackend（新增 Appium Python Client 实现）
```

上层 Worker、任务队列和安全状态机只依赖 `DeviceBackend`；平台 Adapter 再负责把“搜索、点赞、收藏、评论”等语义映射到 Android 或 iOS 的控件和复验规则。这会形成真正可维护的双平台，而不是两份业务系统。

## 二、iPhone 为什么可行，以及必须满足的条件

### 1. 正式主线应是 Appium XCUITest + WebDriverAgent

Apple 的 `XCUIAutomation` 用 XCTest 控制和检查 App UI，`XCUIApplication` 可以按 bundle identifier 创建应用代理，并启动、激活和终止应用。[Apple XCUIAutomation](https://developer.apple.com/documentation/xcuiautomation) · [Apple XCUIApplication](https://developer.apple.com/documentation/xcuiautomation/xcuiapplication)

Appium XCUITest Driver 在此基础上提供 W3C WebDriver 接口，支持 iOS/iPadOS 真机和模拟器；WebDriverAgent（WDA）运行在设备侧，把 HTTP/WebDriver 请求转给 XCTest。Appium 官方当前文档明确列出真机支持及 `usbmux`、Remote XPC、`xcrun devicectl` 等传输职责。[XCUITest Driver 概览](https://appium.github.io/appium-xcuitest-driver/latest/overview/) · [WebDriverAgent 官方仓库](https://github.com/appium/WebDriverAgent)

截至研究日，维护状态健康且不是“旧项目拼装”：

- Appium `3.7.0` 于 2026-08-24 发布；[官方发布记录](https://github.com/appium/appium/releases)
- XCUITest Driver `12.8.3` 于 2026-08-31 发布，Apache-2.0；[官方发布记录](https://github.com/appium/appium-xcuitest-driver/releases) · [官方仓库](https://github.com/appium/appium-xcuitest-driver)
- WebDriverAgent `16.12.1` 于 2026-09-01 发布，BSD；[官方发布记录](https://github.com/appium/WebDriverAgent/releases) · [许可证](https://github.com/appium/WebDriverAgent/blob/master/LICENSE)
- Appium Python Client 仍由 Appium 团队维护，官方项目为 Apache-2.0；[官方仓库](https://github.com/appium/python-client) · [版本记录](https://github.com/appium/python-client/blob/master/CHANGELOG.md)

版本号只是研究日快照。正式实现要把 Appium、XCUITest Driver、WDA、Xcode 和 iOS 的兼容组合作为一个锁定矩阵，不能全部跟随 `latest`。XCUITest Driver 官方目标是完整支持最新两个主要 Xcode/iOS 版本，旧版可能只是部分可用。[系统要求与兼容矩阵](https://appium.github.io/appium-xcuitest-driver/latest/installation/requirements/)

### 2. 真机首次准备不是零配置

每台 iPhone 至少需要：

1. 与 Mac 配对并在手机上“信任此电脑”；
2. iOS/iPadOS 16+ 开启 Developer Mode；
3. 在 Developer 设置中开启 UI Automation；
4. WDA 使用有效的开发证书、Team ID、bundle ID 和 provisioning profile 签名并安装；
5. Web/混合页若要读取 WebView，还需打开 Safari Web Inspector 与 Remote Automation。

这些不是 Appium 自己可以绕过的限制。[Apple Developer Mode](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device) · [Appium 真机准备](https://appium.github.io/appium-xcuitest-driver/latest/preparation/real-device-config/) · [WDA 签名能力参数](https://appium.github.io/appium-xcuitest-driver/latest/reference/capabilities/)

Appium 会在真机安装 `WebDriverAgentRunner-Runner`。有效 provisioning profile 是硬门槛；常用配置包括 `xcodeOrgId`、`xcodeSigningId`、`updatedWDABundleId` 或 `xcodeConfigFile`。[Appium provisioning 指南](https://github.com/appium/appium-xcuitest-driver/blob/master/docs/getting-started/provisioning-profile/index.md) · [手工签名流程](https://github.com/appium/appium-xcuitest-driver/blob/master/docs/getting-started/provisioning-profile/full-manual-config.md)

### 3. 免费 Apple 账号只能做样机，不适合商品化常态运行

Apple 官方给 Personal Team 的限制是：同一时间最多 10 个 App ID；每个平台最多 3 台测试设备；App ID、设备登记和 provisioning profile 都是 7 天有效，需要周期性重新构建和安装。[Apple 会员比较](https://developer.apple.com/support/compare-memberships/)

因此：

- **免费账号**：适合一台 iPhone 的短期 POC；
- **付费 Apple Developer Program**：适合稳定开发和内部设备池，当前年费 99 美元；同一会员年度每个产品家族最多登记 100 台设备，停用设备不会立刻返还额度。[Apple 注册说明](https://developer.apple.com/help/account/membership/program-enrollment) · [设备上限](https://developer.apple.com/help/account/devices/devices-overview)
- 新登记设备 1–10 台通常立即生效，11–100 台可能需要 24–72 小时处理；不能假设一次接 50 台就立刻全可用。[设备登记处理说明](https://developer.apple.com/help/account/reference/device-registration-updates)

新团队签发的 development/ad-hoc App 首次运行还可能需要连 Apple PPQ 服务验证证书；受限网络要把这个依赖纳入验收。[Apple provisioning 更新说明](https://developer.apple.com/help/account/provisioning-profiles/provisioning-profile-updates)

### 4. USB 与无线

**首版应只承诺 USB。** Appium 默认可以通过 `appium-ios-device` 把 Mac 本机端口经 USB 转到真机 WDA；`wdaLocalPort` 默认 8100。[WDA 自管理说明](https://appium.github.io/appium-xcuitest-driver/latest/guides/wda-custom-server/) · [能力参数](https://appium.github.io/appium-xcuitest-driver/latest/reference/capabilities/)

无线不是不可能：libimobiledevice 支持启用 Wi-Fi sync 的网络设备，Appium 的 Remote XPC 文档也覆盖 `usbmuxd` 的有线和无线连接，运行中的 WDA 还可以通过手机可达 IP 连接。[libimobiledevice 官方仓库](https://github.com/libimobiledevice/libimobiledevice) · [Remote XPC 隧道](https://github.com/appium/appium-xcuitest-driver/blob/master/docs/guides/remotexpc-tunnels-real-devices.md) · [连接运行中的 WDA](https://github.com/appium/appium-xcuitest-driver/blob/master/docs/guides/attach-to-running-wda.md)

但无线会增加同网段发现、首次配对、休眠、Wi-Fi 漫游、端口/隧道、网络隔离和 WDA 保活变量。RiskFlow 的生产基线应先完成 USB 稳定性，再把无线作为单独 POC；不能把“Xcode 能无线看到手机”直接等价为“长期无人值守稳定”。

### 5. 真机并发

Appium 官方支持 XCUITest 并行会话，既可多个 Appium 进程，也可一个服务管理多个会话。每台真机至少必须隔离：

- 唯一 `udid`；
- 唯一 `wdaLocalPort`；
- 唯一 `derivedDataPath`；
- 需要 MJPEG/录像时，唯一 `mjpegServerPort`。

[Appium 并行测试说明](https://appium.github.io/appium-xcuitest-driver/latest/guides/parallel-tests/)

这与 RiskFlow“每设备一个 Worker/独占锁”的设计天然一致。但官方没有给出“某款 Mac 固定能跑多少台”的通用数字。并发上限受 Mac CPU/内存、Xcode/WDA 启动、截图频率、USB 供电/Hub、目标 App 响应和模型调用共同影响。正确做法是 1 台通过后测 2 台，再测 4 台；不要在没有实机压测前承诺 10 台或 15 台。

## 三、iOS 与 Android 不能等价替换的限制

1. **不是 ADB 的一比一替代。** libimobiledevice 可以配对、枚举、安装 App、截图、日志、文件和网络连接，但其官方能力清单没有跨 App UI 元素定位和触控执行；它是设备通信底座，不是 uiautomator2 替代品。[libimobiledevice 功能清单](https://github.com/libimobiledevice/libimobiledevice)
2. **WDA 继承 XCTest 的问题。** Appium 官方明确说 WDA 基于 XCTest，也共享 XCTest 的已知问题。真实设备应尽量复用 WDA，而不是每个任务反复卸载和重启。[WDA 自管理说明](https://appium.github.io/appium-xcuitest-driver/latest/guides/wda-custom-server/) · [能力参数中的 `useNewWDA`](https://github.com/appium/appium-xcuitest-driver/blob/master/docs/reference/capabilities.md)
3. **页面树和输入行为不同。** iOS 使用 XCUI 元素树，不是 Android resource-id/UIAutomator 树；目标 App 如果无障碍信息差，仍需截图候选和坐标兜底。Appium 的底层输入事件部分依赖未公开的 XCTest 接口，升级 iOS/Xcode 后必须回归。[iOS 输入事件说明](https://appium.github.io/appium-xcuitest-driver/latest/guides/input-events/)
4. **真机有可见系统提示。** iOS/iPadOS 15+ 运行 WDA 时会显示 “Automation Running” 覆盖提示，这是 XCTest 已知限制，截图中不显示但真人看屏会看到。[Appium 故障说明](https://appium.github.io/appium-xcuitest-driver/latest/guides/troubleshooting/)
5. **部分系统能力受限。** 例如真机剪贴板访问要求 WDA 在前台；键盘隐藏也可能只能按真实用户手势处理。[剪贴板限制](https://appium.github.io/appium-xcuitest-driver/latest/guides/clipboard/) · [执行方法说明](https://appium.github.io/appium-xcuitest-driver/latest/reference/execute-methods/)
6. **App 和系统版本变化风险更高。** 每次 iOS、Xcode、XCUITest Driver、WDA 或目标 App 大版本升级都要跑一套只读→预览→最小写入回归，不能滚动升级后直接恢复生产任务。
7. **技术可行不等于平台业务允许。** 对第三方社交 App 做批量互动可能受其服务条款、风控和账号政策约束；本报告只确认受授权测试设备的技术路径，不把“能点”解释为“允许做增长自动化”。

## 四、开源项目复用建议

| 项目 | 研究日状态/许可证 | 在 RiskFlow 中的定位 | 建议 |
|---|---|---|---|
| [Appium 3](https://github.com/appium/appium) | 3.7.0；Apache-2.0 | W3C WebDriver 服务和驱动管理 | **采用**，作为 iOS 标准入口 |
| [Appium XCUITest Driver](https://github.com/appium/appium-xcuitest-driver) | 12.8.3；Apache-2.0 | iOS/iPadOS 真机驱动，管理 WDA 生命周期 | **采用并锁版本** |
| [WebDriverAgent](https://github.com/appium/WebDriverAgent) | 16.12.1；BSD | 设备侧 XCTest/WebDriver 服务 | **采用**，按每个开发团队签名 |
| [Appium Python Client](https://github.com/appium/python-client) | 2026-08 仍发布；Apache-2.0 | 让现有 Python Worker 调用 Appium | **采用** |
| [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice) | 1.4.0（2025-10）；LGPL-2.1，主线仍更新 | 配对、设备信息、安装、日志、截图、USB/Wi-Fi 通信辅助 | **可选辅助**，不是 UI 执行器；动态链接/分发要履行 LGPL |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | 11.3.0（2026-08-31）；GPL-3.0-or-later | iOS 17+ Remote XPC、隧道、截图、XCTest/WDA 等高级设备服务 | **仅做隔离工具或研究备选**；若打包进闭源商品须先做 GPL 合规评审 |
| [go-ios](https://github.com/danielpaulus/go-ios) | 活跃；MIT | 跨平台安装、启动、截图、XCTest/WDA、隧道 | **可做 Appium 之外的诊断/传输备选**，不替代主驱动 |
| [tidevice](https://github.com/alibaba/tidevice) | 官方发布页最新 0.12.11；更新节奏明显慢于前述项目 | 旧式跨平台 XCTest/WDA、截图、安装 | **不作新主线**，仅用于兼容性参考 |
| [Appium Device Farm](https://github.com/AppiumTestDistribution/appium-device-farm) | 12.0.1；Apache-2.0 | 多主机/多设备发现与会话分配 | **不在首版引入**；达到跨主机/多人预约门槛后再 POC |

Appium Device Farm v12 已移除人工实时控制和直播，只保留自动化会话、设备分配、hub/node、日志和录像。因此它不能直接替代 RiskFlow 现在的“设备画面/人工复核”体验，后者仍需用 WDA 截图做低频预览或另建只读查看通道。[v12 说明与发布记录](https://github.com/AppiumTestDistribution/appium-device-farm/releases)

### 为什么不建议直接裸接 WDA

RiskFlow 自己直接调用 WDA HTTP 能减少一层服务，但会自行承担 Xcode/WDA 构建、签名、设备发现、端口转发、Remote XPC、版本兼容、会话恢复和命令差异。Appium XCUITest Driver 已经处理这些变化，且为现有 Python 提供稳定 W3C 接口。除非单设备 POC 证明 Appium 开销不可接受，否则不值得重做驱动层。

## 五、macOS 商品化与商业分发边界

这里有两份不同的“签名”，不能混为一谈。

### 1. Mac 上的 RiskFlow 应用

Windows 当前使用 WinForms 启动器、Velopack 和未签名内部包；macOS 需要独立构建 `.app`/`.dmg` 或 `.pkg`。对 Mac App Store 外分发，Apple 的正式路径是 Developer ID 签名、Hardened Runtime 和 notarization；公证会检查恶意内容和签名问题，Gatekeeper 用票据验证软件。[Apple 公证说明](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) · [Developer ID 证书](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)

建议 macOS 发行层：

- 首版可用原生 Swift 小启动器或成熟跨平台壳承载“启动后台、打开本地网页、显示错误”；不要把现有 WinForms 翻译成复杂桌面 UI；
- 后台用 `launchd`，运行数据进入 `~/Library/Application Support/RiskFlow`，日志进入 `~/Library/Logs/RiskFlow`；
- 密钥进入 macOS Keychain，不能照搬 Windows DPAPI/PowerShell；
- 内置 Python、Node、Appium 及其原生依赖时，所有嵌套可执行文件和动态库都要按正确顺序签名，再对整个应用做公证；
- 同时产出 Apple Silicon (`arm64`)；是否支持 Intel (`x86_64`) 要按目标客户再决定，不能默认 Universal 包没有额外成本。

### 2. iPhone 上的 WDA 测试 Runner

WDA 不是一个可随意装到所有客户 iPhone、永久有效的普通 App Store 应用；它是开发/测试 Runner，需要 Developer Mode 和 provisioning。商业交付有三种现实模式：

1. **公司内部设备池**：用公司的付费开发者团队签名 WDA并登记所有测试 iPhone。最稳定，受每年每产品家族 100 台登记额度约束。
2. **卖给外部客户，由客户控制自己的设备**：RiskFlow Mac 客户端可以随包提供 WDA 源码或未签名预构建物，再引导客户用自己的 Apple Developer Team 完成签名。产品体验不如普通安装软件，但避免把所有客户 UDID压进供应商团队；具体证书/源码分发方案上线前应由 Apple 开发者协议和开源许可证顾问复核。
3. **Apple Developer Enterprise Program**：只适合满足资格的组织向自己的员工私有分发，不是向任意付费客户绕过 App Store/设备登记的通道。[Apple 会员比较](https://developer.apple.com/support/compare-memberships/) · [Program enrollment](https://developer.apple.com/help/account/membership/program-enrollment)

因此，“做给自己公司几台 iPhone 用”和“做成任何客户下载即用的 Mac 商品”是两个难度级别。前者可行且可控；后者的最大产品成本不是页面开发，而是客户侧 Xcode/Apple 账号/设备信任/Developer Mode/WDA 签名的首次配置和后续证书维护。

## 六、是否需要换技术支持

**不用换掉现有 Python/前端技术团队，但必须补足 iOS 自动化和 Apple 签名能力。**

最低能力组合：

- 现有 Python 工程：抽 `DeviceBackend`、复用队列/Worker/事故证据、接 Appium Python Client；
- iOS 自动化经验：XCUITest、WDA、Xcode signing/provisioning、真实 iPhone 页面树和系统弹窗；
- macOS 工程/发布经验：`launchd`、Keychain、Developer ID、Hardened Runtime、公证、Apple Silicon 打包；
- QA：建立 iOS/Xcode/WDA/目标 App 版本矩阵和实机回归。

一个熟悉 Xcode/WDA 的工程师可以先带 POC，不需要一开始组完整 Swift 团队。只有要做漂亮的原生桌面客户端或 Mac App Store 发行时，才需要增加较重的 Swift/AppKit/SwiftUI 投入。当前网页控制台继续本地运行是成本最低的路线。

## 七、推荐实施顺序与验收门槛

### P0：技术样机（1 台 Mac + 1 台 iPhone，USB，只读）

不修改现有 Android 执行链，单独建立实验目录/分支，锁定 Appium 3、XCUITest Driver、WDA 和 Xcode 版本：

1. 自动识别指定 UDID，不使用“第一台设备”；
2. 检查 Trust、Developer Mode、UI Automation、签名和 WDA 健康；
3. 启动/激活目标 App；
4. 读取截图和 page source；
5. 按 accessibility id / iOS predicate / class chain 定位一个只读控件；
6. 完成一次滑动并复核页面已改变；
7. 断线、锁屏、WDA 崩溃时留下证据并安全失败，不自动重放。

**通过门槛**：连续 50 次只读流程无误触；拔插 USB、锁屏、App 弹窗、WDA 重启都能区分“未执行”和“结果未知”。这个数字是建议的工程验收样本，不是 Apple/Appium 官方指标。

### P1：接回 RiskFlow 控制面（仍为 1 台 iPhone）

1. 新建 `IosAppiumBackend`；
2. Worker、设备发现、截图和初始化 API 按 `platform=android|ios` 路由；
3. 保留同一 SQLite 队列、设备锁、预览和事故模型；
4. 为目标 iOS App 新建平台 Adapter 和设备档案，不复用 Android 坐标；
5. 先只跑观察任务，再加点赞/收藏预览，最后才申请单次真实写入验收。

### P2：macOS 本机运行与安装包

1. 将 Windows 专属进程托管替换为 `launchd`；
2. Keychain 保存模型 Key 和签名相关引用；
3. 打包本地 Python/Node/Appium 依赖；
4. 产出 Developer ID 签名、公证、可卸载、数据不随升级覆盖的 macOS 安装包；
5. 在一台干净 Mac 上从零走完安装、WDA 准备、首台 iPhone 初始化和升级回退。

### P3：多真机与无线

1. 先测 2 台 USB，再测 4 台 USB，逐台分配 UDID/端口/DerivedData；
2. 监测 WDA 启动、截图吞吐、USB 供电、CPU/内存、掉线率和任务尾延迟；
3. 只有 USB 稳定后再做无线配对/保活 POC；
4. 只有跨 Mac、多人预约或设备自动分配成为真实痛点时，再评估 Appium Device Farm。

## 最终判断

| 问题 | 判断 |
|---|---|
| 能否做 Mac 版 | **能**；网页控制台、Python API、SQLite、安全状态机可复用，Windows 启动/打包需重做 |
| 能否在 Mac 控制 iPhone 真机 | **能**；正式路线是 Appium XCUITest + 已签名 WDA + Xcode/XCTest |
| 是否必须 Mac | **正式开发与最少限制的真机链建议必须用 Mac**；Appium 虽已有 Windows/Linux 的有限真机支持，但缺 Xcode、要求 iOS 18+、必须预装/外管 WDA，不适合作为 RiskFlow iPhone 商品版的主线。[非 macOS 主机限制](https://appium.github.io/appium-xcuitest-driver/latest/guides/non-macos-hosts/) |
| 是否要换团队 | **不用整体换**；现有 Python/前端保留，补 iOS/WDA/签名与 macOS 公证能力 |
| 最值得复用的开源项目 | **Appium 3、XCUITest Driver、WebDriverAgent、Appium Python Client** |
| libimobiledevice 能否替代 Appium | **不能**；适合设备通信辅助，不负责完整跨 App UI 自动化 |
| 能否无线 | **可以研究，首版不承诺**；USB 是稳定基线 |
| 能否多台并发 | **协议和架构支持**；每台独立 UDID/端口/构建目录，实际台数必须实机压测 |
| 能否做成普通客户“下载即用” | **难于内部版**；Mac 应用可正常签名公证，但每台 iPhone 的 Developer Mode、信任和 WDA provisioning 仍需要产品化引导与证书策略 |

建议下一项正式工作是一个隔离的 `ios-single-device-readonly-poc`，而不是直接立项“完整 Mac 版”。只要 P0 通过，RiskFlow 的控制面和安全设计值得继续复用；如果连目标 iOS App 的只读 page source、截图和滑动复核都不稳定，就应在投入打包、UI 和多设备之前止损。
