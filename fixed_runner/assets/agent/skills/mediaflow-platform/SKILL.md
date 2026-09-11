---
name: mediaflow-platform
description: Use when a user provides the MediaFlow homepage Skill, wants a content theme or reusable plan, configures an installed platform, or asks about devices, tasks, results, notifications, recovery, preferences, or project repair.
metadata:
  version: "43-content-round-continuity"
---

# MediaFlow 平台操作

## 先识别目标，再读所需资料

把用户目标办完，不做强制问卷。已有对话、回答和计划共同组成当前目标；用户已给出的字段不重复询问，普通缺省值直接采用并说明，只问真正改变结果的歧义。

按下面顺序路由：

1. **理解目标**：区分咨询、只定制文案、保存、配置、运行、恢复、复盘或修复。
2. **定制主题**：用户只要主题、搜索词、评论文案或示例时，直接完成文案，读取[内容计划、主题与评论](references/content-guide.md)；给用户可填写的 `topic_prompt` 固定用“命中 / 必须证据 / 排除”三段，不用模型的相关性标签替代。不检查平台，不要求设备、登录或模型。
3. **按需准备**：需要保存、读取或运行时才连接平台；需要虚拟机/应用时读[配置引导](references/setup.md)。
4. **保存或运行**：保存内容和运行任务是两个目标；保存成功绝不自动 `plan_tasks` 或 `execute_plan`。
5. **结果与复盘**：依据真实回执和 `task_evidence`，区分请求目标、实际结果、建议与已修改。

| 需要什么 | 读取 |
| --- | --- |
| 内容主题、搜索词、评论 | [references/content-guide.md](references/content-guide.md) |
| action、参数、响应字段 | [references/api.md](references/api.md) |
| 计划、运行、恢复、复盘、提醒、模型、修复 | [references/workflows.md](references/workflows.md) |
| MuMu、显示、抖音、输入、登录 | [references/setup.md](references/setup.md) |
| 超时、未知结果、连接或执行故障 | [references/troubleshooting.md](references/troubleshooting.md) |

## 首次接入

用户只交付本 Skill、没有其他明确目标时，立即做首次只读检查：

