# 项目上下文清理清单

本轮范围为维护资料与相关本机MBH指引，不是执行器修复或服务更新。当前运行事实只维护于[STATUS](../STATUS.md)。

## 逐项登记

[机器可读清单](context-inventory.json)逐文件列出路径、角色、处置和依据，范围包括Git可见Markdown/YAML/TOML、完整平台Skill和页面帮助。历史聊天及运行数据不进入清单，也不被清理。盘点不等于逐句人工复核全部历史；当前入口和冲突部分经过人工阅读，其余按原职责登记保留。

| 对象 | 处置 | 原因/可恢复性 |
| --- | --- | --- |
| README、PROJECT、RUNBOOK、STATUS | 更新，清理前原文归档 | 移除旧端口和多代操作混排；快照保留 |
| 控制台设计、执行器说明、真机准备指南 | 更新，旧要求归档 | 移除聊天布局、手机UI验收、Root/登录/整套自检使用门槛 |
| 内置助手、OpenCode契约、Pi运行手册、原生前端README | 旧操作归档，原地址保留跳转索引 | 不恢复退役服务，不破坏旧资料可查性 |
| 启动器与打包说明 | 更新，旧原文归档 | 不宣称未实测的独立运行和新安装包已交付 |
| 设备指南页面 | 仅更新文案 | 不改变设备API、按钮行为或初始化实现 |
| 主规格/长期决定 | 澄清生产与开发、旧兼容适用范围 | 不删除历史场景；实现差距另列 |
| 平台Skill | 更新正式执行/开发带测边界 | 同一来源生成Markdown/ZIP；无本机绝对路径 |
| 本机MBH抖音卡片及两份相关记忆 | 更新，源码外保留原文备份 | 旧列表/访客巡检不再被当作默认；保留5号几何差异 |
| 重复的现行旧流程段落 | 从现行入口删除，已存历史快照 | 不删除独有技术事实；无运行数据或兼容代码删除 |
| 各版本验收、旧聊天、截图、模型用量、数据库、凭据、模板 | 保留 | 不能通过清理伪造成功或影响恢复 |

历史快照在[2026-09-09目录](history/2026-09-09)，文件注明原路径，相对链接按原位置解释；快照不是可执行指引。源码改动可通过Git差异逐项恢复，不需要重置整个工作区。

## 活动变更的适用范围

- integrate-opencode-agent-workbench：名称是历史；当前只维护外部Skill和automation服务，旧聊天/权限/会话验收不重新启动。
- integrate-mumu-virtual-device-provider：按需准备与标准环境仍有效；模板兼容保留，巨大镜像不再必需；未完成的旧初始化流程不作为新用户门槛。
- implement-unified-engagement-inspection-v3、add-queued-engagement-inspection、standardize-engagement-alert-workflow-v2、rollout-engagement-v2-all-devices-and-refresh-console、enhance-engagement-result-feedback：详细巡检仅历史/兼容；新消息巡检为home_badge。
- stabilize-long-running-batches：保留dev.39已实施及失败事实，未修的动作复核问题见后续记录，不以任务框勾选宣称稳定。
- add-hybrid-feed-orchestration、stabilize-topic-conditioning-campaign：按轮次、主题与证据规则仍可参考；旧自动初始化/模型有效率停机要求不恢复。
- redesign-agent-first-console、restore-console-layout-and-motion、simplify-control-console-workflow、fix-run-device-progress-status：保留原验收，当前设计只由control_console/DESIGN.md维护。
- wire-engagement-correction-evidence：已有证据仍保留；旧详细巡检入口不再显示，未验事项不得勾选完成。

未归档这些无关活动变更，不删除未完成任务，也不改变旧验收事实。当前澄清解决文档歧义，不假装全部历史技术债已实现。

## 防失同步检查

在项目根目录执行 `.venv/Scripts/python.exe scripts/check-context.py`：检验新文件登记、现行本地链接/锚点、旧入口与已废弃指令；历史原文不受现行禁词规则约束。
新增资料须更新context-inventory.json；`--inventory`只输出候选清单，人工核对处置后保存，不能自动把新资料归为历史绕过检查。
Skill使用既有test_skill_bundle验证Markdown重建与ZIP逐文件一致、客户端可运行且无本机路径。

## 不依赖旧聊天的阅读验收

只给PROJECT、STATUS和其引用资料，检查：当前首页在哪里、如何首次接入、MBH在哪及如何与Worker交接、是否需要三次校准、消息巡检是否进入消息、当前五机范围、哪些问题尚未修好。
本轮使用无网络/无设备的阅读核对；不另起Agent或调用付费模型冒充独立行为验收。回归测试只证明明确检查项，不能代替自然语言理解。

## 验证记录

2026-09-09完成本轮资料整理，未改变执行器行为：

- 逐项登记460份资料；现行链接、锚点、废弃指令及清单一致性检查通过。13份历史快照与清理前Git原文逐份核对一致（忽略换行编码）。
- 隔离Python完整回归1026项通过；前端66项通过，类型检查、lint、生产构建通过。平台Skill的Markdown/ZIP重建与客户端回归通过。
- OpenSpec归档后全量严格校验47项通过；主规格32项严格校验通过。本轮两项治理要求已同步，变更归档为`2026-09-09-reconcile-current-project-context`，其他活动变更的未完成任务未勾选。
- 本机MBH 1.5.0仅导入通过；抖音卡片、应用记忆、设备差异记忆及索引共4份已备份后更新。平台与MBH Skill结构校验通过；校验器使用现有开发工具环境中的依赖，不代表平台运行依赖已经独立。
- 设备指南采用实际生产构建的离线SSR页面，在1366×768暗色、1920×1080亮色检查内容、排版及返回设备链接。因Browser插件不可用，使用现有Playwright，全部请求由离线材料响应，不启动网页服务。该检查不包含水合、真实后台或设备交互，未作为全站业务验收。

仅凭现行资料的阅读核对结果：PROJECT给出首页3001/API48138与外部Skill路线；RUNBOOK说明检查先行、启动先询问；MBH开发说明给出实际安装发现方法、1.5.0兼容情况和设备锁交接；Skill及配置参考说明按需准备、不要求三次校准；消息巡检仅检查首页角标；后续记录保留五机确认范围及未修缺陷。这是无旧聊天的人工阅读核对，不是独立Agent行为试验。

本次没有服务/设备动作，不升产品版本、不打包、不推送、不更新远端。原批次是否仍保持暂停未重新实时查询，不把旧停止回执当作新验收。下一步五机纠错仍未执行。

本机附加材料存放于用户文档目录的`MediaFlow-Task-Prep/context-cleanup-20260909/`：`mbh-backup/`按原相对路径保留4份备份，可逐文件比对后恢复；`guide-desktop.png`与`guide-desktop-light.png`是本轮离线桌面检查截图。这些本机材料不随通用Skill交付。
