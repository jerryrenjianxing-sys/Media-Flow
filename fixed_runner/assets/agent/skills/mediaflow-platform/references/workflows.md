# 工作流

## 目标路由

1. 咨询或只写主题/搜索词/评论：直接回答；按需读 `content-guide.md`，不连接平台。
2. 保存内容或预设：连接后读取现有对象，保存并报告确定回执；不读取设备，不建计划。
3. 配置虚拟机/应用/输入：先读 `setup.md`，只补本次需要的缺项，不启动视频业务。
4. 运行：读取真实设备、现有计划/任务和所选内容修订，只问必要缺项，再预览与提交。
5. 复盘/恢复/修复：先读原计划、任务和证据，不用历史文本替代当前状态。

不强制完整问卷。用户已经说清的目标、范围、主题和参数继续保留；轮次、条数、停留等普通缺省直接采用并简短说明。

## 保存与修改

### 内容计划

内容计划保存主题、搜索词和评论素材。新建前可 `content_plan_list` 避免误建同类对象；查看或编辑指定版本用 `content_plan_get {content_plan_id,revision_id}`，两者必须匹配。

- 新建：`content_plan_save {document}`。
- 修改：保留完整 `document`，加原 `content_plan_id`；得到同计划的新不可变修订。旧修订仍可读取。
- 归档：`content_plan_archive {content_plan_id}`；不会删除历史版本。

每次写入使用新且稳定的 `request_id`。只保存时到此停止。多搜索组建成多个启用主题；程序在提交批次时冻结修订并按轮次映射，不在记忆中维护游标。完整形态和坚果工厂示例见 `content-guide.md` 与 `examples/content-plan-nut-factory.json`。

### 任务预设

预设只保存运行参数。`preset_list` 是读取，不会套用。用户明确说“采用某预设”时，才把该预设的字段应用于**本次明确范围**；不得因为看到旧预设就继承高点赞、收藏或评论概率。

`preset_save {name,config}` 同名时替换当前自定义预设，没有不可变版本历史。用户要修改并保留旧方案时，默认建议/使用新版本名（如 `严格生产搜索-v2`），不要把同名替换称为新版本。已冻结或已提交任务不受后续预设保存影响。`examples/preset-zero-write.json` 是可运行的严格搜索零互动参数示例。

### 记忆

`memory_save` 只保存用户稳定偏好。主题、内容计划、搜索轮换位置、任务状态和模型 Key 不属于记忆。编辑记忆用真实 `id/expected_version`；结果未知时查询原请求，不猜版本。

## 从目标到运行

1. 调用 `list_devices`、`list_tasks`，并用当前会话的 `plan_status` 核对计划。虚拟机使用清单中的永久 UUID 或当前 ADB 地址；用户明确选真机时使用 `list_devices.online` 中 `device_type=physical` 的准确 `device_id`。不按名称猜地址。平台默认仍用虚拟机，不用范围外设备补位。
2. 若用户选择内容计划，先读取真实 `plan_id/revision_id`。非 `general` 计划同时传 `content_plan_id` 和 `content_plan_revision_id`；计划会从首个启用主题补齐当前轮的 `topic_prompt/search_query`。不要重复询问已有字段，也不要让旧草稿字段覆盖所选修订。
3. 默认一轮20条、首页 `general`、停留8～25秒、不巡检、六项互动概率全0、`preview_only=true`。显式搜索用 `search`，只缺搜索词才问；严格生产/证据场景 `search_trust_results=false`。模式细节见 `content-guide.md#四种内容模式`。
4. `plan_tasks.arguments.config` 必须含 `device_ids,video_count,round_count,content_mode,engagement_inspection_enabled`。`search/hybrid` 需要 `search_query`，`mixed` 需要 `topic_prompt`；选内容计划时由真实修订补齐。启用巡检时传 `inspection_every_rounds`。
5. 用户只要方案、保存或“先不启动”时，可以解释或停在 `plan_tasks`，绝不调用 `execute_plan`。用户明确要运行且参数齐全时，`plan_tasks → execute_plan → plan_status`；不再要求固定口令、卡片或权限等级。

### 互动与评论

新混合计划默认 `hybrid_probability_mode=topic`：搜索、主页均用 `matched_like_probability/matched_favorite_probability/matched_comment_probability` 表示命中概率，`like_probability/favorite_probability/comment_probability` 表示未命中的安全内容概率；30%写 `0.3`。两套互不覆盖；未命中评论按当前内容生成，不套用目标主题素材。未指定时六项全部为0。旧任务 `legacy` 仍保留原分阶段规则，不自动迁移。

