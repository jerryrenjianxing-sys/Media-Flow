## Why
首页角标3在原始截图清晰，但当前本地程序只用UI树取数字；无执行结果的新版任务被页面回退为旧详细巡检并虚报失败。需要以真实样本补齐识别并修正展示。
## What Changes
- 固定角标字形识别和数量状态、裁剪证据。
- 统一消息巡检入口与基于任务参数/结果的状态展示，全站现有风格纠错。
- 停止本次批次并保留证据复盘，技能同步，dev40本机更新。
- 修正评论逐视频证据覆盖和请求诊断丢失，保留实际停止回执与等待索引一致性；不改变请求次数、期限或设备动作策略。
## Capabilities
### New Capabilities
- message-inspection-presentation: 新旧隔离、等待无结果与数量来源展示。
- batch-evidence-integrity: 评论证据与模型诊断按实际视频归属，停止回执和等待状态一致。
### Modified Capabilities
- engagement-inspection: 首页本地数字识别及仅历史开放旧版。
## Impact
固定执行器、业务API、控制台、Skill。兼容旧字段/旧数据，不启动真实任务；回退程序不覆盖数据库。
