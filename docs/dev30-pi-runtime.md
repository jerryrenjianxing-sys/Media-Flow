# Pi候选运行与回退

## 状态及来源

dev.30是源码候选，不是安装包。真实验收仍未完成，本机默认入口没有切换。Agent与平台分别托管；Agent退出不停止管理中心、业务API或固定执行器。

来源与锁文件摘要见 `packaging/pi-source.json`：社区pi-web-ui 0.68.0，提交 `03680b5066433478a423b59af3a57f1b633df62a`，实际Pi SDK 0.84.4。社区界面及Pi使用MIT许可，上游许可/版权随源码保留。没有把社区界面称为官方Pi WebUI。

## 离线装配

1. 在忽略目录中检出上述精确上游提交，核对锁文件摘要；使用MediaFlow专用Node运行锁定依赖安装和上游生产构建。首次获取依赖需要网络，正常启动不安装依赖、不自动更新。
2. 先验收原版。仅在原版通过后执行 `fixed_runner/pi_brand.py` 的品牌装配；不修改会话、路由、消息、滚动或流式实现。上游工程不提交到本项目。
3. 调用 `scripts/prepare-pi-runtime.py`，参数为 `--source`（已构建上游目录）、`--node`、`--python`（专用运行时）、`--root`（Pi独立数据目录）、`--output work/pi-runtime/runtime.json`。隔离测试额外指定 `--port 13032`，正式切换才使用3000。可选 `--old-auth` 只读复制已有千问凭证；不覆盖已有Pi凭证，不迁移OpenCode消息。
4. Pi数据目录应置于项目目录之外，避免原生Agent自动读取项目祖先目录的开发指导。装配器在其中准备工作目录、原生配置、全局Skill及只读管理插件；用户无需选择项目或填写本机服务器地址。不要将该目录、凭证或请求计数提交Git。
5. `pi_host.py` 从配置启动已构建资源。找不到资源时返回错误并写 `last-error.json`，不退回其他Agent或Codex子进程。Node入口通过文件URL预加载请求保护，Windows路径不能当作URL协议。

```powershell
# 用明确的专用Python路径调用；下列运行入口不启动业务任务。
& $MediaFlowPython fixed_runner/pi_host.py status --config work/pi-runtime/runtime.json
& $MediaFlowPython fixed_runner/pi_host.py run --config work/pi-runtime/runtime.json
& $MediaFlowPython fixed_runner/pi_host.py stop --config work/pi-runtime/runtime.json
```

直接运行仅用于隔离诊断，不作为已脱离终端/Codex的证明。日常托管必须通过独立Windows任务。

## 真实请求额度

`trial-request-budget` 中每个 `.claim` 文件代表一次已预留的真实千问请求。失败和工具续接也计数；最多10次，先持久化再发送。重启、重复装配及并发不重置。该保护仅用于本轮验收，不宣称为通用生产计费系统；当前10/10耗尽，禁止通过换目录、删槽或切换服务商继续试用。

原生UI中的美元估计不能证明Token Plan的Credits为零；实际套餐用量以千问工作台为准。管理插件可只读查看本轮次数。配额不足保持“未验收”，不归为Pi聊天失败，不修改聊天核心绕过。

## 通过验收后的受控切换（尚未执行）

1. 重新只读确认队列暂停、0运行、原4条等待及无其他执行占用，备份业务与原生数据库、凭证、配置、浏览器草稿及现有Windows任务定义。
2. 将专用Node/Python及已验收Pi资源放到稳定的忽略目录；运行配置不得依赖即将删除的试验工作区。核对版本、摘要与可执行路径。
3. **先用旧dev.29入口**的 `manage-mediaflow-agent.ps1 -Action Stop` 停止旧OpenCode Agent，核对旧登记进程退出。新Pi宿主不会代替旧宿主清理OpenCode登记；不能直接用新版Stop冒充已停止旧Agent。
4. 检查3000无占用。新版 `manage-mediaflow-agent.ps1 -Action Register` 备份并更换同名 `MediaFlow Agent` 任务；随后Start。平台后台不注册、不重启。发现其他进程占端口时停止切换，不杀未知进程。
5. 验证Pi健康、设置/会话/流式、Skill只读调用、独立管理链接、暂停及等待任务未变。主项目代码身份需与干净候选提交一致。不要把测试端口的配置直接用于默认入口。
6. 关闭Codex后的独立寿命必须另做实际验证；父进程或健康检查不能代替。没有完成时照实保留“待验证”。

切换失败：用Pi宿主停止并核对登记进程，再恢复备份的旧Windows任务定义与旧程序入口。Pi和OpenCode数据均保留，不覆盖新消息，不通过删除数据库回退。

## 外部Skill备用

如果原生核心交互真正不合适才启用备用路线；额度不足不是淘汰理由。现有可移植包由 `skill_bundle.py` 从白名单构建，插件提供下载。包含操作说明、接口参考、标准库CLI和配置示例，不含Key、设备身份或业务数据库。Agent可在外部直接加载，仍只调用同一个 `/api/automation`，不要求安装OpenCode。隔离业务链已有测试；备用首页本轮未启用。
