# Task 2 报告 — Skill 业务引导

## 结果

`mediaflow-platform` 已收敛为“理解目标 → 定制主题 → 按需准备 → 保存/运行 → 结果/复盘”的短路由。纯主题、搜索词或评论文案请求优先直接完成，不运行首次平台检查，也不要求设备、登录或模型；仅交付 Skill 且没有其他明确目标时仍先执行原只读 `check`，服务状态不能确认时先询问，不自动启动。

完整内容规范已经放入 Skill 自带 `references/content-guide.md`，不依赖开发仓库 docs。新增坚果干果工厂内容计划示例与严格生产搜索零互动预设示例；两者通过打包后客户端在隔离 automation facade 上实际保存，内容计划随后冻结修订并按四轮依次映射四组搜索词。Markdown 复制与 ZIP 下载继续从同一白名单构建，新增文件均进入两种格式。

## 关键语义

- 内容计划保存主题、搜索词、全局/主题评论模板和词池；同 `content_plan_id` 修改产生不可变新修订，旧修订可读。
- 任务预设保存运行参数；同名 `preset_save` 是替换，没有不可变版本史。需保留旧方案时使用新版本名。查询预设不生效，高写入值仅在用户本次明确选择该预设及范围时采用。
- 记忆只保存稳定偏好，不保存主题、搜索轮换游标、任务状态或密钥。
- 多搜索组保存为多个启用主题，按轮次循环；同轮所有设备一致，每次新提交从首主题开始。不是逐视频换词。
- `general/mixed/search/hybrid` 与 `search_trust_results` 的真实路线已写明。严格生产检索默认 `false`；即使为 `true` 也只影响搜索阶段点赞/收藏概率路线，评论仍要求当前画面命中，且安全检查不跳过。
- 搜索结果、当前画面主题命中和发布者厂家身份互不等价；生产画面不能单独认证发布者身份。`uncertain` 保持独立，不伪造成 `unrelated` 或 `exact`，也不虚构准确率。
- 未指定互动时六项概率为 0，不继承旧草稿。只保存不调用 `plan_tasks/execute_plan`，普通队列暂停时只放行本计划，其他等待任务保持暂停。
- 外部 Agent/聊天模型与平台视觉模型已区分。纯文案不需模型；Key 只在原平台页面填写。`model_status` 纯读取，`model_test/model_activate` 只在用户明确要求时沿用原同意、费用/次数及空闲门禁。
- 复盘把 `requested` 作为目标，把可空 `result_summary` 作为真实结果；`null` 不写成 0。只引用已有 incidents/evidence，建议不冒充已修改。
- 首页角标保留清晰数字、`99+`、纯红点和 `unknown` 语义；确认平台提醒不清除抖音角标，也不表示抖音消息已读。

## Task 1 端点核对

所有写 action 在 automation 请求顶层需要稳定唯一 `request_id`；读 action 不需要。未知写入使用原编号 `request_status` 并读取业务对象，不换编号重放。

| action | arguments | 关键结果/语义 |
| --- | --- | --- |
| `content_plan_list` | `{include_archived?:boolean}` | `content_plans` |
| `content_plan_get` | `{content_plan_id,revision_id}` | 两个 ID 必须匹配，返回 `content_plan` |
| `content_plan_save` | `{document,content_plan_id?}` | 新建计划或保存不可变新修订 |
| `content_plan_archive` | `{content_plan_id}` | 归档，历史修订仍可读 |
| `preset_list` | `{}` | `presets`，读取不套用 |
| `preset_save` | `{name,config}` | 保存/同名替换，不创建任务 |
| `model_status` | `{provider?}` | 纯读取公开状态并脱敏 |
| `model_test` | `{provider,upload_consent?}` | `passed/failed/blocked` 与真实原因 |
| `model_activate` | `{provider}` | 原测试/空闲/暂停门禁保持 |
| `notification_list` | `{status?,limit?,offset?}` | `notifications,total,unread_count` |
| `notification_acknowledge` | `{notification_ids:[...]}` | 只确认平台提醒 |

`ContentPlanDocument` 精确使用 `{name,comment_template,common_comment_pool,themes}`；主题使用 `{id,name,topic_prompt,search_query,comment_template,comment_pool,enabled}`。`task_evidence.result.task` 的 `requested` 与可空 `result_summary`、以及摘要公开字段均按 Task 1 报告写入 API 与工作流参考。

## RED / GREEN

