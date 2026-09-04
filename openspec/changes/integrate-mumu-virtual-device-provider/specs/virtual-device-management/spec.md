# virtual-device-management Specification

## Purpose
让 MediaFlow 在不打开 MuMu 管理大厅的情况下，以稳定身份、可审计操作和有界恢复完整管理经过授权的本机 MuMu 虚拟设备生命周期。

## ADDED Requirements

### Requirement: Provider 能力探测与只读清单
系统 SHALL 通过统一 `VirtualDeviceProvider` 探测 MuMu 安装目录、版本、Android 引擎和可用命令，并 SHALL 在不改变实例状态的情况下列出运行与停止的全部实例。

#### Scenario: 未托管实例隔离发现
- **WHEN** Provider 列出一台尚未由 MediaFlow 创建或明确接管的实例
- **THEN** 系统只把它列在虚拟机管理板块，不写入普通库存、不显示在任务台，也不得自动启动、改名、连接ADB或绑定档案
- **AND** 只有用户对该实例发起明确操作时才允许启动、停止或确认删除，操作不得使其取得任务资格

#### Scenario: 托管的已停止实例保留在库存
- **WHEN** MediaFlow 创建或用户明确接管的实例停止且没有 ADB 连接
- **THEN** 系统保留其本机永久记录和启动入口，且不得因为实例停止而从设备页删除

#### Scenario: Provider 暂时不可用
- **WHEN** 已记录实例存在但 MuMu 管理命令暂时不可用
- **THEN** 系统保留实例、档案和历史证据并显示引擎不可用，不得把最后ADB地址当成当前在线设备

#### Scenario: 兼容引擎可用
- **WHEN** MuMu 已安装且版本与必需命令均满足兼容清单
- **THEN** Provider 返回安装版本、能力集合和实例清单，并允许后续生命周期操作

#### Scenario: 引擎缺失或不兼容
- **WHEN** MuMu 未安装、版本不在兼容范围或必需命令缺失
- **THEN** 系统明确显示组件缺失或引擎不兼容，阻止创建和变更操作，但对已保存记录保持只读展示

### Requirement: 虚拟设备使用稳定的多层身份
系统 MUST 为每台虚拟机保存 MediaFlow 永久 `virtual_device_id`、MuMu `provider_instance_id`、运行期 `adb_endpoint` 和可读取的 Android 身份；ADB 端口 SHALL NOT 作为虚拟机永久身份。

#### Scenario: ADB 端口在重启后变化
- **WHEN** 已登记实例重启后返回新的 ADB 地址且 Provider 实例与 Android 身份能够匹配
- **THEN** 系统更新运行期 ADB 映射并继续使用原 `virtual_device_id`

#### Scenario: 后续检查失败时保留已验证连接
- **WHEN** ADB和Android身份已经验证，而应用安装、登录、模型或档案复验随后进入等待或失败
- **THEN** 系统保留当前ADB连接和已成功的就绪步骤，只把受影响的后续能力标记为暂不可用

#### Scenario: 从历史地址安全重绑
- **WHEN** 当前地址为空但最后地址或Provider动态地址正在ADB在线清单中
- **THEN** 系统读取Android身份并在唯一匹配后恢复当前地址；身份不同则进入待确认且不得继承旧档案

#### Scenario: 身份匹配存在歧义
- **WHEN** Provider 实例、Android 身份或新增端口无法唯一对应既有虚拟设备
- **THEN** 系统将候选标记为 `degraded` 并等待确认，不创建重复永久身份或自动绑定

### Requirement: 生命周期操作独立排队并持久化
系统 SHALL 将创建、配置、启动、停止、重启、克隆、备份、恢复、显示、隐藏和删除保存为独立虚拟机操作；操作状态 MUST 为 `queued`、`running`、`waiting_user`、`completed`、`failed` 或 `cancelled` 之一。

#### Scenario: 同一实例已有生命周期操作
- **WHEN** 同一虚拟设备存在未终态生命周期操作且收到新的冲突操作
- **THEN** 系统拒绝新操作并返回当前操作及阶段，不并发调用 MuMu

#### Scenario: MediaFlow 在操作期间重启
- **WHEN** 后台恢复时发现数据库记录为非终态
- **THEN** 系统先读取 MuMu 真实实例状态进行对账，再决定完成、失败或等待用户，不按旧进度盲目重放命令

