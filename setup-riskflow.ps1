param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$venv = Join-Path $projectRoot '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$requirements = Join-Path $projectRoot 'fixed_runner\requirements.txt'
$frontend = Join-Path $projectRoot 'control_console'

if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3.11 -m venv $venv
        if ($LASTEXITCODE -ne 0) {
            & $launcher.Source -3 -m venv $venv
        }
    }
    else {
        $fallback = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $fallback) { throw '未找到 Python 3，无法创建 RiskFlow 独立环境。' }
        & $fallback.Source -m venv $venv
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw 'RiskFlow 独立 Python 环境创建失败。'
}

& $python -m pip install --disable-pip-version-check -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'RiskFlow Python 依赖安装失败。' }

if (-not $SkipFrontend) {
    Push-Location $frontend
    try {
        npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'RiskFlow 前端依赖安装失败。' }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'RiskFlow 前端构建失败。' }
    }
    finally {
        Pop-Location
    }
}

& $python (Join-Path $projectRoot 'fixed_runner\runtime_control.py') doctor
if ($LASTEXITCODE -ne 0) { throw 'RiskFlow 环境自检未通过。' }

Write-Host 'RiskFlow 独立环境已就绪。'

