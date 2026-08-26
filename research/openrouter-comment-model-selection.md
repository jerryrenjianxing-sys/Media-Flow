# OpenRouter 单图社媒截图判断与中文短评论模型选型

> 核验日期：2026-08-20（Asia/Shanghai）  
> 资料范围：仅使用 OpenRouter 官方模型页和官方文档。价格、端点可用率和促销折扣均是动态数据，生产配置前应再次核验。

## 结论

**默认模型：`google/gemini-3.1-flash-lite`**  
**跨厂商故障备份：`openai/gpt-4.1-nano`**

理由：当前任务是“一张手机截图 → 安全/内容分类 → 生成一句简体中文候选 → 返回很短的 JSON”，不是复杂的手机控制或长链推理。Gemini 3.1 Flash Lite 的官方定位正是低延迟、高吞吐、简单数据提取；它支持图片输入和 JSON Schema 结构化输出，当前价格也低。GPT-4.1 Nano 的官方定位明确包含分类任务，同样支持图片和 JSON Schema，而且运行在不同于 Google 的模型/供应商体系中，适合在 Google 路线整体异常时容灾。

**不建议把 `google/gemini-3.7-flash` 作为默认模型。** 它面向复杂多步推理和 agentic 工作流，本任务用不到这部分能力；其当前页面价格带有 75% 折扣标记，不能把该促销价格当成长期预算基线。可把它保留为“人工复核仍无法判定的疑难截图”的升级模型，而不是每张截图都调用。

## 核心对比

| 模型（精确 ID） | 图片输入 | JSON Schema 结构化输出 | 当前输入 / 输出价（每 1M token） | OpenRouter 路由与当前可用性 | 适合程度 |
|---|---|---|---:|---|---|
| `google/gemini-3.1-flash-lite` | 是 | 是 | $0.25 / $1.50 | 2 个供应商；3 日可用性 99.97%，24 小时路由后 99.98% | **默认首选** |
| `openai/gpt-4.1-nano` | 是 | 是 | $0.10 / $0.40 | 2 个供应商；3 日可用性 99.94%，24 小时路由后 99.89% | **跨厂商故障备份** |
| `google/gemini-3.7-flash` | 是 | 是 | 当前促销 $0.375 / $1.875 | 2 个供应商；3 日可用性 99.88%，24 小时路由后 99.90% | 复杂疑难样本升级，不作默认 |
| `google/gemini-3.5-flash-lite` | 是 | 是 | $0.30 / $2.50 | 2 个供应商；3 日可用性 99.98%，24 小时路由后 99.99% | 稳定但性价比不如 3.1 Lite |
| `qwen/qwen3-vl-30b-a3b-instruct` | 是 | 是 | $0.13 / $0.52 | 4 个供应商；3 日可用性 99.84%，24 小时路由后 99.83% | 中文质量 A/B 候选，不先替代默认 |
| `mistralai/mistral-small-3.2-24b-instruct` | 是 | 是 | $0.075 / $0.20 | 3 个供应商；3 日可用性 99.71%，24 小时路由后 99.55% | 低成本基准，中文自然度需实测 |

上述模型页证据：