新计划默认 `round_interval_basis=completion`：`round_interval_minutes` 是每台设备本轮实际结束后的休息，有该轮消息检查时先检查、再休息。旧 `scheduled` 是从提交时间排程，不等于实际轮间休息。字段随计划冻结，恢复原批次不改值。

用户明确“只按轮数结束、不设时间停止”时传 `batch_stop_policy=round_count`。批次回执的 `deadline:null` 表示无执行时间截止，不是缺失配置；计划提交前的十分钟有效期是另一回事。未指定时沿用 `deadline` 策略。两种方式都保留暂停、安全停止与单次操作超时。

评论模板/词池属于内容计划；发送前约束 `comment_policy_enabled/comment_policy_prompt` 属于任务参数/预设。用户明确要求评论时才配置。只预览保持 `preview_only=true`；真实发送需本次明确目标并设 `false`，固定安全与画面复核仍优先。

### 队列、继续与恢复

先按 `task_evidence.result.task.status` 选择恢复对象：`waiting_model`（等待模型）、`waiting_device`（等待设备）、`waiting_user`（等待用户）用 `resume_task {task_id}` 恢复原任务/原检查点；仅处理用户指定的设备。临时模型错误连续3条进入等待，后端按30/60/120/300秒、之后每300秒共享一个探针复查，成功后由执行者复核设备身份和页面再继续。Agent 查询回执中的 `next_check_at`，不自建探针、不重复模型测试。明确鉴权、权限或服务商额度错误进入 `waiting_user`，用户在模型页面修复配置后由后端探针验证。设备等待由执行者在恢复时重新检查；单动作不可用只暂停该能力。未确认写入记录 `unknown_actions`，不会重放。

每轮20个名额中的单条失败消耗1个名额，不补刷；零散错误不取消批次。按设备/本轮报告成功、失败、不可用和已处理数量，安全供给跳过单列。`progress.schema_supported=false` 表示旧任务不支持这些名额计数，应读取原 `result_summary`，不能把新增计数字段的0当成实际完成0条。`degraded` 表示“完成，有异常”，可以进入后续轮次；`stopped` 统一报告“已停止”，只有 `error=stopped_by_user` 或 `result_summary.stopped_by_user=true` 才能归因为用户停止。停止等待任务用 `stop_task {task_id}`，原设备后续领取及模型复查恢复都受停止标志约束，其他设备继续。历史 failed/stopped/cancelled 不迁移、不自动恢复；下面的 `resume_plan` 是批次调度入口，不能替代单任务等待恢复。

普通队列 `paused=true` 不要求解除全局暂停：`execute_plan` 只放行本计划，其他等待任务保持暂停。不要恢复旧任务。明确“停止全部自动操作”或设备安全停止仍按真实回执阻断。

“继续”先查原计划：等待答案则补参数；已提交则用原 `plan_id` 恢复；只有过期且从未提交才 `repreview_plan` 并使用返回的新 `plan_id`。用户明确恢复任务安全停止时才传 `resume_stopped_devices=true`。设备在线但任务停止要报告停止原因，不能说设备关机。

写入超时/未知：先 `request_status` 查询原 `request_id`，再按对象查 `plan_status/content_plan_list/content_plan_get/virtual_operation_status/repair_update_status`。未知写入永久不重放。已知模型或业务阻断按返回的具体 `reason_code/user_message` 引导；不要把明确失败说成未知。

## 模型

外部 Agent/聊天模型负责理解用户和写文案；平台视觉模型负责视频画面主题与安全判断，两者不是同一个连接。纯文案、保存预设、读取任务都不要求平台视觉模型。

- `model_status` 纯读取，不测试、不调用付费验证，也不自动切换 provider。
- Key 由用户在原平台模型页面填写，automation 不接收、不回显 Key。
- 只有用户明确要求“测试”时才 `model_test`。千问沿用图片上传同意；OpenRouter 不接受 `upload_consent:true`。
- 只有用户明确要求启用指定 provider 时才 `model_activate`；原测试通过、队列暂停、任务/分析空闲等门禁继续生效。不因状态查询或测试通过自动启用。

平台不设本地请求次数或美元预算上限，不做剩余次数检查，不要求预算确认或加额度。`local_limits_enabled=false`、`request_limit:null`、`requests_remaining:null` 表示平台不限量；`requests_used` 是累计历史调用量，达到或超过 10 也可以继续。用量未知不显示为零费用；服务商实际额度以其工作台为准。

模型测试返回 `passed/failed/blocked` 及真实原因。失败不代表旧有效配置已经改变；不要自动改 provider、重试付费测试或把历史验收次数当当前余额。

## 消息巡检

