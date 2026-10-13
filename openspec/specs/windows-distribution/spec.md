# windows-distribution Specification

## Purpose
定义MediaFlow Windows软件与完整平台Skill的可移植交付要求，区分程序、运行环境和用户数据，保证版本可追溯、失败可回退，不以历史安装包代替当前验收。

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
- **WHEN** 发布目录安装到新的本机路径并启动 `MediaFlow.exe`
- **THEN** 系统使用发布目录中的 Python、Node、ADB和前端产物启动控制台

#### Scenario: 打包内容审计
- **WHEN** 发布目录构建完成
- **THEN** 其中不包含 OpenRouter 密钥、运行数据库、截图、日志或既有设备任务

### Requirement: 更新安全的数据目录
安装版 SHALL 把可变数据保存到程序目录之外，读取既有bootstrap及用户选择的数据根，默认使用当前用户MediaFlow数据目录。程序升级 MUST NOT 覆盖或删除数据库、截图、日志、设备档案、预设和凭据，不隐式迁移目录。

#### Scenario: 更新程序版本
- **WHEN** NSIS或旧版兼容安装更新程序文件
- **THEN** 继续使用原数据根，程序文件与用户数据分离，默认数据根为 `%LocalAppData%\MediaFlow\data`

### Requirement: 可安装和可卸载
系统 SHALL 生成每用户NSIS安装器和稳定快捷方式；安装/更新 SHALL 注册当前用户独立Python后台任务，卸载 SHALL 确认空闲并准确停机后移除该实例后台，默认保留运行数据。旧版Velopack迁移使用已核实的旧卸载入口。

#### Scenario: 首次安装
- **WHEN** 用户运行MediaFlow NSIS安装器
- **THEN** 安装至选择的程序目录、创建稳定Electron入口并能启动完整平台

#### Scenario: 卸载
- **WHEN** 用户卸载MediaFlow且该实例无业务、维护或人工占用
- **THEN** 安全停止并移除该实例后台及程序入口，运行数据保持不变

### Requirement: Electron发行与不可变GitHub更新资产
Electron发行 SHALL 包含自包含业务载荷、Skill和许可，使用NSIS安装及纯版本标签、更新清单、blockmap和哈希；每个版本绑定唯一干净提交，旧包不覆盖。

#### Scenario: 发布开发更新
- **WHEN** 发布新的Electron开发测试包
- **THEN** 对应dev频道可发现与校验完整EXE，差分资源可用且不包含凭据、设备数据或私人镜像

### Requirement: 旧版迁移与隔离升级验收
安装器 SHALL 核对旧版安装身份，旧停机及卸载完成后才注册新版后台，保留数据和程序恢复资料；验收配置 SHALL 隔离目录、数据、端口、托管及注册，跨更新保留。

#### Scenario: 迁移失败
- **WHEN** 旧版占用、实际停止失败或新程序验证失败
- **THEN** 不继续覆盖或删除数据，给出失败原因与程序恢复路径

#### Scenario: 本机独立验收
- **WHEN** 以验收profile执行安装及GitHub升级
- **THEN** 只修改该实例，开发服务和现有手机任务不被操作，模拟检查与真实安装分别记录

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

### Requirement: 外部Skill平台不依赖内嵌引擎
发行资源 SHALL 包含完整平台Skill和现用共用修复材料，不要求OpenCode/Pi源码、二进制或凭证。旧聊天入口 SHALL 返回退役说明，不实例化引擎；历史数据保留。

#### Scenario: 无内嵌引擎资源
- **WHEN** 在隔离目录装配平台资源且没有OpenCode/Pi
- **THEN** Skill和共用材料校验通过，清单标记外部Agent模式，不生成内嵌引擎目录

#### Scenario: 数字识别离线资源完整
- **WHEN** 装配现行平台Windows发行目录
- **THEN** 必须包含与执行器对应的v1/v2角标字库，缺失或空字库阻断构建；不携带原始设备截图或私人模板
