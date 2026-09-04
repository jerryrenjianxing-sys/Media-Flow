## ADDED Requirements

### Requirement: 新设备验证前不复用坐标兜底
系统 SHALL 为新发现的稳定设备 ID 保存只读探查事实，并在 Mobile Harness 与 uiautomator2 截图/UI 树前检通过前保持 `verified=false` 和空坐标兜底。

#### Scenario: 新华为首次接入
- **WHEN** `8KE5T19514001258` 以 `device` 状态接入且尚无档案
- **THEN** 系统保存实际型号、显示特征和控制模式，但不得复制其他华为机型的 `home_fallback`

#### Scenario: 只读前检全部通过
- **WHEN** Mobile Harness 和 uiautomator2 都能读取该设备截图与 UI 树，且显示签名与档案一致
- **THEN** 系统可以标记档案已验证，同时继续保持未单独校准的坐标兜底为空