#### Scenario: 页面显示启动忙碌状态
- **WHEN** 实例状态为 `running` 或 `adb_ready` 但不存在 `queued` 或 `running` 生命周期操作
- **THEN** 页面不得显示永久“启动中”；ADB 不可用时必须显示可重试的连接错误

#### Scenario: 已运行实例请求接入
- **WHEN** 用户对 MuMu 已运行但 ADB 尚未就绪的托管实例点击重试
- **THEN** 系统跳过重复启动命令并直接重新解析 ADB、核对身份和收口操作

### Requirement: 创建流程应用版本化配置配方
系统 SHALL 创建空白Android 15实例，通过创建前后实例清单差异唯一确认新实例，再按版本化配置配方设置并回读名称、CPU、内存、分辨率、DPI、帧率、Root、旋转和音量配置；本机模板 MAY 作为可选加速但不得跨电脑分发。

#### Scenario: 创建与配置成功
- **WHEN** 配置配方版本有效，且唯一新实例的全部关键设置回读一致
- **THEN** 系统登记新的永久虚拟设备身份并允许启动该实例
- **AND** 服务端按本机单调序号命名为 `MediaFlow虚拟机1`、`MediaFlow虚拟机2` 等，已删除序号不得复用

#### Scenario: 创建结果不明确
- **WHEN** 克隆命令结束后无法唯一识别新实例或关键设置回读不一致
- **THEN** 操作以未知或失败结果留证并列出候选，系统不得再次自动克隆或启动半配置实例

### Requirement: 可执行虚拟机锁定标准显示环境
系统 SHALL 将 Android 15、900×1600、320 DPI、竖屏、标准导航方式和 AdbKeyboard 作为任务资格的不可编辑标准；CPU、内存、帧率和静音 MAY 在宿主能力范围内调整。页面 SHALL 隐藏标准显示项，服务端 MUST 拒绝绕过页面修改这些字段。

#### Scenario: 启动后配置回读一致
- **WHEN** 托管实例启动后回读的标准字段与版本化配方一致，且登记身份、档案复验和零写入自检均有效
- **THEN** 系统将其分类为 `managed_standard` 并允许作为任务候选

#### Scenario: 外部修改显示配置
- **WHEN** 已托管实例的分辨率、DPI、方向、导航方式、输入方式或Android镜像发生漂移
- **THEN** 系统将其分类为 `managed_nonstandard`，立即移出任务候选并提供停止后修复入口

#### Scenario: 仅修改名称尝试接入
- **WHEN** 未托管实例被外部改名为 `MediaFlow虚拟机N` 但没有MediaFlow永久UUID、Provider安装身份、实例登记和Android身份核对
- **THEN** 系统仍将其分类为 `unmanaged`，不得连接ADB、继承档案或执行任务

### Requirement: 标准虚拟机池支持补齐与确认重建
系统 SHALL 接受目标数量并提供 `supplement` 和 `reset` 两种持久化标准池操作。`supplement` SHALL 只创建相对当前合格标准实例缺少的数量；`reset` SHALL 在任何创建前逐台停止并删除本机全部MuMu实例，且必须由用户确认不可恢复范围。

#### Scenario: 补齐标准池
- **WHEN** 用户要求目标3台，当前已有2台符合标准且可复验的MediaFlow虚拟机
- **THEN** 系统仅创建1台，不修改、删除或重命名其他实例

#### Scenario: 标准池数量已满足
- **WHEN** 当前合格标准实例数量等于或超过目标数量
- **THEN** 系统完成补齐操作且不创建新实例，多余设备继续由用户手工管理

#### Scenario: 重建标准池确认
- **WHEN** 用户选择重建目标N台
- **THEN** 页面列出将删除的全部MuMu实例、名称和运行状态，并要求完整输入 `删除全部并重建N台` 后才允许提交
- **AND** 页面明确说明本操作不创建备份，账号、应用和数据不可恢复

#### Scenario: 重建删除结果不明确
- **WHEN** 任一实例被占用，或停止、删除与重新扫描的结果不明确
- **THEN** 系统立即停止标准池操作、不重放未知删除且不开始创建任何新实例

