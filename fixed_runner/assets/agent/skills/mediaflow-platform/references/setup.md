# 标准虚拟机配置引导

## 场景与目标

用户已经安装 MediaFlow Windows 软件，从首页复制或下载本 Skill 交给外部 Agent。平台、运行环境、控制组件及 Skill 随软件提供，开发版共用这套 Skill。这里不是从零安装 MediaFlow、Python、Node 或 Agent 的教程，也不要求巨大虚拟机镜像。

收到“配置虚拟机、准备环境、安装抖音”等请求，先检查现状，只补本次缺项。推荐开场：“我先检查平台和 MuMu，已有环境直接复用；缺的软件再补齐，登录由你完成。”仅配置不启动视频或互动任务。

## 1. 连接已安装的平台

复制版按材料标题还原目录和脚本；下载版解压进入 mediaflow-platform。Windows PowerShell 执行：

```powershell
./scripts/mediaflow.ps1 platform_status
./scripts/mediaflow.ps1 list_devices
./scripts/mediaflow.ps1 list_tasks
```

脚本保留明确配置的 Python，否则从 `/api/automation` 的 client_runtime 自动取得平台运行环境；不要求系统 PATH 有 Python。不要让用户找开发目录或安装开发工具。只有 Bash 的 Windows 宿主调用系统 PowerShell 运行脚本，不把 .ps1 当 Bash 执行。

平台不可达：请启动已安装的 MediaFlow，通过任务台检查后台，恢复后重试只读查询。旧版缺少运行信息时提示更新平台或保留用户已配置的 Python；不因此重装 MuMu。无 Key 不妨碍检查。

默认 API 为 http://127.0.0.1:48138，管理入口为 http://127.0.0.1:3001；已有配置及平台返回信息优先。connected 只表示接口接入，不等于设备验收通过。检查当前任务及占用，不为准备设备恢复其他等待任务。

## 2. MuMu 与空白实例

- 已安装且有实例：优先复用。用户范围内唯一实例可直接采用；多台且指代不清只问哪台。不因名称、来源不同重建或清空。
- 已安装但未发现：在任务台“设备”检查管理程序路径及连接问题，重新扫描，不立刻重装。
- 未安装：引导从 MuMu 官方网站获取 Windows 安装器。联网时先核对官方来源和当前说明；无法核对时请用户提供官方安装文件，不猜下载地址。目录沿用用户选择。安装器要求虚拟化、系统设置或重启时，说明具体步骤，用户处理后继续。
- 没有实例：在 MuMu 管理器点击“新建设备”，新建空白实例。宿主能操作界面就操作，否则引导用户完成该步后回平台扫描。

**当前 POST /api/virtual-devices 是从模板创建，不是空白创建。** 本路线不用它，也不以重建标准池、克隆或模板导入代替空白创建。缺少接口不是权限不足，明确给出 MuMu 操作位置。已有模板保留。

## 3. 显示标准、连接与看屏

MuMu 实例设置：自定义宽900、高1600、DPI320。已达标不重复改。需停止/重启才能生效时，确认选定实例无任务及人工控制占用，说明影响，只处理它。

启动后核对**实际运行**900×1600、320 DPI、竖屏，不只看配置值。名称、Root、来源、精确版本、自动旋转开关、导航及默认输入法都不是整机门槛；实际横屏时恢复竖屏再检查。

对账入口 `POST /api/virtual-devices/reconcile`，JSON `{}`；可选 mumu_path 是实际找到的 MuMu 管理程序路径。它会尝试连接ADB，不是纯读，只用于用户要求的配置/连接。API 地址沿用现有运行配置。例如：

这些直接维护REST接口不经过automation客户端，不具有其request_id去重保证，也不能凭空给它们加编号参数。保存原返回和操作时间；超时先看设备/初始化状态，不自动重发POST。已注册实例的启停可以走api.md中的虚拟机操作工具链，取得持久化回执。

