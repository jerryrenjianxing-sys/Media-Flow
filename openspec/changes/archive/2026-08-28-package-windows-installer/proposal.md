# Change: 生成独立 Windows 运行目录与 Velopack 安装包

## Why

RiskFlow 已能脱离 Codex 常驻，但当前启动器仍依赖项目目录中的 `.venv`、系统 PATH 中的 Node/ADB，以及 UTF-8 无 BOM 的 PowerShell 脚本。使用者需要一个不依赖开发工具的本机入口，也需要一个可复制给其他 Windows 测试电脑的一键安装包。

## What Changes

- 修复 Windows PowerShell 5.1 对运行入口脚本的编码兼容问题并建立回归检查。
- 生成包含可搬迁 Python、Node、ADB、前端 standalone 产物和 RiskFlow 程序文件的发布目录。
- 将 SQLite、日志、截图、设备档案和加密密钥放到 `%LocalAppData%\RiskFlow\data`，避免 Velopack 更新覆盖。
- 启动器支持便携/安装布局、后台计划任务注册、Velopack 安装/更新/卸载钩子。
- 使用 Velopack 生成便携压缩包和每用户 `Setup.exe`。

## Impact

- Affected specs: `independent-runtime-host`、新增 `windows-distribution`
- Affected code: 启动器、运行时路径解析、前端 standalone 构建、后台任务脚本和发布脚本
- 不改变设备动作、任务重试、安全闸门、主题判断或评论策略。
- 安装包默认不携带本机数据库、截图、日志、设备档案或模型密钥。

