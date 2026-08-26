# 统一手机 Agent 池架构

> [!WARNING]
> 历史方案，未进入当前正式架构。AutoGLM/MBH 不参与 RiskFlow 日常运行；当前生产链路见根目录 `PROJECT.md` 和 `openspec/specs/runtime-safety-baseline/spec.md`。正文保留用于追溯当时的方案比较。

## 目标

让 AutoGLM 与 Codex + Mobile Harness 从同一任务池领取任务、读取同一种设备状态、回写同一种执行记录，并在失败时安全交接手机控制权。

核心原则：**共享信息，不共享并发控制权。**同一台手机在任一时刻只能有一个有效租约和一个执行者。

## 最小可用架构

```text
                     ┌─────────────────────┐
                     │     任务池 TaskPool  │
                     │ 目标/白名单/限制/状态 │
                     └──────────┬──────────┘
                                │ claim
                     ┌──────────▼──────────┐
                     │  调度器 Orchestrator │
                     │ 选工具/租设备/交接/重试│
                     └──────┬────────┬─────┘
                            │        │
                 ┌──────────▼─┐    ┌─▼──────────────┐
                 │AutoGLM适配器│    │Harness适配器    │
                 │常规低成本任务│    │Codex专家接管    │
                 └──────────┬─┘    └─┬──────────────┘
                            │        │
                     ┌──────▼────────▼─────┐
                     │  设备池 DevicePool   │
                     │ 租约/心跳/状态/独占锁 │
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │  实体机/云手机设备池  │
                     └─────────────────────┘

每一步同时写入 EvidenceStore：截图、UI树、动作、日志、风控反馈。
```

## 五个深模块

### 1. TaskPool

小型接口：

- `claim(worker_capabilities)`：领取一个匹配任务；
- `complete(task_id, result)`：完成任务；
- `escalate(task_id, handoff)`：升级给另一种工具。

内部隐藏任务优先级、重试、超时、工具选择和状态流转。

建议任务状态：

```text
queued
→ running_autoglm
→ escalated
→ running_harness
→ completed / failed / needs_human
```

### 2. DevicePool

小型接口：

- `lease(requirements)`：领取设备并返回带版本号的租约；
- `heartbeat(lease)`：证明执行者仍存活；
- `release(lease)`：释放设备。

租约必须包含超时和 fencing token。过期执行者即使恢复，也不能继续向手机发送动作，从而避免 AutoGLM 与 Harness 双击同一设备。

### 3. Runner

统一接口：

```text
run(task, device_lease, handoff_context?) → RunResult
```

两个真实 Adapter：

- `AutoGLMRunner`：翻译统一任务为 AutoGLM prompt，捕获逐步输出和错误；
- `MobileHarnessRunner`：通过 `mobilerun_core.Mobilerun`执行，保存观察—动作—复核记录。

调度器只认识 `Runner` 接口，不需要了解两套工具内部命令。

### 4. EvidenceStore

统一保存：

- 运行清单与版本；
- 动作前后截图；
- UI树与前台包名；
- Agent判断、实际动作与执行结果；
- AutoGLM标准输出和 Harness事件；
- 风控风险分、挑战、限流、动作失效等反馈；
- 文件哈希、设备时间和主机时间。

凭据、验证码和 API Key 不进入 EvidenceStore，单独使用凭据模块或人工接管。

### 5. WorkflowRegistry

保存两套工具都能理解的业务级流程，而不是坐标脚本：

```text
前置状态 → 目标状态 → 允许动作 → 成功判据 → 停止条件 → 升级条件
```

同一流程可由 AutoGLM、Mobile Harness或后续 Appium Adapter执行，便于公平 AB 测试。

## 统一任务格式

```json
{
  "task_id": "case-001",
  "app": "target-app",
  "goal": "受控测试目标",
  "account_tag": "test-account-a",
  "content_allowlist": ["test-content-001"],
  "allowed_actions": ["observe", "scroll"],
  "max_steps": 20,
  "max_duration_seconds": 300,
  "runner_policy": "autoglm_then_harness",
  "evidence_level": "full",
  "stop_on": ["credential", "captcha", "payment", "outside_allowlist"]
}
```

## 失败交接包

AutoGLM 升级到 Mobile Harness时，调度器传递：

- 任务和白名单；
- 当前截图与 UI树；
- 前台包名/Activity；
- 已执行动作序列；
- 最后一个成功状态；
- 错误、低置信度原因和剩余预算；
- 当前设备租约的新 fencing token。

Mobile Harness完成诊断后回写：

- 是否恢复；
- 修复动作；
- 可固化的页面判据；
- 建议修改的 AutoGLM prompt、应用映射或流程规则；
- 是否适合进一步固化为 Appium程序。

## 两种运行模式

### 生产式升级模式

AutoGLM优先，失败后交给 Mobile Harness。适合评估低成本 Agent 与高级 Agent 的组合威胁。

