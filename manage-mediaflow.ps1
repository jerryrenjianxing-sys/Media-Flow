param(
    [ValidateSet('Start', 'Stop', 'Restart', 'Status', 'Doctor')][string]$Action = 'Status',
    [switch]$NoBrowser,
    [string]$AppRoot = $PSScriptRoot,
    [string]$LauncherPath = '',
    [string]$InstanceProfilePath = '',
    [string]$UpdateOperationId = '',
    [int]$TimeoutSeconds = 45
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
$phase = 'configuration'
$instance = $null
$diagnosticsHelper = Join-Path $AppRoot 'installer\electron\manager-diagnostics.ps1'
if (-not (Test-Path -LiteralPath $diagnosticsHelper)) {
    [pscustomobject]@{ ok=$false; code='management_helper_missing'; message='后台管理组件缺失，请修复安装。'; phase=$phase } | ConvertTo-Json -Compress
    exit 1
}
. $diagnosticsHelper
try {
$helper = Join-Path $AppRoot 'installer\electron\instance-profile.ps1'
if (-not (Test-Path -LiteralPath $helper)) { throw (New-MediaFlowManagementError 'management_helper_missing' '后台管理组件缺失，请修复安装。' $phase) }
. $helper
$instance = Get-MediaFlowInstance -AppRoot $AppRoot -LauncherPath $LauncherPath -InstanceProfilePath $InstanceProfilePath
Set-MediaFlowInstanceEnvironment $instance
try { $python = Get-MediaFlowPython $instance }
catch { throw (New-MediaFlowManagementError 'runtime_missing' 'MediaFlow 专用运行环境缺失，请修复安装。' 'runtime') }
$hostScript = Join-Path $instance.AppRoot 'fixed_runner\background_host.py'
$control = Join-Path $instance.AppRoot 'fixed_runner\runtime_control.py'
$profileArguments = @(Get-MediaFlowProfileArguments $instance)
function Invoke-MediaFlowJson {
    param([string]$Script, [string[]]$Arguments)
    $oldPreference = $ErrorActionPreference
    try { $ErrorActionPreference = 'Continue'; $result = & $python $Script @Arguments @profileArguments 2>&1; $helperExitCode = $LASTEXITCODE }
    finally { $ErrorActionPreference = $oldPreference }
    $value = $null
    try { $value = ($result -join "`n") | ConvertFrom-Json } catch { }
    if ($helperExitCode -ne 0 -or ($value -and $value.ok -eq $false)) {
        $code = if ($value.reason_code -eq 'legacy_host_gate_unavailable') { 'legacy_host_gate_unavailable' } elseif ($value.state -eq 'stop_refused') { 'stop_refused' } else { 'helper_failed' }
        $message = if ($code -eq 'legacy_host_gate_unavailable') { '旧后台不支持安全停机交接。请通过原版本的正常入口停止后重试。' } elseif ($code -eq 'stop_refused') { '后台仍有任务、维护或人工控制占用，不能安全停止。' } else { '后台管理子程序未能完成，请查看启动日志。' }
        throw (New-MediaFlowManagementError $code $message $phase @{ helper_exit_code=$helperExitCode; bootstrap_log=(Join-Path $instance.DataRoot 'background-startup.log') })
    }
    if (-not $value) { throw (New-MediaFlowManagementError 'helper_invalid_response' '后台管理未返回可验证的状态，请查看日志。' $phase) }
    return $value
}
function Get-MediaFlowStatus {
    $background = Invoke-MediaFlowJson $hostScript @('status')
    $runtime = Invoke-MediaFlowJson $control @('status')
    $processes = @($runtime.processes)
    $remaining = @($processes | Where-Object { $_.running -eq $true -or $_.pid -or $_.identity -notin @('missing','exited','stopped') })
    [pscustomobject]@{ ok=$true; instance_id=$instance.Id; task_name=$instance.TaskName; background=$background; processes=$processes; stopped_confirmed=($background.state -eq 'stopped' -and $background.host_exited -eq $true -and $remaining.Count -eq 0) }
}
if ($Action -eq 'Doctor') {
    $phase = 'diagnostics'
    Invoke-MediaFlowJson $control @('doctor') | ConvertTo-Json -Depth 20
    return
}
if ($Action -eq 'Status') { Get-MediaFlowStatus | ConvertTo-Json -Depth 20; return }
if ($Action -in @('Stop','Restart')) {
    $phase = 'stopping'
    $stopArguments = @('stop', '--timeout-seconds', [string]$TimeoutSeconds)
    if ($UpdateOperationId) { $stopArguments += @('--update-operation-id', $UpdateOperationId) }
    $stopResult = Invoke-MediaFlowJson $hostScript $stopArguments
    $confirmed = Get-MediaFlowStatus
    if (-not $confirmed.stopped_confirmed) { throw (New-MediaFlowManagementError 'stop_unconfirmed' '仍有后台进程存活或身份未核实，不能继续安装。' $phase) }
    if ($Action -eq 'Stop') { $confirmed | ConvertTo-Json -Depth 20; return }
}
if ($Action -in @('Start','Restart')) {
    $phase = 'registration'
    $registered = Get-ScheduledTask -TaskName $instance.TaskName -ErrorAction SilentlyContinue
    if (-not $registered) { throw (New-MediaFlowManagementError 'task_missing' 'MediaFlow 后台尚未登记，请修复后台注册后重试。' $phase) }
    try { Assert-MediaFlowTaskOwnership $registered $instance }
    catch { throw (New-MediaFlowManagementError 'wrong_instance' '后台登记属于其他程序或实例，未启动它。请修复此实例安装。' $phase) }
    if ($registered.State -in @('Disabled', 1)) { throw (New-MediaFlowManagementError 'task_disabled' 'MediaFlow 后台计划任务已被禁用，请修复后台注册后重试。' $phase) }
    $manifest = Join-Path $instance.AppRoot 'release-manifest.json'
    if (Test-Path -LiteralPath $manifest) { $release = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json }
    elseif ($instance.DevelopmentSource) {
        $canonical = Get-Content -LiteralPath (Join-Path $instance.AppRoot 'packaging\version.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $revision = (& git -C $instance.AppRoot rev-parse --short=12 HEAD 2>$null) -join ''
        $release = [pscustomobject]@{ version=([string]$canonical.version + '-dev.' + [string]$canonical.development_iteration); source_revision=$revision }
    }
    else { throw (New-MediaFlowManagementError 'release_manifest_missing' '安装版本清单缺失，无法核验后台，请修复安装。' 'identity') }
    $apiUrl = 'http://127.0.0.1:' + $instance.ApiPort + '/api/runtime/instance'
    $pageUrl = 'http://127.0.0.1:' + $instance.UiPort + '/'
    # Refuse an identifiable foreign runtime before releasing shutdown gates or
    # scheduling this host. A ready matching instance needs no duplicate start.
    $identity = $null
    try {
        $identity = Invoke-RestMethod -Uri $apiUrl -TimeoutSec 2
        Assert-MediaFlowReadyIdentity $identity $instance $release
        if ((Invoke-WebRequest -UseBasicParsing $pageUrl -TimeoutSec 2).StatusCode -eq 200) {
            Get-MediaFlowStatus | ConvertTo-Json -Depth 20
            return
        }
    } catch { if ($_.Exception.Data['MediaFlowCode']) { throw } }
    $phase = 'starting'
    $startResult = Invoke-MediaFlowJson $hostScript @('request-start')
    $previousAttemptId = [string](Get-MediaFlowStartupSnapshot $instance).attempt_id
    $previousTaskInfo = Get-ScheduledTaskInfo -TaskName $instance.TaskName -ErrorAction SilentlyContinue
    $previousLastRunTime = if ($previousTaskInfo) { $previousTaskInfo.LastRunTime } else { [DateTime]::MinValue }
    $startedAt = [DateTimeOffset]::Now
    try { Start-ScheduledTask -TaskName $instance.TaskName }
    catch { throw (New-MediaFlowManagementError 'task_start_failed' 'Windows 未能启动后台计划任务，请检查后台注册及任务状态。' 'scheduled_task') }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $ready = $false
    $phase = 'waiting'
    do {
        try {
            $identity = Invoke-RestMethod -Uri $apiUrl -TimeoutSec 2
            Assert-MediaFlowReadyIdentity $identity $instance $release
            $ready = (Invoke-WebRequest -UseBasicParsing $pageUrl -TimeoutSec 2).StatusCode -eq 200
        } catch {
            if ($_.Exception.Data['MediaFlowCode']) { throw }
            $ready = $false
        }
        if ($ready) { break }
        $task = Get-ScheduledTask -TaskName $instance.TaskName -ErrorAction SilentlyContinue
        $taskInfo = Get-ScheduledTaskInfo -TaskName $instance.TaskName -ErrorAction SilentlyContinue
        $bootstrap = Get-MediaFlowStartupSnapshot $instance
        $startupFailure = Get-MediaFlowStartupFailure $instance $startedAt $task $taskInfo $bootstrap $previousAttemptId $previousLastRunTime
        if ($startupFailure) { throw (New-MediaFlowManagementError $startupFailure.code $startupFailure.message $startupFailure.phase $startupFailure.diagnostic) }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    if (-not $ready) {
        $occupied = @(Get-NetTCPConnection -State Listen -LocalPort @($instance.ApiPort, $instance.UiPort, $instance.StreamPort) -ErrorAction SilentlyContinue)
        $apiOccupied = @($occupied | Where-Object { $_.LocalPort -eq $instance.ApiPort })
        $code = if ($apiOccupied.Count -and -not $identity) { 'port_occupied' } else { 'startup_timeout' }
        $message = if ($code -eq 'port_occupied') { '服务端口已有进程占用，但未返回本实例身份。请查看端口占用信息，不要结束其他程序。' } else { '后台未在限定时间内就绪，请查看启动日志后重试。' }
        throw (New-MediaFlowManagementError $code $message $phase @{ task_name=$instance.TaskName; bootstrap_log=(Join-Path $instance.DataRoot 'background-startup.log'); ports=@($occupied | ForEach-Object { @{ port=$_.LocalPort; pid=$_.OwningProcess } }) })
    }
    if (-not $NoBrowser) { Start-Process $pageUrl }
    Get-MediaFlowStatus | ConvertTo-Json -Depth 20
}
} catch {
    Get-MediaFlowManagementFailure $_ $phase $instance | ConvertTo-Json -Depth 20 -Compress
    exit 1
}
