# RiskFlow Agent 规则

## 开始工作

1. 先读 `PROJECT.md`；涉及当前状态时再读 `STATUS.md`，涉及运行或排障时读 `RUNBOOK.md`。
2. 运行 `openspec list`，读取相关主规格和活动 change，再核对实际代码与测试。
3. `outputs/`、`research/`、历史聊天和已标记的历史文档只作证据；当前要求以主规格和 `docs/decisions/` 为准。

## 变更门槛

- **默认走 OpenSpec**：凡是会修改代码、配置、接口、数据结构、任务状态、设备动作、模型、安全策略、架构、依赖、构建或发布行为的请求，先运行 `openspec list`；有相关活动 change 就更新，没有就创建 change。写清范围、非目标、风险、回退和可观察验收条件，再进入实施。
- 用户要求“修改、增加、优化、修复”时，默认视为需要 OpenSpec；无需等待用户再次提醒“走 OpenSpec”。OpenSpec 约束开发过程，不代表用户预先批准付费、删除数据、真实设备写操作或发布上线，这些高风险动作仍须单独确认。
- 只有纯文案、注释、说明，以及不改变行为的小型可逆样式修正可以简化；简化时仍需记录改动并完成对应验证。拿不准是否改变行为时，按 OpenSpec 处理。
- 保持运行数据库、截图、日志、依赖、工具包、设备资料和秘密在 Git 之外；清理或覆盖它们前先列出精确目标并获得确认。

## 实施与验收

- 采用与现有实现一致的最小改动；大文件拆分、环境迁移和新功能分别立项。
- 设备动作默认使用预览或无写入路径。只有离线、接口和页面检查不足时，才申请最小设备预演。
- 验收顺序固定为：OpenSpec 严格校验 → Python 测试 → 控制台测试 → API → 浏览器页面 → 必要设备预演。
- 所有必需验证通过且任务完成后，同步主规格、归档 change，并更新 `STATUS.md` 或 `CHANGELOG.md` 中受影响的内容。

OpenSpec 项目技能位于 `.agents/skills/`；在新 Codex 任务中使用 `$openspec-explore`、`$openspec-propose`、`$openspec-apply-change`、`$openspec-update-change`、`$openspec-sync-specs` 和 `$openspec-archive-change`。
