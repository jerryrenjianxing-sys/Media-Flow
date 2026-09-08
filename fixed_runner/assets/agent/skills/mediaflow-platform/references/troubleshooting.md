# 回执与故障

- 首次接入check：connected仅证明本机平台可达，设备为带时间的登记快照；snapshot_unavailable表示未知，不是无设备。连接成功但client_runtime.available=false时，引导修复平台运行环境，不要求另装开发工具。
- connection_failed / connection_timeout：尚不能确认服务停止。先询问用户；用户同意启动才按setup.md使用已核实入口，30秒内未确认不重发启动、不恢复队列。
- unexpected_service：地址返回非预期内容或平台过旧，先核对当前软件及地址，不抢占端口、不启动第二份后台。
- 宿主无法执行本机命令：说明当前Agent不能访问这台Windows电脑，提供check命令或引导换成本机Agent，不声称连接成功。

- result_unknown：写请求可能已送达。先request_status查原编号，再plan_status/virtual_operation_status/repair_update_status核对。原写入不自动重试、不删除SQLite回执、不换编号试跑。
- content_plan/preset写入阻断：读取原对象核对名称、ID、修订和字段限制；只有用户要求的新操作才使用新编号。内容计划同ID保存是新修订，预设同名保存是替换且没有历史版本。
- 模型 `failed/blocked`：保留返回的具体reason_code和user_message。凭据不可读、配置锁定、次数/预算不足、缺少上传同意、任务或分析忙碌等都是已知阻断，不是`result_unknown`；引导用户在原平台修复对应状态后，由用户决定是否再次测试或启用。不要自动联网验证、切换provider或重复付费测试。
- worker_start_unconfirmed：任务已存，执行者未确认。查原plan_status；用户要求继续时用新请求编号恢复原plan_id，任务ID保持原批次。
- missing_parameters：采用已知参数和合理默认，只问真正缺少或含糊字段；没有创建任务。
- 设备映射、ADB或身份错误：区分虚拟机离线、端点变更、任务停止与执行者故障。先list_devices；不能猜目标、静默缩减设备或把在线但停止说成关机。
- expired：先确认原计划没有result，再repreview_plan；已提交禁止重建。
- request_id_conflict：该编号已有其他参数，核对原回执。只有确实的新操作才生成新编号。
- 更新补丁变化或基准不一致：返回diff与真实原因，重新验证候选，不能跳过哈希或空闲检查。result_unknown的更新不重复启动。
- timeout/interrupted：修复测试超时或后台重启中断，ok=false；读取原测试输出，不能当作验证通过。需要重新测试时保留原回执并使用新的测试请求编号。
- HTTP响应体被截断：写入返回并持久化unknown，同编号不再发送；读取最多尝试3次。不能把部分响应或连接成功当作业务成功。
- 缺少证据：明确现场未保存，不能推断根因、重新运行历史任务或批量分析所有异常。
- task_evidence 的 result_summary 为null或字段为null：表示没有持久化结果，不是0；requested只表示目标条数。当前摘要没有四类相关性明细时，不推算分类分布。
- local_only/json_required：检查回环地址、JSON Content-Type和本机页面Origin，不改为公网监听。配置优先MEDIAFLOW_API_URL，其次config.json。

脚本stdout输出业务JSON（--help除外），exitcode=0仅表示该动作的接入响应ok；设备任务仍以终态为准。失败stderr为固定脱敏说明。读请求对连接错误、429和5xx最多尝试3次；写请求最多发送一次，跨进程仍保留同编号结果。
