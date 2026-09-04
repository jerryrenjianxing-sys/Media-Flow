## ADDED Requirements

### Requirement: 一键入口优先使用独立后台宿主
已安装 Windows 后台任务时，一键启动、停止和重启入口 SHALL 通过后台宿主管理 RiskFlow；未安装时 MUST 明确报告非独立兼容路径或引导完成安装，不得把直接子进程误报为独立运行。

#### Scenario: 双击已安装的 RiskFlow
- **WHEN** 用户双击 `RiskFlow.exe` 且后台任务已经注册
- **THEN** 启动器触发后台宿主、等待本地 API 和网页就绪并打开控制台后退出

#### Scenario: 后台宿主已经运行
- **WHEN** 用户再次执行一键启动
- **THEN** 系统复用现有后台宿主和受管进程，不创建第二套服务

### Requirement: 后台状态纳入 doctor
运行诊断 SHALL 报告独立后台任务是否注册、宿主心跳是否新鲜以及受管进程是否可访问，并继续将服务状态与设备状态分开。

#### Scenario: 页面无法访问
- **WHEN** doctor 发现后台任务已注册但宿主心跳过期或本地端口不可访问
- **THEN** 诊断明确指出后台生命周期故障，不把它归因为 Android 设备离线

