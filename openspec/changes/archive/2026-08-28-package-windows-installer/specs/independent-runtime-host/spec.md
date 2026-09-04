## MODIFIED Requirements

### Requirement: 当前用户级独立后台宿主
RiskFlow SHALL 能安装为当前 Windows 用户的后台任务，并在用户登录后启动；后台宿主及其受管进程的存活不得依赖 Codex、启动终端、开发目录或启动器继续运行。开发版可以直接使用项目 Python；发布版 MUST 使用发布目录内的运行时和版本目录外的数据根。

#### Scenario: Codex 退出或更新
- **WHEN** RiskFlow 已由后台任务启动且 Codex 退出、重启或更新
- **THEN** 后台宿主、控制台、API、分析器和设备 Worker 继续运行

#### Scenario: 用户重新登录
- **WHEN** 已安装后台任务的 Windows 用户重新登录
- **THEN** RiskFlow 后台宿主自动启动且不自动提交新的设备任务

#### Scenario: Velopack 更新程序目录
- **WHEN** 安装版程序目录被新版本替换
- **THEN** 后台任务指向稳定启动入口且重新使用既有用户数据根

