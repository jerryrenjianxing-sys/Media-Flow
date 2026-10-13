## Context

见proposal。现用页面3001、API48138、画面48139；安装数据在安装目录外。当前停机仅检查业务running，且停止失败也可能写stopped。旧安装允许自选程序/数据目录，因此不能猜目录或覆盖旧current。用户选择本机隔离配置进行真实升级。

## Goals / Non-Goals

实现可安装Electron壳及GitHub更新，复用业务界面和后台。第一轮不执行设备任务、不改主题概率、不调用模型、不新增内置聊天、不移动用户数据；不把模拟迁移当真实旧版覆盖安装。

## Decisions

- desktop使用CommonJS主进程/preload，锁定Electron44.7.0、builder26.17.0、updater6.8.10。React页面增加可选桥接组件；浏览器没有桥接时隐藏桌面更新控件。
- 包内业务根为resources/platform；后台任务直接运行包内pythonw，Electron退出不影响后台。安装脚本接收AppRoot、LauncherPath和InstanceProfilePath。
- 实例配置schema1含id、data_root、ui_port、api_port、stream_port、task_name、acceptance；production默认为既有数据/3001/48138/48139，验收使用acceptance实例、独立数据/31301/49338/49339与MediaFlow Background Acceptance。配置及Electron偏好在程序目录外，NSIS安装参数INSTANCEPROFILE和更新installerArgs持久传递；验收登记、快捷方式、互斥体不得碰生产实例。
- 预加载接口window.mediaflowDesktop提供getState/subscribe/checkForUpdates/startUpdate/deferUpdate/cancelUpdate/retryStartup/openLogs。state含desktopVersion/sourceRevision/startup/update/instance/packaged；update.phase为idle/checking/available/downloading/verifying/waiting_idle/countdown/installing/current/error。
- 主进程只启动一次检查，不自动下载、不退出自动安装。点击后差分优先并显示进度；下载校验后准备更新，空闲后可取消倒计时并调用非静默NSIS。差分失败转完整；缓存及请求状态使重复点击/页面刷新不重复执行。
- 新POST /api/runtime/update/prepare接收request_id/target_version/sha512，返回operation_id/status/blockers；GET同操作核对空闲；POST cancel解除锁；POST complete在新版回读后完成。SQLite锁与领取/设备操作在同一事务检查，暂停标记和旧任务不改写；保留运行、初始化、恢复、VM、人工控制及短活动租约阻塞信息。
- stop-sync停止真正已登记进程并核验退出；失败报告stop_failed且保留登记。新宿主启动时保持更新锁直至桌面完成版本回读；接受验收模式时不枚举ADB、不起Worker或模型分析器。
- Electron发行用纯v0.4.1-dev.N标签、dev.yml/EXE/blockmap；旧标签/安装包保留。dev.45→dev.46实测，发布后修代码递增版本，旧资产不替换。

## Risks / Trade-offs

- 未签名安装被Windows限制：如实记录系统提示，不绕过系统策略。
- 旧卸载删除新版任务/入口：旧后台退出、旧卸载完成后才注册新版。
- 更新竞争或停机假成功：持久化领取门禁、全占用核验和进程退出回读。
- 开发环境被验收修改：测试profile隔离端口、目录、任务、注册/互斥体；验收不得静默降级至生产。
- 旧dev.44不认识测试profile：只在能证明全隔离时进行真迁移；不可行则报告该项未验证，不使用模拟成功替代。

## Migration Plan

新载荷先验证；保存旧安装身份、任务定义及原安装器/程序备份。旧版空闲后安全停机并实际核验，禁止旧托管重启；旧Update.exe卸载完成后启用NSIS程序及新后台。更新后回读桌面/API版本与提交、数据根、暂停及任务记录。失败恢复程序与托管资料，不覆盖数据库、不删除新证据。保留独立安装操作记录、失败出口及重试；安装版更新不复用开发Git补丁执行器。
