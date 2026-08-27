# RiskFlow Domain Context

## Topic Specification（主题规格）

一个可版本化的业务判断边界，包含允许类别、纳入线索、相邻类别、排除线索和最低证据。它不是关键词列表，也不是互动概率配置。

## Eligible Category（允许类别）

可以被判为 `exact` 的明确语义类别。主要画面必须直接属于其中至少一类。

## Inclusion Cue（纳入线索）

帮助模型识别允许类别的关键词、物体、流程、标题或事件示例。线索只用于理解，不能单独决定命中。

## Exclusion Cue（排除线索）

即使出现部分目标词也不应判为命中的场景。明确排除与安全阻断优先于纳入线索。

## Relevance（相关性）

- `exact`：主体与证据直接满足允许类别。
- `adjacent`：同一宽泛领域，但没有直接满足允许类别。
- `unrelated`：主体明确无关。
- `uncertain`：当前画面证据不足。

## Reference Label（参考标签）

经人工确认、可进入准确率分母的样本标签。

## Candidate Label（候选标签）

由 Codex、MBH 或其他模型提出但尚未人工确认的标签，只能用于待复核和发现边界问题。

## Recovery Suggestion（纠错建议）

AI 根据截图提出的页面类型、低风险候选区域和理由。它本身不产生设备动作。

## Fixed Recovery Rule（固定纠错规则）

经重复观察、设备范围验证和回归测试后写入固定执行器的确定性恢复规则。
