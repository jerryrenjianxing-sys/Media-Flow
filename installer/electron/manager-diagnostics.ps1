# Pure management diagnostics; importing this file does not start services.
function New-MediaFlowManagementError {
    param([string]$Code, [string]$Message, [string]$Phase, $Diagnostic = $null)
    $failure = New-Object System.Exception($Message)
    $failure.Data['MediaFlowCode'] = $Code
    $failure.Data['MediaFlowPhase'] = $Phase
    $failure.Data['MediaFlowDiagnostic'] = $Diagnostic
    return $failure
}

function Get-MediaFlowManagementFailure {
    param($Failure, [string]$Phase = 'management', $Instance = $null)
    $exception = $Failure.Exception
    $code = if ($exception.Data['MediaFlowCode']) { [string]$exception.Data['MediaFlowCode'] } else { 'management_failed' }
    $message = if ($exception.Data['MediaFlowCode']) { $exception.Message } else { '后台管理未能完成。请打开日志查看具体阶段，然后重试。' }
    if ($code -eq 'storage_path_unstable') { $message='后台配置实际位于应用沙盒缓存，Windows 计划任务无法可靠读取。请选择稳定的程序、数据和配置路径后重新登记。' }
    elseif ($code -eq 'storage_path_unverifiable') { $message='无法核实后台配置的实际磁盘路径。请检查目录权限，选择可读取的稳定路径后重试。' }
    $failurePhase = if ($exception.Data['MediaFlowPhase']) { [string]$exception.Data['MediaFlowPhase'] } else { $Phase }
    $diagnostic = $exception.Data['MediaFlowDiagnostic']
    [pscustomobject]@{ ok=$false; code=$code; message=$message; phase=$failurePhase; instance_id=$Instance.Id; diagnostic=$diagnostic }
}

