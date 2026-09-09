# MediaFlow 运行与排障手册

当前架构见[PROJECT](PROJECT.md)，真实完成范围见[STATUS](STATUS.md)。本手册不承诺尚未验证的安装版或独立运行能力。

## 1. 先检查，不自动启动

首页/任务台：http://127.0.0.1:3001/ 。业务 API：http://127.0.0.1:48138 。

用户仅提供 Skill 时，运行包内 scripts/mediaflow.ps1 check：只读 GET /api/automation，5秒总截止，不依赖系统Python，不连接ADB、不启动服务、不收口任务。设备登记快照带检测时间；online:null不是在线。

服务确认未启动时先询问。仅连接失败不能断言软件没开。用户同意后才用已核实入口启动一次，最多30秒复查；不能猜路径、重复拉起后台或恢复等待任务。无本机工具时明确限制。

## 2. 用户明确要求后的后台操作

开发版在已确认的项目根目录使用现有入口：

```powershell
.\manage-mediaflow.ps1 -Action Status
.\manage-mediaflow.ps1 -Action Doctor
.\run-mediaflow-console.ps1
```

Status/Doctor用于运行环境诊断，不替代首次纯只读检查。安装版从已安装软件打开；不能把源码路径、开发工具或旧私人安装包当成新电脑的必备条件。

后台由现有Windows当前用户托管配置管理；本机是否独立需检查实际进程、任务和运行时，见STATUS的未验证项。缺依赖时说明缺什么，不能静默借Codex进程启动。
Agent旧启动链已退役；保留旧数据但不注册或恢复内置聊天服务。

停止或重启前确认业务、维护和人工控制空闲；用已登记后台管理入口，不结束所有Python/Node进程。不得抢占其他程序占用的端口。

## 3. 设备与任务按需准备

完整操作读取[Skill配置指引](fixed_runner/assets/agent/skills/mediaflow-platform/references/setup.md)与[工作流](fixed_runner/assets/agent/skills/mediaflow-platform/references/workflows.md)。

- 复用现有MuMu，缺少才引导安装；空白实例按现有MuMu界面创建，不误用仍依赖模板的平台创建接口。
- 标准MuMu实际画面900×1600、320 DPI、竖屏；不以名字、Root、精确版本淘汰。
- 抖音缺失用已批准来源，未配置则询问安装包；不偷偷换下载源。
- 看屏只依赖连接与截图；搜索/评论才验证中文输入，必须实际写入并读回，启用输入法不等于成功。
- 用户手工登录/验证码；实际遇到验证页面才暂停相关业务，不阻断管理或参数准备。
- 真机显式启用后按实际设备与显示确认，不能套用MuMu显示门槛。见[真机准备](docs/real-device-agent-initialization.md)。

## 4. 执行、等待和停止

参数齐全且明确执行时，Skill用原计划提交与查询，不需要聊天权限等级或重复确认卡。咨询、保存不执行；未指定互动全零。主题与参数版本冻结，恢复原任务不补刷。

普通队列暂停阻止业务领取，不等于停止全部操作。停止本批次使用既有批次入口；紧急停止用任务台的停止入口。恢复只恢复指定对象，不放行历史等待任务。结果未知先查原回执，不换请求编号重复提交。

单条失败留证并消耗名额；degraded是完成有异常。waiting_model / waiting_device / waiting_user保留原任务进度；连续临时模型故障由后端共享探针复查，Agent不另建循环。未知互动不重放，当前实现会暂停该设备批次的对应能力；误暂停问题见待修记录。

消息巡检仅确认首页并观察角标，不点击消息。数字可见记录数字；仅红点记录有消息；失败保留原提醒状态，不当成无消息，不运行旧详细巡检。

## 5. 证据与开发带测

先读取任务回执、检查点、截图、UI树和轨迹。目标数量不是成功数量；空结果不是零。模型失败区分网络、鉴权、限流、格式和业务拒绝，不将它们全部归为Key问题。

MBH位于当前用户的 .codex/skills/mobile-harness；先读其SKILL.md、AGENTS.md及Android指南，再用其 .venv/Scripts/python.exe。
入口为 mobilerun_core.Mobilerun，已核实本机版本1.5.0有Windows兼容补丁，不自动升级或重装。

开发带测的操作顺序：
1. 核对用户指定设备与本次任务范围；历史设备表不能证明在线。
2. 确认对应Worker停止，使用项目同一设备独占锁；找不到锁适配则先查代码，不绕过。
3. 通过Mobilerun的local-android-adb观察、单步动作、复验；UI树与截图冲突时不盲点。
4. 截图、前后状态、耗时和结果组成失败回归；共享规则不能硬编码某台手机的整套流程。
5. 释放MBH锁，再由固定程序复验。同一时刻不允许双控制者。
6. MBH跑通、固定程序跑通和长批次稳定分别报告；登录验证不绕过。

具体方法与已知设备差异见[MBH接入说明](docs/mbh-development.md)；本轮授权参数和待修缺陷见[后续记录](docs/current-followup.md)。

## 6. 验证、发布和回退

```powershell
openspec validate --all --strict
.\.venv\Scripts\python.exe scripts/check-context.py
.\.venv\Scripts\python.exe scripts/test-python.py
```

Python必须通过隔离测试入口，不直接发现测试读取本机模型配置。控制台测试见[前端说明](control_console/README.md)。只改文档不为验收操作设备。

版本以packaging/version.json为准；发布包必须唯一版本、干净提交及校验清单。开发状态和安装包交付分别验证，不凭构建成功宣称新电脑可用。
回退只回退对应程序/文档，不覆盖新数据或删除凭证、任务、设备与证据。历史操作原文见[旧手册快照](docs/history/2026-09-09/RUNBOOK.md)。
