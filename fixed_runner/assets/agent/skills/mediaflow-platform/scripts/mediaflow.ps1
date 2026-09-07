param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $MediaFlowArguments
)

$runtime = $env:MEDIAFLOW_PYTHON
$configPath = if ($env:MEDIAFLOW_SKILL_CONFIG) {
    $env:MEDIAFLOW_SKILL_CONFIG
} else {
    Join-Path (Split-Path -Parent $PSScriptRoot) 'config.json'
}

if (-not $runtime -and (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    try {
        $runtime = (Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json).python
    } catch {
        Write-Error 'MediaFlow Skill config is not valid JSON.'
        exit 2
    }
}

if (-not $runtime) {
    foreach ($candidate in @('python3', 'python')) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command) {
            $runtime = $command.Source
            break
        }
    }
}

if (-not $runtime) {
    Write-Error 'Python 3 was not found. Set MEDIAFLOW_PYTHON or the config python value.'
    exit 2
}

& $runtime (Join-Path $PSScriptRoot 'mediaflow.py') @MediaFlowArguments
exit $LASTEXITCODE
