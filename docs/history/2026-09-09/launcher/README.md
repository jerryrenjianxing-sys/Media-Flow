> 历史快照；原路径 launcher/README.md。相对链接按原路径解释，非当前操作。

# MediaFlow EXE 启动器

`MediaFlow.exe` 是 MediaFlow Windows 独立桌面版入口。

- 双击后触发当前用户的 `MediaFlow Background` 计划任务；Codex 或启动器退出不影响后台运行。
- 后台宿主检查并维护本地 API、网页、纠错分析器和已配置设备 Worker。
- 服务就绪后在 WebView2 桌面窗口内打开同一套 `http://127.0.0.1:3000/` 页面；浏览器入口永久保留。
- 关闭窗口会收进系统托盘，后台任务继续；托盘菜单可重新打开、转到浏览器或显式停止后台。
- 重复启动只激活已有窗口，不重复拉起后台。
- 启动失败时显示错误窗口，详细日志仍保存在 `fixed_runner/runtime/`。
- 开发版 EXE 放在项目根目录；发行版由 Velopack 放入版本目录并通过稳定入口启动。

重新生成启动器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\launcher\build-launcher.ps1
```

只拉起并验证后台、不打开桌面界面：

```powershell
.\MediaFlow.exe --background-only
```

开发时可用 `MediaFlow.exe --dev-url=http://127.0.0.1:3000/` 连接热更新页面。发行版内置 React、Python、Node、ADB、实时画面网关和锁定的 scrcpy server；WebView2 使用系统 Evergreen Runtime，缺失时提供微软官方安装入口。数据库、截图、日志、设备档案和加密密钥保存在 `%LocalAppData%\MediaFlow\data`，不会打进安装包，也不会随 Velopack 更新覆盖。
