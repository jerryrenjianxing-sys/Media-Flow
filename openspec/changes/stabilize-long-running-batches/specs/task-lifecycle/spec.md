## MODIFIED Requirements

### Requirement: 看门狗恢复不得重放中断任务
后台宿主重新拉起Worker时系统 MUST 对没有安全检查点的旧任务保留worker_interrupted失败规则。有dev.39检查点的非终态任务 SHALL 保留原任务和计数进入等待恢复；未确认动作不得重放，应关闭相应能力并在身份/页面复核后继续下一名额。已失败/停止/取消任务 MUST NOT 重写为等待。

#### Scenario: Worker 在可能改变状态后退出
- **WHEN** 检查点含未确认写入动作
- **THEN** 该动作标为unknown而非成功，不再次执行；安全复核通过后继续下一名额，否则仅本设备等待

#### Scenario: Worker 空闲时退出
- **WHEN** Worker没有运行中任务时意外退出
- **THEN** 后台宿主可重新拉起，任务历史保持不变
