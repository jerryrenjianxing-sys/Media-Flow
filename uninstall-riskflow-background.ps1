param([switch]$KeepShortcut)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$taskName = 'RiskFlow Background'
$bundledPython = Join-Path $projectRoot 'runtime\python\python.exe'
$isDistribution = Test-Path -LiteralPath $bundledPython
if ($isDistribution) {
    $env:RISKFLOW_APP_ROOT = $projectRoot
    $env:RISKFLOW_DATA_ROOT = Join-Path $env:LOCALAPPDATA 'RiskFlow\data'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PATH = (Join-Path $projectRoot 'runtime\platform-tools') + ';' +
        (Join-Path $projectRoot 'runtime\node') + ';' + $env:PATH
}
$python = if ($isDistribution) { $bundledPython } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
$hostScript = Join-Path $projectRoot 'fixed_runner\background_host.py'

if (Test-Path -LiteralPath $python) {
    & $python $hostScript request-stop
    if ($LASTEXITCODE -ne 0) { throw 'RiskFlow still has running tasks; background task was not removed.' }
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $statusText = & $python $hostScript status
        try { $state = ($statusText | ConvertFrom-Json).state } catch { $state = 'unknown' }
    } while ($state -ne 'stopped' -and (Get-Date) -lt $deadline)
}

$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($registered) {
    if ($registered.State -eq 'Running') { Stop-ScheduledTask -TaskName $taskName }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

if (-not $KeepShortcut) {
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'RiskFlow.lnk'
    if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath -Force }
}

Write-Host 'RiskFlow background task removed. User data was preserved.'
