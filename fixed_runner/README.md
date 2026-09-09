# MediaFlow 固定执行器

本目录包含业务API、SQLite任务队列、每设备Worker、固定设备动作、模型判断及离线测试。正式链路和开发MBH边界见[项目说明](../PROJECT.md)。

## 主要模块

control_api.py提供HTTP接口；task_store.py保存任务；worker.py与worker_runtime.py编排执行；douyin_uia2_runner.py执行抖音动作；device_initialization.py保留维护兼容入口；control_vision.py只生成受限视觉候选。
生产模型不直接点击；开发MBH按[带测说明](../docs/mbh-development.md)取得设备锁，与Worker交接。

## 当前语义

pending等待、running执行、completed完成、degraded完成有异常；waiting_model / waiting_device / waiting_user保留进度；failed、stopped、cancelled分别记录故障、用户停止和取消。
恢复使用原任务与检查点，未知写入不重放；不能将所有中断统一改成整批失败。实际暂停能力误判见[待修记录](../docs/current-followup.md)。
按需准备不要求整套初始化；输入才验证中文，登录验证由用户处理。旧初始化和详细巡检兼容代码不作为新任务门槛。消息巡检默认home_badge。

## 测试与运行数据

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts/test-python.py
```

该入口先隔离数据和模型配置。不要直接运行全量unittest发现而读取本机活动配置。后台操作见[RUNBOOK](../RUNBOOK.md)。
运行数据位置由runtime_layout与当前运行配置解析；数据库、凭据、设备档案、截图与日志不进入Git。目录清理不删除运行数据。
