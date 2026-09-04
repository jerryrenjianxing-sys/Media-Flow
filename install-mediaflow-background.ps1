param([switch]$NoStart, [switch]$NoShortcut)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$taskName = 'MediaFlow Background'
$legacyTaskName = 'RiskFlow Background'
$bundledPython = Join-Path $projectRoot 'runtime\python\python.exe'
$isDistribution = Test-Path -LiteralPath $bundledPython
$python = if ($isDistribution) { $bundledPython } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
$pythonw = if ($isDistribution) { $bundledPython } else { Join-Path $projectRoot '.venv\Scripts\pythonw.exe' }
$hostScript = Join-Path $projectRoot 'fixed_runner\background_host.py'
$launcher = Join-Path $projectRoot 'MediaFlow.exe'

if ($isDistribution) {
    $bootstrap = Join-Path $env:LOCALAPPDATA 'MediaFlow\bootstrap.json'
    $dataRoot = Join-Path $env:LOCALAPPDATA 'MediaFlow\data'
    if (Test-Path -LiteralPath $bootstrap) {
        try { $configured = (Get-Content -Raw -LiteralPath $bootstrap | ConvertFrom-Json).data_root; if ($configured) { $dataRoot = [string]$configured } } catch {}
    }
    $env:MEDIAFLOW_APP_ROOT = $projectRoot
    $env:MEDIAFLOW_DATA_ROOT = $dataRoot
    $env:RISKFLOW_APP_ROOT = $projectRoot
    $env:RISKFLOW_DATA_ROOT = $dataRoot
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
}
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'MediaFlow Python runtime is missing.' }
if (-not (Test-Path -LiteralPath $hostScript)) { throw 'MediaFlow background host is missing.' }

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($isDistribution) {
    if (-not (Test-Path -LiteralPath $launcher)) { throw 'MediaFlow.exe is missing.' }
    $action = New-ScheduledTaskAction -Execute $launcher -Argument '--background-run' -WorkingDirectory $projectRoot
}
else {
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $hostScript + '" run') -WorkingDirectory $projectRoot
}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'MediaFlow current-user background host.'
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null

if (-not $NoShortcut -and (Test-Path -LiteralPath $launcher)) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'MediaFlow.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcher
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.IconLocation = "$launcher,0"
    $shortcut.Description = 'Open MediaFlow media automation platform'
    $shortcut.Save()
    $legacyShortcut = Join-Path $desktop 'RiskFlow.lnk'
    if (Test-Path -LiteralPath $legacyShortcut) { Remove-Item -LiteralPath $legacyShortcut -Force }
}

$legacyTask = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
if ($legacyTask) { Disable-ScheduledTask -TaskName $legacyTaskName | Out-Null }
if (-not $NoStart) {
    & $python $hostScript request-start
    Start-ScheduledTask -TaskName $taskName
}
[pscustomobject]@{ TaskName = $taskName; User = $userId; Installed = $true }
