## ADDED Requirements

### Requirement: 外部Skill平台不依赖内嵌引擎
发行资源 SHALL 包含完整平台Skill和现用共用修复材料，不要求OpenCode/Pi源码、二进制或凭证。旧聊天入口 SHALL 返回退役说明，不实例化引擎；历史数据保留。

#### Scenario: 无内嵌引擎资源
- **WHEN** 在隔离目录装配平台资源且没有OpenCode/Pi
- **THEN** Skill和共用材料校验通过，清单标记外部Agent模式，不生成内嵌引擎目录

#### Scenario: 数字识别离线资源完整
- **WHEN** 装配现行平台Windows发行目录
- **THEN** 必须包含与执行器对应的v1/v2角标字库，缺失或空字库阻断构建；不携带原始设备截图或私人模板
