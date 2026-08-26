# RiskFlow Agent 规则

## 开始工作

1. 先读 `PROJECT.md`；涉及当前状态时再读 `STATUS.md`，涉及运行或排障时读 `RUNBOOK.md`。
2. 运行 `openspec list`，读取相关主规格和活动 change，再核对实际代码与测试。
3. `outputs/`、`research/`、历史聊天和已标记的历史文档只作证据；当前要求以主规格和 `docs/decisions/` 为准。

## 变更门槛

- 任务状态、数据库、设备动作、模型、密钥、安全策略、架构或发布改动：先创建或更新 OpenSpec change，写清范围、禁止事项、风险、回退和验收；获得用户确认后再实施。
- 文案、说明和无行为影响的小型可逆样式修改可以简化，但仍需记录和验证。
- 保持运行数据库、截图、日志、依赖、工具包、设备资料和秘密在 Git 之外；清理或覆盖它们前先列出精确目标并获得确认。

## 实施与验收

- 采用与现有实现一致的最小改动；大文件拆分、环境迁移和新功能分别立项。
- 设备动作默认使用预览或无写入路径。只有离线、接口和页面检查不足时，才申请最小设备预演。
- 验收顺序固定为：OpenSpec 严格校验 → Python 测试 → 控制台测试 → API → 浏览器页面 → 必要设备预演。
- 所有必需验证通过且任务完成后，同步主规格、归档 change，并更新 `STATUS.md` 或 `CHANGELOG.md` 中受影响的内容。

OpenSpec 项目技能位于 `.agents/skills/`；在新 Codex 任务中使用 `$openspec-explore`、`$openspec-propose`、`$openspec-apply-change`、`$openspec-update-change`、`$openspec-sync-specs` 和 `$openspec-archive-change`。
