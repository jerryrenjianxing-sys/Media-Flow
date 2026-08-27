# Design

## Architecture

主题复核使用独立 `TopicReviewStore` 写入同一个 SQLite 数据库的新表。候选标签和模型输出作为不可覆盖的样本来源字段，人工标签单独保存。API 将数据库记录转换为现有 `evaluate_topic_samples` 输入，避免重复实现准确率口径。

证据治理由独立只读库存模块扫描白名单目录。策略只记录保留天数且永久标记 `auto_delete=false`；备份使用 SQLite backup API 写入本地 runtime/backups，并生成 JSON 清单。

## Safety Boundaries

- 图片读取只能解析到明确白名单目录中的文件。
- 没有删除端点、清理计划或自动覆盖备份。
- 主题复核和证据治理模块不得导入设备执行模块。
- 页面不提供“AI 自动确认”按钮。

## Rollback

回滚代码不会删除新增表或备份文件；旧控制台和执行器可继续使用。新增 SQLite 表对既有任务表无侵入，运行数据保持不变。

## Runtime Protection

所有库存扫描为只读；备份先建立新文件再返回结果。数据库任务状态、设备状态和现有证据文件均不会被修改。
