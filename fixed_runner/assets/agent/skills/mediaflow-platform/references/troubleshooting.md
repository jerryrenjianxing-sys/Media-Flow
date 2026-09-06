# 回执与故障

- result_unknown：写请求可能已送达。先request_status查原编号，再plan_status/virtual_operation_status/repair_update_status核对。原写入不自动重试、不删除SQLite回执、不换编号试跑。
- worker_start_unconfirmed：任务已存，执行者未确认。查原plan_status；用户要求继续时用新请求编号恢复原plan_id，任务ID保持原批次。
- missing_parameters：采用已知参数和合理默认，只问真正缺少或含糊字段；没有创建任务。
- 设备映射、ADB或身份错误：区分虚拟机离线、端点变更、任务停止与执行者故障。先list_devices；不能猜目标、静默缩减设备或把在线但停止说成关机。
- expired：先确认原计划没有result，再repreview_plan；已提交禁止重建。
- request_id_conflict：该编号已有其他参数，核对原回执。只有确实的新操作才生成新编号。
- 更新补丁变化或基准不一致：返回diff与真实原因，重新验证候选，不能跳过哈希或空闲检查。result_unknown的更新不重复启动。
- timeout/interrupted：修复测试超时或后台重启中断，ok=false；读取原测试输出，不能当作验证通过。需要重新测试时保留原回执并使用新的测试请求编号。
- HTTP响应体被截断：写入返回并持久化unknown，同编号不再发送；读取最多尝试3次。不能把部分响应或连接成功当作业务成功。
- 缺少证据：明确现场未保存，不能推断根因、重新运行历史任务或批量分析所有异常。
- local_only/json_required：检查回环地址、JSON Content-Type和本机页面Origin，不改为公网监听。配置优先MEDIAFLOW_API_URL，其次config.json。

脚本stdout输出业务JSON（--help除外），exitcode=0仅表示该动作的接入响应ok；设备任务仍以终态为准。失败stderr为固定脱敏说明。读请求对连接错误、429和5xx最多尝试3次；写请求最多发送一次，跨进程仍保留同编号结果。