```powershell
# 使用前一步确认的运行配置；默认安装无需填写路径。
$setupConfigPath = if ($env:MEDIAFLOW_SKILL_CONFIG) { $env:MEDIAFLOW_SKILL_CONFIG } else { './config.json' }
$setupConfig = if (Test-Path -LiteralPath $setupConfigPath) { Get-Content -Raw -LiteralPath $setupConfigPath | ConvertFrom-Json } else { @{} }
$api = if ($env:MEDIAFLOW_API_URL) { $env:MEDIAFLOW_API_URL } elseif ($setupConfig.api_url) { $setupConfig.api_url } else { 'http://127.0.0.1:48138' }
$api = $api.TrimEnd('/') -replace '/api/automation$', ''
Invoke-RestMethod -Method Post -Uri "$api/api/virtual-devices/reconcile" -ContentType 'application/json' -Body '{}'
./scripts/mediaflow.ps1 list_devices
```

核对实例与永久设备 ID 一一对应，ADB 端口变化不另建平台设备。任务台 `/devices` 打开画面，确认截图能刷新；看屏只需 ADB。映射不明先核对对象，不连接另一台冒充成功，不循环启停。

若前面使用了 --config 指定另一个文件，setupConfigPath也沿用那个文件；只接受已经由客户端验证的本机地址，不借此连接其他电脑。

显示及应用验收以设备页后端返回的实际检查结果为准；页面只显示设置值或尚未检查时，记为待验证。截图只能证明画面尺寸，不能单独证明320 DPI。这里不提供不存在的DPI或输入验证API。

## 4. 按需补软件，不做资格闯关

| 项目 | 何时需要 | 处理方式 |
| --- | --- | --- |
| 抖音 | 抖音业务 | 已安装保留；缺少按批准来源处理，管理和看屏仍可用 |
| uiautomator2 控制组件 | 页面读取及固定执行器 | 复用平台准备入口，不另找第三方组件 |
| AdbKeyboard／兼容 FastInputIME | 搜索、中文输入、评论 | 按需准备；不阻断纯浏览，不要求关闭其他输入法 |
| 登录 | 实际抖音业务 | 用户手工登录及处理验证码，不凭初始化记录推断登录 |
| 视觉模型 | 确实依赖模型的步骤 | 平台模型设置处理，不是整机安装前提 |

抖音使用平台已配置的批准 APK/下载源；没有则请用户提供安装文件，通过 MuMu 安装 APK 功能安装。分包必须完整，不偷换来源，不通过复制账号环境解决安装。安装后回读应用状态，传文件成功不等于安装成功。

基础准备入口：`POST /api/devices/{URL编码后的实际ADB地址}/initializations`，JSON `{}`。虚拟机内部检查连接、显示、应用，仅在本次需要准备控制通道时使用，不是取得任务资格的必经步骤。它**不是抖音安装接口，也不是独立中文输入验收接口**。

查询 `GET /api/devices/{地址}/initialization`；继续/取消分别 POST 同路径 `/continue`、`/cancel`。结果不明先查原记录，不重复创建。业务队列暂停不应挡住维护；“停止全部自动操作”则按用户当前目标处理，不能擅自解除。

搜索执行会按需准备输入组件。若用户只想提前验证输入，不启动搜索任务：使用平台实际存在的输入检查入口；当前版本没有独立入口时，请用户在系统设置搜索框输入短中文并读回，再清除测试文字。不要用评论/私信框测试，不把“已启用输入法”当实测通过，不编造自动检查接口。未实测标为“输入待验证”，首页浏览仍可继续。

## 5. 收尾与恢复

每步说明“已有能力、缺项、处理位置、完成后继续什么”。中断后重新读取状态，只做未完成项；不重复创建实例或重装已有软件。

回执列出选定实例、连接、实际显示、画面、抖音及本次所需输入状态，区分已验证、未验证、等待用户、失败。未登录不让整个设备失去管理资格。访客记录只提示可能影响访客结果；三次规则验证是开发工作，不是用户考试。

只要求准备就交付准备结果；明确要求运行才接 [任务流程](workflows.md)，只问缺少参数，默认零互动，不继承旧草稿或恢复历史任务。不把准备完成说成业务成功。
