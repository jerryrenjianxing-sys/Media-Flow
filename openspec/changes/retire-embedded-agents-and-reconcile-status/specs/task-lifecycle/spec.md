## ADDED Requirements

### Requirement: 执行者与设备等待分别解释
任务查询 SHALL 保留真实状态、检查点数量、等待原因及更新时间，执行者中断不得据此推断设备离线。缺少结果 SHALL 显示尚无结果；批次暂停与执行者存活、设备连接分别表达。只读查询 MUST NOT 恢复、收口或提交任务。

#### Scenario: 中断后保存进度
- **WHEN** 任务waiting_device且reason_code为worker_interrupted
- **THEN** 显示执行者中断、进度已保存，并保留批次暂停与独立设备连接信息

#### Scenario: 尚无执行结果
- **WHEN** 等待任务没有检查点统计和最终结果
- **THEN** 返回未知数量，不伪造零、失败或成功
