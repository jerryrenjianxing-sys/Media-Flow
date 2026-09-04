# model-connection Specification

## ADDED Requirements

### Requirement: OpenRouter Key使用候选验证和旧值保护
系统 MUST 校验Key格式、加密候选并完成同用户解密回读，再通过OpenRouter当前Key接口执行不生成内容的鉴权。鉴权成功后 SHALL 原子替换正式Key；401或403 MUST 保留旧Key；网络错误或429 SHALL 将候选标记为待验证且不得覆盖已有可用Key。

#### Scenario: 有效Key首次保存
- **WHEN** 候选Key可加密回读且当前Key接口返回成功
- **THEN** 页面就地显示鉴权成功、验证时间和“待测试当前模型”，接口不返回明文Key

#### Scenario: 无效Key替换已有配置
- **WHEN** 当前已有可用Key且候选鉴权返回401或403
- **THEN** 系统拒绝候选、保留原正式Key并在模型卡片内显示Key无效

#### Scenario: OpenRouter暂时不可达
- **WHEN** 候选本机回读成功但鉴权超时或返回429
- **THEN** 系统保留候选供重新验证，不覆盖已有正式Key

### Requirement: 当前模型实测需要明确用户动作
系统 SHALL 提供“测试当前模型”按钮并说明会产生极小额调用；测试结果 MUST 保存模型ID、时间、耗时和成功或失败状态。

#### Scenario: 模型必需任务尚未实测
- **WHEN** 用户预览搜索、主题筛选或混合内容任务，但当前Key和当前模型没有完成真实测试
- **THEN** 启动摘要显示阻断；不需要模型的纯观察任务仍可运行
