# runtime-safety-baseline Specification

## Purpose
固化 RiskFlow 当前固定执行器的正式运行架构与安全边界，使调试工具、AI 判断和高频设备控制的职责不会在后续开发中被混用。

## Requirements

### Requirement: 正式运行链路
RiskFlow 的正式运行链路 SHALL 为控制台、本地控制接口、SQLite 任务队列、每设备独立 Worker、uiautomator2 固定程序和 Android 设备；MBH 与 Codex 只承担探索、调试和新异常分析。

#### Scenario: 日常批量执行
- **WHEN** 用户从控制台提交已支持的自动化任务
- **THEN** 任务由固定 Worker 和 uiautomator2 程序执行，不要求 MBH 或 Codex 常驻参与

#### Scenario: 遇到未知页面
- **WHEN** 固定程序遇到尚未支持的页面或恢复失败
- **THEN** 当前任务安全失败并保留证据，后续由 MBH/Codex 独立分析后把稳定处理方法写回固定程序

### Requirement: 设备独占
系统 MUST 保证同一时间每台设备最多由一个执行者发送点击、滑动或输入动作。

#### Scenario: 同一设备已有 Worker
- **WHEN** 另一个执行者尝试领取或控制已被占用的设备
- **THEN** 系统拒绝并发控制，直至原执行者完成或锁被安全回收

### Requirement: 状态改变失败不自动重跑
可能已改变账号或页面状态的任务在执行中失败后 MUST 标记失败并保留证据，不得自动从头重跑；尚未领取且因设备离线无法开始的任务可以保持等待。

#### Scenario: 动作后连接中断
- **WHEN** 点赞、收藏、评论或其他状态改变动作后连接中断且结果无法确认
- **THEN** 任务进入失败终态，不自动再次执行该动作

#### Scenario: 领取前设备离线
- **WHEN** Worker 在领取任务前发现设备离线
- **THEN** 任务保持等待，Worker 先重连设备而不制造失败动作

### Requirement: AI 不直接控制设备
AI 模型 SHALL 只返回主题判断、安全判断和候选内容；设备动作、动作前闸门和动作后复核 MUST 由本地固定程序执行。

#### Scenario: 生成候选评论
- **WHEN** AI 返回一条评论候选
- **THEN** 固定程序重新执行长度、置信度、商业内容、联系方式和推广风险检查，AI 响应本身不会触发点击

### Requirement: 默认安全预演
所有可能产生账号状态变化的流程 SHALL 默认启用预览或无写入模式；真实状态改变需要明确授权和对应安全闸门。

#### Scenario: 未获得发送授权
- **WHEN** 用户未明确授权真实点赞、收藏或评论发送
- **THEN** 系统只执行浏览、截图、判断和候选预览，不产生对应账号写入

### Requirement: 服务状态与设备状态分离
控制台和本地接口启动成功 SHALL 只表示服务可访问，不得据此声称 Android 设备在线或任务可执行。

#### Scenario: 服务在线但设备离线
- **WHEN** 页面与接口返回正常但 ADB 设备不可用
- **THEN** 状态界面和验收结论分别报告服务在线与设备离线
