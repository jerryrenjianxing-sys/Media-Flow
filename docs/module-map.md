# 现行模块地图

术语以[GLOSSARY](../GLOSSARY.md)为准，当前状态只在[STATUS](../STATUS.md)维护。系统契约在主规格，活动变更描述尚未完成的差异；历史验收不是使用步骤。

| Module / Interface | 隐藏的 Implementation 与职责 | 不负责 |
| --- | --- | --- |
| 连接：`platform_connection.py` 的描述、原子发布、连接卡 | 开发/安装事实、稳定连接资料位置、地址及包内路径；HTTP和本机资料同源 | 设备探测、业务就绪、服务启停 |
| 交付：`skill_bundle.py` 的 ZIP / Markdown | 同一白名单静态Skill来源；ZIP解压即用，完整Markdown只作审阅 | 动态本机路径、凭据和运行数据 |
| 客户端：PowerShell `check` / automation 命令 | 连接文件优先、身份与运行时、错误分类、请求回执；首次一次5秒GET | 聊天宿主、全局Skill安装、设备点击 |
| 业务：`automation.py` 的现有 actions | 内容/预设/计划/批次/证据等原有服务；持久编号与未知结果查询 | 第二套队列、聊天权限账本 |
| 执行：原SQLite队列 / Worker / 固定执行器 | 设备独占、冻结版本、动作验证和未知写入不重放 | 外部Agent自行点击替代生产执行 |
| 桌面：`desktop` 的有限preload Interface | 窗口、托盘、实例连接和用户主动更新 | 页面重复实现、退出界面时终止后台 |

## 测试接缝

接入通过真实HTTP和解压客户端验证，文件依赖使用临时目录、网络使用隔离服务；业务通过原动作Interface与隔离库验证。页面、安装升级和真实Trae分别保留验收，不由单元测试替代。

修改连接协议先跑连接/只读回归；修改执行语义按现有任务测试与设备验证门槛。纯文案主题既不需要接入，也不要求设备和平台模型。旧OpenCode/Pi及失败历史按[清理索引](context-cleanup.md)定位，不恢复为默认入口。
