## ADDED Requirements

### Requirement: 默认 OpenSpec 变更流程
RiskFlow 对会修改代码、配置、接口、数据结构或运行行为的需求 SHALL 默认先创建或更新 OpenSpec change；用户无需在每次请求中重复指定该流程。

#### Scenario: 收到行为改动请求
- **WHEN** 用户要求增加、修改、优化或修复会影响系统行为的内容
- **THEN** Codex 先检查活动 change，有相关 change 时更新它，否则创建新 change，再进入实施

#### Scenario: 用户没有重复指定 OpenSpec
- **WHEN** 用户提出行为改动但未提及 OpenSpec
- **THEN** Codex 仍按默认 OpenSpec 流程处理

#### Scenario: 无行为影响的低风险修改
- **WHEN** 改动仅涉及纯文案、注释、说明或小型可逆样式且不改变行为
- **THEN** 可以使用简化流程，但必须记录修改并完成与风险相称的验证

#### Scenario: 涉及独立授权的动作
- **WHEN** OpenSpec change 包含付费、数据删除、真实设备写操作或线上发布
- **THEN** 默认流程不得被视为这些动作的预授权，Codex 必须在执行前获得对应明确授权
