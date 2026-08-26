# RiskFlow EXE 启动器

`RiskFlow.exe` 是现有 RiskFlow 本地服务的一键启动入口。

- 双击后检查并启动本地 API 与网页服务。
- 服务就绪后自动打开 `http://127.0.0.1:3000/`。
- 启动失败时显示错误窗口，详细日志仍保存在 `fixed_runner/runtime/`。
- EXE 必须放在项目根目录，与 `run-riskflow-console.ps1` 保持同级。

重新生成启动器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\launcher\build-launcher.ps1
```

自动验证但不打开浏览器：

```powershell
.\RiskFlow.exe --no-browser
```

这个 EXE 只负责启动和报错提示。React、Python、Node、ADB、数据库和模型配置仍保留在原项目中，方便继续更新和排障。
