# MediaFlow 架构决定索引

- [0001 固定程序承担正式运行](0001-fixed-runner-production.md)
- [0002 状态改变失败不自动重跑](0002-no-automatic-retry-after-state-change.md)
- [0003 AI 只做判断和候选生成](0003-ai-advisory-only.md)

这些文件解释长期有效的选择及其原因。当前可测试行为以 `openspec/specs/` 为准。

0002已经按dev.39动作/设备粒度更新；0003限定生产业务模型，不禁止用户指定的开发MBH带测。现行入口与实现差距见[项目索引](../../PROJECT.md)，历史材料不覆盖新要求。
