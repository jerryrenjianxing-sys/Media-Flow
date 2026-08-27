## MODIFIED Requirements

### Requirement: 正式运行链路
RiskFlow 的正式运行链路 SHALL 为项目独立运行环境中的控制台、本地控制接口、SQLite 任务队列、每设备独立 Worker、uiautomator2 固定程序和 Android 设备；MBH 与 Codex 只承担探索、调试、新异常分析和参考标签候选生成。经 MBH/Codex 单步验证且具有明确页面信号的恢复步骤 SHALL 以有界规则写回固定程序，日常运行不得要求 MBH/Codex 常驻或借用其 Python 环境。

#### Scenario: 日常批量执行
- **WHEN** 用户从控制台提交已支持的自动化任务
- **THEN** 任务由项目独立环境中的固定 Worker 和 uiautomator2 程序执行，不要求 MBH、Codex 或其他项目环境常驻参与

#### Scenario: 遇到未知页面
- **WHEN** 固定程序遇到尚未支持的页面或恢复失败
- **THEN** 当前任务安全失败并保留证据，后续由 MBH/Codex 独立分析后把稳定处理方法写回固定程序

#### Scenario: 已验证的未成年人模式弹窗再次出现
- **WHEN** 启动阶段页面明确显示未成年人模式或青少年模式弹窗及可验证的关闭控件
- **THEN** 固定程序执行一次关闭、重新观察页面，并在仍有评论面板时继续既有有界恢复，不依赖 MBH/Codex 在线控制

### Requirement: 设备独占
系统 MUST 保证同一时间每台设备最多由一个执行者发送点击、滑动或输入动作，运行管理 MUST 以真正持有设备锁的项目 Worker 进程作为状态依据。

#### Scenario: 同一设备已有 Worker
- **WHEN** 另一个执行者尝试领取或控制已被占用的设备
- **THEN** 系统拒绝并发控制，直至原执行者完成或锁被安全回收

#### Scenario: Worker 外壳退出但子进程仍在
- **WHEN** 包装进程已经退出但真正的项目 Worker 仍持有设备锁
- **THEN** 系统继续报告设备 Worker 运行中，不启动第二个 Worker
