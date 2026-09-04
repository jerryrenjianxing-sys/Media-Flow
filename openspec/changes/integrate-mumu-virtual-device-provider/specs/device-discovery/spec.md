# device-discovery Specification

## MODIFIED Requirements

### Requirement: 只读发现与显式托管本机 Root 虚拟机
系统 SHALL 周期性只读读取 MuMu Provider 的全部运行与停止实例；只有 MediaFlow 创建或用户明确接管的实例才进入普通库存和初始化候选。未托管MuMu的回环ADB端口 SHALL NOT 伪装为真机、进入任务台或反向创建普通库存。

#### Scenario: 新的未托管 MuMu 实例启动
- **WHEN** Provider返回一个已启动、Root且尚未由MediaFlow托管的本机实例
- **THEN** 系统只在高级未托管候选中展示；用户明确接管后才分配稳定身份、统一名称并进入零写入初始化

#### Scenario: 停止中的托管MuMu实例
- **WHEN** Provider返回一个已停止且已经托管的实例
- **THEN** 设备页在虚拟设备标签中展示其实例状态和可用生命周期操作，但普通任务选择不得将其视为在线设备

#### Scenario: Root 真机在线
- **WHEN** 在线设备虽然为Root但没有Provider实例或标准模拟器序列号证据
- **THEN** 系统不得自动接管，设备继续使用现有真机人工流程

### Requirement: ADB 别名按设备身份去重
系统 MUST 以MediaFlow永久虚拟设备ID为主键，并使用Provider实例编号与可读取的稳定Android身份辅助去重同一虚拟机的多个ADB地址；ADB端口只能作为运行期映射。

#### Scenario: 同一实例暴露两个端口
- **WHEN** 新发现端口的Provider实例或Android身份与已配置虚拟设备一致
- **THEN** 系统更新或记录ADB别名并跳过重复身份、初始化、Worker和任务提交

#### Scenario: 重启后端口改变
- **WHEN** 同一Provider实例重启并取得不同ADB端口
- **THEN** 系统保持原永久虚拟设备ID，更新当前端点并使历史任务保留当时地址

#### Scenario: MuMu实例由用户手动启动
- **WHEN** 托管实例已经完成Android启动并报告本机ADB端口，但该端口尚未登记到当前ADB服务
- **THEN** 系统主动连接Provider报告的回环端点、读取Android身份并安全恢复映射，不要求用户在MuMu或命令行中手工连接

#### Scenario: MuMu管理命令误报ADB不可连接
- **WHEN** Provider实例状态和本机端口确认Android已运行，但MuMu管理命令没有返回可用ADB地址
- **THEN** 系统使用安装包内ADB直接连接Provider报告的回环端点并复核设备状态；身份不一致时仍失败关闭并等待确认

## ADDED Requirements

### Requirement: 实时库存不得由草稿或历史记录生成
系统 SHALL 只把当前ADB实际返回的设备作为实时设备库存；任务草稿、默认配置、历史任务和本机档案 MUST NOT 反向生成设备卡片。

#### Scenario: 新电脑保留旧草稿但没有设备
- **WHEN** 新电脑没有ADB设备和Provider绑定，但草稿仍引用旧设备ID
- **THEN** 设备库存为零，草稿仅显示失效选择并在提交时阻断

#### Scenario: 旧版开发种子升级
- **WHEN** 保存选择精确匹配旧版五设备开发种子且本机没有对应ADB或虚拟机绑定
- **THEN** 系统幂等清空当前选择，不删除或改写任何历史任务

### Requirement: 设备发现返回Provider与虚拟设备摘要
实时状态 SHALL 区分真机ADB设备、运行中的虚拟设备、停止中的虚拟设备和Provider不可用状态，并 SHALL 向后兼容扩展 `/api/status`。

#### Scenario: Provider正常且部分实例停止
- **WHEN** 状态接口读取到两个运行实例和三个停止实例
- **THEN** 普通在线设备计数只包含两个运行实例，虚拟设备摘要单独返回五个实例及各自状态

#### Scenario: Provider探测失败
- **WHEN** MuMu命令超时或版本不兼容
- **THEN** 系统保留常规ADB真机发现并在虚拟设备摘要中报告Provider错误，不把全部设备伪报为离线
