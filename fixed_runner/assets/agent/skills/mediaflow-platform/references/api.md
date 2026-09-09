# 本地Automation API v1

首次接入用 `./scripts/mediaflow.ps1 check`，仅GET `/api/automation`，不调用Python或业务POST。响应新增product、product_version、onboarding；onboarding含available、source=stored_snapshot、read_at、paused、task_summary和devices。设备仅提供登记名称、实例号、last_known_state、last_known_environment、last_seen_at、updated_at，online固定null；read_at是读取时间，不是设备检测时间。快照不可读时available=false、队列与设备值为null，不伪装零任务/零设备。

check的退出码0表示平台身份及连接已确认，不表示设备、Python或业务能力就绪。失败返回结构化reason_code、service_state=unknown、next_action=ask_user；连接失败不自动启动。只有已批准的配置/业务操作才使用下方旧状态工具，它们可能执行设备对账或任务收口。

GET 同时返回 `client_runtime`：api_url、management_url、mode（development/installed）、python、available、reason_code、user_message。Windows启动脚本用它自动找到平台运行环境；路径仅在当前本机接口返回，不写入可分发材料。缺失运行环境不会阻断查看动作清单，但客户端应提示修复平台，不能自动安装另一套开发工具。老接口没有此字段时保留显式Python配置兼容。

`GET /api/automation` 返回动作清单、mutates/request_id_required、connected及版本。connected仅表示模块接入，不保证设备、模型或独立安装更新链可用。`POST /api/automation` 接受 `action`、`arguments`对象、写入必需的 `request_id`、可选 `session_id`。请求编号和会话编号为1–100个字母、数字、下划线或连字符。会话只是批次关联，不需要在宿主注册。

响应含 `ok,status,reason_code,user_message,retryable,operation_id,plan_id,result`；result保留实际深模块回执。状态submitted/active/queued/ready不代表业务完成，worker_start_unconfirmed表示已存批次但执行者未确认。blocked/waiting_user/expired/failed/unknown/timeout/interrupted都应按原因处理。HTTP 202可代表未知结果，不能据HTTP状态断言成功。

所有 action 使用同一响应包络。读取通常为 `request_id:null,status:"observed"`；写入必须在请求顶层提供 `request_id`。业务拒绝为 `ok:false,status:"blocked",reason_code:"business_rejected"`。已接收但结果无法确认的写入为 `ok:false,status:"unknown",reason_code:"result_unknown",retryable:false`，不得重放。模型测试等已知失败会保留可读 `status/reason_code/user_message`；按其明确指引处理，不把已知阻断误报成未知成功。

## 内容计划、预设、模型与提醒

| action | arguments | result | 写 |
| --- | --- | --- | --- |
| `content_plan_list` | `{include_archived?:boolean}`，默认false | `{content_plans:ContentPlanRevision[]}` | 否 |
| `content_plan_get` | `{content_plan_id,revision_id}` | `{content_plan:ContentPlanRevision}`；ID不匹配即拒绝 | 否 |
| `content_plan_save` | `{document,content_plan_id?}` | `status=completed`及`content_plan`；有ID时新增不可变修订，无ID时新建 | 是 |
| `content_plan_archive` | `{content_plan_id}` | `archived:true,content_plan_id`；历史修订仍可get | 是 |
| `preset_list` | `{}` | `{presets:Preset[]}` | 否 |
| `preset_save` | `{name,config}` | `preset`及`presets`；不创建任务 | 是 |
| `model_status` | `{provider?:"openrouter"|"qwen_token_plan"}` | `{model:ModelStatus}`；只读状态，不测试 | 否 |
| `model_test` | `{provider,upload_consent?:boolean}` | `status=passed|failed|blocked`及真实原因 | 是 |
| `model_activate` | `{provider}` | `status=completed,model`；原空闲/暂停/测试门禁仍适用 | 是 |
| `notification_list` | `{status?:"unread"|"viewed",limit?:1..100,offset?:整数>=0}` | `{notifications,total,unread_count,limit,offset}` | 否 |
| `notification_acknowledge` | `{notification_ids:[...]}` | `{acknowledged,acknowledged_ids,remaining_unread}` | 是 |

`ContentPlanRevision` 为 `{id,plan_id,name,archived,current_revision_id,created_at,updated_at,revision_id,revision_number,document,revision_created_at}`。`document` 的真实结构为：

```json
{
  "name": "计划名",
  "comment_template": "全局评论模板",
  "common_comment_pool": [{"id":"common-1","text":"候选句","enabled":true}],
  "themes": [{
    "id": "theme-1",
    "name": "主题名",
    "topic_prompt": "命中、必须证据、排除",
    "search_query": "短搜索词",
    "comment_template": "主题补充",
    "comment_pool": [{"id":"theme-line-1","text":"主题候选句","enabled":true}],
    "enabled": true
  }]
}
```

