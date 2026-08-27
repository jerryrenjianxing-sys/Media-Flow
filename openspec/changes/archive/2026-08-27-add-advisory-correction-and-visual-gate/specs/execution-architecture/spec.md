## MODIFIED Requirements

### Requirement: 固定执行器拥有唯一设备动作平面
系统 SHALL 只允许固定执行器持有设备连接并执行点击、滑动、输入和返回；AI、纠错分析器、控制接口和网页 SHALL NOT 直接操作设备。AI 结果 SHALL 仅作为固定执行器验证的结构化主题、安全或只读纠错数据。

#### Scenario: AI 返回建议
- **WHEN** AI 完成主题、安全或纠错分析
- **THEN** 系统 SHALL 将主题或安全结果作为结构化数据交给固定执行器验证，或将纠错结果保存为只读建议
- **AND** AI SHALL NOT 获得设备对象或点击接口

#### Scenario: AI 返回纠错建议
- **WHEN** 独立纠错分析器完成异常证据分析
- **THEN** 系统 SHALL 只保存结构化建议
- **AND** AI SHALL NOT 获得设备对象、点击接口、坐标执行或任务重放能力
