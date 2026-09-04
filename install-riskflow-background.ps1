param(
    [switch]$NoStart,
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$taskName = 'RiskFlow Background'
$bundledPython = Join-Path $projectRoot 'runtime\python\python.exe'
$isDistribution = Test-Path -LiteralPath $bundledPython
$python = if ($isDistribution) { $bundledPython } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
$pythonw = if ($isDistribution) { $bundledPython } else { Join-Path $projectRoot '.venv\Scripts\pythonw.exe' }
$hostScript = Join-Path $projectRoot 'fixed_runner\background_host.py'
$launcher = Join-Path $projectRoot 'RiskFlow.exe'

if ($isDistribution) {
    $env:RISKFLOW_APP_ROOT = $projectRoot
    $env:RISKFLOW_DATA_ROOT = Join-Path $env:LOCALAPPDATA 'RiskFlow\data'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PATH = (Join-Path $projectRoot 'runtime\platform-tools') + ';' +
        (Join-Path $projectRoot 'runtime\node') + ';' + $env:PATH
}

if (-not (Test-Path -LiteralPath $pythonw)) {
    throw 'RiskFlow Python runtime is missing.'
}
if (-not (Test-Path -LiteralPath $hostScript)) {
    throw 'RiskFlow background host is missing.'
}

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($isDistribution) {
    if (-not (Test-Path -LiteralPath $launcher)) { throw 'RiskFlow.exe is missing.' }
    $action = New-ScheduledTaskAction -Execute $launcher -Argument '--background-run' -WorkingDirectory $projectRoot
}
else {
    $actionArguments = '"' + $hostScript + '" run'
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $actionArguments -WorkingDirectory $projectRoot
}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'RiskFlow current-user background host.'
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null

if (-not $NoShortcut -and (Test-Path -LiteralPath $launcher)) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'RiskFlow.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcher
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.IconLocation = "$launcher,0"
    $shortcut.Description = 'Open RiskFlow control console'
    $shortcut.Save()
}

if (-not $NoStart) {
    & $python $hostScript request-start
    Start-ScheduledTask -TaskName $taskName
    $heartbeat = if ($isDistribution) {
        Join-Path $env:RISKFLOW_DATA_ROOT 'background-host.json'
    }
    else {
        Join-Path $projectRoot 'fixed_runner\runtime\background-host.json'
    }
    $deadline = (Get-Date).AddSeconds(45)
    do {
        if (Test-Path -LiteralPath $heartbeat) {
            try {
                $payload = Get-Content -Raw -LiteralPath $heartbeat | ConvertFrom-Json
                if ($payload.state -eq 'running') { break }
            }
            catch {}
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
}

$registered = Get-ScheduledTask -TaskName $taskName
[pscustomobject]@{
    TaskName = $registered.TaskName
    State = [string]$registered.State
    User = $userId
    LogonType = [string]$registered.Principal.LogonType
    Installed = $true
}
