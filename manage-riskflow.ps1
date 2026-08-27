param(
    [ValidateSet('Start', 'Stop', 'Restart', 'Status', 'Doctor')]
    [string]$Action = 'Status',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$control = Join-Path $projectRoot 'fixed_runner\runtime_control.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'RiskFlow 独立环境尚未安装。请先运行 setup-riskflow.ps1。'
}

$normalized = $Action.ToLowerInvariant()
& $python $control $normalized
if ($LASTEXITCODE -ne 0) { throw "RiskFlow $Action 失败。" }

if ($Action -in @('Start', 'Restart')) {
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
    if (-not ($apiReady -and $pageReady)) {
        throw 'RiskFlow 启动超时，请查看 fixed_runner\runtime 中的日志。'
    }
    if (-not $NoBrowser) { Start-Process $pageUrl }
}

