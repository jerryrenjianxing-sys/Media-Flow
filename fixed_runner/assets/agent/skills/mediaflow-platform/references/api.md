# 本地Automation API v1

`GET /api/automation` 返回动作清单、mutates/request_id_required、connected及版本。connected仅表示模块接入，不保证设备、模型或独立安装更新链可用。`POST /api/automation` 接受 `action`、`arguments`对象、写入必需的 `request_id`、可选 `session_id`。请求编号和会话编号为1–100个字母、数字、下划线或连字符。会话只是批次关联，不需要在宿主注册。

响应含 `ok,status,reason_code,user_message,retryable,operation_id,plan_id,result`；result保留实际深模块回执。状态submitted/active/queued/ready不代表业务完成，worker_start_unconfirmed表示已存批次但执行者未确认。blocked/waiting_user/expired/failed/unknown/timeout/interrupted都应按原因处理。HTTP 202可代表未知结果，不能据HTTP状态断言成功。

| action | arguments | 写 |
| --- | --- | --- |
| platform_status / list_devices | `{}` | 否 |
| list_tasks | limit(1–50，默认10),offset(0–100000) | 否 |
| plan_tasks | config：见workflows.md | 是，独立计划无设备动作 |
| execute_plan / resume_plan | plan_id，按用户恢复意图设置resume_stopped_devices=true | 是 |
| plan_status | 可选plan_id，省略列本关联最近计划 | 否 |
| repreview_plan / pause_batch / stop_batch | plan_id | 是 |
| task_evidence | task_id | 否 |
| incident_evidence | incident_id | 否 |
| request_status | request_id（要查询的原请求） | 否 |
| plan_virtual_operation | virtual_device_id,action；可选settings,backup | 是，计划不启动设备 |
| execute_virtual_operation | command_id；删除须带真实confirmation_name | 是 |
| virtual_operation_status | command_id | 否 |
| memory_list / memory_history | `{}` / id | 否 |
| memory_save | title,body；编辑附id,expected_version，可用enabled=false禁用 | 是 |
| memory_restore | id,version,expected_version | 是 |
| repair_create | purpose | 是，返回id与workspace_path |
| repair_list / repair_status | `{}` / repair_id | 否 |
| repair_files / repair_diff | repair_id | 否 |
| repair_read | repair_id,path | 否 |
| repair_test | repair_id,mode(unit/syntax/python/frontend/lint/build/openspec/all),path（unit/syntax时） | 是 |
| repair_validate | repair_id，运行完整验证 | 是 |
| repair_test_status | test_id | 否 |
| repair_export | repair_id；返回已验证候选的artifact_path与patch_sha256，宿主文件工具可读取，不依赖旧会话下载链接 | 是 |
| repair_close / repair_cancel | repair_id | 是 |
| repair_prepare_apply / repair_apply | repair_id | 是 |
| repair_update_status | operation_id | 否 |
| repair_cancel_update / repair_prepare_rollback | operation_id | 是 |

源文件编辑由宿主文件工具在workspace_path完成；repair_edit/revert/delete不作为automation动作公开。不支持的动作返回unsupported_action，不返回模拟成功。宿主终端不要直接ADB控制设备或替换Worker。

Bash命令工具（Windows同样适用）在Skill目录调用：

```bash
"$MEDIAFLOW_PYTHON" scripts/mediaflow.py list_devices
"$MEDIAFLOW_PYTHON" scripts/mediaflow.py task_evidence --arguments '{"task_id":"已有任务编号"}'
```

直接读取脚本JSON和退出码，不用`| tail`吞掉失败退出码。仅当命令工具确实是PowerShell时用以下启动器（先取 `MEDIAFLOW_PYTHON`，再取本地配置的 `python`）：

```powershell
./scripts/mediaflow.ps1 list_devices
./scripts/mediaflow.ps1 plan_tasks --arguments-file task-arguments.json --request-id plan-unique-001 --session-id ses_current
```

`--arguments`可直接传JSON对象；Windows转义复杂或大参数使用`--arguments-file`。`--timeout`默认为30秒，上限120秒。写入返回超时不自动重试。`MEDIAFLOW_SKILL_CONFIG`或`--config`指定配置文件，客户端配置支持api_url与receipt_db，启动器还读取python；环境MEDIAFLOW_API_URL/MEDIAFLOW_RECEIPT_DB优先。回执默认位于当前用户LOCALAPPDATA/MediaFlow/skill/receipts.db，不能放进源码或发行包。

同一request_id同参数返回原接入响应（不刷新成后来业务状态）；同编号不同参数返回request_id_conflict。用查询动作获取实时状态。接入中断会永久保留未知回执，不能删除回执或换ID来试成功。
