# Design

## Evidence boundary

- `video`: 当前运行目录中已存在的 `video-*-topic-analysis-before.png`。
- `like`: `events.jsonl` 中存在 `like_state_after` 且 `active=true`，并且同视频的 `video-*-like-after.png` 存在。
- `favorite`: `events.jsonl` 中存在 `favorite_state_after` 且 `active=true`，并且同视频的 `video-*-favorite-after.png` 存在。
- `comment`: 仅使用任务结果 `comment_screenshots` 中登记且位于该运行目录的图片。
- `correction`: 仅使用该任务已登记 incident 的截图，且图片位于该运行目录。

所有详情图片继续通过已有的受限 `/api/task-image` 或 `/api/incident-image` 路由读取，不向网页返回本地绝对路径。

## Interaction

每轮统计区域仍保持原布局。有证据的项目渲染为按钮并显示截图数量；点击后在本轮下方展开该类图库，再次点击或点击“收起”关闭。图库图片延迟加载，避免打开五轮详情时一次读取全部图片。

## Compatibility

没有事件日志或无法验证动作成功的旧任务不会显示该动作截图入口。原始统计和通用详情字段保留，执行流程不变。
