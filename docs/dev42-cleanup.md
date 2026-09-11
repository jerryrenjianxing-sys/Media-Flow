# dev.42 清退记录

基线：42b45d640e95；清退保留dev.41有效修复和原STATUS差异。未完成验收不改写。

## 处置依据

- 下列Git跟踪文件仅属于退役聊天、内嵌引擎、专用界面/测试/构建依赖，移除后以410或Skill首页代替。提交可恢复原代码。
- 保留agent_platform、agent_queue、agent_operations、agent_memory、agent_evidence、agent_repairs、agent_repair_updates、agent_process及相关业务测试；它们由automation或更新器实际引用。
- 更新器改用已有请求/补丁回执校验，不再导入聊天权限类。业务数据库路径不迁移。
- 保留外部Agent图标与许可、原始历史说明、旧消息巡检兼容代码；不删除缓存、构建产物、安装包、模板或任何运行数据。

## 删除清单

清退阶段验证：OpenSpec严格校验49项、隔离Python947项、前端57项、类型检查、lint与生产构建通过。首次Python运行的唯一失败是新文档登记时序问题，更新登记后全量复跑947项通过。

测试变化可由下列删除清单核对：删除聊天专属Python测试模块、前端agent-state的6项及rendered-html中的3项聊天测试；共用记忆、证据、修复更新、设置和状态图标测试迁移保留。新增test_retired_agents的4项验证业务上下文不加载引擎、410、生产引用及资源装配。前端原66项变为57项，不以数量下降作为通过依据。Windows实际页面验收放在第二阶段统一执行；无安装、付费请求或设备动作。

- `control_console/app/agent-studio.css`
- `control_console/app/agent.css`
- `control_console/app/chatgpt-auth.ts`
- `control_console/app/components/agent-commands.tsx`
- `control_console/app/components/agent-inbox.tsx`
- `control_console/app/components/agent-markdown.tsx`
- `control_console/app/components/agent-memories.tsx`
- `control_console/app/components/agent-plans.tsx`
- `control_console/app/components/agent-provider-settings.tsx`
- `control_console/app/components/agent-repairs.tsx`
- `control_console/app/components/agent-usage.tsx`
- `control_console/app/components/agent-workbench.tsx`
- `control_console/app/lib/agent-api.ts`
- `control_console/app/lib/agent-workspace-state.d.mts`
- `control_console/app/lib/agent-workspace-state.mjs`
- `control_console/tests/agent-state.test.mjs`
- `fixed_runner/agent_bridge.py`
- `fixed_runner/agent_contract.py`
- `fixed_runner/agent_dependencies.py`
- `fixed_runner/agent_handoff.py`
- `fixed_runner/agent_mcp.py`
- `fixed_runner/agent_model_policy.py`
- `fixed_runner/agent_native.py`
- `fixed_runner/agent_permissions.py`
- `fixed_runner/agent_providers.py`
- `fixed_runner/agent_response_state.py`
- `fixed_runner/agent_runtime.py`
- `fixed_runner/agent_service.py`
- `fixed_runner/agent_tool_context.py`
- `fixed_runner/assets/agent/context-plugin.mjs`
- `fixed_runner/assets/agent/workflow.md`
- `fixed_runner/independent_agent.py`
- `fixed_runner/native_console_host.py`
- `fixed_runner/pi_brand.py`
- `fixed_runner/pi_host.py`
- `fixed_runner/pi_runtime.py`
- `fixed_runner/test_agent_contract.py`
- `fixed_runner/test_agent_dependencies.py`
- `fixed_runner/test_agent_execution.py`
- `fixed_runner/test_agent_handoff.py`
- `fixed_runner/test_agent_model_policy.py`
- `fixed_runner/test_agent_native.py`
- `fixed_runner/test_agent_permissions.py`
- `fixed_runner/test_agent_providers.py`
- `fixed_runner/test_agent_response_state.py`
- `fixed_runner/test_agent_runtime.py`
- `fixed_runner/test_agent_service.py`
- `fixed_runner/test_independent_agent.py`
- `fixed_runner/test_native_console_host.py`
- `fixed_runner/test_pi_brand.py`
- `fixed_runner/test_pi_runtime.py`
- `native_console/branding.json`
- `native_console/build.py`
- `native_console/build_tests.py`
- `native_console/gateway.mjs`
- `native_console/integration/bootstrap.ts`
- `native_console/integration/migration.test.ts`
- `native_console/integration/migration.ts`
- `native_console/overlay/packages/app/public/mediaflow.webmanifest`
- `native_console/overlay/packages/app/src/i18n/mediaflow.ts`
- `native_console/overlay/packages/app/src/mediaflow/about.tsx`
- `native_console/overlay/packages/app/src/mediaflow/bar.tsx`
- `native_console/overlay/packages/app/src/mediaflow/mediaflow.css`
- `native_console/overlay/packages/app/src/mediaflow/server-path.test.ts`
- `native_console/overlay/packages/app/src/mediaflow/server-path.ts`
- `native_console/overlay/packages/app/src/mediaflow/status.test.ts`
- `native_console/overlay/packages/app/src/mediaflow/status.ts`
- `native_console/overlay/packages/app/src/mediaflow/stop.test.ts`
- `native_console/overlay/packages/app/src/mediaflow/stop.ts`
- `native_console/overlay/packages/ui/src/theme/themes/mediaflow.json`
- `native_console/server.mjs`
- `native_console/test/gateway.test.mjs`
- `native_console/test/management-navigation.browser.cjs`
- `native_console/upstream.json`
- `packaging/agent-engine/package-lock.json`
- `packaging/agent-engine/package.json`
- `packaging/opencode.lock.json`
- `packaging/pi-source.json`
- `scripts/pi-plugin.test.mjs`
- `scripts/pi-request-budget.mjs`
- `scripts/pi-request-budget.test.mjs`
- `scripts/pi-shell.test.mjs`
- `scripts/prepare-pi-runtime.py`
- `fixed_runner/assets/pi/plugins/mediaflow/client/entry.mjs`
- `fixed_runner/assets/pi/plugins/mediaflow/index.mjs`
- `fixed_runner/assets/pi/plugins/mediaflow/manifest.json`

