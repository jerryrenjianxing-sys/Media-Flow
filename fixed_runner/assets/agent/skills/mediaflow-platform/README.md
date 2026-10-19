# MediaFlow Platform Skill

把搜索主题、设备准备、任务安排和结果复盘交给外部 Agent，由已安装的 MediaFlow 执行业务。无需另装 Python、Node 或内置聊天引擎。

## 两种接入方式

### 首页“复制 Skill”

复制的是简短 Markdown **连接说明**，包含当前电脑已有 Skill、客户端和连接资料的位置。把它交给能访问这台电脑的 Agent，Agent 直接读取现成 Skill 并检查连接，不需要逐字重建文件。说明中的本机路径只适用于当前电脑；升级后路径失效时重新复制。

### 首页“下载 Skill”

1. 解压 ZIP，保留 `mediaflow-platform` 目录结构。
2. 让 Agent 读取该目录的 `SKILL.md`，在目录内调用 `./scripts/mediaflow.ps1 check`。
3. 根据用户目标继续。解压即用，无需放入宿主全局技能目录或重启宿主。

完整 Markdown 导出用于阅读、审阅和离线参考；需要执行客户端时使用软件现成文件或 ZIP。仅收到 Skill 时的检查与回复流程，以 [SKILL.md](SKILL.md#首次接入一次检查即可) 为准。

## 连接与运行环境

`check` 使用系统 PowerShell，一次只读 GET，不需要 Python，不创建工作文件、不启动服务或扫描设备。后台未启动先询问用户；连接成功不等于设备在线或任务就绪。

连接卡中的 `--connection-file` 在每次调用时重读，优先于旧地址和 Python 配置。它包含软件更新的本机资料，不含凭据；通用 ZIP 不携带这份动态文件。

没有显式连接文件时保留原配置方式：API 默认 `http://127.0.0.1:48138`，仅接受本机回环 HTTP。无需配置时直接使用。需要覆盖配置时才从 `config.example.json` 创建自己的 `config.json`；已有文件保持不变。

| 原配置项 | 用途 |
| --- | --- |
| `MEDIAFLOW_SKILL_CONFIG` 或 `--config` | 选择本地配置文件 |
| `MEDIAFLOW_API_URL` | 覆盖 `config.json` 的 `api_url` |
| `MEDIAFLOW_PYTHON` | 显式 Python；其次取配置 `python`，再取运行平台提供的包内 Python |
| `MEDIAFLOW_RECEIPT_DB` | 覆盖配置 `receipt_db`，保存持久写入回执 |

业务命令由启动器使用已安装平台的 Python；无系统 Python 也能使用。只有已明确配置 Python 的宿主才直接调用 `scripts/mediaflow.py`。配置、回执、凭据、设备信息及截图不属于可再分发技能包。

## 按需阅读

- [内容指南](references/content-guide.md)：主题标准、搜索轮换、评论材料及坚果厂家示例。
- [配置引导](references/setup.md)：MuMu、显示、应用、输入和登录。
- [工作流](references/workflows.md)：保存、方案与执行、评论方式、模型、提醒、恢复和复盘。
- [API](references/api.md)：动作、参数及真实回执。
- [故障处理](references/troubleshooting.md)：连接、未知结果及执行故障。

`examples/` 只提供可复用示例或占位参数。写请求使用稳定唯一的请求编号，未知结果先查询原回执。主题示例保存成功不意味着启动任务；`preview_only` 只控制评论是否发送，不是整个任务的执行开关。

本 Skill 不绑定特定 Agent 宿主或聊天模型；客户端使用 Python 标准库。第三方归属见 [NOTICE](NOTICE.md)。
