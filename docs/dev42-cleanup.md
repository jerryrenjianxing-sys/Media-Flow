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
