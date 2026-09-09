# task-lifecycle Specification

## Purpose
TBD - created by archiving change stabilize-runtime-lifecycle. Update Purpose after archive.

## Requirements

> 适用范围（现行）：自检后自动提交条款仅适用于旧显式维护流程，不是普通提交门槛。dev.39长批次按原检查点等待和恢复，单条失败不取消整批；当前已实施要求见 [长批次变更规格](../../changes/stabilize-long-running-batches/specs/task-lifecycle/spec.md)。旧failed/stopped/cancelled终态不迁回等待，未知写入不重放。历史看门狗收口条款不能用于抹掉新检查点。

### Requirement: 实际轮末休息
新计划 SHALL 冻结 `round_interval_basis=completion`，每台设备在本轮结束后休息配置分钟再开始下一轮；本轮安排的检查先运行，检查收口后计休息。等待时间 MUST 持久化、阻止后续轮次越过，并隔离其他设备及批次。历史缺少字段 SHALL 保留 scheduled 排程。

#### Scenario: 视频执行比提交间隔更久
- **WHEN** completion 任务指定一分钟间隔且上一轮执行十分钟
- **THEN** 下一轮仍在该轮实际收口后至少六十秒才能领取，重启不重置等待或跳轮

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
系统 SHALL 允许将一个或多个 `pending`、`waiting_model`、`waiting_device`、`waiting_user` 任务原子转为 `cancelled` 终态，运行中或已结束任务不得被取消。显式停止等待或恢复中的原任务 SHALL 与设备停止标志原子协调，不允许探针随后自启。

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

### Requirement: 看门狗恢复不得重放中断任务
历史未启用检查点的任务 MUST 保持失败收口且不重放。新resilience_version=v1任务 SHALL 保存原任务、轮次、阶段、下一名额、随机状态和确认动作；已证实旧执行者退出时转等待设备并恢复原任务下一名额，未知写入不重放且暂停本设备本批次该能力。清理 MUST 在事务内核对观察到的执行者与开始时间，不能释放新执行者的租约。等待释放动作租约、保留同设备队首，显式维护和人工接管优先。

#### Scenario: Worker 在可能改变状态后退出
- **WHEN** Worker 在运行任务期间意外退出且后台宿主重新拉起该设备 Worker
- **THEN** 历史任务以 `worker_interrupted` 失败终态结束；v1任务保留检查点并等待身份/目标流复核后从下一名额继续，两者均不得从头或重放未知写入

#### Scenario: Worker 空闲时退出
- **WHEN** Worker 没有运行中任务时意外退出
- **THEN** 后台宿主可重新拉起 Worker，任务历史保持不变

### Requirement: 模型降级结果必须成为独立终态
任务生命周期 SHALL 在现有成功、失败、安全停止和取消之外区分 `degraded` 降级终态；降级任务必须具有结束时间、结构化原因和已完成进度，并且不得进入完整成功率分子。

#### Scenario: 可选模型能力不可用
- **WHEN** 非主题只读任务完成浏览，但可选模型能力在执行期间不可用
- **THEN** 任务以 `degraded` 终态结束并显示模型故障原因
- **AND** 已完成视频数继续保留

#### Scenario: 必需模型能力不可用
- **WHEN** 主题必需任务的模型通道熔断打开
- **THEN** 历史任务保持failed；v1任务按临时或永久故障进入waiting_model或waiting_user，零散失败留证继续且轮末degraded
- **AND** 等待不得显示为成功、结束或用户停止，也不取消其他设备的批次任务

#### Scenario: 汇总成功率
- **WHEN** 控制台汇总二十视频验证轮次
- **THEN** 只有 `completed` 终态进入完整成功轮次
- **AND** `degraded`、`failed`、`stopped` 和 `cancelled` 分别展示且不计入完整成功轮次

### Requirement: 自检通过后单次提交当前方案
系统 SHALL 仅在自动运行开关开启且自动接管自检通过后，为该新虚拟机按当时保存的当前方案提交一次任务；自动提交任务遵守暂停、设备独占和全部既有安全门。

#### Scenario: 自动运行开启
- **WHEN** 新虚拟机自检通过、自动运行开启且不存在同一初始化对应的正式任务
- **THEN** 系统为该设备提交一次当前方案，并用初始化 ID 标记任务来源

#### Scenario: 自动任务已有终态
- **WHEN** 同一初始化对应的正式任务已经完成、降级、失败、停止或取消
- **THEN** 系统不得再次提交该任务

#### Scenario: 调度已暂停
- **WHEN** 自动任务已排队但全局调度处于暂停状态
- **THEN** 任务保持等待且系统不得自动恢复调度

### Requirement: 视频轮次与巡检任务在提交时确定排序
系统 SHALL 按每台设备各自的视频轮次计数，在每完成 `inspection_every_rounds` 个完整视频轮次后插入一个互动巡检任务。巡检间隔 MUST 为有上限保护的正整数，默认值为 5；关闭巡检时 MUST 生成与历史行为相同的纯视频轮次队列。

#### Scenario: 十二轮每五轮巡检
- **WHEN** 用户为一台设备提交 12 个视频轮次并启用每 5 轮巡检
- **THEN** 同设备领取顺序为第 1 至 5 轮、巡检、第 6 至 10 轮、巡检、第 11 至 12 轮
- **AND** 不为最后不足 5 轮的部分额外插入巡检

### Requirement: 巡检不得抢占正在运行的轮次
互动巡检任务 MUST 使用与视频任务相同的每设备串行队列和设备独占规则，只能在前一个完整视频轮次进入终态后领取；系统不得在单条视频或互动动作中途插入巡检。

#### Scenario: 第五轮正在执行
- **WHEN** 第 5 个视频轮次仍为 `running` 且对应巡检已在队列等待
- **THEN** 巡检保持 `pending`，直到第 5 轮结束并释放任务执行权
- **AND** 同设备不存在第二个并发执行者

### Requirement: 巡检终态不改变后续排队计划
巡检任务的 `completed`、`degraded` 或 `failed` 终态 SHALL 独立保存，且不得自动取消、重排或从头重放后续视频轮次。

#### Scenario: 巡检部分不可用
- **WHEN** 第 5 轮后的巡检以 `degraded` 结束
- **THEN** 第 6 轮继续保持原排队位置并可由同设备 Worker 正常领取

### Requirement: 巡检任务保持提交批次关联
插入的巡检任务 SHALL 保存与对应视频轮次相同的提交批次、设备和配置快照，并记录巡检序号、触发轮次和间隔；巡检任务不得占用或改写视频 `round_index`。

#### Scenario: 多设备五轮提交
- **WHEN** 用户为四台设备分别提交 5 个视频轮次并启用巡检
- **THEN** 系统为每台设备各生成一个关联该设备批次的巡检任务
- **AND** 每台设备的视频批次仍显示 5 个视频轮次
