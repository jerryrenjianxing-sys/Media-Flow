# MediaFlow Windows 交付

交付目标是平台、必要运行环境、控制组件和完整Skill；巨大虚拟机镜像不再是默认必需项，旧私人模板及打包能力保留。当前源码与实际已交付包范围见[STATUS](../STATUS.md)。

版本唯一来源为version.json；发布使用干净提交、不可变版本与哈希，不照搬旧版本示例。不包含真实Key、数据库、设备档案、截图和日志。模型由用户在安装后配置，不限于旧OpenRouter。

工具与产物分别在忽略的tools和out目录。运行数据独立于程序目录。用户2026-09-23要求制作并上传最新安装包；dev.44范围和未通过项见[发行说明](release-notes/0.4.1-dev.44.md)，不据此宣称新电脑安装通过。历史说明见[旧交付文档](../docs/history/2026-09-09/packaging/README.md)。

Electron发行入口为 `build-electron-release.ps1`，精确依赖与锁文件在desktop。它先调用 `build-windows-release.ps1 -PayloadOnly`，在独立目录构建现有控制台并装配Python、Node、ADB、控制组件和Skill；不装配WinForms/WebView2或内嵌Agent。

正式包必须来自干净提交与指向同一提交的纯版本标签 `v0.4.1-dev.N`；输出在out/electron-releases的独立版本目录。旧含斜线标签保留，不修改。`-LocalAcceptanceInstaller` 与 `-Unpacked` 仅生成源码外临时验收物，不能直接发布。`verify-stage.py` 和 `verify-electron-payload.py` 使用独立数据及随机端口，不注册后台、不启动Worker、不探测设备。

GitHub开发预发行包含NSIS EXE、dev.yml、blockmap、发行清单、SHA-256和许可证；ZIP为这些交付物的压缩副本，不冒称免安装版。更新流程不自动下载，使用dev频道，拒绝同版本及降级。未签名测试包、实际迁移与升级验收范围须在对应发行说明中明确。

旧 `build-windows-release.ps1` 的Velopack入口只用于历史发行回退；Electron新包不再依赖C#构建。只上传当前版本明确列出的软件资产，不上传整个Releases目录或私人模板包。
