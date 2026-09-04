## ADDED Requirements

### Requirement: 模型降级结果必须成为独立终态
任务生命周期 SHALL 在现有成功、失败、安全停止和取消之外区分 `degraded` 降级终态；降级任务必须具有结束时间、结构化原因和已完成进度，并且不得进入完整成功率分子。

#### Scenario: 可选模型能力不可用
- **WHEN** 非主题只读任务完成浏览，但可选模型能力在执行期间不可用
- **THEN** 任务以 `degraded` 终态结束并显示模型故障原因
- **AND** 已完成视频数继续保留

#### Scenario: 必需模型能力不可用
- **WHEN** 主题必需任务的模型通道熔断打开
- **THEN** 任务以 `failed` 终态结束并使用模型通道失败错误码
- **AND** 该任务不得显示为成功或降级成功

#### Scenario: 汇总成功率
- **WHEN** 控制台汇总二十视频验证轮次
- **THEN** 只有 `completed` 终态进入完整成功轮次
- **AND** `degraded`、`failed`、`stopped` 和 `cancelled` 分别展示且不计入完整成功轮次