需要 1～20 个主题且至少一个启用；计划名/主题名最多80字，`topic_prompt`最多800字，搜索词最多80字，模板最多1000字，词池最多100条且单条最多80字。保存响应会把词池规范化成上面的对象结构。

`Preset` 为 `{name,builtin,config}`。自定义同名 `preset_save` 是替换当前同名预设，**没有不可变版本历史**；想保留旧方案时用用户确认的新版本名（如“严格生产搜索-v2”）。读取预设不会套用或运行。内容计划的新修订、预设替换以及以后提交的新任务都不会改变已经冻结/提交的任务。

`ModelStatus` 保留 provider 的公开状态字段并递归移除 `api_key/key/key_ref/active_key/authorization`。Key 不通过本 API 保存或回显，只能由用户在原平台界面填写。千问测试把 `upload_consent` 原样交给既有门禁；OpenRouter 不接受 `upload_consent:true`。只读 `model_status` 不联网验证、不调用模型。`local_limits_enabled:false` 和 `request_limit:null/requests_remaining:null` 明确表示平台不设本地次数或金额上限，不是额度用完或配置缺失；`requests_used` 是累计历史用量，不是任务门槛。保留服务商真实配额/限流错误，不自动更换服务商或把未知用量当零费用。

`Notification` 为 `{id,task_id,device_id,sources,summary,fingerprint,status,detected_at,viewed_at}`。确认只改变 MediaFlow 平台提醒状态，不打开消息页、不清除抖音角标，也不表示抖音消息已读。

虚拟机 action 支持 start、stop、restart、clone、backup、repair_standard、settings、delete；安装配置引导优先复用已有实例，轻量创建走MuMu界面，不以clone代替空白创建。settings仅用于平台允许的宿主性能项，不猜分辨率字段；显示调整按配置引导执行。操作前后都用原command_id查询真实状态。

| action | arguments | 写 |
| --- | --- | --- |
| platform_status / list_devices | `{}` | 否 |
| list_tasks | limit(1–50，默认10),offset(0–100000) | 否 |
| content_plan_list / preset_list | 见上表 | 否 |
| content_plan_get / model_status / notification_list | 见上表 | 否 |
| content_plan_save / content_plan_archive / preset_save | 见上表 | 是 |
| model_test / model_activate / notification_acknowledge | 见上表 | 是 |
| plan_tasks | config：见workflows.md | 是，独立计划无设备动作 |
| execute_plan / resume_plan | plan_id，按用户恢复意图设置resume_stopped_devices=true | 是 |
| plan_status | 可选plan_id，省略列本关联最近计划 | 否 |
| repreview_plan / pause_batch / stop_batch | plan_id | 是 |
| task_evidence | task_id | 否 |
| resume_task / stop_task | task_id；原任务编号 | 是 |
| incident_evidence | incident_id | 否 |
| request_status | request_id（要查询的原请求） | 否 |
| plan_virtual_operation | virtual_device_id,action；可选settings,backup | 是，计划不启动设备 |
| execute_virtual_operation | command_id；删除须带真实confirmation_name | 是 |
| virtual_operation_status | command_id | 否 |
| memory_list / memory_history | `{}` / id | 否 |
| memory_save | title,body；编辑附id,expected_version，可用enabled=false禁用 | 是 |
| memory_restore | id,version,expected_version | 是 |
| repair_create | purpose | 是，返回id与workspace_path |
| repair_list / repair_status | `{}` / repair_id | 否 |
| repair_files / repair_diff | repair_id | 否 |
| repair_read | repair_id,path | 否 |
| repair_test | repair_id,mode(unit/syntax/python/frontend/lint/build/openspec/all),path（unit/syntax时） | 是 |
| repair_validate | repair_id，运行完整验证 | 是 |
| repair_test_status | test_id | 否 |
| repair_export | repair_id；返回已验证候选的artifact_path与patch_sha256，宿主文件工具可读取，不依赖旧会话下载链接 | 是 |
| repair_close / repair_cancel | repair_id | 是 |
| repair_prepare_apply / repair_apply | repair_id | 是 |
| repair_update_status | operation_id | 否 |
| repair_cancel_update / repair_prepare_rollback | operation_id | 是 |

源文件编辑由宿主文件工具在workspace_path完成；repair_edit/revert/delete不作为automation动作公开。不支持的动作返回unsupported_action，不返回模拟成功。宿主终端不要直接ADB控制设备或替换Worker。

Bash命令工具（Windows同样适用）在Skill目录调用：

