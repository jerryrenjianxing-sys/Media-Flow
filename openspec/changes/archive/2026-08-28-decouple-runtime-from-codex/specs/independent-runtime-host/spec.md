## Purpose

让 RiskFlow 在当前 Windows 用户桌面会话中拥有独立于 Codex、终端和一次性启动脚本的后台生命周期，同时保持 USB 设备、用户密钥和本地网页可用。

## ADDED Requirements

### Requirement: 当前用户级独立后台宿主
RiskFlow SHALL 能安装为当前 Windows 用户的后台任务，并在用户登录后启动；后台宿主及其受管进程的存活不得依赖 Codex、启动终端或启动器继续运行。

#### Scenario: Codex 退出或更新
- **WHEN** RiskFlow 已由后台任务启动且 Codex 退出、重启或更新
- **THEN** 后台宿主、控制台、API、分析器和设备 Worker 继续运行

#### Scenario: 用户重新登录
- **WHEN** 已安装后台任务的 Windows 用户重新登录
- **THEN** RiskFlow 后台宿主自动启动且不自动提交新的设备任务

### Requirement: 单实例与有界自恢复
后台宿主 MUST 保持单实例，并 SHALL 有界检查受管角色；无状态服务或已配置且未请求停止的 Worker 意外退出时 SHALL 被重新拉起，不得产生同角色重复进程。

#### Scenario: 网页服务意外退出
- **WHEN** 后台宿主发现网页服务的已核验进程已经退出
- **THEN** 系统重新启动一个网页服务并更新可审计状态

#### Scenario: Worker 被明确停止
- **WHEN** 某设备已有安全停止请求或用户明确停止该 Worker
- **THEN** 后台宿主不得把该 Worker 视为意外退出并反复拉起

### Requirement: 可观察安装与启停
系统 SHALL 提供安装、卸载、启动、停止和状态入口，并 SHALL 区分后台任务状态、后台宿主心跳与各受管进程状态。

#### Scenario: 安装完成
- **WHEN** 当前用户完成 RiskFlow 后台安装
- **THEN** 状态入口报告后台任务已注册、宿主可启动且不暴露模型密钥

#### Scenario: 有任务运行时请求停机
- **WHEN** 用户请求停止整个 RiskFlow 且仍有运行中设备任务
- **THEN** 系统拒绝停机并保持后台宿主和受管进程运行

#### Scenario: 无任务运行时请求停机
- **WHEN** 用户请求停止整个 RiskFlow 且没有运行中任务
- **THEN** 后台宿主按 Worker、分析器、网页、API 的受控顺序停止已核验进程并退出

