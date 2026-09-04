## ADDED Requirements

### Requirement: 自动发现本机 Root 虚拟机
系统 SHALL 在用户开启自动接管后周期性读取 MuMu 已启动实例与实时 ADB 状态，并且只把本机回环 ADB、Root、由模拟器管理器或标准模拟器序列号证明的设备作为候选。

#### Scenario: 新 MuMu 实例启动
- **WHEN** MuMuManager 返回一个已启动、Root 且尚未登记 Android 身份的本机实例
- **THEN** 系统连接其独立 ADB 端口、加入设备池并创建零写入初始化

#### Scenario: Root 真机在线
- **WHEN** 在线设备虽然为 Root 但没有模拟器管理器或标准模拟器序列号证据
- **THEN** 系统不得自动接管，设备继续使用现有人工流程

### Requirement: ADB 别名按设备身份去重
系统 MUST 以可读取的稳定 Android 身份辅助去重同一虚拟机的多个 ADB 地址，不能仅按端口创建独立设备。

#### Scenario: 同一实例暴露两个端口
- **WHEN** 新发现端口的 Android 身份与已配置在线设备一致
- **THEN** 系统记录别名并跳过初始化、Worker 和任务提交

