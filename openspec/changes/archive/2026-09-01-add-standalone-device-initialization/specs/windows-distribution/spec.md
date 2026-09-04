## ADDED Requirements

### Requirement: 安装版独立执行设备初始化
Windows 发行目录 SHALL 包含初始化所需的 ADB、uiautomator2 服务文件和输入法资源，并 SHALL 能在没有 Codex、MBH、Mobilerun Portal或源码虚拟环境时完成初始化。

#### Scenario: 新电脑安装RiskFlow
- **WHEN** 用户在安装版中配置模型密钥并连接已授权设备
- **THEN** 控制台能够启动初始化、准备控制组件并生成运行数据目录中的设备与平台档案

#### Scenario: 发行内容审计
- **WHEN** 构建 Windows 发行目录
- **THEN** 构建检查确认初始化资源存在，同时继续排除密钥、设备档案、数据库、截图和日志
