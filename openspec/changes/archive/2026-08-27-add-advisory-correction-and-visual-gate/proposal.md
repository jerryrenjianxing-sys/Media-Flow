# Change: 增加只读纠错分析与视觉安全门

## Why

固定程序已经会记录异常并尝试恢复，但异常记录仍需要人工逐条理解；同时现有主题模型虽然返回 `safe`，缺少一个明确、可审计、先于全部互动执行的视觉安全门。为了提高长时间运行的可观察性与安全边界，需要把两者固化为独立能力。

## What Changes

- 增加独立后台纠错分析器，异步读取异常截图与元数据，只写入结构化建议。
- 纠错分析器不得持有设备对象、不得输出坐标、不得触发点击或重放任务。
- 将同一次视频视觉判断同时用于主题路由与视觉安全门，避免额外模型调用。
- 当视觉安全不明确或模型调用失败时，所有点赞、收藏和评论均按失败关闭处理，并保留原因。
- 在首页和全部记录页显示纠错分析状态与简短建议。

## Non-goals

- 不让 AI 直接控制设备，不自动应用建议，不自动重放失败动作。
- 不改变设备档案、坐标或已存在的任务概率配置。
- 不删除历史任务、异常、截图或数据库记录。
- 本 change 不执行真实点赞、收藏、评论或发布。

## Impact

- Affected specs: `correction-advisory`, `visual-safety-gate`, `execution-architecture`
- Affected code: `fixed_runner/task_store.py`, `fixed_runner/comment_ai.py`, `fixed_runner/execution_tasks.py`, 新增纠错分析后台模块，控制台异常展示
- 模型调用继续使用现有 OpenRouter 配置；异常分析与设备 Worker 进程隔离。
