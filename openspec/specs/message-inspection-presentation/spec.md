# message-inspection-presentation Specification

## Purpose
统一电脑端消息巡检的模式、执行状态、数量和证据展示，避免未执行记录回退成旧巡检或将读取失败解释为无消息，同时保留历史兼容能力。

## Requirements
### Requirement: 新版巡检状态真实展示
控制台 SHALL 将home_badge称为消息巡检，结合冻结任务参数与实际结果判断模式。未执行任务 MUST 只显示排期和未检查，不回退旧分区或伪造失败。模式冲突 MUST 显示诊断。旧详细模式执行入口 SHALL 隐藏，历史只读保留。
#### Scenario: 新版等待任务无结果
- **WHEN** home_badge任务pending且结果为空
- **THEN** 显示消息巡检与尚未检查，不出现私信等旧分类，不显示视频零计数
#### Scenario: 两个版本冲突
- **WHEN** 冻结参数与结果携带不同巡检版本
- **THEN** 显示版本冲突及历史证据，不拼接新旧卡片
#### Scenario: 旧巡检准备等待人工处理
- **WHEN** 保存的inspection_recheck未明确指定home_badge，包括没有mode的旧记录
- **THEN** 设备列表、画面卡与接入弹窗依据只读标记隐藏旧巡检继续入口，保留安全取消、报告和兼容API，普通及home_badge准备仍可继续
### Requirement: 数量与失败语义分开
控制台 SHALL 分别显示纯红点、可靠数字、数字未识别及检查失败，内部原因和暂停能力 SHALL 中文解释。无结果/读取失败 MUST NOT 显示无消息。
#### Scenario: 有数字但未读出
- **WHEN** 回执存在角标但quantity_status为unreadable
- **THEN** 显示有消息及数字尚未识别，提供原图和裁剪证据