function Test-MediaFlowPathEqual {
    param([string]$Expected, [string]$Actual)
    if (-not $Expected -or -not $Actual -or -not [IO.Path]::IsPathRooted($Expected) -or -not [IO.Path]::IsPathRooted($Actual)) { return $false }
    try { return [IO.Path]::GetFullPath($Expected).TrimEnd('\','/').Equals([IO.Path]::GetFullPath($Actual).TrimEnd('\','/'), [StringComparison]::OrdinalIgnoreCase) }
    catch { return $false }
}

function Test-MediaFlowRevisionEqual {
    param([string]$Expected, [string]$Actual)
    return $Expected -match '^[a-f0-9]{12,40}$' -and $Actual -match '^[a-f0-9]{12,40}$' -and
        ($Expected.StartsWith($Actual, [StringComparison]::OrdinalIgnoreCase) -or $Actual.StartsWith($Expected, [StringComparison]::OrdinalIgnoreCase))
}

function Get-MediaFlowStartupDirectories {
    param($Instance)
    $fallback = Join-Path $env:LOCALAPPDATA 'MediaFlow'
    if ($Instance.Id -cne 'production') { $fallback = Join-Path $fallback ('instances\' + $Instance.Id) }
    $fallback = Join-Path $fallback 'startup-diagnostics'
    return @($Instance.DataRoot, $fallback)
}

function Get-MediaFlowStartupSnapshot {
    param($Instance)
    $latest = $null
    $latestAt = [DateTimeOffset]::MinValue
    foreach ($directory in @(Get-MediaFlowStartupDirectories $Instance)) {
        try {
            $value = Get-Content -LiteralPath (Join-Path $directory 'background-startup.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
            if (-not $value -or $value.instance_id -cne $Instance.Id) { continue }
            $started = [DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParse([string]$value.started_at, [ref]$started)) { continue }
            # A log entry is a local artifact, not an arbitrary path supplied by
            # a status file. Only this known directory's diagnostic log is valid.
            $trustedLog = Join-Path ([IO.Path]::GetFullPath($directory)) 'background-startup.log'
            if (-not (Test-MediaFlowPathEqual $trustedLog ([string]$value.log_path))) {
                $value | Add-Member -NotePropertyName log_path -NotePropertyValue $trustedLog -Force
            }
            if ($started -gt $latestAt) { $latest = $value; $latestAt = $started }
        } catch { }
    }
    return $latest
}

function Assert-MediaFlowReadyIdentity {
    param($Identity, $Instance, $Release)
    $actualId = if ($Identity.id) { [string]$Identity.id } else { [string]$Identity.instance_id }
    $matching = $Identity.ok -ne $false -and $actualId -ceq $Instance.Id -and
        (Test-MediaFlowPathEqual $Instance.DataRoot $Identity.data_root) -and
        (Test-MediaFlowPathEqual $Instance.AppRoot $Identity.app_root) -and
        $Identity.acceptance -eq $Instance.Acceptance -and
        $Identity.api_port -eq $Instance.ApiPort -and $Identity.ui_port -eq $Instance.UiPort -and $Identity.stream_port -eq $Instance.StreamPort
    if (-not $matching) { throw (New-MediaFlowManagementError 'wrong_instance' '该端口属于另一实例或程序目录，未操作它的后台。请检查安装登记。' 'identity') }
    if (-not $Release -or $Identity.product_version.version -cne $Release.version -or -not (Test-MediaFlowRevisionEqual $Release.source_revision $Identity.product_version.source_revision)) {
        throw (New-MediaFlowManagementError 'release_mismatch' '后台版本或提交与当前程序不一致。请修复此实例安装后重试。' 'identity')
    }
}

function Get-MediaFlowStartupFailure {
    param($Instance, [DateTimeOffset]$StartedAt, $Task, $TaskInfo, $Bootstrap, [string]$PreviousAttemptId = '', [DateTime]$PreviousLastRunTime = [DateTime]::MinValue)
    $diagnostic = [ordered]@{ task_name=$Instance.TaskName; last_task_result=$null; last_run_time=$null; bootstrap_log=(Join-Path $Instance.DataRoot 'background-startup.log') }
    if ($TaskInfo) { $diagnostic.last_task_result = [long]$TaskInfo.LastTaskResult; $diagnostic.last_run_time = $TaskInfo.LastRunTime.ToString('o') }
    if ($Bootstrap -and $Bootstrap.instance_id -ceq $Instance.Id) {
        $observed = [DateTimeOffset]::MinValue
        $newAttempt = -not $PreviousAttemptId -or [string]$Bootstrap.attempt_id -cne $PreviousAttemptId
        if ($newAttempt -and [DateTimeOffset]::TryParse([string]$Bootstrap.started_at, [ref]$observed) -and $observed -ge $StartedAt -and $Bootstrap.state -eq 'failed') {
            foreach ($directory in @(Get-MediaFlowStartupDirectories $Instance)) {
                $trustedLog = Join-Path ([IO.Path]::GetFullPath($directory)) 'background-startup.log'
                if (Test-MediaFlowPathEqual $trustedLog ([string]$Bootstrap.log_path)) { $diagnostic.bootstrap_log = $trustedLog; break }
            }
            $diagnostic.bootstrap_phase = [string]$Bootstrap.phase
            $diagnostic.bootstrap_error_code = [string]$Bootstrap.error_code
            $phaseMessages = @{
                configuration='后台实例配置读取失败，请打开启动日志检查配置。'
                imports='后台组件加载失败，请检查安装完整性和启动日志。'
                database='后台数据存储初始化失败，请打开启动日志查看原因。'
                supervisor='后台宿主初始化失败，请打开启动日志查看原因。'
                mutex='后台单实例检查失败，请打开启动日志查看原因。'
                signal='后台停止通道初始化失败，请打开启动日志查看原因。'
                heartbeat='后台首个心跳保存失败，请检查数据目录和启动日志。'
                running='后台宿主启动后异常退出，请打开启动日志查看原因。'
            }
            $message = if ($phaseMessages.ContainsKey([string]$Bootstrap.phase)) { $phaseMessages[[string]$Bootstrap.phase] } else { '后台在启动过程中提前退出。请打开启动日志查看具体原因。' }
            return [pscustomobject]@{ code='service_early_exit'; message=$message; phase=[string]$Bootstrap.phase; diagnostic=$diagnostic }
        }
    }
    if ($TaskInfo -and $Task -and $Task.State -notin @('Running', 4)) {
        $lastRun = [DateTimeOffset]$TaskInfo.LastRunTime
        $result = [long]$TaskInfo.LastTaskResult
        if ($TaskInfo.LastRunTime -ne $PreviousLastRunTime -and $lastRun -ge $StartedAt.AddSeconds(-1) -and $result -notin @(0, 267009, 267011)) {
            return [pscustomobject]@{ code='service_early_exit'; message='Windows 已启动后台，但进程提前退出。请打开启动日志查看具体原因。'; phase='scheduled_task'; diagnostic=$diagnostic }
        }
    }
    return $null
}
