param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$runtime = Join-Path $projectRoot 'fixed_runner\runtime'
$python = 'C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe'
$apiScript = Join-Path $projectRoot 'fixed_runner\control_api.py'
$frontend = Join-Path $projectRoot 'control_console'
$apiUrl = 'http://127.0.0.1:48138/api/config'
$pageUrl = 'http://127.0.0.1:3000/'

New-Item -ItemType Directory -Force -Path $runtime | Out-Null

try { $apiReady = (Invoke-WebRequest -UseBasicParsing $apiUrl -TimeoutSec 2).StatusCode -eq 200 } catch { $apiReady = $false }
if (-not $apiReady) {
    Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList @($apiScript) -WorkingDirectory (Split-Path $apiScript) -RedirectStandardOutput (Join-Path $runtime 'control-api.log') -RedirectStandardError (Join-Path $runtime 'control-api-error.log')
}

try { $pageReady = (Invoke-WebRequest -UseBasicParsing $pageUrl -TimeoutSec 2).StatusCode -eq 200 } catch { $pageReady = $false }
if (-not $pageReady) {
    Start-Process -WindowStyle Hidden -FilePath 'npm.cmd' -ArgumentList @('run','start','--','--host','127.0.0.1','--port','3000') -WorkingDirectory $frontend -RedirectStandardOutput (Join-Path $runtime 'control-ui.log') -RedirectStandardError (Join-Path $runtime 'control-ui-error.log')
}

$deadline = (Get-Date).AddSeconds(60)
do {
    try {
        $apiReady = (Invoke-WebRequest -UseBasicParsing $apiUrl -TimeoutSec 2).StatusCode -eq 200
        $pageReady = (Invoke-WebRequest -UseBasicParsing $pageUrl -TimeoutSec 2).StatusCode -eq 200
    } catch {
        Start-Sleep -Milliseconds 700
        continue
    }
    if ($apiReady -and $pageReady) { break }
    Start-Sleep -Milliseconds 700
} while ((Get-Date) -lt $deadline)

if (-not ($apiReady -and $pageReady)) {
    throw 'RiskFlow startup timed out. Check logs under fixed_runner\runtime.'
}
if (-not $NoBrowser) {
    Start-Process $pageUrl
}
