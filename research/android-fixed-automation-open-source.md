# Windows 多实体 Android 固定自动化与设备池开源方案评估

研究日期：2026-08-20  
范围：Windows 主机、多台 USB 实体 Android、抖音类原生 App、固定流程自动化与后续设备池。只使用项目官方 GitHub 仓库和官方文档。

## 结论先行

当前项目不应直接套一个“大而全”的现成群控系统。最合适的组合是：

1. **近期固定执行层首选 `openatx/uiautomator2`（Python）**：轻、快、按设备序列号连接，原生控件、XPath、截图和坐标动作都够用，最容易接入现有 Python 定时程序、Mobile Harness 轨迹与日志。
2. **视觉兜底选 Airtest，不把它作为所有动作的主引擎**：在抖音视频面这类 UI 树缺失或不稳定页面，用局部模板识别补位；可以继续用 Poco/原生 UI 树的地方，不依赖纯坐标。
3. **把 Appium UiAutomator2 保留为标准化升级路线**：当平台数量、语言生态或测试规范复杂度上升时使用；其官方驱动、W3C 协议、Python 客户端和并发能力更完整，但单机固定流程的启动、会话与端口管理比直接使用 uiautomator2 更重。
4. **设备达到约 5 台以上、需要自动分配/跨电脑/多人看板时，再试 Appium Device Farm plugin**。当前只有一台 Android 时上设备池会增加数据库、端口、版本和运维成本，没有速度收益。
5. **不建议以 DeviceFarmer/STF 为当前 Windows 主机基础**：它适合远程看屏和设备预约，不是固定动作引擎；官方明确不提供 Windows 安装支持，且 README 声明的 Android 支持只到 Android 12。
6. **Maestro 适合清晰的 E2E 测试流，不是本项目最优核心**：YAML 很易读、Windows 和本地分片已支持，但持续运行、动态任务池、Python 数据闭环和细粒度随机策略不如自有 Python 执行器自然。

推荐的三层落点是：

```text
Codex + Mobile Harness：探索页面、调试新流程、审查失败轨迹
             ↓ 固化规则
AutoGLM：仅处理普通未知页面或恢复，不进入正常任务必经路径
             ↓ 成功轨迹再固化
Python 固定执行器：uiautomator2 主控 + Airtest 局部视觉兜底
             ↓ 多设备调度
每设备独立 Worker + 设备锁 + 任务队列 + 前后状态验证
```

这里所谓“训练固定程序”主要是把轨迹整理为**页面指纹、动作、前置禁止条件、成功判据和恢复路线**，不是训练新的神经网络。

## 快速比较

