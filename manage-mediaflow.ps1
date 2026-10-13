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
$helper = Join-Path $AppRoot 'installer\electron\instance-profile.ps1'
if (-not (Test-Path -LiteralPath $helper)) { throw 'MediaFlow instance management helper is missing.' }
. $helper
$instance = Get-MediaFlowInstance -AppRoot $AppRoot -LauncherPath $LauncherPath -InstanceProfilePath $InstanceProfilePath
Set-MediaFlowInstanceEnvironment $instance
$python = Get-MediaFlowPython $instance
$hostScript = Join-Path $instance.AppRoot 'fixed_runner\background_host.py'
$control = Join-Path $instance.AppRoot 'fixed_runner\runtime_control.py'
$profileArguments = @(Get-MediaFlowProfileArguments $instance)
function Invoke-MediaFlowJson {
    param([string]$Script, [string[]]$Arguments)
    $result = & $python $Script @Arguments @profileArguments
    if ($LASTEXITCODE -ne 0) { throw ('MediaFlow helper refused ' + ($Arguments -join ' ') + ': ' + ($result -join "`n")) }
    try { return ($result -join "`n") | ConvertFrom-Json } catch { throw 'MediaFlow helper returned invalid JSON.' }
}
function Get-MediaFlowStatus {
    $background = Invoke-MediaFlowJson $hostScript @('status')
    $runtime = Invoke-MediaFlowJson $control @('status')
    $processes = @($runtime.processes)
    $remaining = @($processes | Where-Object { $_.running -eq $true -or $_.pid -or $_.identity -notin @('missing','exited','stopped') })
    [pscustomobject]@{ ok=$true; instance_id=$instance.Id; task_name=$instance.TaskName; background=$background; processes=$processes; stopped_confirmed=($background.state -eq 'stopped' -and $background.host_exited -eq $true -and $remaining.Count -eq 0) }
}
if ($Action -eq 'Doctor') {
    & $python $control doctor @profileArguments
    if ($LASTEXITCODE -ne 0) { throw 'MediaFlow diagnostics failed.' }
    return
}
if ($Action -eq 'Status') { Get-MediaFlowStatus | ConvertTo-Json -Depth 20; return }
if ($Action -in @('Stop','Restart')) {
    $stopArguments = @('stop', '--timeout-seconds', [string]$TimeoutSeconds)
    if ($UpdateOperationId) { $stopArguments += @('--update-operation-id', $UpdateOperationId) }
    $stopResult = Invoke-MediaFlowJson $hostScript $stopArguments
    $confirmed = Get-MediaFlowStatus
    if (-not $confirmed.stopped_confirmed) { throw 'MediaFlow still has a live or unverifiable owned process. Installation cannot continue.' }
    if ($Action -eq 'Stop') { $confirmed | ConvertTo-Json -Depth 20; return }
}
if ($Action -in @('Start','Restart')) {
    $registered = Get-ScheduledTask -TaskName $instance.TaskName -ErrorAction SilentlyContinue
    if (-not $registered) { throw 'MediaFlow background task is missing. Repair its registration before starting.' }
    Assert-MediaFlowTaskOwnership $registered $instance
    $startResult = Invoke-MediaFlowJson $hostScript @('request-start')
    Start-ScheduledTask -TaskName $instance.TaskName
    $apiUrl = 'http://127.0.0.1:' + $instance.ApiPort + '/api/runtime/instance'
    $pageUrl = 'http://127.0.0.1:' + $instance.UiPort + '/'
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $ready = $false
    do {
        try {
            $identity = Invoke-RestMethod -Uri $apiUrl -TimeoutSec 2
            $apiIdentity = if ($identity.id) { $identity.id } else { $identity.instance_id }
            $ready = $apiIdentity -eq $instance.Id -and (Invoke-WebRequest -UseBasicParsing $pageUrl -TimeoutSec 2).StatusCode -eq 200
        } catch { $ready = $false }
        if ($ready) { break }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    if (-not $ready) { throw 'MediaFlow startup timed out or the port belongs to another instance.' }
    if (-not $NoBrowser) { Start-Process $pageUrl }
    Get-MediaFlowStatus | ConvertTo-Json -Depth 20
}