1. 确认宿主能访问用户这台 Windows 电脑。云端、容器或另一台电脑的回环地址不是用户电脑。
2. 在完整 Skill 目录运行 `./scripts/mediaflow.ps1 check`。它只 GET `/api/automation` 的登记快照，不需要系统 Python，不创建任务、不启动服务、不扫描设备。
3. 按真实结果报告“平台连接 → 已登记设备与检测时间 → 下一步”。`online:null` 表示当前在线未核实，不能用上次状态冒充在线。
4. 已核实服务未运行时问：“MediaFlow 尚未启动，需要我帮你启动吗？”仅超时或拒绝连接时说尚不能确认是否启动，先问再处理。用户同意后按[启动服务](references/setup.md#启动服务仅在用户同意后)；不自动启动、注册、修复或恢复队列。

**短路规则**：用户明确要求只阅读、解释、修改 Skill，或只定制主题/搜索词/评论文案时，服从该目标，不运行首次检查。首次检查也不要求模型 Key、设备在线、抖音登录或输入验证。

## 使用本地客户端

Windows 优先运行 `scripts/mediaflow.ps1`；它会使用显式配置或已安装平台提供的 Python 运行环境，不要求系统另装 Python。只有 Bash 且已配置 `MEDIAFLOW_PYTHON` 时才直接运行 `scripts/mediaflow.py`。默认 API 为 `http://127.0.0.1:48138`，只接受本机回环地址。

```powershell
./scripts/mediaflow.ps1 content_plan_list
./scripts/mediaflow.ps1 content_plan_save --arguments-file ./examples/content-plan-nut-factory.json --request-id REPLACE_WITH_UNIQUE_REQUEST_ID --session-id current-session
```

纯读不需要 `request_id`。每个写操作使用本次操作专用、稳定且唯一的 `request_id`，保存原回执；相同编号重复调用不会产生新副作用。写入超时或 `status=unknown` 时，用原编号执行 `request_status` 并查询对应业务对象，绝不换编号重放。结构化回执的 `submitted/active/queued/ready` 不等于视频业务已完成。

## 核心业务边界

- 内容计划保存主题、搜索词和评论素材；任务预设保存运行参数；记忆只保存用户偏好。不要把主题或搜索轮换游标存进记忆。
- 多搜索词用内容计划中的多个启用主题，按全程轮次循环；同轮所有设备一致，默认新提交从首主题开始。明确分阶段执行时用 `content_round_start` 保留轮次与巡检排期，见工作流；不是逐视频换词，也不是重放失败任务的入口。
- `general/mixed/search/hybrid` 与 `search_trust_results` 的真实含义见[内容指南](references/content-guide.md#四种内容模式)。严格生产检索默认 `search_trust_results=false`；搜索结果、主题命中和厂家身份是三个不同判断。
- 默认一轮 20 条、首页 `general`、停留 8～25 秒、不巡检、六项互动概率全 0。不要从旧草稿或查询到的高写入预设继承值。只有用户本次明确选择某个预设及范围时才采用；读取预设本身不生效。
- `plan_tasks` 只冻结/预览计划，`execute_plan` 才提交原计划。用户说“只保存、先不启动、给方案”时不执行。普通队列暂停不需恢复全部队列；执行本计划只放行本计划，其他等待和暂停状态保留。
- 外部 Agent 负责聊天与文字整理；平台视觉模型负责业务画面判断。纯文案不需要任何模型。Key 只能由用户在原平台界面填写；`model_status` 是只读，不测试、不调用模型。只有用户明确要求时才 `model_test` / `model_activate`，沿用上传同意、忙碌和测试通过检查，不自动切换 provider。
- 平台不设 API 次数或金额预算上限；无需再向用户索取预算或追加次数授权。累计用量只是记录，不是执行门槛。服务商实际额度、权限、限流及网络错误仍按真实结果处理，不能假称服务商不限量或免费。
- 正式业务的设备动作由固定执行器执行，不用外部 Agent 的点击循环替代 Worker。用户明确要求项目开发带测时，开发者可按项目 MBH 指引取得同一设备锁，单步观察与纠错，再释放锁交固定程序复验；本通用 Skill 不附带本机 MBH 路径或替用户启动带测。登录/验证码由用户在目标设备中完成。
- 当前功能叫“消息巡检”（`home_badge`），只看首页角标。已检查的业务结论是“有消息 / 无消息”；数量未识别与检查失败分开，pending/running 无结果时不下结论。旧详细巡检仅保留历史只读，不创建、切换或复查，也不作为“看详情”的推荐选项。数量、证据与提醒语义见[消息巡检](references/workflows.md#消息巡检)。

## 报告结果

长批次的等待、按名额计数和单设备恢复使用[持久进度与单任务控制](references/api.md#持久进度与单任务控制)。先读取原任务 `progress`：`waiting_model/waiting_device/waiting_user` 是可查询、可停止的等待，`degraded` 是“完成，有异常”，与用户停止不同。恢复用 `resume_task {task_id}` 保留原编号及进度，不能用新计划补刷或迁移历史终态。模型等待的复查由后端统一执行，Agent 不另建探针。

计划完成后读取每个实际任务的 `task_evidence`。`result.task.requested` 是请求的条数/轮次；真实完成数量、主题命中、跳过、异常、互动和模型情况只来自可空的 `result.task.result_summary`。`result_summary:null` 表示未保存结果，不能写成 0。异常现场只引用返回的 `incidents`、`evidence_status` 和证据入口；没有截图或分类明细就明确说未保存，不能猜。

给出：实际状态与完成数量 → 主题命中/跳过/异常 → 已有证据 → 下一步建议。建议调整主题、预设、模型或流程不等于已经修改；只有完成对应写操作并取得确定回执，才能说已修改。

本 Skill 不依赖特定 Agent 宿主或聊天模型。第三方归属见 [NOTICE](NOTICE.md)。
