# MediaFlow · 一站式媒体自动化Agent

MediaFlow 是 Windows 本地媒体自动化平台。外部 Agent 通过随平台提供的 Skill 理解目标、准备参数和调用业务接口；正式设备任务由固定程序执行。

## 使用入口

1. 打开已安装的 MediaFlow，或本机开发版已有启动入口。
2. 首页为 http://127.0.0.1:3001/ ：“复制 Skill”提供完整 Markdown，“下载 Skill”提供同源 ZIP；角落“任务台”保留全部管理功能。
3. 把 Skill 交给能访问本机的外部 Agent。首次只读检查不启动服务，连接不上先询问用户。
4. 按需配置设备、定制主题、保存或运行；已给参数不重复询问。咨询或保存不启动任务，明确要求执行才提交。
5. 默认使用本机标准 MuMu；真机须明确启用并选择。登录和验证码由用户完成，不以登录或完整初始化作为设备管理门槛。

当前不内置聊天引擎，也不要求安装 OpenCode、Pi 或 Hermes。软件交付和本机开发环境的实际验证范围见当前状态，不把源码能力当作已发布安装包。

## 从哪里读起

- [当前项目与阅读索引](PROJECT.md)：架构、规则及各资料分工。
- [当前状态与未完成项](STATUS.md)：唯一现行状态摘要。
- [运行和排障](RUNBOOK.md)：启动、检查、停止、设备准备及开发带测。
- [完整平台 Skill](fixed_runner/assets/agent/skills/mediaflow-platform/SKILL.md)：实际业务引导。
- [上下文清理清单](docs/context-cleanup.md)：历史材料、处置依据及检查方法。
- [主题规范](docs/topic-policy-standard.md)与[提示词说明](docs/content-prompt-guide.md)。
- [前端说明](control_console/README.md)、[固定执行器说明](fixed_runner/README.md)、[主规格](openspec/specs)。

本轮整理不启动任务、不修改设备、不删除数据库、凭证或证据。历史验收仍可追溯，不等同于当前功能已通过。

## 许可证

本项目原创代码与文档采用 [MIT License](LICENSE)，版权署名为 `Copyright (c) 2026 jerryrenjianxing-sys`。使用、修改或分发时须保留许可证要求的版权与许可声明。

第三方组件、素材及商标仍受各自权利和许可约束；根目录许可证不替代已有第三方声明，也不表示第三方对本项目的认可。软件按“现状”提供，完整授权及责任限制以 [LICENSE](LICENSE) 原文为准。
