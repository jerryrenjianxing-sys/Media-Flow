param([switch]$KeepShortcut)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$taskName = 'MediaFlow Background'
$python = if (Test-Path (Join-Path $projectRoot 'runtime\python\python.exe')) { Join-Path $projectRoot 'runtime\python\python.exe' } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
$hostScript = Join-Path $projectRoot 'fixed_runner\background_host.py'
if (Test-Path -LiteralPath $python) { & $python $hostScript request-stop | Out-Null }
$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($registered) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
if (-not $KeepShortcut) {
    $shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'MediaFlow.lnk'
    if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
}
Write-Host 'MediaFlow background service removed. User data was preserved.'