专用 Python：`C:/Users/jerry/Documents/Codex/2026-08-19/wo-c/work/agent-runtime/python/python.exe`；运行时 `PYTHONPATH=fixed_runner`、`PYTHONUTF8=1`。该路径只记录验证环境，不进入分发材料。

RED（先改测试，未改打包/文档）：

```text
python.exe -m unittest fixed_runner.test_skill_bundle fixed_runner.test_platform_skill_download
Ran 16 tests in 16.677s
FAILED (failures=4)
```

四处均为预期缺口：ZIP 白名单缺少三个新材料、Markdown 重建缺少相同材料、打包后的业务示例无法运行。旧 Skill 的独立行为基线已经记录在 `baseline-answer.md`，本任务没有重跑冒充 RED。

GREEN：

```text
python.exe -m unittest fixed_runner.test_automation fixed_runner.test_skill_bundle fixed_runner.test_skill_onboarding fixed_runner.test_platform_skill_download
Ran 46 tests in 41.727s
OK
```

覆盖：新业务 action 与客户端分类、ZIP 确定性/白名单、Markdown 逐文件重建并与 ZIP 内容一致、重建客户端 `--help`、首次只读 check、打包后内容计划/预设通过隔离 facade 保存、内容计划修订进入计划并按四轮映射。所有服务、存储和执行器均为 unittest 临时目录/隔离 fake；没有访问生产 API、真实设备或付费模型。

附加静态检查：所有 6 个随包 JSON 可解析；临时构建成功（17项白名单文件）；`git diff --check` 无内容错误；分发内容机器路径检查通过。没有保留临时 ZIP、回执或运行数据。

行为验证由 root 使用无历史新 Agent 执行。第一轮新版场景正确做到纯文案不连接、不执行，并理解内容计划与按轮轮换，但把运行/评测的四类相关性标签误作用户填写结构。依据这次真实失败，Skill 和内容指南收敛为可填写 `topic_prompt` 固定“命中 / 必须证据 / 排除”三段，坚果示例改为分段可复制块，四类标签只留在实际复盘说明，并把后三组搜索词从4个词缩为2个词。第二个无历史新 Agent 重测已按三段输出，且仍未连接或启动，行为缺口关闭；后续隔离保存/运行验收由 root 继续记录。

## 修改文件

- `fixed_runner/assets/agent/skills/mediaflow-platform/SKILL.md`：短路由、首次检查例外、保存/运行/复盘核心边界。
- `fixed_runner/assets/agent/skills/mediaflow-platform/README.md`：安装入口及新增材料说明。
- `fixed_runner/assets/agent/skills/mediaflow-platform/references/content-guide.md`：完整主题、搜索、模式、评论规范与坚果厂家示例。
- `fixed_runner/assets/agent/skills/mediaflow-platform/references/api.md`：Task 1 精确 action、schema、结果摘要与预设替换语义。
- `fixed_runner/assets/agent/skills/mediaflow-platform/references/workflows.md`：目标路由、保存、运行、模型、提醒、复盘、恢复及修复流程。
- `fixed_runner/assets/agent/skills/mediaflow-platform/references/troubleshooting.md`：内容/模型阻断和空结果的真实处理。
- `fixed_runner/assets/agent/skills/mediaflow-platform/examples/content-plan-nut-factory.json`：完整可运行内容计划参数。
- `fixed_runner/assets/agent/skills/mediaflow-platform/examples/preset-zero-write.json`：可运行零互动预设参数。
- `fixed_runner/skill_bundle.py`：17项同源白名单及纯文案优先的完整 Markdown 入口。
- `fixed_runner/test_skill_bundle.py`：白名单与隔离业务示例回归。
- `fixed_runner/test_platform_skill_download.py`：Markdown/ZIP 17项重建与下载回归。
- `.superpowers/sdd/dev35-plan/task-2-report.md`：本报告。

## 剩余事项

- 新版 Skill 的纯定制无历史独立 Agent 回归已经按上述 RED/修订/复测关闭；内容计划保存、改版和明确运行的端到端隔离验收由 root 继续汇总。
- Task 1 reviewer 发现的模型已知凭据/配置锁错误映射窄修复属于后端所有权，root 将在本提交之后处理；本 Skill 已按最终应有合同说明 `failed/blocked` 的可读原因，同时保留真正 `unknown` 写入不重放。
- 本任务未修改后端、Task 1 测试、版本总表、OpenSpec、根 docs/STATUS，也未更新本机运行版本、打包发行、推送远端、操作 MuMu 或执行真实业务。