| 方案 | 维护/许可证（截至研究日） | Windows + 实体 Android | 多设备 | 原生定位与视觉 | Python | 对本项目的判断 |
|---|---|---|---|---|---|---|
| Appium UiAutomator2 | 官方核心团队维护；2026-07 发布 v8.2.1；Apache-2.0 | 支持；官方 Android 快速入门覆盖真实 USB 设备 | 官方支持单服务多会话或多服务；每台要唯一 `udid`、`systemPort`，必要时唯一视频/WebView 端口 | W3C WebDriver、ID/无障碍/XPath/UiSelector、截图；原生/混合/Web | 官方 Python Client | **标准化首选，当前略重** |
| openatx/uiautomator2 | 2026-06 发布 3.7.0；MIT | 支持，ADB + Python 3.8+ | `connect(serial)`；调度、锁和队列需自己做 | text/resource-id/class/XPath、UI 树、截图、坐标 | 原生 Python | **当前固定执行层首选** |
| Airtest + Poco | Airtest 仓库 2026-03 仍更新；Airtest/Poco Apache-2.0；Poco 最近公开版本较旧 | 支持 | 官方有多设备 runner 示例 | Airtest 强项是图像模板；Poco 可读 UI 层级、支持原生 Android | 原生 Python | **视觉兜底优秀，主控次选** |
| Maestro | 2026-07 仍活跃；Apache-2.0 | 官方提供原生 Windows CLI/Studio | CLI 支持 sharding；也可选设备运行；云端并行是付费产品 | 可访问文本/ID等语义元素，截图断言；YAML flow | 非 Python 原生（YAML/JS） | **适合测试用例，不适合主要任务池** |
| DeviceFarmer/STF | 仓库仍有维护；Apache-2.0；公开 changelog 3.7.5 为 2025-02 | 官方写明 Windows 不提供支持；推荐 Linux/BSD/Docker | 强项是设备预约、远程看屏、ADB 接入 | 远程屏幕/输入/日志，不是高级控件自动化主引擎 | 有 REST API，可由 Python 调 | **当前不选；以后也优先考虑 Appium Device Farm** |
| Appium Device Farm plugin | 2026-07 发布 v12.0.1；Apache-2.0 | 基于 Appium/Node/ADB；官方文档未给出明确 Windows 生产保证，需在本机 POC | 自动发现、分配、并行、hub/node、看板、日志/录像 | 底层仍由 Appium 驱动完成 | Python 客户端无需专用改造 | **5+ 台或跨主机时再引入** |
| scrcpy | 2026 年 v4.x，活跃；Apache-2.0 | 明确支持 Windows | 可按设备序列号启动多个实例 | 高帧率看屏、录屏、手动控制；没有控件选择器/状态机 | 无正式 Python 自动化层 | **运维观察工具，不是执行器** |
| AutoJs6 | 2026-03 发布 6.7.0；MPL-2.0 | 脚本在 Android 端运行，Windows 仅用于开发/分发 | 可一机一脚本，但中央队列、版本与日志要自己建设 | Android 无障碍节点、坐标、OCR 等 | JavaScript，不是 Python | **不作为本项目主线** |

## 分项判断

### 1. openatx/uiautomator2：当前最合适的固定程序底座

