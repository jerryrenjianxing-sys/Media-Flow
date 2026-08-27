# task-lifecycle Specification

## ADDED Requirements

### Requirement: 暂停只控制领取
暂停 MUST 阻止 Worker 领取新任务，但不得把正在执行的任务显示为已停止或强制中断当前动作。

#### Scenario: 任务执行中点击暂停
- **WHEN** 一个任务正在执行且用户暂停调度
- **THEN** 当前任务继续到安全检查点，其他等待任务不再被领取，页面继续显示该任务运行中

### Requirement: 当前任务安全停止
用户请求安全停止时，Worker SHALL 在当前视频步骤完成并落盘后结束任务，将其标记为 `stopped` 终态，并不得自动重新领取或重放。

#### Scenario: 视频步骤间收到停止请求
- **WHEN** Worker 完成当前视频步骤并观察到本设备停止请求
- **THEN** 当前任务以 `stopped` 结束、记录已完成进度和停止原因，Worker 不领取下一项任务

#### Scenario: 暂无运行任务
- **WHEN** 用户请求安全停止但所选设备没有运行任务
- **THEN** 系统保持该设备不领取新任务并返回没有活动任务，而不制造失败记录

### Requirement: 等待任务可取消
系统 SHALL 允许将一个或多个 `pending` 任务原子转为 `cancelled` 终态，运行中或已结束任务不得被取消。

#### Scenario: 取消全部等待任务
- **WHEN** 用户确认取消全部等待任务
- **THEN** 所有当前 `pending` 任务进入 `cancelled`，历史记录、配置和证据继续保留

#### Scenario: 取消时任务已经被领取
- **WHEN** 目标任务已从 `pending` 进入 `running`
- **THEN** 取消不修改该任务，接口返回未取消并提示使用安全停止

### Requirement: 终态完整可见
任务列表和统计 MUST 区分 `completed`、`failed`、`stopped` 和 `cancelled`，所有终态 SHALL 具有 `finished_at` 且不再被 Worker 领取。

#### Scenario: 查看历史任务
- **WHEN** 页面读取包含新旧状态的任务记录
- **THEN** 成功、失败、安全停止、用户取消分别显示清晰文案和耗时

### Requirement: 历史清理独立受保护
清空历史 MUST 与取消和停止分离；存在 `running` 任务时不得清空，并继续要求显式确认。

#### Scenario: 运行中尝试清空
- **WHEN** 至少一个任务仍为 `running`
- **THEN** 系统拒绝删除任务和纠错记录，现有数据库和证据保持不变
