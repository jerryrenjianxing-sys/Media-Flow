param(
    [ValidateSet('Start', 'Stop', 'Restart', 'Status', 'Doctor')]
    [string]$Action = 'Status',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$bundledPython = Join-Path $projectRoot 'runtime\python\python.exe'
$isDistribution = Test-Path -LiteralPath $bundledPython

function Set-MediaFlowEnvironment {
    if (-not $isDistribution) { return }
    $bootstrap = Join-Path $env:LOCALAPPDATA 'MediaFlow\bootstrap.json'
    $dataRoot = Join-Path $env:LOCALAPPDATA 'MediaFlow\data'
    if (Test-Path -LiteralPath $bootstrap) {
        try {
            $configured = (Get-Content -Raw -LiteralPath $bootstrap | ConvertFrom-Json).data_root
            if ($configured) { $dataRoot = [string]$configured }
        }
        catch {}
    }
    $env:MEDIAFLOW_APP_ROOT = $projectRoot
    $env:MEDIAFLOW_DATA_ROOT = $dataRoot
    $env:RISKFLOW_APP_ROOT = $projectRoot
    $env:RISKFLOW_DATA_ROOT = $dataRoot
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PATH = (Join-Path $projectRoot 'runtime\platform-tools') + ';' +
        (Join-Path $projectRoot 'runtime\node') + ';' + $env:PATH
}

Set-MediaFlowEnvironment
$python = if ($isDistribution) { $bundledPython } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
$pythonwCandidate = if ($isDistribution) { Join-Path $projectRoot 'runtime\python\pythonw.exe' } else { Join-Path $projectRoot '.venv\Scripts\pythonw.exe' }
$pythonw = if (Test-Path -LiteralPath $pythonwCandidate) { $pythonwCandidate } else { $python }
$control = Join-Path $projectRoot 'fixed_runner\runtime_control.py'
$backgroundHost = Join-Path $projectRoot 'fixed_runner\background_host.py'
$taskName = 'MediaFlow Background'

if (-not (Test-Path -LiteralPath $python)) { throw 'MediaFlow runtime is incomplete. Please reinstall MediaFlow.' }
if ($Action -eq 'Doctor') {
    & $python $control doctor
    if ($LASTEXITCODE -ne 0) { throw 'MediaFlow diagnostics failed.' }
    return
}
if ($Action -eq 'Status') {
    & $python $backgroundHost status
    & $python $control status
    return
}

$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$backgroundState = 'stopped'
$backgroundStatusText = & $python $backgroundHost status
try { $backgroundState = ($backgroundStatusText | ConvertFrom-Json).state } catch { $backgroundState = 'unknown' }

function Wait-BackgroundStopped {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $statusText = & $python $backgroundHost status
        try { $state = ($statusText | ConvertFrom-Json).state } catch { $state = 'unknown' }
        if ($state -eq 'stopped') { return }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw 'MediaFlow background service did not stop in time.'
}

if ($Action -in @('Stop', 'Restart')) {
    if ($backgroundState -eq 'running') {
        & $python $backgroundHost request-stop
        if ($LASTEXITCODE -ne 0) { throw "MediaFlow $Action failed." }
        Wait-BackgroundStopped
    }
    else {
        & $python $control stop
        if ($LASTEXITCODE -ne 0) { throw "MediaFlow $Action failed." }
    }
    if ($Action -eq 'Stop') { return }
}

if ($Action -in @('Start', 'Restart')) {
    & $python $backgroundHost request-start
    if ($LASTEXITCODE -ne 0) { throw "MediaFlow $Action failed." }
    if ($registered) { Start-ScheduledTask -TaskName $taskName }
    else { Start-Process -WindowStyle Hidden -FilePath $pythonw -ArgumentList @($backgroundHost, 'run') -WorkingDirectory $projectRoot }
    $apiUrl = 'http://127.0.0.1:48138/api/config'
    $pageUrl = 'http://127.0.0.1:3000/'
    $deadline = (Get-Date).AddSeconds(45)
    $apiReady = $false
    $pageReady = $false
    do {
        try { $apiReady = (Invoke-WebRequest -UseBasicParsing $apiUrl -TimeoutSec 2).StatusCode -eq 200 } catch { $apiReady = $false }
        try { $pageReady = (Invoke-WebRequest -UseBasicParsing $pageUrl -TimeoutSec 2).StatusCode -eq 200 } catch { $pageReady = $false }
        if ($apiReady -and $pageReady) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    if (-not ($apiReady -and $pageReady)) { throw 'MediaFlow startup timed out. Check the runtime logs.' }
    if (-not $NoBrowser) { Start-Process $pageUrl }
}
