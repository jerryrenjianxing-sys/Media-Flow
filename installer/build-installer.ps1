param(
    [Parameter(Mandatory = $true)][string]$SetupPath,
    [string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$PackageVersion,
    [Parameter(Mandatory = $true)][string]$SourceRevision,
    [switch]$PrivateTemplate
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutputPath) { $OutputPath = Join-Path $projectRoot 'packaging\out\Releases\MediaFlow-Installer.exe' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Windows C# compiler was not found.' }
if (-not (Test-Path -LiteralPath $SetupPath)) { throw "Velopack setup is missing: $SetupPath" }
$icon = Join-Path $projectRoot 'assets\brand\mediaflow.ico'
$manifest = Join-Path $PSScriptRoot 'MediaFlowInstaller.exe.manifest'
$source = Join-Path $PSScriptRoot 'MediaFlowInstaller.cs'
New-Item -ItemType Directory -Force -Path (Split-Path $OutputPath -Parent) | Out-Null
$versionSource = [IO.Path]::ChangeExtension([IO.Path]::GetTempFileName(), '.cs')
try {
    @(
        'using System.Reflection;'
        ('[assembly: AssemblyInformationalVersion("{0}")]' -f $PackageVersion)
        ('[assembly: AssemblyMetadata("SourceRevision", "{0}")]' -f $SourceRevision)
        ('[assembly: AssemblyMetadata("PrivateTemplateExpected", "{0}")]' -f $PrivateTemplate.ToString().ToLowerInvariant())
    ) | Set-Content -LiteralPath $versionSource -Encoding utf8
    & $compiler /nologo /target:winexe /optimize+ /platform:x64 /win32manifest:$manifest /win32icon:$icon `
        /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll `
        "/resource:$SetupPath,MediaFlow.Setup.exe" "/out:$OutputPath" $source $versionSource `
        (Join-Path $PSScriptRoot 'PrivateTemplatePayload.cs') (Join-Path $PSScriptRoot 'PrivateTemplateInstall.cs')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputPath)) { throw 'MediaFlow installer compilation failed.' }
}
finally {
    Remove-Item -LiteralPath $versionSource -Force -ErrorAction SilentlyContinue
}
Get-Item -LiteralPath $OutputPath | Select-Object FullName, Length, LastWriteTime
