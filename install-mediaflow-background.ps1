param(
    [switch]$NoStart, [switch]$NoShortcut,
    [string]$AppRoot = $PSScriptRoot,
    [string]$LauncherPath = '',
    [string]$InstanceProfilePath = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $AppRoot 'installer\electron\instance-profile.ps1')
if (-not $LauncherPath) { $LauncherPath = Join-Path $AppRoot 'MediaFlow.exe' }
$instance = Get-MediaFlowInstance -AppRoot $AppRoot -LauncherPath $LauncherPath -InstanceProfilePath $InstanceProfilePath
Set-MediaFlowInstanceEnvironment $instance
$pythonw = Get-MediaFlowPython $instance -Windowless
$hostScript = Join-Path $instance.AppRoot 'fixed_runner\background_host.py'
if (-not (Test-Path -LiteralPath $hostScript)) { throw 'MediaFlow background host is missing.' }
$registered = Get-ScheduledTask -TaskName $instance.TaskName -ErrorAction SilentlyContinue
Assert-MediaFlowTaskOwnership $registered $instance
# Stable external configuration means Task Scheduler never inherits Electron's environment.
if (-not $instance.ProfilePath) {
    $profilePath = Join-Path $instance.DataRoot 'instance-profile.json'
    New-Item -ItemType Directory -Force -Path (Split-Path $profilePath -Parent) | Out-Null
    $profile = [ordered]@{ schema=1; id=$instance.Id; data_root=$instance.DataRoot; ui_port=$instance.UiPort; api_port=$instance.ApiPort; stream_port=$instance.StreamPort; task_name=$instance.TaskName; app_root=$instance.AppRoot; acceptance=$false; development_source=[bool]$instance.DevelopmentSource }
    [IO.File]::WriteAllText($profilePath, ($profile | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    $instance = Get-MediaFlowInstance -AppRoot $AppRoot -LauncherPath $LauncherPath -InstanceProfilePath $profilePath
}
$arguments = '"' + $hostScript + '" run --instance-profile "' + $instance.ProfilePath + '"'
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $instance.AppRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description ('MediaFlow current-user independent background: ' + $instance.Id)
Register-ScheduledTask -TaskName $instance.TaskName -InputObject $task -Force | Out-Null
if (-not $NoShortcut) {
    if (-not (Test-Path -LiteralPath $LauncherPath)) { throw 'MediaFlow desktop executable is missing.' }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($directory in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $shortcut = $shell.CreateShortcut((Join-Path $directory ($instance.ShortcutName + '.lnk')))
        $shortcut.TargetPath = $LauncherPath
        $shortcut.Arguments = '--instance-profile="' + $instance.ProfilePath + '"'
        $shortcut.WorkingDirectory = Split-Path $LauncherPath -Parent
        $shortcut.IconLocation = "$LauncherPath,0"
        $shortcut.Description = 'Open MediaFlow'
        $shortcut.Save()
    }
}
if (-not $NoStart) { & (Join-Path $AppRoot 'manage-mediaflow.ps1') -Action Start -NoBrowser -AppRoot $AppRoot -LauncherPath $LauncherPath -InstanceProfilePath $instance.ProfilePath }
[pscustomobject]@{ TaskName=$instance.TaskName; User=$userId; Installed=$true; InstanceProfilePath=$instance.ProfilePath; Execute=$pythonw; Arguments=$arguments } | ConvertTo-Json
