## Why

用户批准将现有WinForms/WebView2壳迁移为Electron，保留平台页面和独立Python后台，并增加可见、用户发起的GitHub更新。已发布dev.44只有Velopack资产，不能直接使用Electron更新器；旧停机流程还缺少维护占用检查及停止失败核验。

## What Changes

- Electron窗口、托盘、启动反馈和有限preload接口，浏览器页面继续兼容。
- 启动检查、横幅提醒、主动下载、差分优先、明确进度和可取消安装流程。
- 持久化更新锁及准备/状态/取消/完成接口，核验业务、维护、控制租约和真实进程退出。
- **BREAKING** 新安装采用NSIS和纯版本GitHub标签；dev.44通过完整迁移安装接管，不改写旧安装包。
- 完整运行环境、Skill和MIT/第三方许可随包；增加本机隔离验收配置并跨更新保留。

## Capabilities

### New Capabilities

- electron-desktop: 桌面生命周期、桥接和用户可见GitHub更新。

### Modified Capabilities

- windows-distribution: NSIS资源、旧版迁移、不可变发布与隔离升级验收。
- independent-runtime-host: 更新占用门槛、准确停机及独立实例参数。

## Impact

影响desktop、前端更新提示、安装/打包脚本和后台更新控制。用户明确授权本机隔离安装及GitHub开发预发行；目标dev.45候选→dev.46实测交付，修正已发布内容继续递增。不恢复手机任务、不调用模型、不迁移或清空现有数据、不替换当前开发服务、不内置Agent或巨大镜像。真实安装、升级和差分效果必须实际验收，不以单测替代。
