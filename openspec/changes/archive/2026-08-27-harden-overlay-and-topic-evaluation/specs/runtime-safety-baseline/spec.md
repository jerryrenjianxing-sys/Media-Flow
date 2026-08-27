## MODIFIED Requirements

### Requirement: 正式运行链路
RiskFlow 的正式运行链路 SHALL 为控制台、本地控制接口、SQLite 任务队列、每设备独立 Worker、uiautomator2 固定程序和 Android 设备；MBH 与 Codex 只承担探索、调试、新异常分析和参考标签候选生成。经 MBH/Codex 单步验证且具有明确页面信号的恢复步骤 SHALL 以有界规则写回固定程序，日常运行不得要求 MBH/Codex 常驻。

#### Scenario: 日常批量执行
- **WHEN** 用户从控制台提交已支持的自动化任务
- **THEN** 任务由固定 Worker 和 uiautomator2 程序执行，不要求 MBH 或 Codex 常驻参与

#### Scenario: 遇到未知页面
- **WHEN** 固定程序遇到尚未支持的页面或恢复失败
- **THEN** 当前任务安全失败并保留证据，后续由 MBH/Codex 独立分析后把稳定处理方法写回固定程序

#### Scenario: 已验证的未成年人模式弹窗再次出现
- **WHEN** 启动阶段页面明确显示未成年人模式或青少年模式弹窗及可验证的关闭控件
- **THEN** 固定程序执行一次关闭、重新观察页面，并在仍有评论面板时继续既有有界恢复，不依赖 MBH/Codex 在线控制
