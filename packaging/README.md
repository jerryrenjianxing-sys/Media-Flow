# MediaFlow Windows 交付

交付目标是平台、必要运行环境、控制组件和完整Skill；巨大虚拟机镜像不再是默认必需项，旧私人模板及打包能力保留。当前源码与实际已交付包范围见[STATUS](../STATUS.md)。

版本唯一来源为version.json；发布使用干净提交、不可变版本与哈希，不照搬旧版本示例。不包含真实Key、数据库、设备档案、截图和日志。模型由用户在安装后配置，不限于旧OpenRouter。

工具与产物分别在忽略的tools和out目录。运行数据独立于程序目录。用户2026-09-23要求制作并上传最新安装包；dev.44范围和未通过项见[发行说明](release-notes/0.4.1-dev.44.md)，不据此宣称新电脑安装通过。历史说明见[旧交付文档](../docs/history/2026-09-09/packaging/README.md)。

构建先执行 `build-windows-release.ps1 -SkipVelopack` 验证隔离目录，再从干净提交和同版本不可变标签构建完整安装器。`verify-stage.py` 使用独立数据及随机端口，不注册后台、不启动Worker、不探测设备。只上传当前版本明确列出的软件资产，不上传整个Releases目录或私人模板包。
