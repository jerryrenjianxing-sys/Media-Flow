# RiskFlow 长期平台与开源复用方案评估

研究日期：2026-08-26（Asia/Shanghai）
研究对象：当前正式链路“控制台 → 本地 API → SQLite WAL 队列 → 每设备独立 Worker → uiautomator2 固定程序 → Android”。
资料边界：项目当前代码与规格；外部事实仅引用项目官方 GitHub 仓库、官方文档、官方发布记录和许可证。版本与维护状态是研究日快照，真正引入时必须重新核查。

## 结论先行

**固定程序现在不需要 Dify、n8n、Temporal、Prefect 或 Celery 来辅助执行。** RiskFlow 已经拥有任务队列、设备独占、任务状态、失败证据和控制台，再放一个通用工作流平台进主执行链，会产生两个调度中心、两套重试语义和不清楚的设备所有权。对“可能已改变账号状态的动作不得自动重跑”这一安全规则尤其危险。

更适合长期项目的做法是：**保留 RiskFlow 为唯一设备动作控制面，在外围按实际缺口逐个增加可替换的能力。**

推荐优先级：

1. **现在补基础工程，不补大平台**：独立 Python 环境与依赖锁定、数据库版本迁移、结构化事件与关联 ID、运行证据保留/备份、安全停机、离线回放测试集。
2. **第一项可复用组件优先考虑 OpenTelemetry**：先在代码中埋标准追踪/指标接口，初期可只输出本地，不必同时部署 Prometheus 和 Grafana。
3. **设备规模扩大后再评估 Appium Device Farm**：它的当前强项是自动化会话分配、hub/node、日志和录像；v12 已移除人工实时控制。若仍以 uiautomator2 为执行器，引入它需要先做 Appium 适配层，不能直接接上现有 Worker。
4. **AI 调用达到多供应商、多预算或多人共用后再上 LiteLLM**；需要保存提示词版本、模型判断和人工评价时再上 Langfuse。当前代码已经使用 OpenAI 兼容接口并对 OpenRouter 配置回退，LiteLLM 现在的收益有限。
5. **n8n 只可做外围集成**，例如日报、告警、备份通知和人工审批；不得直接点击设备或绕开 RiskFlow 的任务安全闸门。Dify 只适合作为隔离的 AI 提示词/候选内容实验室，不应成为设备工作流引擎。
6. **Temporal 是未来跨主机、长时、可恢复业务流程的候选，不是当前升级项**。Prefect/Dagster 面向数据工作流，Celery/Taskiq 只替换队列，均不解决当前最迫切问题。

一句话目标架构：

```text
外部触发/通知（可选 n8n） ──只调用受控 API──┐
AI 实验（可选 Dify） ──只返回候选结果──────┤
                                               ▼
RiskFlow 控制台 → 本地 API → SQLite 队列 → 每设备 Worker → 固定程序 → Android
                                               │
                     只出不入的遥测/审计 ──────┼→ OpenTelemetry / Sentry
                                               └→ Langfuse（仅 AI 调用）
模型请求：当前直连/OpenRouter；达到门槛后可经 LiteLLM
```

## 为什么 Dify 不是当前答案