### Requirement: 创建后衔接应用安装、登录、初始化和自检
系统 SHALL 在实例启动、Android完成引导、ADB可用且Root验证通过后隐藏MuMu原生窗口；抖音缺失时只使用公司批准的APK或固定来源自动安装，否则进入人工安装；未登录时 SHALL 进入 `waiting_login`，用户继续后才可衔接独立初始化和3条零写入自检。

#### Scenario: 模板实例尚未登录
- **WHEN** 新实例启动后识别到抖音登录或身份验证页面
- **THEN** 系统显示“等待你登录”并开放受锁人工接管，不自动填写凭据或绕过验证

#### Scenario: 初始化和自检完成
- **WHEN** 用户完成登录、初始化全部通过且3条自检没有写入或页面错误
- **THEN** 虚拟设备进入 `ready` 并允许加入任务草稿，但不得自动提交正式任务

### Requirement: 停止与重启遵守设备占用
系统 MUST 在停止或重启前确认设备没有运行任务、初始化或人工控制会话，并 SHALL 使用MuMu正常关机与启动命令。

#### Scenario: 设备正被使用
- **WHEN** 用户请求停止或重启但设备锁由Worker、初始化或人工控制会话持有
- **THEN** 系统拒绝操作并说明占用来源，不强制结束设备进程

#### Scenario: 空闲设备重启
- **WHEN** 空闲设备完成重启且ADB地址变化
- **THEN** 系统重新解析并验证ADB与Android身份，更新映射后恢复原永久设备记录

#### Scenario: 从MediaFlow启动已停止实例
- **WHEN** 用户在设备页或任务台点击一台空闲停止实例的启动按钮
- **THEN** 系统只启动目标MuMu实例，等待Android与ADB就绪并核对身份，然后在MediaFlow内打开画面，不得打开MuMu管理大厅或自动提交任务

#### Scenario: 实例编号被其他Android身份复用
- **WHEN** 已记录的Provider实例编号启动后返回与原记录不同的Android身份
- **THEN** 系统进入身份待确认状态、禁用旧档案且不得加入任务草稿

### Requirement: 克隆、备份和恢复保留来源边界
系统 SHALL 只从停止实例克隆或导出备份，备份 SHALL 保存到MediaFlow用户数据目录并记录SHA-256与元数据；恢复 MUST 导入为新实例而不是覆盖原实例。

#### Scenario: 创建备份
- **WHEN** 用户备份一个停止且无占用的实例并且磁盘空间充足
- **THEN** 系统导出备份、计算校验值并把路径与元数据写入操作记录

#### Scenario: 从备份恢复
- **WHEN** 用户选择有效备份进行恢复
- **THEN** 系统导入新实例、生成新的 `virtual_device_id` 并要求重新核对登录与初始化状态，原实例保持不变

### Requirement: 删除为受确认的可恢复操作
系统 MUST 要求用户输入实例名称确认删除，并确认无任务、初始化或控制会话；默认先备份，删除后 SHALL 将虚拟设备标记为 `retired` 并保留历史任务和证据。

标准池 `reset` 是唯一例外：它在显示完整删除清单并通过专用确认短语后按用户选择不备份，但仍 MUST 保留MediaFlow历史任务和操作证据。

#### Scenario: 确认名称不匹配
- **WHEN** 删除请求中的确认名称与当前实例名称不完全一致
- **THEN** 系统拒绝删除且不调用MuMu命令

#### Scenario: 备份后删除成功
- **WHEN** 默认备份校验成功且MuMu确认实例已删除
- **THEN** 系统将记录标记为 `retired`，历史任务、截图和操作审计仍可查看

### Requirement: 批量操作逐台收口
系统 SHALL 对批量启动或停止逐台建立独立操作与结果，不因一台失败取消其他实例。

#### Scenario: 批量启动部分失败
- **WHEN** 五台实例中一台启动超时而其他实例成功
- **THEN** 四台分别进入各自真实状态，失败实例留存进程、ADB和MuMu证据，批次显示部分完成

### Requirement: 管理功能不提供设备身份伪装
系统 SHALL NOT 暴露IMEI、设备指纹或批量身份修改等与正常生命周期管理无关的设置。

#### Scenario: 读取可配置项
- **WHEN** 控制台展示或接口返回虚拟机设置
- **THEN** 可编辑项只包含CPU、内存、帧率和静音；标准显示、Root、旋转、导航和输入方式仅可查看且不得修改
