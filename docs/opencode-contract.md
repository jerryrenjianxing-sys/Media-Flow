# OpenCode 1.18.29 接入契约

以已校验 Windows baseline 二进制的 `/doc` 为实际契约，不把 latest SDK 当作固定版本接口。
自动检查位于 `fixed_runner/agent_contract.py`；模拟缺路由/移除服务商测试位于 `test_agent_contract.py`。
本机真实契约回执存于忽略目录 `work/agent-native-contract/receipt.json`，17 项路由通过，追加千问后 214 项服务商目录完整保留。

## 与在线说明的差异

[官方服务端说明](https://opencode.ai/docs/server/) 的创建会话表格只列 `parentID/title`；固定版本 `/doc` 还包含 `agent/model/permission/metadata/workspaceID`。
创建会话的模型对象使用 `id/providerID`，发送消息的模型对象使用 `modelID/providerID`，不能共用一个未经转换的对象。
实际 OAuth 路径占位符为 `providerID`；认证方法由 `/provider/auth` 返回，不能假定所有服务商都支持同一个方法。
问题和权限分别使用 `/question/{requestID}/reply` 与 `/permission/{requestID}/reply`，前者是二维答案数组，后者是 once/always/reject。

## 已通过的真实模型链路

2026-09-06，用户明确确认测试指令、指南、版本及队列统计发送至千问 Token Plan 后，完成一次只读对话。
本地审计记录两次模型请求完成、一次 platform_status 工具完成。
回执 `c41434fe81784c45add17baca453eed5` 与模型回答一致，状态为 dev.18、业务暂停、0 运行、0 等待。
未上传截图、私信或数据库文件，未操作虚拟机，未修改 Key 或启用其他服务商。

## 原生凭证决定与实现

[锁定版本 Auth 实现](https://github.com/anomalyco/opencode/blob/v1.18.29/packages/opencode/src/auth/index.ts) 可以从 `OPENCODE_AUTH_CONTENT` 读取，但原生 `set/remove` 仍直接写入 `auth.json`。
2026-09-06 用户明确选择原生存储，因此不再增加加密适配、不 fork 官方引擎。auth.json 保存在隔离的本机 Agent 数据目录，排除于源码、修复材料和发行包；既有 MediaFlow 加密 Key 保持不变，可显式选择引用。
实测原生 API Key 写入该文件成功；登录方法目录保留原生 OAuth、API 及动态 prompts。新增 agent_providers.py 和首页设置负责状态、回调及恢复。假凭证写入只验证保存和隔离，不代表任何上游账号已登录或模型已通过。
原生回调在后台等待，有截止时间；刷新只读取同一操作，重启标记中断，不重放授权回调。运行中对话阻止配置变更，配置修订变化后旧会话要求新建，避免在同次流程中混用凭据。

## 冷启动依赖与第二次真实验收

固定版本启动健康接口早于服务商/插件准备，不能仅凭healthy显示可聊天。官方插件SDK `@opencode-ai/plugin@1.18.29` 及锁定依赖随程序载荷提供，宿主校验并只复制依赖文件到自己的配置目录；不复制auth.json，不禁用原生认证插件。服务商目录可读后才显示已连接。包内Python与OpenCode在npm地址不可达的条件下已完成冷启动和8步实际修复工具回传；模型上游使用本机夹具，不代表真实模型修复质量。

真实千问补充验收为3次请求、platform_status与incident_evidence两个工具。截图为无账号的合成测试图，模型正确读出仅图片中出现的4729和MESSAGES，回执在 `work/agent-real-acceptance/receipt.json`；没有设备动作或批量历史上传。其他原生服务商账号尚未完成真实OAuth登录。
