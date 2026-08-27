# RiskFlow 固定执行器

本目录包含当前正式的本地控制接口、SQLite 队列、每设备 Worker、uiautomator2 执行器、视觉判断与离线测试。

## 正式数据流

```text
控制台 → 本地 API → SQLite 任务队列 → 每设备独立 Worker
      → 固定执行器 → uiautomator2 / ADB → Android 测试设备
```

- `control_api.py`：本地 HTTP 控制接口，不直接执行任务。
- `task_store.py`：任务、终态、异常、配置与系统状态。
- `worker.py`：队列编排和任务终态。
- `worker_runtime.py`：连接、设备锁、唤醒和运行辅助。
- `execution_tasks.py`：固定流程、动作闸门、复核和证据。
- `control_config.py`：控制台配置、预设和提交校验。
- `incident_analyzer.py`：独立只读异常分析器，不持有设备连接。
- `topic_review_store.py`：人工主题复核账本。
- `evidence_governance.py`：只读证据盘点和非破坏性数据库备份。

## 任务状态

- `pending`：等待执行。
- `running`：被对应设备 Worker 独占领取。
- `completed`：任务正常结束。
- `failed`：异常失败并保留证据。
- `stopped`：用户请求后在安全边界停止。
- `cancelled`：尚未开始的等待任务被取消。

Worker 异常结束后，遗留的 `running` 任务会收口为失败，不自动重试。结果不明的点赞、收藏或评论不会从头重放。

## AI 与设备动作边界

AI 可以读取单帧截图并返回：

- 主题相关性与画面证据；
- 内容是否安全；
- 评论候选文本；
- 异常页面的只读分类与候选规则。

AI 不持有设备对象、不调用 ADB、不直接点击。所有动作仍由固定执行器经过本地页面闸门、概率判断和动作后复核后执行。

## 运行和测试

项目根目录统一管理正式服务：

```powershell
.\setup-riskflow.ps1
.\manage-riskflow.ps1 -Action Doctor
.\run-riskflow-console.ps1
```

运行全部 Python 离线测试：

```powershell
& '.\.venv\Scripts\python.exe' -m unittest discover -s '.\fixed_runner' -p 'test_*.py'
```

## 运行数据

- SQLite：`fixed_runner/runtime/tasks.db`
- 运行截图与轨迹：`fixed_runner/runtime/artifacts/runs/`
- 服务日志：`fixed_runner/runtime/*.log`
- 数据库备份：`fixed_runner/runtime/backups/`
- 设备档案：`fixed_runner/device_profiles.json`

这些本机数据不会纳入 Git。当前治理页面只记录保留政策，不会自动删除；任何实际清理都要先核对准确路径和备份，再单独确认。
