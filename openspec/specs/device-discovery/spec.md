# device-discovery Specification

## Purpose
让 RiskFlow 控制台以本机实时 ADB 状态发现和选择设备，避免离线或未授权设备进入新任务配置。

## Requirements

### Requirement: 主动检索可用设备
控制台 SHALL 提供主动检索设备的入口，并从实时状态接口取得本机已发现的全部 ADB 设备。

#### Scenario: 检索到可用设备
- **WHEN** 用户点击检索且状态接口返回一个或多个状态为 `device` 的设备
- **THEN** 控制台更新设备列表并只勾选这些可用设备，同时显示检索到的可用数量

#### Scenario: 没有可用设备
- **WHEN** 用户点击检索但没有设备处于 `device` 状态
- **THEN** 控制台清空当前设备选择、提示未检索到可用设备，并禁止保存或提交任务

### Requirement: 不可用设备不得被选择
控制台 MUST 展示已发现但状态不符合执行条件的设备及其状态，同时不得允许用户勾选这些设备。

#### Scenario: 设备未授权或离线
- **WHEN** 设备状态为 `unauthorized`、`offline`、`unknown` 或其他非 `device` 值
- **THEN** 该设备不勾选、复选框禁用，并显示对应状态说明

#### Scenario: 常规状态轮询
- **WHEN** 控制台后台刷新设备状态但用户没有主动点击检索
- **THEN** 系统只更新显示状态，不重建用户当前对可用设备的手工选择
