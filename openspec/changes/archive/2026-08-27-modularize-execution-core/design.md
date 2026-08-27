# Design

## 边界

采用深模块而不是按函数数量平均拆分：

- `execution_tasks.py`：公开 `execute_task`，内部拥有具体任务流程和评论处理。
- `worker_runtime.py`：公开连接、设备健康、暂停/停止等待和报告辅助。
- `worker.py`：只保留队列领取、终态写入和 CLI 兼容入口。
- `control_config.py`：拥有默认配置、预设、标准化和 Key 校验。
- `control_api.py`：只负责运行服务组合、状态读取和 HTTP 路由。

设备点击仍只在固定执行器内发生。AI 输出不会越过现有固定执行器接口，也不会直接持有设备对象。

## 兼容策略

`worker.py` 和 `control_api.py` 继续重新导出既有测试和脚本使用的公开符号。先移动代码并保持行为，再在后续 change 上增加新能力。

## 验收

- 全部现有 Python 和控制台测试不变或仅修改导入位置后通过。
- 控制 API、Worker CLI 与实机预览任务继续使用原参数。
- 代码中不新增第二套设备动作实现。
