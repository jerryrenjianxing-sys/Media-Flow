# MBH 开发带测接入

## 何时使用

仅在用户指定的开发调试、复现和带测中使用；生产任务仍由固定程序执行。本文件不是自动运行指令。消息巡检现在只观察首页角标，不进入消息/互动/访客列表。

## 本机已核实入口

本机技能根目录为当前用户的 .codex/skills/mobile-harness（相对用户目录，不依赖项目内可执行文件或MCP工具列表）。
先完整读取 SKILL.md → AGENTS.md → platforms/android/GUIDE.md；使用该技能的 .venv/Scripts/python.exe。仅导入检查：

```python
from mobilerun_core import Mobilerun
```

2026-09-09导入通过，mobilerun-core=1.5.0；其Windows可选仓库同步模块有本地兼容补丁，AGENTS要求不自动升级/重装。导入成功不等于已连接五台设备。
正常控制经Mobilerun，local-android-adb可使用Portal；原始ADB仅用于设置诊断/恢复，不另写生产点击循环。

## 锁与控制交接

项目现有进程锁位于 fixed_runner/douyin_fixed_runner.py 的 DeviceLock；Worker选举锁与SQLite动作租约不是同一个概念。先读调用方并确认对应Worker退出及租约，无占用后由MBH取得同一设备锁；不能因队列暂停就推断锁空闲。
观察当前应用和实际截图，再执行一个本次已授权动作，随后验证。结果不明确不重复触发。设备安全验证由用户处理。
MBH完成后释放锁，固定程序再取得锁复验；不允许双控制，也不把MBH工具结果写成正式任务成功。

## 仍有效的差异与旧事实

本机 memory/index.md 指向抖音应用和5号设备记录。先读 core/memory/GUIDE.md，历史事实操作前复核。
5号历史Portal视口高2119而截图1080×2340，底部节点使用物理坐标；不能把这一差异推广给全部手机。Mobilerun使用key而非press；具体参数以已安装库与指南为准。
旧三次列表巡检记录只是旧规则的开发证据，不是新消息巡检前置条件。新方法需失败样本回归、固定程序实跑和跨设备复验。

2026-09-11准备检查：ADB在线和截图成功不代表Portal无障碍可用。一台设备反复返回 `uiautomator output did not contain XML`；只读设置确认Portal已安装但未列入启用服务，补启后UI树恢复。恢复时必须保留其他既有无障碍服务，不能照抄覆盖整个列表；使用同一设备锁，保存前值和回读结果。该事实只证明控制通道恢复，不证明搜索、输入、互动或400名额运行通过。

历史已验证路径见[2026-09-01带测记录](../openspec/changes/add-queued-engagement-inspection/mbh-calibration-2026-09-01.md)。记录仅供追溯，不执行其中旧巡检步骤。