## 第二阶段修复与验证

- 状态查询与批次回执不再顺带收口/过期取消任务或改写配置；执行路径仍保留原生命周期管理。隔离库重复查询前后完整数据库内容一致。
- 检查点保留真实数量，缺失值为null、部分汇总标记counts_complete=false；兼容旧汇总增加result_available=false。运行、列表、详情不再把缺失结果显示为0或模型100%成功。
- 执行者中断、设备连接未核实、批次暂停分别展示；详情等待原因中文化，不再误称手机离线或失败。旧详细巡检复验不再由设备状态推荐。
- 千问故障保留实际传输阶段、耗时及尝试次数；公开字段使用有限白名单，不返回上游正文或凭据。不增加重试/截止，不调用真实模型。慢写套接字与错误包装使用现有隔离模拟回归；慢响应头、慢读取、断连、429和无效流使用本机隔离HTTP服务验证。
- 消息巡检保持dev.41有效字形规则；本轮没有证据要求改算法。正式入口离线复核原始15:59截图为精确3；裁剪、配对UI及新复核保存于work/dev42/badge-original-three，未覆盖旧回执。来源图片SHA-256：ef9f380c84c5b0769f66b2809f17814ab7c2c936a9dac94df0e29637b479c55d。红点、无角标、非首页、冲突和遮挡等在隔离回归覆盖；不能据少量真实样本宣称普遍95%。
- STATUS改为当前快照，原流水完整保存在docs/history/2026-09-11/STATUS-before-dev42.md；Skill、项目索引与三项主规格同步。不归档dev.41为成功。

### 验证结果

- OpenSpec严格49项、上下文登记474文件无错误；Python完整隔离951项通过（第一阶段947，新增状态/只读2项、模型诊断2项）；前端59项（第一阶段57，新增诊断与缺失计数2项）。类型检查、lint、生产构建通过。
- 隔离HTTP实测automation、config、presets、content-plans、model、status、records/task-groups均200，旧agent/status返回410；不启Worker或连接真实设备。计划幂等、历史回执、维护与资源装配由完整隔离回归验证。
- Windows Chrome生产构建：40个页面状态（首页、任务台、运行、结果、设置；亮暗；1366/1920；125%/150%等效CSS视口）。截图与报告work/dev42/desktop-final-pass/report.json，复制完整Markdown、下载ZIP、任务详情开关及任务台导航实际通过。人工核对五页及详情；未执行Windows系统DPI切换，不将等效视口称为真实DPI验收。浏览器插件不可用，使用普通Playwright。
- 首次页面测试夹具为健康检查任务，详情不会列入视频轮次；更换为隔离视频记录后复验。页面审查另修复了缺失计数、等待时钟及详情英文原因；最终40项0页面错误、无横向溢出。
- skill-creator通用quick_validate因专用/附带Python均缺PyYAML未执行；未安装额外依赖。项目现有Skill复制/ZIP重建与客户端回归通过，此项工具不可用不冒充已通过。

### 交付与未完成

两个阶段分别提交；代码可从Git恢复，未删除运行数据、凭据、模板、依赖缓存或历史安装包。本轮没有手机动作、付费模型、任务恢复、打包、推送或远端更新。

本机只读核对仍为全局暂停、0运行、101pending（含原4条无关等待）、3waiting_device、四暂停批次；Worker PID35588仍存活持锁。因此不强制切换API、不备份后覆盖数据，空闲更新及更新后前后端身份/数据核对仍未完成。开发前端构建使用原共享产物目录，网页已刷新候选前端，API仍是旧dev.41进程；不能宣称整机dev.42已部署。实际关闭Codex独立性、安装版迁移、真实业务成功率均不计为本轮通过。