### AB 对照模式

同一任务分别从相同初始状态运行 AutoGLM 与 Mobile Harness。两者不共享前一次运行造成的推荐流状态，结果用于比较完成率、延迟、错误动作和风控响应。

## 分阶段落地

### 第一版：单机单设备

- Python 调度器；
- SQLite任务、运行和租约表；
- 本地证据目录；
- AutoGLM与 Mobile Harness两个子进程 Adapter；
- 一个 OPPO设备；
- 每次只运行一个任务。

### 第二版：多设备

- PostgreSQL保存任务与运行元数据；
- Redis负责队列、短租约和心跳；
- MinIO保存截图、UI树和日志；
- 每台设备独立 Worker与硬租约；
- 增加 Appium Adapter。

## 第一版验收标准

1. 两个 Runner 均能领取统一任务并写入相同格式的结果。
2. AutoGLM失败后能够释放设备，由 Mobile Harness安全接管。
3. 任意时刻只有一个有效设备租约。
4. 每一步都有前后证据和时间戳。
5. 凭据、验证码和白名单外动作会进入 `needs_human`，不会自动继续。
6. 进程异常退出后租约能自动过期，旧进程不能恢复控制。

## 两条路线如何互相学习

可以互相优化，但不让 AutoGLM 与 Mobile Harness 直接修改对方，也不让它们在设备上并行试错。统一池在二者之间增加一个边界清晰的深模块：`ExperienceBundle`（经验包）。它只暴露稳定、通用的业务信息，工具差异由各自的 Adapter 在模块内部消化。

顺序如下：

```text
路线 A 独占设备执行
  → EvidenceStore 保存完整证据
  → Reviewer 生成候选经验包
  → 对方 Adapter 翻译成自己的配置
  → 测试账号/可复位环境回放
  → 指标对比与人工审核
  → 发布新版本；不达标则回滚
```

这意味着 Mobile Harness 发现了一个新弹窗的识别和恢复方法后，可以生成“页面判据 + 恢复步骤”，由 AutoGLM Adapter 翻译成提示词、应用映射或预设流程；AutoGLM 在大量运行中发现了稳定路径后，也可以把状态转移、失败频率和成功判据交给 Mobile Harness，减少其探索步数。后续 Appium 也复用同一接口。

### 可直接共享的信息

- 任务目标、前置状态、目标状态和允许动作；
- 页面状态判据、包名、UI节点特征和经过脱敏的截图；
- 成功/失败条件、错误分类和恢复步骤；
- 步数、超时、重试预算、升级阈值和停止条件；
- 风控测试结果、挑战类型、异常发生位置和回归指标；
- 流程状态机及其版本、来源、适用 App 版本和证据哈希。

### 必须由 Adapter 翻译的信息

- AutoGLM：任务提示词、动作协议、模型参数、应用映射；
- Mobile Harness：应用操作卡、节点匹配规则、Agent 指令和恢复规则；
- Appium：定位器、等待条件和固定程序步骤。

这些参数语义相近，但格式和执行机制不同，不能直接复制覆盖。会话令牌、账号凭据、验证码、隐藏推理过程和当前设备租约不得进入经验包。

## 参数包的分享与版本管理

调优后的参数可以在授权团队内分享，建议按版本发布为一个可审计包：

```text
workflow-bundles/social-app/case-001/v3/
├─ manifest.json          # 版本、来源、适用范围、文件哈希
├─ workflow.yaml          # 业务级状态机
├─ states.yaml            # 页面判据与成功/失败状态
├─ guardrails.yaml        # 白名单、预算、停止和人工接管条件
├─ evidence-index.json    # 脱敏证据索引
├─ benchmark.json         # 回放样本与新旧版本指标
└─ adapters/
   ├─ autoglm.yaml
   ├─ mobile-harness.md
   └─ appium.json
```

分享分为三层：

1. 通用层：超时、最大步数、重试预算、状态判据、应用映射和证据等级，可在内部广泛复用。
2. 工具层：各 Runner 的提示词、模型采样参数、节点规则和定位器，只提供给对应 Adapter。
3. 受限风控层：平台特定风险反馈、频率模式、挑战阈值和测试内容集，仅向获授权风控成员开放，不作为可直接部署的规避包对外发布。

任何密钥、登录态、个人数据和真实账号凭据都不随参数包分享。

## 防止错误经验污染整个池

经验包只允许走 `proposed → replayed → approved → active` 四个状态。Agent 可以提出候选修改，但不能自行把修改发布给全部设备。每次升级至少验证：

- 完成率是否提高；
- 错误动作、越界动作和风控挑战是否增加；
- 平均步数、耗时和模型成本是否改善；
- 原有回归样本是否仍通过；
- 是否能够一键切回上一个已批准版本。

这样两条路线可以持续互相提供杠杆，但修改仍保持局部、可验证和可回滚。
