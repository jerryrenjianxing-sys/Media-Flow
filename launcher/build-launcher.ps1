$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$source = Join-Path $PSScriptRoot 'RiskFlowLauncher.cs'
$output = Join-Path $projectRoot 'RiskFlow.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'

if (-not (Test-Path $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}

if (-not (Test-Path $compiler)) {
    throw 'Windows C# compiler was not found.'
}

& $compiler /nologo /target:winexe /optimize+ /platform:anycpu `
    /reference:System.dll /reference:System.Windows.Forms.dll `
    /out:$output $source

if ($LASTEXITCODE -ne 0 -or -not (Test-Path $output)) {
    throw 'RiskFlow launcher compilation failed.'
}

Get-Item $output | Select-Object FullName, Length, LastWriteTime
