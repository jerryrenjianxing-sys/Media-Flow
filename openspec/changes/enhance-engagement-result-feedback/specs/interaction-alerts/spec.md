## MODIFIED Requirements

### Requirement: 横幅确认形成可见结果闭环
所有控制台页面 SHALL 轮询未查看提醒。确认成功后 MUST 跳转全部历史并高亮刚确认的回执；确认失败时提醒 MUST 保持可见。

#### Scenario: 确认成功后查看结果
- **WHEN** 用户点击横幅且确认接口成功
- **THEN** 页面展示刚确认回执的可读摘要和证据入口，而不是空的未查看列表

### Requirement: 本地提醒提供实际可用信息
提醒 SHALL 返回本地回执引用、来源、实际存在的数量、可读摘要和证据数量。系统 MUST 省略不存在的可选字段，并 MUST NOT 返回绝对文件路径。

#### Scenario: 来源没有数字角标
- **WHEN** 来源只有红点而没有可靠数量
- **THEN** 返回来源与红点结论且省略 `unread_count`

## ADDED Requirements

### Requirement: 无互动巡检保留简明回执
完整巡检未发现新互动时 SHALL 保存一条无互动回执且不得产生提醒横幅。

#### Scenario: 四个分区均完整且无变化
- **WHEN** v2 完成四个分区并安全返回首页
- **THEN** 回执结论为“无新互动”且提醒数量不增加

