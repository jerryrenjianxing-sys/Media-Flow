# Task 1 报告 — automation 代办与内容计划预检

## 结果

后端和标准库客户端已接入内容计划、任务预设、模型及消息提醒操作。所有新增写操作复用 `/api/automation` 的持久化 `request_id` 回执；相同请求重复、并发和进程重建不重复副作用，已接收但结果未知的写操作不会重放。模型状态为纯读取；测试和启用只调用现有 provider 方法，不接收或返回 Key。

`plan_tasks` 现在先核对内容计划和修订，再从所选修订的第一个启用主题填充预检字段。所选内容计划对 `topic_prompt` / `search_query` 是权威来源，不受空值或旧预设残值影响；正式排队继续由现有轮次映射冻结修订并按 A/B/C/A/B 跨设备一致循环。`general` 明确清除并忽略内容计划字段。

`task_evidence` 新增只读结果摘要，区分请求数量与真实结果；没有持久化结果时 `result_summary` 为 `null`，不会把未知计数报告成 0。

## 通用响应包络

所有 action 返回：

```json
{
  "ok": true,
  "api_version": "1",
  "request_id": null,
  "status": "observed",
  "reason_code": "observed",
  "user_message": "已读取实际业务回执",
  "retryable": false,
  "operation_id": null,
  "plan_id": null,
  "result": {}
}
```

写操作必须在顶层提供 `request_id`，并可选提供 `session_id`；读取不需要。下表的 `args` 指顶层 `arguments`，`result` 指包络内的 `result`。业务拒绝返回 `ok:false,status:"blocked",reason_code:"business_rejected"`；写入结果无法确认返回 `ok:false,status:"unknown",reason_code:"result_unknown",retryable:false`。`model_test` 失败保留真实 `reason_code`，不会包装成成功。

## 精确接口表

| action | 写入 | arguments | `result` |
| --- | --- | --- | --- |
| `content_plan_list` | 否 | `{include_archived?: boolean}`，默认 `false` | `{content_plans: ContentPlanRevision[]}` |
| `content_plan_get` | 否 | `{content_plan_id: string, revision_id: string}` | `{content_plan: ContentPlanRevision}`；计划与修订不匹配即拒绝 |
| `content_plan_save` | 是 | `{document: ContentPlanDocument, content_plan_id?: string}`；有 ID 时保存不可变新版本，无 ID 时新建计划 | `{status:"completed", reason_code:"content_plan_saved", user_message, content_plan: ContentPlanRevision}` |
| `content_plan_archive` | 是 | `{content_plan_id: string}` | `{status:"completed", reason_code:"content_plan_archived", user_message, archived:true, content_plan_id}`；历史修订仍可 get |
| `preset_list` | 否 | `{}` | `{presets: Preset[]}` |
| `preset_save` | 是 | `{name: string, config: object}` | `{status:"completed", reason_code:"preset_saved", user_message, preset: Preset, presets: Preset[]}`；不创建任务 |
| `model_status` | 否 | `{provider?: "openrouter"|"qwen_token_plan"}` | `{model: ModelStatus}`；只调用现有 `model_providers.status`，不测试、不联网验证 |
| `model_test` | 是 | `{provider:"openrouter"|"qwen_token_plan", upload_consent?:boolean}` | `{status:"passed"|"failed"|"blocked", reason_code, user_message, model:ModelStatus}`；千问把 consent 原样交给现有测试门禁，OpenRouter 不接受 true |
| `model_activate` | 是 | `{provider:"openrouter"|"qwen_token_plan"}` | 成功为 `{status:"completed", reason_code:"model_activated", user_message, model:ModelStatus}`；已知 provider 前置拒绝为 `{status:"blocked", reason_code:<provider code>, user_message, model:ModelStatus}`。现有空闲、暂停、测试通过门禁继续生效 |
| `notification_list` | 否 | `{status?:"unread"|"viewed", limit?:integer 1..100, offset?:integer >=0}` | `{notifications:Notification[], total, unread_count, limit, offset}` |
| `notification_acknowledge` | 是 | `{notification_ids:string[]}` | `{status:"completed", reason_code:"notifications_acknowledged", user_message, acknowledged, acknowledged_ids, remaining_unread}`；仅确认平台提醒，不代表抖音已读 |

`ContentPlanRevision` 的键为 `{id,plan_id,name,archived,current_revision_id,created_at,updated_at,revision_id,revision_number,document,revision_created_at}`。`ContentPlanDocument` 继续使用既有规范化结构 `{name,comment_template,common_comment_pool,themes}`，每个主题为 `{id,name,topic_prompt,search_query,comment_template,comment_pool,enabled}`。

`Preset` 为 `{name,builtin,config}`；`config` 由既有 `PRESET_FIELDS` 规范化。`Notification` 为既有提醒记录 `{id,task_id,device_id,sources,summary,fingerprint,status,detected_at,viewed_at}`。

`ModelStatus` 复用既有 provider 公共状态字段，公共字段为 `{provider,provider_id,model,key_configured,storage_status,auth_status,model_test_status,model_ready,has_pending_key,message,active_provider,config_version,providers}`；千问另含 `{last_model_test_at,last_model_latency_ms,reason_code,experimental,request_limit,requests_used,requests_remaining,credits,usage_message,can_enable}`，OpenRouter 保留其既有验证时间与候选状态字段。`api_key`、`key`、`key_ref`、`active_key`、`authorization` 会从嵌套响应中移除。

## `task_evidence` 增量

`result.task` 保留原字段，并新增：

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

结果不存在时 `result_summary:null`；结果存在但某个计数字段缺失或无效时该字段为 `null`。原始 decisions、结果路径、截图路径、提示词和其他未列字段不会返回。

