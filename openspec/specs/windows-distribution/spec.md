# windows-distribution Specification

## Purpose
TBD - created by archiving change package-windows-installer. Update Purpose after archive.

## Requirements

### Requirement: 自包含 Windows 发布目录
系统 SHALL 能生成不依赖 Codex、项目虚拟环境或系统 PATH 中 Node/ADB 的 Windows 发布目录，并 SHALL 排除本机秘密和运行证据。

#### Scenario: 在没有开发目录的路径启动
- **WHEN** 发布目录被复制到新的本机路径并启动 `RiskFlow.exe`
- **THEN** 系统使用发布目录中的 Python、Node、ADB和前端产物启动控制台

#### Scenario: 打包内容审计
- **WHEN** 发布目录构建完成
- **THEN** 其中不包含 OpenRouter 密钥、运行数据库、截图、日志或既有设备任务

### Requirement: 更新安全的数据目录
安装版 SHALL 把可变数据保存到 Velopack 版本目录之外的当前用户数据目录，更新程序文件时不得覆盖或删除运行数据。

#### Scenario: 更新程序版本
- **WHEN** Velopack 用新版本替换 `current` 程序目录
- **THEN** 数据库、截图、日志、设备档案、预设和加密密钥继续保留在 `%LocalAppData%\RiskFlow\data`

### Requirement: 可安装和可卸载
系统 SHALL 生成每用户安装器和稳定快捷方式；安装/更新 SHALL 注册当前用户后台任务，卸载 SHALL 移除后台任务并默认保留运行数据。

#### Scenario: 首次安装
- **WHEN** 用户运行 Velopack Setup.exe
- **THEN** RiskFlow 安装到当前用户目录、创建稳定入口并能启动控制台

#### Scenario: 卸载
- **WHEN** 用户卸载 RiskFlow 且没有运行中任务
- **THEN** 后台任务被安全停止并移除，运行数据保持不变

### Requirement: 安装版独立执行设备初始化
Windows 发行目录 SHALL 包含初始化所需的 ADB、uiautomator2 服务文件和输入法资源，并 SHALL 能在没有 Codex、MBH、Mobilerun Portal或源码虚拟环境时完成初始化。

#### Scenario: 新电脑安装RiskFlow
- **WHEN** 用户在安装版中配置模型密钥并连接已授权设备
- **THEN** 控制台能够启动初始化、准备控制组件并生成运行数据目录中的设备与平台档案

#### Scenario: 发行内容审计
- **WHEN** 构建 Windows 发行目录
- **THEN** 构建检查确认初始化资源存在，同时继续排除密钥、设备档案、数据库、截图和日志
