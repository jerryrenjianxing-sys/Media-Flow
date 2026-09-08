# execution-architecture Specification

## Purpose
TBD - created by archiving change modularize-execution-core. Update Purpose after archive.

## Requirements

### Requirement: Skill 首次接入是独立只读路径
系统 SHALL 提供无需 Python 的首次连接检查，并从已有数据库只读返回平台及登记设备摘要。该路径 SHALL NOT 创建业务上下文、收口任务、连接 ADB、扫描或操作虚拟机；历史设备状态 SHALL 带检测时间且不得解释为当前在线。

#### Scenario: 单独提供平台 Skill
- **WHEN** 用户提供首页复制内容或技能入口，且没有要求仅阅读、解释或修改
- **THEN** Agent SHALL 先判断本机工具能力，再运行首次只读检查并报告真实结果
- **AND** 单独提供 Skill SHALL NOT 授权业务任务或服务启动

#### Scenario: 平台无法连接
- **WHEN** 检查失败或超时
- **THEN** 系统 SHALL 区分错误服务、原因未知及已确认停止，不把连接失败直接称为服务停止
- **AND** Agent SHALL 先询问用户，获得同意并核实当前安装的后台入口后才启动一次，观察最多30秒；未知结果不重复启动

#### Scenario: Agent 没有本机工具
- **WHEN** Agent 无法访问用户电脑
- **THEN** Agent SHALL 明确限制并给出用户可执行的检查步骤，不查询云端回环地址冒充本机检查

### Requirement: 固定执行器拥有唯一设备动作平面
系统 SHALL 只允许固定执行器持有设备连接并执行点击、滑动、输入和返回；AI、纠错分析器、控制接口和网页 SHALL NOT 直接操作设备。AI 结果 SHALL 仅作为固定执行器验证的结构化主题、安全或只读纠错数据。

#### Scenario: AI 返回建议
- **WHEN** AI 完成主题、安全或纠错分析
- **THEN** 系统 SHALL 将主题或安全结果作为结构化数据交给固定执行器验证，或将纠错结果保存为只读建议
- **AND** AI SHALL NOT 获得设备对象或点击接口

#### Scenario: AI 返回纠错建议
- **WHEN** 独立纠错分析器完成异常证据分析
- **THEN** 系统 SHALL 只保存结构化建议
- **AND** AI SHALL NOT 获得设备对象、点击接口、坐标执行或任务重放能力

### Requirement: 通用流程与设备差异分离
系统 SHALL 以一套通用任务状态机执行所有已支持设备，并只通过设备档案提供分辨率、语义控件和归一化坐标差异。

#### Scenario: 已知设备重新连接
- **WHEN** 已验证设备再次连接
- **THEN** Worker SHALL 读取该设备档案并复用通用流程
- **AND** 系统 SHALL NOT 为该设备复制完整任务脚本

### Requirement: 模块拆分保持兼容
执行核心拆分后 SHALL 保持 Worker CLI、控制 API、SQLite 任务结构、任务终态和失败留证语义兼容。

#### Scenario: 旧入口运行
- **WHEN** 现有启动器或测试导入 `worker.py`、`control_api.py` 的公开入口
- **THEN** 调用 SHALL 继续工作
- **AND** 返回字段与终态含义 SHALL 不变

### Requirement: 平台Adapter不得扩大动作权限
固定执行器 SHALL 是平台 Adapter 的唯一动作调用者；Adapter 和视觉模型不得持有独立设备执行线程或绕过设备锁、动作前闸门与动作后验证。

#### Scenario: 初始化视觉模型返回坐标
- **WHEN** 云端模型返回一个高置信控件候选
- **THEN** 本地固定程序在持有设备锁时完成复验，模型和控制接口本身不得点击设备

### Requirement: 初始化与普通任务共享设备独占
初始化和普通任务 MUST 使用同一设备独占机制，同一设备任一时刻最多存在一个动作执行者。

#### Scenario: 设备正在运行普通任务
- **WHEN** 用户尝试启动该设备初始化
- **THEN** 系统拒绝开始并显示设备被占用，不中断或接管现有任务
