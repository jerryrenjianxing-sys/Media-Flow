# windows-distribution Specification

## Purpose
TBD - created by archiving change package-windows-installer. Update Purpose after archive.

## Requirements

### Requirement: 平台携带标准虚拟机配置 Skill
Windows软件 SHALL 携带平台运行环境、控制组件及完整mediaflow-platform Skill；巨大虚拟机镜像 MUST NOT 成为获取Skill和配置空白MuMu的前提。首页下载和复制 SHALL 使用同源材料，包含配置引导和客户端。新建空白实例暂无平台接口时 SHALL 如实引导MuMu界面，不调用模板创建冒充空白创建。

#### Scenario: 无系统Python的外部Agent接入
- **WHEN** 已安装平台的用户从首页取得Skill并调用Windows客户端
- **THEN** 客户端保留显式配置，否则从本机GET automation取得运行环境并调用包内Python；旧平台或后台断开给出启动/修复入口，不要求安装开发工具；本机路径和凭据不进入通用材料

#### Scenario: 仅补本次必要条件
- **WHEN** 标准实例已安装抖音但未登录、中文输入尚未验证
- **THEN** 配置引导复用实例，不阻断管理和浏览准备；用户手工登录，搜索或评论才要求实际中文输入验证，不以输入法启用代替实测，不强制整套初始化或逐机三次校准

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

### Requirement: 测试安装包具有唯一发布身份
每次安装包内容变化 SHALL 递增测试版本；同一测试版本 MUST 绑定唯一干净Git提交、不可变标签、发行清单和SHA-256，不得生成同号不同内容的安装包。

#### Scenario: 构建开发测试包
- **WHEN** 构建 `0.4.1-dev.2` 安装包
- **THEN** 源码提交、Git标签、安装包、接口和发行清单记录同一版本及提交号

#### Scenario: 工作区存在未提交修改
- **WHEN** 构建脚本发现工作区不干净
- **THEN** 安装包构建失败且不得覆盖已有发行资产