```bash
"$MEDIAFLOW_PYTHON" scripts/mediaflow.py list_devices
"$MEDIAFLOW_PYTHON" scripts/mediaflow.py task_evidence --arguments '{"task_id":"已有任务编号"}'
```

直接读取脚本JSON和退出码，不用`| tail`吞掉失败退出码。仅当命令工具确实是PowerShell时用以下启动器（先取 `MEDIAFLOW_PYTHON`，再取本地配置的 `python`）：

```powershell
./scripts/mediaflow.ps1 list_devices
./scripts/mediaflow.ps1 plan_tasks --arguments-file task-arguments.json --request-id plan-unique-001 --session-id ses_current
```

`--arguments`可直接传JSON对象；Windows转义复杂或大参数使用`--arguments-file`。`--timeout`默认为30秒，上限120秒。写入返回超时不自动重试。`MEDIAFLOW_SKILL_CONFIG`或`--config`指定配置文件，客户端配置支持api_url与receipt_db，启动器还读取python；环境MEDIAFLOW_API_URL/MEDIAFLOW_RECEIPT_DB优先。回执默认位于当前用户LOCALAPPDATA/MediaFlow/skill/receipts.db，不能放进源码或发行包。

同一request_id同参数返回原接入响应（不刷新成后来业务状态）；同编号不同参数返回request_id_conflict。用查询动作获取实时状态。接入中断会永久保留未知回执，不能删除回执或换ID来试成功。

## 任务证据中的真实结果

### 持久进度与单任务控制

`list_tasks.result.tasks[]`、`task_evidence.result.task`、`plan_status.result.result.tasks[]` 的 `progress` 保留原任务/本轮检查点计数：`processed_slots,successful_slots,failed_slots,unavailable_slots,unknown_actions,skipped_slots`，以及 `waiting_reason,next_check_at,last_progress_at,next_slot,affected_device,affected_capabilities,available_actions,evidence_dirs`。批次回执中的每个任务均带 `progress`；按设备/批次汇总时明确范围，不把单轮数量当整个计划数量。`next_check_at` 是 Unix 秒；`null` 表示没有定时复查。证据目录只引用回执已有值。

已处理名额 = 成功 + 失败 + 不可用；20 条中失败 2 条就是成功 18、失败 2，不补刷到成功20条。`skipped_slots` 是安全识别的供给跳过，不消耗名额，不能加进已处理计数。`unknown_actions` 是未确认写入，不是成功次数，不重放；`affected_capabilities` 指仅本任务暂停的动作能力。

`resume_task {task_id}` 与 `stop_task {task_id}` 都要求稳定唯一的 `request_id`，支持原回执去重。页面同义接口为 `POST /api/tasks/{task_id}/resume`、`POST /api/tasks/{task_id}/stop`，请求体含 `request_id`。返回 `result.changed` 和 `result.task`（原编号、实际状态和最新 `progress`）。恢复成功为 `status=queued`，仅为原设备启动执行者；不是业务已开始或已完成。`worker_start_unconfirmed` 表示原任务排队成功但执行者未确认。未满足条件时 `ok=false`，保留 `waiting_model/waiting_device/waiting_user` 和具体原因，Agent 不宣称恢复成功。修复后新恢复操作使用新请求编号；网络未知则继续查询原编号。

等待时 `available_actions` 为 `resume_task,stop_task`。模型恢复需要当前配置的成功后端探针，Agent 不重复调用 `model_test`。`stop_task` 置该设备停止标志，阻断后端探针/同设备后续任务越过用户停止；不取消其他设备或整个批次。`failed/stopped/cancelled` 历史终态不可 `resume_task`，也不自动改写为等待。

`task_evidence.result.task` 保留任务公开字段，并含：

```json
{
  "requested": {"video_count": 10, "round_index": 2},
  "result_summary": {
    "result_status": "passed_with_recovery",
    "videos_seen": 8,
    "topic_matches": 5,
    "skipped_videos": 2,
    "video_errors": 1,
    "likes": 3,
    "favorites": 2,
    "comments_generated": 2,
    "comments_sent": 1,
    "model_attempts": 8,
    "model_valid_decisions": 7,
    "model_errors": 1,
    "stopped_by_user": false
  }
}
```

`requested` 是目标，不是完成数量。未保存结果时 `result_summary` 为 `null`；已保存结果中缺失或无效的计数仍为 `null`，不能写成0。该摘要只公开 `topic_matches` 聚合，不提供 `exact/adjacent/unrelated/uncertain` 完整分布；没有字段就不要推算。异常证据在同一结果的 `incidents`、`evidence_status` 与 `evidence_url` 中，最多使用已有证据，不重放历史任务。
