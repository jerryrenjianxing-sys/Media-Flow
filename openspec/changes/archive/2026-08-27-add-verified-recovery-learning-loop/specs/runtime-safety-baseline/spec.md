## MODIFIED Requirements

### Requirement: 正式运行链路
RiskFlow 的正式运行链路 SHALL 为项目独立运行环境中的控制台、本地控制接口、SQLite 任务队列、每设备独立 Worker、uiautomator2 固定程序和 Android 设备；MBH 与 Codex 只承担探索、调试、新异常分析和参考标签候选生成。经 MBH/Codex 单步验证且具有明确页面信号、允许动作、尝试上限和成功验证条件的恢复步骤 SHALL 进入版本化已验证规则目录，日常运行不得要求 MBH/Codex 常驻或借用其 Python 环境。

#### Scenario: 日常批量执行
- **WHEN** 用户从控制台提交已支持的自动化任务
- **THEN** 任务由项目独立环境中的固定 Worker 和 uiautomator2 程序执行，不要求 MBH、Codex 或其他项目环境常驻参与

#### Scenario: 遇到未知页面
- **WHEN** 固定程序遇到尚未支持的页面或恢复失败
- **THEN** 当前视频或任务按既有安全边界收口并保留证据
- **AND** 后续由 MBH/Codex 独立分析、验证稳定页面信号与恢复步骤后写入规则目录

#### Scenario: 已验证的未成年人模式弹窗再次出现
- **WHEN** 启动阶段页面明确显示未成年人模式或青少年模式弹窗
- **THEN** 固定程序优先点击语义“关闭”，缺失时点击已授权的语义“不再提醒”备用动作
- **AND** 系统重新观察页面，只有确认主信息流后才记录恢复成功并继续，不依赖 MBH/Codex 在线控制

