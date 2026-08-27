# Change: 模块化固定执行核心

## Why

`worker.py`、`douyin_uia2_runner.py` 和 `control_api.py` 同时承担过多职责。继续增加纠错建议、视觉商业内容门和人工复核前，需要先形成稳定模块边界，否则每次改动都容易影响任务状态或设备动作。

## What Changes

- 将设备连接与运行辅助、任务执行流程、Worker 队列循环分开。
- 将控制台配置/预设校验与 HTTP 路由分开。
- 保持设备差异只来自设备档案，通用状态机不复制成多份机型脚本。
- 保留兼容入口，使现有命令、API、数据库和测试调用不变。

## Impact

- Affected specs: `execution-architecture`
- Affected code: `fixed_runner/worker.py`, `fixed_runner/control_api.py`, 新增执行与配置模块
- 不改变设备动作策略、概率路由、任务终态或历史数据结构。
