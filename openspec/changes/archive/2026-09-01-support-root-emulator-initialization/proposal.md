# Change: 支持 Root 虚拟机独立初始化

## Why

MuMu Android 15 虽然通过本地 ADB 暴露 Root shell，但系统会禁用 `ime enable/set`，导致控制 APK 已安装仍被初始化误判为需要人工确认。虚拟机应优先走全自动、可复验路径，同时不能削弱现有真机初始化和写入安全门。

## What Changes

- 保留现有真机 FastInputIME/AdbKeyboard 安装、启用、原输入法恢复和人工断点逻辑。
- Root 设备在控制 APK 已安装但系统拒绝切换输入法时，允许进入 UIAutomator 控件文本兜底。
- 兜底必须在无写入搜索框内写入中文并原样回读成功后才可标记中文输入能力通过。
- 评论输入优先使用可回读的控件 `set_text`，失败后才沿用现有输入法路径；发送前和发送后验证不变。
- 不改变普通任务、互动概率、评论安全门或未知写入不重放规则。

## Impact

- Affected specs: `device-initialization`
- Affected code: `fixed_runner/device_initialization.py`, `fixed_runner/execution_tasks.py` 及对应测试

