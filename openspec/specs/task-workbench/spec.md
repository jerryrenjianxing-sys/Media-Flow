# task-workbench Specification

## Purpose
TBD - created by archiving change refactor-task-workbench-lifecycle. Update Purpose after archive.

## Requirements

### Requirement: 任务草稿自动保存且版本化
系统 SHALL 将任务台编辑内容保存为带递增版本的本地草稿，并在刷新后恢复；陈旧版本不得覆盖新版本。

#### Scenario: 页面刷新
- **WHEN** 用户修改任务参数并等待自动保存完成后刷新页面
- **THEN** 页面恢复相同草稿并显示服务端保存版本

#### Scenario: 陈旧草稿写入
- **WHEN** 客户端使用旧 revision 保存已经被其他页面更新的草稿
- **THEN** 服务端返回冲突和最新草稿，不覆盖新内容

### Requirement: 服务端生成唯一任务预览
系统 SHALL 由后端统一返回可执行设备、任务数量、预计耗时、动作摘要、警告和阻断原因；前端不得独立构造提交数量。

#### Scenario: 设备不可用
- **WHEN** 草稿选择了离线、未授权、被运行任务占用或需要初始化的设备
- **THEN** 预览逐台显示原因，并只将满足现有执行条件的设备计入可执行计划

### Requirement: 提交冻结不可变快照
系统 SHALL 只接受与当前草稿 revision 和计划哈希一致的提交，并将本次草稿与内容计划快照写入每个排队任务。

#### Scenario: 预览后草稿变化
- **WHEN** 用户预览后修改草稿或内容计划版本发生变化
- **THEN** 旧预览提交被拒绝，系统要求使用新预览

### Requirement: 写入任务条件确认
系统 SHALL 仅在任务可能点赞、收藏或真实发送评论时要求明确确认；纯观察任务无需二次确认。

#### Scenario: 评论仅预览
- **WHEN** 评论概率大于零但评论设置为仅生成预览，且点赞与收藏概率均为零
- **THEN** 任务被视为纯观察任务并可直接提交

#### Scenario: 任意写入动作
- **WHEN** 点赞或收藏概率大于零，或评论可能真实发送
- **THEN** 未携带写入确认的提交被拒绝，并返回需要确认的动作摘要

### Requirement: 旧接口保持兼容
系统 SHALL 保留既有配置、提交、预设、任务与历史记录接口；旧任务缺少草稿字段时继续按旧语义读取与执行。

#### Scenario: 旧客户端提交
- **WHEN** 旧客户端继续调用 `/api/config` 和 `/api/run`
- **THEN** 系统复用同一规范化与规划实现，不要求迁移历史数据
