# execution-architecture Specification

## ADDED Requirements

### Requirement: 人工维护控制会话属于唯一动作平面的显式例外
系统 SHALL 将人工控制实现为持有现有设备独占锁的维护会话，而不是第二套自动执行器；人工会话、普通Worker和初始化固定程序在同一设备上 MUST 互斥。

#### Scenario: 空闲设备建立人工控制
- **WHEN** 服务端确认设备没有运行任务、初始化或其他维护会话
- **THEN** 人工会话取得同一设备独占锁后才允许转发用户发起的点击、滑动、键盘和剪贴板消息

#### Scenario: Worker正在运行
- **WHEN** 用户尝试接管正由Worker操作的设备
- **THEN** 系统拒绝控制会话并提示先安全停止该设备任务，不抢占Worker或直接结束任务

#### Scenario: 人工控制断线
- **WHEN** 会话断线超过规定租约时间
- **THEN** 系统撤销令牌、释放设备独占锁且不重放最后一条或任何结果未知的人工输入

### Requirement: 虚拟机生命周期操作与设备动作分层互斥
Provider生命周期操作 SHALL 在启动前检查普通任务、初始化和人工控制占用；设备动作层 SHALL NOT 直接调用创建、删除、导入或导出等Provider命令。

#### Scenario: 运行中设备请求删除
- **WHEN** 设备锁被任务或人工会话持有且收到删除请求
- **THEN** 生命周期协调器拒绝操作并保留现有设备动作，不通过结束进程绕过互斥

#### Scenario: 画面只读观察
- **WHEN** Worker运行时存在只读画面会话
- **THEN** 只读会话不取得动作锁且不阻断Worker，任何输入消息均被拒绝

### Requirement: 画面网关故障不得扩大到任务执行平面
设备画面网关 SHALL 作为后台宿主的独立受监管角色运行，重启或失败 MUST NOT 重启设备Worker、修改普通任务状态或创建新设备动作。

#### Scenario: 画面网关退出
- **WHEN** 后台宿主发现画面进程异常退出
- **THEN** 系统使画面会话失效并有界重启该角色，API、队列和Worker继续保持各自真实状态
