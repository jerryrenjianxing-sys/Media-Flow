param(
    [switch]$KeepShortcut,
    [string]$AppRoot = $PSScriptRoot,
    [string]$LauncherPath = '',
    [string]$InstanceProfilePath = '',
    [string]$UpdateOperationId = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $AppRoot 'installer\electron\instance-profile.ps1')
$instance = Get-MediaFlowInstance -AppRoot $AppRoot -LauncherPath $LauncherPath -InstanceProfilePath $InstanceProfilePath
$registered = Get-ScheduledTask -TaskName $instance.TaskName -ErrorAction SilentlyContinue
Assert-MediaFlowTaskOwnership $registered $instance
& (Join-Path $AppRoot 'manage-mediaflow.ps1') -Action Stop -NoBrowser -AppRoot $AppRoot -LauncherPath $LauncherPath -InstanceProfilePath $instance.ProfilePath -UpdateOperationId $UpdateOperationId
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'MediaFlow background stop failed; task registration was preserved.' }
if ($registered) { Unregister-ScheduledTask -TaskName $instance.TaskName -Confirm:$false }
if (-not $KeepShortcut) {
    foreach ($directory in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $shortcut = Join-Path $directory ($instance.ShortcutName + '.lnk')
        if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
    }
}
[pscustomobject]@{ ok=$true; instance_id=$instance.Id; task_name=$instance.TaskName; removed=$true; data_preserved=$true } | ConvertTo-Json
