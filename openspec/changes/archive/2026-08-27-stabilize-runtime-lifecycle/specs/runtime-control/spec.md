# runtime-control Specification

## ADDED Requirements

### Requirement: 项目独立运行环境
RiskFlow 正式运行 SHALL 使用项目自己的 Python 虚拟环境和固定依赖，不得要求 Mobile Harness、Open-AutoGLM 或其他项目环境作为日常运行前提。

#### Scenario: 首次部署缺少环境
- **WHEN** doctor 检测到项目虚拟环境或依赖缺失
- **THEN** 系统明确报告缺失项和引导入口，不使用其他项目 Python 静默替代

#### Scenario: 日常冷启动
- **WHEN** 项目环境、Node 前端和本机配置完整
- **THEN** 一键入口启动控制接口与网页且不提交任何设备任务

### Requirement: 进程身份核验
运行管理 MUST 在停止进程前核验 PID、进程镜像和项目命令身份，只能停止由当前 RiskFlow 项目登记且仍匹配的进程。

#### Scenario: PID 已被其他程序复用
- **WHEN** PID 文件存在但对应进程的镜像或命令不再属于 RiskFlow
- **THEN** 系统将记录视为陈旧并拒绝停止该进程

#### Scenario: Worker 正常运行
- **WHEN** Worker PID 对应项目虚拟环境 Python、当前项目 `worker.py` 和同一设备 ID
- **THEN** 状态返回真实运行中并允许受控停止或重启

### Requirement: 幂等启动停止重启
运行管理 SHALL 为 API、网页和每设备 Worker 提供幂等 `status/start/stop/restart` 行为，并保存可审计日志。

#### Scenario: 重复启动
- **WHEN** 同一角色的已核验进程已经运行
- **THEN** 启动返回现有进程状态，不创建第二个进程

#### Scenario: 安全停机
- **WHEN** 用户在无运行任务时请求停止 RiskFlow
- **THEN** 系统只结束已核验的本项目进程并返回每个角色的结果

### Requirement: 本机秘密不泄露
OpenRouter Key MUST 以当前 Windows 用户可解密的本机加密文件保存，接口、日志、doctor 和版本历史不得输出明文。

#### Scenario: 从旧路径迁移
- **WHEN** 项目秘密文件不存在且兼容旧加密文件存在
- **THEN** 系统复制加密载荷到项目秘密目录并保留旧文件，不打印或记录解密内容
