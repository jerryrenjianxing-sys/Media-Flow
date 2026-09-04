## ADDED Requirements

### Requirement: 看门狗恢复不得重放中断任务
后台宿主因 Worker 意外退出而重新拉起该设备 Worker 时，系统 MUST 先把原 Worker 留下的运行中任务收口为失败并保留证据；新 Worker 只能领取其他尚未开始的等待任务。

#### Scenario: Worker 在可能改变状态后退出
- **WHEN** Worker 在运行任务期间意外退出且后台宿主重新拉起该设备 Worker
- **THEN** 原任务以 `worker_interrupted` 失败终态结束并且不会自动重新提交或从头执行

#### Scenario: Worker 空闲时退出
- **WHEN** Worker 没有运行中任务时意外退出
- **THEN** 后台宿主可重新拉起 Worker，任务历史保持不变

