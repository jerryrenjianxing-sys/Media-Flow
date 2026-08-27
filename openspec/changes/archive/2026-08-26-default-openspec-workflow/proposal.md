## Why

RiskFlow 将长期持续迭代，仅对高影响改动强制使用 OpenSpec 容易让普通功能和缺陷修复再次依赖聊天上下文。需要把 OpenSpec 设为行为改动的默认入口，使需求、实现、验证和归档持续可追溯。

## What Changes

- 所有会改变代码、配置、接口、数据结构或运行行为的请求默认先创建或更新 OpenSpec change。
- 用户无需在每次需求中重复指定“走 OpenSpec”。
- 保留纯文案、注释、说明和无行为影响的小型可逆样式修正的简化例外。
- 明确默认使用 OpenSpec 不等于预先授权付费、删除数据、真实设备写操作或线上发布。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `project-governance`：将 OpenSpec 从高影响变更门槛扩展为所有行为改动的默认开发流程，同时保留低风险例外与独立授权边界。

## Impact

影响 `AGENTS.md` 与 `openspec/specs/project-governance/spec.md`。不修改运行代码、API、数据库、模型、设备状态、依赖或发布流程。
