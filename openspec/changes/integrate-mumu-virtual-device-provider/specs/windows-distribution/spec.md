# windows-distribution Delta Specification

## ADDED Requirements

### Requirement: MediaFlow 使用唯一发行版本身份
系统 SHALL 从一个受版本控制的清单生成桌面程序、品牌安装器、Velopack包和发行清单的版本，并 SHALL 在源码开发运行中同时展示Git提交身份；构建不得接受与该清单冲突的独立版本号。

#### Scenario: 多台开发电脑构建同一提交
- **WHEN** 两台电脑从同一提交和同一版本清单构建MediaFlow
- **THEN** 两者产生相同的发行版本与提交身份，不得各自形成不可区分的分叉安装版

#### Scenario: 工作区含未提交修改
- **WHEN** 从带有未提交修改的源码运行开发版
- **THEN** 页面明确标记开发版和脏工作区，不得将其显示成已发布安装版

#### Scenario: 脏工作区请求生成安装包
- **WHEN** 任意电脑从含已修改或未跟踪源文件的工作区请求生成Velopack安装包
- **THEN** 构建失败并要求先形成唯一提交；只允许继续运行明确标记的开发版

### Requirement: 品牌安装器支持升级、修复与降级保护
MediaFlow品牌安装器 MUST 在保留独立用户数据目录的前提下允许旧版本升级和同版本修复覆盖，并 MUST 阻止更高已安装版本被较旧安装包静默降级。

#### Scenario: 覆盖旧版本
- **WHEN** 安装包版本高于当前安装版
- **THEN** 安装器安全停止旧后台、完成升级并验证桌面程序与后台启动项后才报告成功

#### Scenario: 修复相同版本
- **WHEN** 安装包版本等于当前安装版
- **THEN** 安装器提供同版本修复，重新安装程序文件并保留数据库、截图、Key、档案与历史证据

#### Scenario: 阻止降级
- **WHEN** 安装包版本低于当前安装版
- **THEN** 安装器停止操作并显示已安装版本和安装包版本，不改动程序或用户数据

### Requirement: 控制台长操作必须有界收口
所有用户触发的本机API操作 SHALL 具有前端最长等待时间、后端操作互斥或幂等保护以及失败后的可重试终态；页面 MUST NOT 因网络、后台重启或响应丢失永久停留在“正在保存”或“启动中”。

#### Scenario: Key鉴权请求没有及时返回
- **WHEN** OpenRouter网络验证或本机后台超过页面等待上限
- **THEN** 页面解除“正在保存”、保留原可用Key并提示先刷新真实状态后再重试

#### Scenario: 虚拟机状态轮询连续失败
- **WHEN** 生命周期操作状态连续三次无法读取或超过有界轮询时间
- **THEN** 页面解除本地忙碌状态并提示刷新对账，不得重复发送结果未知的启动命令

#### Scenario: 草稿发生新的编辑
- **WHEN** 前一次自动保存尚未完成而用户再次修改草稿
- **THEN** 页面取消过时请求，只以最新修订继续保存，不允许旧响应覆盖新草稿

### Requirement: Windows密钥存储不受开发环境模块污染
系统 SHALL 使用Windows自带PowerShell安全模块完成OpenRouter Key的DPAPI加密与解密，并 SHALL 为加密、解密和后台导入三个子进程显式隔离模块搜索路径；开发工具或第三方运行时注入的PowerShell模块不得影响已安装版或开发版的密钥存储。

#### Scenario: 开发环境注入了第三方PowerShell模块目录
- **WHEN** MediaFlow进程继承的 `PSModulePath` 优先指向第三方或不兼容的 `Microsoft.PowerShell.Security` 模块
- **THEN** 密钥保存与运行时解密仍使用Windows自带模块成功完成，不得永久停留在“正在保存”

#### Scenario: 新Key未通过鉴权
- **WHEN** 候选Key能够安全加密但OpenRouter返回鉴权失败
- **THEN** 页面进入可重试终态并保留原有可用Key，不得把加密环境错误与Key无效混为同一故障