## RED / GREEN

专用 Python：`C:/Users/jerry/Documents/Codex/2026-08-19/wo-c/work/agent-runtime/python/python.exe`；`PYTHONPATH` 指向工作区 `fixed_runner`，`PYTHONUTF8=1`。

初始 RED：

```text
python.exe -m unittest fixed_runner.test_automation
Ran 25 tests in 8.468s
FAILED (failures=5, errors=2)
```

失败均为预期缺口：新 action 未支持、内容计划仍要求重复 `search_query`、general 未忽略计划 ID、`task_evidence` 无结果摘要、客户端把新读取误判为写入。

针对空值/旧值权威覆盖的补充 RED：

```text
python.exe -m unittest fixed_runner.test_automation.AutomationTests.test_content_plan_preflight_resolves_revision_before_required_theme_fields
Ran 1 test in 0.056s
FAILED (failures=1)
```

最终 automation GREEN：

```text
python.exe -m unittest fixed_runner.test_automation
Ran 25 tests in 9.131s
OK
```

要求的基线组合 GREEN：

```text
python.exe -m unittest fixed_runner.test_automation fixed_runner.test_skill_bundle fixed_runner.test_skill_onboarding
Ran 40 tests in 34.365s
OK
```

相邻内容计划、模型、通知 GREEN（全部为隔离存储/fake transport，无真实模型或设备调用）：

```text
python.exe -m unittest fixed_runner.test_content_plans fixed_runner.test_model_providers fixed_runner.test_home_badge
Ran 43 tests in 4.025s
OK
```

平台 facade GREEN：

```text
python.exe -m unittest fixed_runner.test_agent_platform
Ran 19 tests in 1.893s
OK
```

### 评审后模型错误分类修正

根因：`model_activate` 的已知 `ProviderError` 和 OpenRouter 配置互斥锁的普通 `RuntimeError` 都越过薄适配器，被外层当成不确定执行异常。互斥锁现在抛出仍兼容 `RuntimeError` 的 `ModelOperationBusyError(reason_code="model_operation_busy")`；automation 只捕获该类型及 `ProviderError`。其他未分类 `RuntimeError` 继续返回持久化 `unknown`，不会因扩大捕获范围而误报为安全拒绝。

类型 RED：

```text
python.exe -m unittest fixed_runner.test_model_connection.ModelConnectionTest.test_exclusive_model_operation_busy_refusal_has_a_typed_reason
Ran 1 test in 0.001s
FAILED (failures=1)
```

automation 分类 RED：

```text
python.exe -m unittest fixed_runner.test_automation.AutomationTests.test_model_activation_provider_refusal_is_blocked_and_cached fixed_runner.test_automation.AutomationTests.test_typed_model_operation_busy_refusal_is_blocked fixed_runner.test_automation.AutomationTests.test_untyped_model_runtime_failure_remains_durable_unknown
Ran 3 tests in 0.136s
FAILED (failures=2)
```

修正后针对性 GREEN：

```text
python.exe -m unittest fixed_runner.test_model_connection.ModelConnectionTest.test_exclusive_model_operation_busy_refusal_has_a_typed_reason fixed_runner.test_automation.AutomationTests.test_model_activation_provider_refusal_is_blocked_and_cached fixed_runner.test_automation.AutomationTests.test_typed_model_operation_busy_refusal_is_blocked fixed_runner.test_automation.AutomationTests.test_untyped_model_runtime_failure_remains_durable_unknown
Ran 4 tests in 0.153s
OK
```

修正后聚焦回归（automation + 两套现有模型实现）：

```text
python.exe -m unittest fixed_runner.test_automation fixed_runner.test_model_connection fixed_runner.test_model_providers
Ran 57 tests in 11.712s
OK
```

## 修改文件

- `fixed_runner/automation_business.py`：薄业务适配、参数白名单、模型结果真实性与脱敏。
- `fixed_runner/model_connection.py`：模型配置互斥锁使用可分类、兼容 `RuntimeError` 的忙碌异常。
- `fixed_runner/automation.py`：catalog/write receipt 分类及业务 dispatch 连接。
- `fixed_runner/agent_platform.py`：内容计划优先预检、general 忽略语义、任务结果白名单摘要。
- `fixed_runner/assets/agent/skills/mediaflow-platform/scripts/_common.py`：新增只读 action 分类；无 Skill 文档变更。
- `fixed_runner/test_automation.py`：持久化/并发/重启/未知、版本/归档、预检/冻结/轮换、模型/通知、客户端及结果摘要回归。
- `fixed_runner/test_model_connection.py`：模型配置忙碌异常类型回归。
- `.superpowers/sdd/dev35-plan/task-1-report.md`：本报告。

## 担忧与边界

- 并发相同 `request_id` 时，原请求仍在执行的调用可能先得到 `unknown`；不会再次执行。原请求随后正常完成时可用只读 `request_status` 取得最终服务端回执；若原请求本身在副作用后丢失结果，则服务端和客户端都永久保留 `unknown`，要求核对原业务对象而不重放。
- `model_test` 是生产中可能消耗额度的显式写 action；本轮测试只使用 fake transport。千问仍要求上传同意并保留 10 次限额，OpenRouter 仍走美元预算；接口不提供 Key 保存能力。
- `model_activate` 仍要求任务/初始化/分析空闲且队列暂停。失败返回现有具体原因，不修改原有效配置。
- 通知确认只改变 MediaFlow 平台提醒状态，不打开消息页，也不表示外部平台消息已读。
- 本任务未修改 Skill Markdown、清单、版本、OpenSpec、README/STATUS，未运行设备、业务任务、真实服务或真实模型。
