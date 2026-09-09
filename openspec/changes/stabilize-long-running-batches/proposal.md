## Why

两次五机运行暴露模型临时失败误熔断、95%指标误作停机门槛及单台失败取消整批。千问合成图片测试通过，不等于长任务稳定。

## What Changes

- 单视频失败留证消耗名额、不补刷，完成有异常不阻断后续轮次。
- 有界模型重试、等待和单探针；任务检查点保证后台重启后不重放未知动作。
- 单设备故障隔离、能力暂停，页面与Skill显示真实进度和等待原因。
- dev.39本机更新，验证后明确授权五机新批次。

## Capabilities

### New Capabilities
- `long-batch-resilience`: 检查点、等待、单机故障隔离和可观测续跑。

### Modified Capabilities
- `model-runtime-resilience`: 临时重试与连续故障等待，95%只作评估。
- `task-lifecycle`: 有检查点任务可安全恢复，旧任务终态不变。

## Impact

现有Python Worker/SQLite、模型传输、控制台与Skill。不新增队列。非目标：换Key/模型、改账号、操作MuMu、公开发行、重放旧任务或扩大写入权限。失败仍留证，实际模型及五机结果另行记录。