新计划默认只在用户要求时设置 `engagement_inspection_enabled=true,inspection_mode="home_badge",inspection_every_rounds=N`。固定执行器在轮次结束后回到首页看角标，不点消息、不进列表：

- 已检查的两个业务状态是 `present`（有消息）与 `absent`（无消息）。清晰的 `3` 保留原文且 `message_count=3`；`99+` 原样报告且精确 `message_count=null`，不能写成99条或相加推断新增。
- `quantity_status=dot` 是纯红点、有消息但没有数字；`unreadable` 是已确认角标但数字尚未识别；`conflict` 是数量证据冲突。三者均不得猜数，后两者不单独否定已确认的 `present`。历史记录没有数量字段时说“未记录可靠数量”，不声称图上没有数字。
- 截图失败、遮挡、页面或角标存在性不明为 `unknown`（检查失败/未能确认），不是第三种业务结论，也不是无消息，不写0、不改变上一轮提醒状态。任务 `pending/running` 且结果为空时只报告“尚未检查 / 检查中，尚无结果”，不生成成功、失败或旧分区结论。冻结模式与结果版本冲突时明确报告冲突，保留证据，不拼接新旧结论。
- 持续存在只通知一次，消失后再出现才是新提醒；连续数字不能相加推断新增。

可选数量字段、原图与角标裁剪证据的读取入口见 [API 消息巡检回执](api.md#消息巡检回执)。优先读取已保存证据；不能通过点击消息、进入列表或 Agent/ADB 点击循环兜底。平台已启用的视觉兜底沿用原设置，不因数字不清自动开启模型。

`notification_list` 查询平台提醒，`notification_acknowledge` 只把指定平台记录改为 viewed。它不打开抖音消息页、不清除抖音角标、不代表抖音已读。

旧版 `inspection_mode=legacy` 详细巡检只保留历史只读，当前入口不创建、不切换、不复查旧模式。用户泛泛要求“看详情”或明确要求私信、点赞、访客等旧分区时，说明当前消息巡检只确认首页角标，并可读取已有历史记录；不推荐旧模式、不给旧模式执行参数、不替用户重跑。旧任务/预设原文与兼容 API 保留；这不改变 `hybrid_probability_mode=legacy` 的历史分阶段概率语义，也不改其他运行参数。

## 结果与复盘

dev.41起，动作确认区分 `confirmed`（本次成功）、`not_applied`（确认未生效）、`unknown`（结果未知）、`already_active`（原本已激活）。慢速保存证据不会否认已取得的成功；只有真实未知暂停对应能力，不重复点击。已有点赞/收藏不能再次点击取消，也不计为本次新增。

用户要求监督互动比例时，分开报告冻结概率、抽中次数、实际新增以及原本激活/模型跳过/空评论区/执行故障；不能只用所有视频数作统一分母。明确所用相对误差或百分点，不以少量样本宣称概率稳定，不补发互动凑数。开发阶段的设备数量、试跑名额和成功率目标按本次要求记录，不作为产品默认。

`plan_status` 说明批次是否提交与当前任务，不等于视频完成。对每个任务执行 `task_evidence {task_id}`，按以下顺序报告：

1. `task.status/finished_at`；
2. `task.requested` 仅作为请求目标；
3. `task.result_summary` 中真实 `videos_seen/topic_matches/skipped_videos/video_errors`、互动与模型计数；
4. `incidents`、`evidence_status`、`has_screenshot/has_ui_tree/evidence_url`。

`result_summary:null` 或某字段 `null` 表示没有持久化结果，不能写0。当前接口只给 `topic_matches` 聚合，不给四类相关性明细；不自行推算 `adjacent/unrelated/uncertain`。结合已有证据定位可能属于搜索来源、主题标准、模型、页面/设备或流程问题；证据不足时写“当前未核实”。“建议修改”与“已通过 content_plan_save/preset_save/model_activate 等确定回执修改”必须分开。

## 修复

先 `task_evidence/incident_evidence`，再按用户目标决定只诊断、创建候选还是应用：`repair_create → 宿主文件工具编辑返回的workspace_path → repair_test/repair_validate → repair_diff → repair_prepare_apply → repair_apply`。先失败回归再最小修复；完整验证通过并匹配补丁哈希才准备应用。候选不覆盖正式目录；更新等待空闲并保留旧版本和数据。

`repair_apply` 返回 `operation_id` 后用 `repair_update_status` 跟踪。回退也先生成候选、验证、准备、应用，不回滚用户数据。安装版独立更新链未验收时如实报告。不要访问其他项目、读取凭据、删除数据或用终端直接控制设备。