官方仓库将其定义为“Android Uiautomator2 Python Wrapper”，设备端运行基于 UiAutomator 的 HTTP 服务，电脑端用 Python 调用；支持通过 ADB 序列号连接多台设备。README 已给出 `u2.connect('serial')`、包启动、XPath、控件文本、截图等用法。[官方仓库](https://github.com/openatx/uiautomator2) · [发布记录](https://github.com/openatx/uiautomator2/releases)

适合本项目的原因：

- 当前定时、截图、日志和评论输入补救已经是 Python/ADB 路线，迁移成本最低；
- 每个 Worker 显式绑定一个 serial，即可在 Windows 上多进程并发；
- 不需要每次动作都经过云模型，也不需要 Appium 服务会话，固定动作延迟更小；
- 能把 UI 控件定位、相对坐标和截图验证组合起来。

边界：它不自带完整设备池、任务看板和分布式调度；抖音部分视频页可能不给稳定的无障碍/UIAutomator 树，因此必须保留截图/视觉和动作后验证。不能把“能点击”当成“操作成功”。

### 2. Appium UiAutomator2：最稳妥的标准化升级路线

Appium UiAutomator2 是 Appium 核心团队维护的官方 Android 驱动，支持真实设备与模拟器上的原生、混合和移动 Web App，使用 W3C WebDriver；官方 Python Client 由 Appium 团队维护。[驱动仓库](https://github.com/appium/appium-uiautomator2-driver) · [Android 安装指南](https://appium.io/docs/en/3.3/quickstart/uiauto2-driver/) · [官方 Python Client 列表](https://appium.io/docs/en/3.2/ecosystem/clients/)

并发是明确支持的：一个 Appium 服务可以创建多个会话，也可以多服务进程；真实设备至少要为每个会话指定唯一 `udid` 与 `systemPort`，涉及 WebView 或录制时还要隔离 `chromedriverPort`、`mjpegServerPort`。[官方并发说明](https://github.com/appium/appium-uiautomator2-driver#parallel-tests)

它比直接 uiautomator2 更适合：

- 后续同时接 Android、iOS、Web 或多语言客户端；
- 需要 W3C 标准接口、Appium Inspector、CI 测试体系；
- 希望把设备分配交给 Appium Device Farm。

当前不直接作为第一版的原因不是能力不足，而是固定短动作不需要为每台设备维护 Appium 会话、端口与服务生命周期。执行器接口应预留 `uiautomator2` / `appium` 两个后端，避免未来重写业务规则。

### 3. Airtest + Poco：作为“UI 树失效时”的视觉补位

Airtest 是 Python 跨平台自动化框架，核心优势是不用注入业务 App即可用图像模板找元素，并能生成带截图和录屏的报告；官方提供多设备 runner 示例。Poco 提供 UI 层级访问和相对选择器，并包含 Android 原生 App 驱动。[Airtest 仓库](https://github.com/AirtestProject/Airtest) · [官方多设备说明](https://airtest.doc.io.netease.com/en/tutorial/9_Improved_compatibility/) · [Poco 仓库](https://github.com/AirtestProject/Poco)

本项目应把它限制在这些场景：

- UI 树为空但按钮图标稳定；
- 需要识别点赞心形、收藏星形的颜色/状态变化；
- 弹窗无法通过 resource-id 或文字找到。

不建议对整个信息流做纯模板匹配：主题、图标动画、分辨率、遮罩和 A/B 界面会提高误点率。模板只负责“候选定位”，动作前仍要做禁用区检查，动作后还要校验状态变化。

### 4. Maestro：容易写，但更像测试流而不是长期任务执行器

Maestro 用 YAML 描述移动端 E2E 流，支持 Android/iOS/Web、文本/元素操作、截图断言和 JavaScript；官方现已提供 Windows CLI/Studio，并在 CLI 中加入本地 sharding。[官方仓库](https://github.com/mobile-dev-inc/Maestro) · [Windows 安装文档](https://github.com/mobile-dev-inc/maestro-docs/blob/main/maestro-cli/how-to-install-maestro-cli/README.md) · [Changelog（sharding/Windows）](https://github.com/mobile-dev-inc/Maestro/blob/main/CHANGELOG.md)

它适合“打开 App—进入页面—断言结果”这类确定性回归测试。对本项目不占优的地方是：任务来自动态池、持续数小时运行、每台设备有独立状态、动作频率要由策略引擎生成、失败轨迹要沉淀回 Python 数据层。YAML 可以做少量验收流，但不值得替换固定执行核心。

### 5. DeviceFarmer/STF：设备展示与预约平台，不是当前 Windows 执行层

STF 能在浏览器中看屏、远程输入、安装 APK、读取日志，并通过 REST API 预约/释放设备；这些适合实验室设备共享。[官方仓库](https://github.com/DeviceFarmer/stf) · [官方 REST API](https://github.com/DeviceFarmer/stf/blob/master/doc/API.md)

但官方 README 明确写着 Windows 安装“不提供支持”，生产建议 Linux/BSD，并依赖 RethinkDB、ZeroMQ、GraphicsMagick 等；其声明的 Android 支持范围目前只到 12。[官方要求](https://github.com/DeviceFarmer/stf#requirements) 因此在这台 Windows 电脑上部署 STF 会引入明显运维负担，而且仍要另外接 Appium/uiautomator2 才能完成稳定控件自动化。

### 6. Appium Device Farm plugin：扩容时的优先设备池候选

该插件负责自动发现设备、会话分配、并行、hub/node、看板、日志和录像，底层动作仍由 Appium 驱动完成。官方参数包含 Android 真实设备过滤、最大并发数、端口范围和远程 hub。[官方仓库](https://github.com/AppiumTestDistribution/appium-device-farm) · [安装文档](https://devicefarm.org/setup/) · [服务器参数](https://devicefarm.org/server-args/) · [发布记录](https://github.com/AppiumTestDistribution/appium-device-farm/releases)

注意两点：

- v12 移除了仪表板里的手动实时控制，但自动化、设备分配、日志和录像仍保留；人工看屏可配 scrcpy。[v12 说明](https://github.com/AppiumTestDistribution/appium-device-farm#-breaking-changes---version-1200)
- 官方旧版 setup 页仍写 Appium 2.4.x，而新 hub 文档已写 Appium 3.x；升级变化较快，必须锁定 Appium、驱动和插件版本做本机 POC，不能直接滚动到 latest。[新 Hub 要求](https://docs.devicefarm.org/hub-setup/)

## scrcpy 与 AutoJs6 的位置

- **scrcpy**：官方明确支持 Windows、USB/TCP 设备，低延迟看屏、控制与录制都很好，但没有 UI 语义选择器、任务调度或动作后断言。适合作为设备墙、人工复核和故障录屏。[官方仓库](https://github.com/Genymobile/scrcpy) · [Windows 文档](https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md)
- **AutoJs6**：是仍活跃的 Android 端 JavaScript 无障碍自动化工具，适合单机离线脚本；但需要在每台设备安装、授权、分发和维护脚本，中央任务池、Python 模型接口、跨平台适配都要自建。它可做边缘执行实验，不宜作为当前主线。[官方仓库](https://github.com/SuperMonster003/AutoJs6) · [发布记录](https://github.com/SuperMonster003/AutoJs6/releases)

## 建议的实施顺序

### 阶段 A：一台手机验证固定执行器

1. 用 `uiautomator2` 按 serial 建立单设备 Worker。
2. 只实现页面识别、观看计时、上滑和截图记录；先不做状态改变动作。
3. 为每次点赞/收藏/评论增加三道门：页面类型允许、目标候选可信、动作后状态确实改变。
4. UI 树不可用时，仅对局部区域使用 Airtest 模板；识别不确定就跳过，不猜坐标。
5. 记录每一步耗时、定位来源、动作前后截图、验证结果、失败原因和是否升级给 AutoGLM/MBH。

### 阶段 B：三台本机并发

1. 每台设备一个进程、一个 serial、一把互斥锁；任何时刻固定程序、AutoGLM、Mobile Harness 只能有一个控制者。
2. 任务池只分配业务任务，Worker 自己维护平台适配状态。
3. 评论生成与异常识别走限流队列，常规滑动和精确等待完全本地化。
4. 验证 USB 供电、ADB 稳定性、CPU/内存、截图吞吐和云模型并发费用。

### 阶段 C：5 台以上或跨电脑

先比较两条路径：

- 继续自有 Python 调度器：如果仅内部单人使用、设备固定，维护最简单；
- 引入 Appium + Device Farm plugin：如果需要自动占用/释放、多人看板、跨主机节点、统一录像和会话治理。

不建议为“看起来像设备池”而提前部署 STF；只有需要浏览器远程借用设备且愿意单独维护 Linux 服务时再重新评估。

## 最终推荐

**现在就做：`Python + openatx/uiautomator2 + Airtest局部视觉 + 自有设备锁/任务队列`。** 这套最贴合当前 Windows、现有 ADB/Mobile Harness 环境和“先调试、后固化、不断降本”的目标。

**接口上保留 Appium 后端，但第一版不强制上 Appium。** 当多平台标准化或设备池需求出现时，把同一份页面状态机映射到 Appium UiAutomator2；设备规模达到阈值后再 POC Appium Device Farm plugin。

**质量上不要追求每条必操作。** 固定程序的优先级应是：错误动作归零 > 动作成功率 > 速度。遇到广告、直播、商品、特效页、按钮语义冲突或状态无法验证时，直接跳过或升级到 AutoGLM/MBH，随后再把已验证路径固化。