Dify 是 LLM 应用开发平台，提供可视化 AI 工作流、RAG、Agent、模型管理、调试和日志；它适合快速试验“输入画面/文本 → 模型判断 → 候选内容”，而不是确定性设备执行。[Dify 官方仓库](https://github.com/langgenius/dify) · [中文功能说明](https://github.com/langgenius/dify/blob/main/docs/zh-CN/README.md)

与 RiskFlow 的关系：

- **互补**：提示词试验、模型比较、非生产异常归因、候选评论生成、知识库问答。
- **重叠**：工作流画布、任务历史、变量、循环、错误处理、模型调用；这些若进入主链，会与 RiskFlow 的队列、Worker 和事故记录重复。
- **关键冲突**：Dify 工作流的通用重试/恢复不能判断某次设备点击是否已经生效；设备独占和动作后复核仍必须由固定程序负责。
- **部署成本**：官方本地开发说明需要前后端、PostgreSQL、Redis、向量数据库、中间件和异步 Worker；这远重于当前单机 Python + SQLite。[Dify 后端官方 README](https://github.com/langgenius/dify/blob/main/api/README.md)
- **许可证边界**：Dify 使用修改版 Apache 2.0，官方许可证对多租户服务以及前端 Logo/版权信息有额外条件。如果以后把 RiskFlow 产品化为多租户平台，不能把它当作标准 Apache 2.0 组件直接嵌入。[Dify 官方许可证](https://github.com/langgenius/dify/blob/main/LICENSE)
- **维护状态**：仓库持续发布，2026-08-25 发布 1.17.0，研究日主分支仍有提交；“不现在引入”是架构判断，不是维护质量判断。[Dify 官方发布记录](https://github.com/langgenius/dify/releases/tag/1.17.0)

建议：现在不部署。以后若 AI 试验明显拖慢代码迭代，可单独启动一个 Dify 实验环境，只接脱敏截图和候选生成 API；经人工/固定规则验证后的结果再写回 RiskFlow 代码和测试，Dify 不持有设备凭据，也不调用写入型设备接口。

## 工作流与任务平台比较

| 项目 | 官方定位与维护快照 | 与当前架构的关系 | Windows/本地适配 | 当前决定 |
|---|---|---|---|---|
| **n8n** | 可视化 AI/业务集成工作流，1500+ 集成；2026-08-25 发布 2.36.7 | 适合通知、报表、人工审批、外部系统同步；若调度设备任务，会重复控制台和队列 | npm 可直接试用，官方也主推 Docker；单实例可用 SQLite，队列模式需 PostgreSQL + Redis | **外围可选，主链禁用** |
| **Temporal** | durable execution；服务自动处理间歇故障并支持 Activity 重试；Server 2026-07 发布 1.31.2，Python SDK 2026-08 发布 1.32.0 | 真正适合跨主机、持续数小时/数天、断点恢复；但会替换现有队列与状态机，且默认重试思想与写入动作冲突 | Python SDK支持本地开发；正式服务仍是独立基础设施，Windows 本机宜通过 dev server/容器验证 | **达到跨主机长流程门槛后 POC** |
| **Prefect** | Python 数据工作流编排，带调度、缓存、重试和 UI；2026-08 发布 3.8.4 | Python 接入轻于 Temporal，但本质仍重做队列、状态和监控；更擅长数据管道 | `prefect server start` 可本地启动，Python 3.10+ | **不引入；批量离线分析可再评估** |
| **Dagster** | 面向数据资产、血缘、数据质量和可观测性的编排器；2026-08-21 发布 1.13.19 | 对表、数据集、模型、报表很强，对手机设备独占和动作确认不对口 | Python 本地可运行，生产文档明显偏云/Kubernetes | **不引入** |
| **Celery** | 成熟分布式任务队列，依赖 Redis/RabbitMQ 等 broker；2026-03 发布 5.6.3 | 只能替换 SQLite 队列，不提供设备语义；会增加 broker 和结果后端 | 官方明确“不支持 Microsoft Windows，虽可能工作” | **排除当前 Windows 主线** |
| **Taskiq** | 支持 sync/async 的 Python 分布式任务队列，可接 Redis、RabbitMQ、NATS、Kafka；2026-08-21 发布 0.12.5 | 比 Celery 现代、类型支持更好，但仍只是第二套 broker/worker；不解决设备状态安全 | 包元数据标为 OS Independent，但没有官方 Windows 专章；仍需外部 broker | **不引入，除非未来必须分布式换队列** |

来源：[n8n 官方仓库](https://github.com/n8n-io/n8n) · [n8n 自托管架构](https://github.com/n8n-io/n8n-hosting) · [n8n 发布记录](https://github.com/n8n-io/n8n/releases) · [Temporal 官方仓库](https://github.com/temporalio/temporal) · [Temporal Python SDK](https://github.com/temporalio/sdk-python) · [Prefect 官方仓库](https://github.com/PrefectHQ/prefect) · [Prefect 本地服务](https://github.com/PrefectHQ/prefect/blob/main/docs/v3/how-to-guides/self-hosted/server-cli.mdx) · [Dagster 官方仓库](https://github.com/dagster-io/dagster) · [Celery 官方仓库](https://github.com/celery/celery) · [Taskiq 官方仓库](https://github.com/taskiq-python/taskiq)

### n8n 的正确边界

n8n 比 Dify 更适合 RiskFlow 的外围业务自动化：定时读取只读状态、生成日报、把故障摘要发到企业微信/邮件、等待人工批准、触发数据库备份。它不应该获得 ADB 权限，也不应该直接调用“点赞/收藏/评论”等写入动作。

建议只暴露两类受控接口给 n8n：

1. 只读：服务状态、设备状态、失败摘要、容量统计；
2. 经审批的任务申请：进入 RiskFlow 自己的队列，仍经过预览、设备锁、动作前闸门和动作后复核。

许可证要提前记录：n8n 官方明确称其为 fair-code/source-available，不是 OSI 意义的开源；Sustainable Use License 允许内部业务使用，但限制把 n8n 的主要功能作为对外收费产品或托管服务。[n8n 官方许可证说明](https://github.com/n8n-io/n8n-docs/blob/main/docs/privacy-and-security/sustainable-use-license.md)

### Temporal 的升级触发条件

只有同时出现下列两项以上，再做 Temporal POC：

- Worker 分布到多台电脑，SQLite 无法再作为单一协调点；
- 一个业务流程持续数小时/数天，并需要断电后恢复等待状态；
- 流程需要可靠的定时器、信号、人工审批和版本化回放；
- 任务量和状态组合使自有状态机维护成本明显高于业务代码。

POC 必须对设备动作 Activity 显式禁用自动重试，使用稳定幂等键，并把“是否已改变状态未知”落为不可自动重放的失败终态。Temporal 的 Activity 默认会自动重试，这正是 RiskFlow 必须特别约束的地方。[Temporal 官方 README](https://github.com/temporalio/temporal/blob/main/README.md) · [Retry Policy 官方说明](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/retry-policies.mdx)

## 设备管理与执行器复用

| 项目 | 能复用的部分 | 与 RiskFlow 的重叠/风险 | 决定 |
|---|---|---|---|
| **uiautomator2 / adbutils** | 现有 Android 固定执行和 ADB 连接底座；Python 原生、轻量 | 设备池、调度、锁、审计仍需 RiskFlow 自己负责 | **继续作为主线并锁定版本** |
| **Appium** | W3C WebDriver、Android/iOS/Windows 等多平台、驱动/客户端生态 | 若替换 uiautomator2，需要重写执行适配、管理 Appium 服务和会话 | **作为未来执行器适配器，不替换当前主线** |
| **Appium Device Farm 12.x** | 自动发现/分配设备、并行会话、hub/node、看板、日志、录像；2026-07 发布 12.0.1 | 它分配的是 Appium 会话，不会直接管理现有 uiautomator2 Worker；v12 已移除人工实时控制 | **跨主机或标准化 Appium 后再 POC** |
| **DeviceFarmer/STF** | 浏览器看屏、设备库存、预约/分区、ADB 接入、REST API、Prometheus 指标 | 依赖 RethinkDB/ZeroMQ/GraphicsMagick 等；官方不支持 Windows；官方还警告进程间缺少安全/加密、设备不会完整重置 | **不作为 RiskFlow 基础设施** |
| **Sonic** | 历史上覆盖远程调试与移动自动化测试 | `SonicCloudOrg/sonic-server` 已于 2025-03-25 被所有者归档并只读 | **排除长期主线** |
| **scrcpy** | Windows 上低延迟看屏、录屏、人工控制，无需在设备常驻 App | 没有控件语义、任务状态和动作后验证；人工控制会与 Worker 抢设备 | **可直接复用为暂停状态下的人工诊断工具** |

来源：[uiautomator2 官方仓库](https://github.com/openatx/uiautomator2) · [adbutils 官方仓库](https://github.com/openatx/adbutils) · [Appium 官方仓库](https://github.com/appium/appium) · [Appium Device Farm 官方仓库](https://github.com/AppiumTestDistribution/appium-device-farm) · [Appium Device Farm v12 发布记录](https://github.com/AppiumTestDistribution/appium-device-farm/releases) · [DeviceFarmer/STF 官方仓库](https://github.com/DeviceFarmer/stf) · [Sonic 官方归档仓库](https://github.com/SonicCloudOrg/sonic-server) · [scrcpy 官方仓库](https://github.com/Genymobile/scrcpy) · [scrcpy Windows 文档](https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md)

Appium Device Farm 的 v12 变化需要特别注意：官方 README 和 12.0.0 release 明确说明，人工远程控制/实时串流已移除，保留的是自动化会话、设备分配、hub/node、Dashboard、日志和录像；如果依赖人工控制只能停留在 11.x。RiskFlow 若采用 12.x，应把 scrcpy 作为独立人工观察工具，而不是期待 Device Farm 同时提供设备墙。[v12 官方说明](https://github.com/AppiumTestDistribution/appium-device-farm#-breaking-changes---version-1200)

## 可观测性与错误追踪

### 1. OpenTelemetry：现在最值得预留

OpenTelemetry 是供应商中立的追踪、指标和日志标准。Python 官方实现中 traces 和 metrics 已标为 stable，logs 仍处于 development；SDK 可把数据导向 OTLP、Prometheus 等后端。[OpenTelemetry Python 官方仓库](https://github.com/open-telemetry/opentelemetry-python) · [Python instrumentation 仓库](https://github.com/open-telemetry/opentelemetry-python-contrib)

RiskFlow 现在应先统一这些字段，而不是先装大看板：

- `trace_id`：一次控制台提交到 Worker 完成的全链路；
- `task_id`、`device_id`、`worker_id`、`run_id`；
- `task_type`、`stage`、`attempt_kind`（连接恢复/页面恢复/模型请求，不能把设备动作重试混在一起）；
- 耗时：排队、设备连接、截图、UI 树、模型、动作后验证；
- 结果：completed/failed/blocked/unknown-state，以及 incident fingerprint；
- 隐私默认：不把评论正文、完整截图、UI 树、API Key 作为 span attribute。

第一阶段只在本地 JSON 事件中保留兼容字段；第二阶段再接 OTel SDK/Collector。这样不会为了“有 Grafana”先引入三四个常驻服务。

### 2. Prometheus + Grafana：有持续运行数据后再部署

Prometheus 负责按周期抓取指标、计算规则和告警，单节点自治；Grafana 负责从 Prometheus、日志和追踪数据源查询并展示。[Prometheus 官方仓库](https://github.com/prometheus/prometheus) · [Grafana 官方仓库](https://github.com/grafana/grafana)

适合 RiskFlow 的指标包括：在线设备数、队列深度、每设备任务成功率、P95 任务耗时、ADB 重连次数、页面阻断率、模型失败/费用、证据目录增长量。达到“无人值守运行、每天都有任务、靠肉眼已看不出趋势”后再部署。单机早期可以先在现有控制台显示这些聚合值。

### 3. Sentry：SDK 可用，自托管现在过重

Sentry 提供 Python/JavaScript 等官方 SDK，用于异常聚合、错误追踪和性能定位。[Sentry 官方仓库](https://github.com/getsentry/sentry)

选择建议：

- 若允许把脱敏异常元数据发送到云端，可在独立变更中试 Sentry SDK；必须过滤截图路径、评论内容、UI 树和设备标识。
- 不建议在当前 Windows 主机自托管 Sentry。官方自托管方案使用 Docker Compose，最低建议 2 CPU/4GB RAM，推荐 4 CPU/16GB RAM/20GB 磁盘，维护成本明显高于 RiskFlow 本身。[Sentry 官方自托管说明](https://github.com/getsentry/develop/blob/master/src/docs/self-hosted/index.mdx) · [Sentry self-hosted 仓库](https://github.com/getsentry/self-hosted)
- 若所有数据必须本机留存，先完善现有 incidents 表、fingerprint、日志检索和证据关联，比部署整套 Sentry 更划算。

## AI 模型网关与审计

### LiteLLM：满足门槛后引入，不要为“统一接口”而引入

LiteLLM 提供面向 100+ 模型的 OpenAI 兼容 SDK/网关，并支持虚拟 Key、成本追踪、护栏、负载均衡和管理界面。[LiteLLM 官方仓库](https://github.com/BerriAI/litellm) · [官方发布记录](https://github.com/BerriAI/litellm/releases)

当前 RiskFlow 已经：

- 使用可配置的 OpenAI 兼容 `base_url`；
- 支持 OpenRouter 模型回退；
- 有严格 JSON Schema 和本地二次安全校验；
- 模型只做候选和判断，不控制设备。

因此现在再加 LiteLLM 主要只是多一个服务和故障点。满足以下任一条件后才值得引入：三个以上模型供应商、多个项目共用 Key、需要按模型/设备/任务做预算、需要集中限流/熔断、或要在不改业务代码的情况下统一切换供应商。即使引入，固定程序仍必须独立校验模型输出，网关的 guardrail 不能替代设备动作闸门。

### Langfuse：AI 审计量上来后很有价值

Langfuse 聚焦 LLM trace、提示词管理、评估、数据集和指标，支持 Python/JS SDK、OpenTelemetry、OpenAI、LiteLLM 等集成；官方提供 Docker Compose 自托管，但生产推荐 Kubernetes。[Langfuse 官方仓库](https://github.com/langfuse/langfuse) · [官方发布记录](https://github.com/langfuse/langfuse/releases)

它适合回答：哪个提示词/模型版本导致安全跳过率上升、候选评论为何被本地规则拒绝、每类画面的模型成本和延迟如何、人工复核是否认可模型判断。它不适合监控 ADB、设备锁和页面动作。

建议触发条件：每周都在改 prompt/模型、累计了可复用标注样本、需要 A/B 评估或多人共同调试。自托管默认会上报基本使用统计，官方提供 `TELEMETRY_ENABLED=false` 关闭方式；正式使用还应先确定截图和提示词的脱敏策略。[Langfuse 官方自托管/遥测说明](https://github.com/langfuse/langfuse#-deploy-langfuse)

## 还应直接复用的基础能力

这些比工作流平台更接近 RiskFlow 当前的真实缺口：

1. **数据库迁移：Alembic 或同等显式迁移机制**
   当前 `task_store.py` 在启动时直接建表/补列，短期够用；长期需要数据库版本号、向前迁移、备份、失败回滚说明和旧运行库兼容测试。Alembic 官方支持有序升级/降级脚本，并针对 SQLite 提供 batch move-and-copy 工作流。[Alembic 官方仓库](https://github.com/sqlalchemy/alembic)
2. **依赖锁定与可重复环境**
   建立独立 Python 虚拟环境，优先用 uv 的 `pyproject.toml + uv.lock` 锁定 Python、uiautomator2、adbutils、Pillow、requests 等版本；控制台保留 lockfile。升级以“新环境 + 全部离线测试 + 单设备预演”进行，不在可用环境上原地追 latest。[uv 官方仓库](https://github.com/astral-sh/uv)
3. **设备后端适配接口**
   把业务步骤依赖的能力定义为小接口，例如截图、UI 树、点击、输入、当前包、健康检查；当前实现仍是 uiautomator2。未来 Appium POC 只新增 adapter，不复制任务状态机和安全闸门。
4. **离线回放与事故样本库**
   复用现有截图、UI 树和 incident fingerprint，建立脱敏 fixture；让页面分类、安全闸门、坐标候选和错误归因无需连接手机即可回归。这比让 Dify/Agent 在线“猜页面”更能支撑持续更新。
5. **结构化配置和 schema 校验**
   正式配置应有类型、默认值、范围和版本；密钥只从本机凭据区注入。可先用 Pydantic 固化输入输出模型；当控制接口需要鉴权、WebSocket 或对外客户端时，再通过独立变更把手写 HTTP 层迁到 FastAPI，不与运行队列重构同时进行。任何工作流平台都不能成为唯一配置真相。[Pydantic 官方仓库](https://github.com/pydantic/pydantic) · [FastAPI 官方仓库](https://github.com/fastapi/fastapi)
6. **本地发布与升级通道**
   每个可用版本保留 Git 标签、依赖清单、数据库 schema 版本、启动器版本和验收记录。未来连接私有 Git 远程后再加 CI、Dependabot/Renovate；当前纯本地仓库不需要为了 CI 先上传设备证据或密钥。
7. **安全的人工诊断**
   直接复用 scrcpy 做暂停状态下的看屏/录屏；启动前必须取得设备维护锁，结束后释放。不能让 scrcpy 人工输入和 Worker 同时控制同一设备。

## 分阶段路线图

### P0：现在到下一稳定版本

1. 独立 Python 环境和依赖锁定。
2. 安全停机、启动身份和进程健康检查。
3. 运行证据保留/备份策略；先统计再清理。
4. 数据库 schema 版本与迁移测试；是否采用 Alembic单独做 OpenSpec change。
5. 全链路 `trace_id/task_id/device_id/run_id` 和结构化 JSON 事件。
6. 从历史 incident 建第一批离线回放 fixture；设备写入动作继续默认关闭。

**本阶段不安装 Dify、n8n、Temporal、Prometheus/Grafana、Sentry、Langfuse、LiteLLM 或设备池。**

### P1：稳定无人值守运行

触发标准：每天持续运行、失败只能靠人工翻目录排查、开始需要周/月趋势。

1. 接 OpenTelemetry SDK，初期导出到本地或 Collector。
2. 在现有 API 暴露低基数 Prometheus metrics；确认数据有用后再部署 Prometheus/Grafana。
3. 若允许云端脱敏上报，比较 Sentry SDK；否则强化本地 incident 查询。
4. 模型判断样本与人工结果足够后，小规模试 Langfuse。

### P2：设备池和团队协作

触发标准：稳定设备达到约 10 台、跨两台以上主机，或多人需要预约/统一自动化会话。

1. 先做 Appium adapter 单设备 POC，验证同一安全状态机在 Appium 下通过。
2. 再用 Appium Device Farm 12.x 验证设备分配、hub/node、日志与录像；人工看屏独立使用 scrcpy。
3. 如果只增加设备数量但仍是一台 Windows 主机和单人使用，继续自有 Worker/SQLite 可能仍是最低成本方案。
4. 不采用已归档的 Sonic；DeviceFarmer/STF 只在独立 Linux 实验室、纯测试账号、多用户远程预约这个特定场景重新评估。

### P3：跨系统业务编排

1. 需要通知、审批、报表和外部 SaaS 同步：n8n 作为旁路服务，仅调用受控 API。
2. 需要多项目统一模型 Key/预算/路由：LiteLLM 网关。
3. 需要跨主机、长时间 durable workflow：Temporal POC；先证明收益，再替换 SQLite 队列。
4. Dify 只在 AI 产品实验/知识库成为独立业务时部署，不接管设备运行。

## 最终选型表

| 组件 | 现在 | 以后 | 不可突破的边界 |
|---|---|---|---|
| RiskFlow 自有队列/Worker | **保留** | 单机阶段继续 | 唯一设备动作控制面 |
| Dify | 不装 | AI 实验室可选 | 不持有设备权限，不成为执行链 |
| n8n | 不装 | 通知/审批/报表可选 | 只走 RiskFlow 受控 API；注意 fair-code 许可证 |
| Temporal | 不装 | 跨主机长流程 POC | 写入型 Activity 必须显式禁用自动重试，未知状态失败终止 |
| Prefect/Dagster | 不装 | 独立数据分析项目再看 | 不替换设备队列 |
| Celery/Taskiq | 不装 | 必须换分布式 broker 时再比 | 设备锁与状态语义仍归 RiskFlow |
| OpenTelemetry | **先设计字段** | P1 接 SDK/Collector | 默认不采集隐私正文和截图 |
| Prometheus/Grafana | 不装 | 有持续指标后部署 | 监控系统不触发设备动作 |
| Sentry | 不装服务 | 脱敏 SDK 可试 | 自托管过重；严禁上传设备隐私 |
| Langfuse | 不装 | AI 评估成熟后接 | 只管模型调用，不管 ADB/设备动作 |
| LiteLLM | 不装 | 多供应商/预算/限流时接 | 本地安全校验不能下放给网关 |
| Appium | 不替换 | 做 adapter | 不复制业务状态机 |
| Appium Device Farm 12.x | 不装 | 10+设备/跨主机后 POC | v12 没有人工实时控制；版本必须锁定 |
| DeviceFarmer/STF | 不装 | 特定 Linux 实验室才重评 | 官方 Windows 不支持且有安全边界 |
| Sonic | **排除** | 不作为长期依赖 | 官方服务仓库已归档 |
| scrcpy | 可直接复用 | 人工诊断/录屏 | 只在 Worker 暂停且取得设备锁时使用 |

## 最终判断

RiskFlow 要成长为长期项目，当前最需要的不是“再套一个工作流产品”，而是把已有固定程序变成一个**可升级、可回放、可观测、可迁移、可替换设备后端**的稳定内核。

建议下一项正式变更不是 `add-dify`，而是类似 `riskflow-runtime-foundation-v1`：只完成独立环境、依赖锁、数据库迁移、结构化事件、证据保留和安全停机。完成并稳定运行后，再根据真实规模依次评估 OpenTelemetry、Langfuse/LiteLLM、Appium Device Farm、n8n 或 Temporal。每个组件都应作为可拔插旁路或适配器接入，不能削弱现有设备独占、默认预演和状态改变失败不自动重跑规则。
