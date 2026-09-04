## Why

现有互动巡检 v1 早于 Mobile Harness 真实校准，无法稳定覆盖抖音 33.0.0 的“互动消息 → 全部消息”聚合页、拆分后的评论/弹幕入口，也没有访客变化去重和工作台提醒闭环。需要以抖音5号为单设备首发，把 MBH 观察结果冻结成版本化固定流程。

## What Changes

- 新增任务载荷 `inspection_workflow_version`，仅已校准的抖音5号冻结为 v2，其余设备默认 v1。
- v2 固定检查私信入口角标、赞与收藏、收到的评论、收到的弹幕和主页访客；页面进入及每次滑动后重新截图观察，未知页面失败关闭。
- 新增本地访客基线和互动提醒表，只保存计数、状态及脱敏哈希，不保存姓名、预览、访客身份或成功运行原图。
- 新增提醒读取/确认接口、独立互动消息页面和全局滚动横幅；确认失败时横幅不得假装关闭。
- 保持每5轮插入一次巡检、同设备唯一执行者和所有对外互动为0。

## Capabilities

### New Capabilities

- `interaction-alerts`: 本地聚合提醒、去重、查看状态、横幅与历史页面。

### Modified Capabilities

- `engagement-inspection`: 增加 MBH-first v2 页面路径、评论/弹幕、访客本地基线和临时证据规则。
- `task-lifecycle`: 提交时按设备校准档案冻结 v1/v2，不在领取时重新选择。
- `runtime-safety-baseline`: v2 对应用版本、显示签名和未知控件失败关闭。

## Impact

- `fixed_runner` 增加 v2 分支、两张兼容 SQLite 表、提醒接口和设备校准档案字段。
- `control_console` 增加 `/interactions` 和全局提醒横幅。
- OpenSpec 与脱敏夹具记录三遍 MBH 校准结果；真实截图和 UI 树不进入长期材料。
