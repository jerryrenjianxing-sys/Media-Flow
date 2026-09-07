param(
    [ValidateSet('Register', 'Start', 'Stop', 'Restart', 'Status', 'Run')]
    [string]$Action = 'Status',
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$taskName = 'MediaFlow Agent'
$hostScript = Join-Path $projectRoot 'fixed_runner\independent_agent.py'
$python = Join-Path $projectRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $projectRoot 'work\agent-runtime\python\python.exe'
}
if (-not (Test-Path -LiteralPath $python)) {
    throw 'MediaFlow Agent dedicated runtime is missing. Prepare it before registering or starting the Agent.'
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if ($Action -eq 'Register') {
    $previous = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($previous) {
        if ($previous.State -eq 'Running') { throw 'Stop the Agent before replacing its task definition.' }
        $backup = Join-Path $projectRoot ('work\agent-task-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.xml')
        New-Item -ItemType Directory -Force -Path (Split-Path $backup) | Out-Null
        Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $backup -Encoding UTF8
    }
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # Use the GUI-subsystem interpreter directly: no console window or shell
    # control event may own the lifetime of this background service.
    $runner = Join-Path (Split-Path $python) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $runner)) { throw 'Dedicated pythonw.exe is missing; the Agent was not registered.' }
    $arguments = '-X utf8 "' + $hostScript + '" run'
    $taskAction = New-ScheduledTaskAction -Execute $runner -Argument $arguments -WorkingDirectory $projectRoot
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Principal $principal -Settings $settings -Trigger $trigger -Description 'Official OpenCode Web UI, independent of MediaFlow platform.' -Force | Out-Null
    return
}
if ($Action -eq 'Run') {
    & $python $hostScript run
    exit $LASTEXITCODE
}
if ($Action -eq 'Status') {
    & $python $hostScript status
    exit $LASTEXITCODE
}
$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $registered) { throw 'MediaFlow Agent is not registered. Run -Action Register first. No fallback child process will be started.' }
if ($Action -in @('Stop', 'Restart')) {
    & $python $hostScript stop
    if ($LASTEXITCODE -ne 0) { throw 'Agent stop failed; no other process was stopped.' }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running') {
        if ((Get-Date) -ge $deadline) { throw 'Agent task did not finish in time. Inspect it before retrying.' }
        Start-Sleep -Milliseconds 250
    }
    if ($Action -eq 'Stop') { return }
}
& $python $hostScript request-start
if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare Agent startup.' }
Start-ScheduledTask -TaskName $taskName
$deadline = (Get-Date).AddSeconds(45)
do {
    $state = & $python $hostScript status | ConvertFrom-Json
    if ($state.running) {
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:3000/global/health' -TimeoutSec 2
            if ($health.healthy -and $health.version -eq '1.18.29') {
                if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:3000' }
                return
            }
        } catch {}
    }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
throw 'Agent startup timed out. Check the independent Agent log; the platform was not restarted.'
