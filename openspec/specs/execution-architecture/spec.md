# execution-architecture Specification

## Purpose
TBD - created by archiving change modularize-execution-core. Update Purpose after archive.

## Requirements

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