- Gemini 3.1 Flash Lite：OpenRouter 官方页给出精确 ID、图片输入、`response_format` JSON Schema、$0.25/$1.50、2 个供应商和自动故障转移；页面还把它定位为低延迟、高吞吐及简单数据提取。当前页面显示 3 日可用性 99.97%。[官方模型页](https://openrouter.ai/google/gemini-3.1-flash-lite)
- GPT-4.1 Nano：官方页明确称其适合分类，支持图片与 JSON Schema，价格 $0.10/$0.40，OpenAI/Azure 路由；当前 3 日可用性 99.94%。[官方模型页](https://openrouter.ai/openai/gpt-4.1-nano)
- Gemini 3.7 Flash：官方页将其定位为复杂多步推理与快速 agentic 工作流，支持图片与结构化输出；页面当前显示 75% off、$0.375/$1.875，并显示 3 日可用性 99.88%。[官方模型页](https://openrouter.ai/google/gemini-3.7-flash)
- Gemini 3.5 Flash Lite：支持图片与 JSON Schema，$0.30/$2.50，2 个 Google 端点体系；当前 3 日可用性 99.98%。[官方模型页](https://openrouter.ai/google/gemini-3.5-flash-lite)
- Qwen3 VL 30B A3B Instruct：支持图片与 JSON Schema，$0.13/$0.52，4 个供应商自动故障转移；当前 3 日可用性 99.84%。[官方模型页](https://openrouter.ai/qwen/qwen3-vl-30b-a3b-instruct)
- Mistral Small 3.2 24B：支持图片与结构化输出，$0.075/$0.20，3 个供应商；当前 3 日可用性 99.71%。[官方模型页](https://openrouter.ai/mistralai/mistral-small-3.2-24b-instruct-2506)

## 为什么 3.1 Flash Lite 胜过 3.7 Flash

1. **任务匹配更准。** 3.1 Lite 的官方说明直接包含“simple data extraction”；3.7 Flash 的卖点是复杂、多步推理。当前流程已有本地动作前安全闸门，云模型只需要理解一张截图并返回极短决定，不应为未使用的推理能力持续付费。
2. **当前单价更低。** 以页面现价计算，3.7 的输入价高 50%，输出价高 25%；而其价格有促销标记，未来价格风险反而更大。
3. **当前路由可用性不差。** 3.1 Lite 当前 3 日/24 小时路由后数据均略高于 3.7。这里不是模型质量基准，只能作为此刻的服务可靠性快照。
4. **输出很短。** 一句中文评论和几个判定字段不需要 3.7 的复杂推理预算。只有在后续标注集证明 3.1 Lite 对困难画面误判明显时，才值得升级。

## 为什么备份选 GPT-4.1 Nano

1. **与任务强匹配：** OpenRouter 官方模型页明确写明适合 classification；图片和严格 JSON 都支持。
2. **跨厂商容灾：** 默认是 Google 模型，备份改用 OpenAI/Azure，能降低“同一 Google 模型家族或上游同时异常”造成的相关性风险。单纯把 Gemini 3.5/2.5 作为备份，仍在 Google 供应商体系内。
3. **成本低：** $0.10/$0.40，作为偶发备份不会增加明显成本。
4. **当前可靠性较好：** 官方页面当前显示 3 日可用性 99.94%。

限制：官方页面没有对“简体中文社媒评论自然度”作专门质量承诺。因此它是**可靠性备份**，不是未经实测就认定的中文文风冠军。若后续 A/B 标注显示中文自然度不足，可把 `qwen/qwen3-vl-30b-a3b-instruct` 加入离线评测，但不建议在没有样本评估前直接替换主模型。

## 推荐请求约束

生产请求不应只靠提示词要求“返回 JSON”，而应使用 JSON Schema：

```json
{
  "models": [
    "google/gemini-3.1-flash-lite",
    "openai/gpt-4.1-nano"
  ],
  "provider": {
    "allow_fallbacks": true,
    "require_parameters": true,
    "data_collection": "deny"
  },
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "comment_decision",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "decision": { "type": "string", "enum": ["comment", "skip"] },
          "comment": { "type": "string", "maxLength": 40 },
          "reason_code": { "type": "string" }
        },
        "required": ["decision", "comment", "reason_code"],
        "additionalProperties": false
      }
    }
  }
}
```

OpenRouter 官方结构化输出文档说明：结构化输出支持是**按供应商端点**决定的，不只是按模型决定；应使用 `response_format.type=json_schema`、`strict:true`，并设置 `provider.require_parameters=true`，避免路由到不支持这些参数的端点。[结构化输出文档](https://openrouter.ai/docs/guides/features/structured-outputs)

OpenRouter 官方文档还建议多模态消息中先发送文本提示、再发送图片；本地私有截图应使用 base64 data URL。[图片输入文档](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding)

官方模型回退文档说明，`models` 按顺序尝试；主模型因限流、停机或内容审核拒绝等错误失败时会尝试下一个模型，实际费用按最终成功使用的模型计算。[模型回退文档](https://openrouter.ai/docs/guides/routing/model-fallbacks)

## 隐私与审计建议

- OpenRouter 官方称，默认不存储提示和响应正文，除非用户主动开启私有输入/输出日志或允许 OpenRouter 使用输入/输出；但仍会保存 token、时延等请求元数据。[数据收集文档](https://openrouter.ai/docs/guides/privacy/data-collection)
- 模型供应商自身的数据保留规则不同。对于内部风控截图，请设置 `provider.data_collection="deny"`；如果账户和目标端点支持，还可设置 `provider.zdr=true`。开启 ZDR 可能缩小可用端点池，应在真实环境验证容灾效果。[供应商日志文档](https://openrouter.ai/docs/guides/privacy/provider-logging/) · [ZDR 文档](https://openrouter.ai/docs/guides/features/zdr)
- 模型响应中应记录实际使用的 `model` 字段，并建议开启路由元数据以便审计具体供应商、重试与成本，但不要在常规日志里写入截图正文、完整模型原始回复或 API Key。[路由元数据文档](https://openrouter.ai/docs/guides/features/router-metadata)

## 上线前最小验证

官方模型页只能证明能力、价格和当前服务数据，**不能证明该模型对本项目截图的实际中文质量**。正式扩大运行前，应使用同一批已脱敏、已人工标注的内部截图做离线盲测：

1. 至少覆盖正常内容、广告、未成年人、敏感信息、评论区遮挡/加载失败等样本。
2. 对 3.1 Lite、GPT-4.1 Nano 和一个中文候选（Qwen3 VL 30B）使用完全相同的 JSON Schema 与提示词。
3. 先看安全闸门的漏拦截率，再看 JSON 合规率、中文自然度、P95 延迟和单次成本；安全漏拦截应是一票否决项。
4. 默认仅生成候选，不直接发布；只有本地安全闸门、云端 `decision=comment`、结构化响应校验、评论长度/字符白名单全部通过时，才允许进入后续动作验证。

## 最终建议

- 立即使用：`google/gemini-3.1-flash-lite`
- 错误级自动回退：`openai/gpt-4.1-nano`
- 疑难截图人工/二阶段升级：`google/gemini-3.7-flash`（不要对所有截图默认调用）
- 中文风格离线 A/B：`qwen/qwen3-vl-30b-a3b-instruct`

这个组合比“所有请求都上 3.7 Flash”更符合当前固定程序的降本、稳定和审计目标。
