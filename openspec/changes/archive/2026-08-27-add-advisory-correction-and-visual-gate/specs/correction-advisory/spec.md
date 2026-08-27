## ADDED Requirements

### Requirement: 纠错分析与设备执行隔离
系统 SHALL 由独立后台分析器异步处理异常证据；分析器 SHALL NOT 持有设备连接、调用 ADB、点击、滑动、输入或重放任务。

#### Scenario: 新异常入队
- **WHEN** 固定执行器记录一条异常
- **THEN** 异常 SHALL 进入待分析状态
- **AND** 设备 Worker SHALL 无需等待分析完成即可继续或收口当前任务

### Requirement: 纠错建议只读且结构化
纠错分析结果 SHALL 只包含分类、摘要、建议规则、置信度和风险等级，并明确 `auto_applicable=false`。

#### Scenario: 分析完成
- **WHEN** 模型返回有效纠错建议
- **THEN** 系统 SHALL 将建议写入原异常记录
- **AND** 系统 SHALL NOT 自动修改规则、执行建议或重放状态未知的动作

### Requirement: 分析失败不影响执行终态
纠错分析失败 SHALL 只标记分析状态，不得改变任务的完成、失败、停止或取消终态。

#### Scenario: 模型服务不可用
- **WHEN** 异常分析请求失败
- **THEN** 异常 SHALL 标记为分析失败并保留错误摘要
- **AND** 原任务终态 SHALL 保持不变
