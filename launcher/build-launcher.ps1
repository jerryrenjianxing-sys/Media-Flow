param(
    [string]$PackageVersion = '',
    [string]$SourceRevision = ''
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path $PSScriptRoot -Parent
$source = Join-Path $PSScriptRoot 'RiskFlowLauncher.cs'
$manifest = Join-Path $PSScriptRoot 'MediaFlow.exe.manifest'
$icon = Join-Path $projectRoot 'assets\brand\mediaflow.ico'
$output = Join-Path $projectRoot 'MediaFlow.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$webViewVersion = '1.0.4191.47'
$packageRoot = Join-Path $PSScriptRoot "packages\Microsoft.Web.WebView2.$webViewVersion"
$packageFile = Join-Path $PSScriptRoot "packages\Microsoft.Web.WebView2.$webViewVersion.nupkg"

if ([string]::IsNullOrWhiteSpace($PackageVersion)) {
    $canonical = Get-Content -LiteralPath (Join-Path $projectRoot 'packaging\version.json') -Raw | ConvertFrom-Json
    $PackageVersion = "$($canonical.version)-dev.$([int]$canonical.development_iteration)"
}
if ([string]::IsNullOrWhiteSpace($SourceRevision)) {
    $SourceRevision = (& git -c core.excludesfile= -C $projectRoot rev-parse --short=12 HEAD 2>$null).Trim()
    if ([string]::IsNullOrWhiteSpace($SourceRevision)) { $SourceRevision = 'unknown' }
}

if (-not (Test-Path $compiler)) { $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $compiler)) { throw 'Windows C# compiler was not found.' }

if (-not (Test-Path (Join-Path $packageRoot 'lib\net462\Microsoft.Web.WebView2.Core.dll'))) {
    New-Item -ItemType Directory -Force -Path (Split-Path $packageFile -Parent) | Out-Null
    if (-not (Test-Path $packageFile)) {
        & curl.exe --fail --location --silent --show-error `
            "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$webViewVersion/microsoft.web.webview2.$webViewVersion.nupkg" `
            --output $packageFile
        if ($LASTEXITCODE -ne 0) { throw 'WebView2 SDK download failed.' }
    }
    $archive = [IO.Path]::ChangeExtension($packageFile, '.zip')
    Copy-Item -LiteralPath $packageFile -Destination $archive -Force
    Expand-Archive -LiteralPath $archive -DestinationPath $packageRoot -Force
    Remove-Item -LiteralPath $archive -Force
}

$core = Join-Path $packageRoot 'lib\net462\Microsoft.Web.WebView2.Core.dll'
$winforms = Join-Path $packageRoot 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'
$loader = Join-Path $packageRoot 'runtimes\win-x64\native\WebView2Loader.dll'
foreach ($path in @($core, $winforms, $loader)) { if (-not (Test-Path $path)) { throw "WebView2 build asset is missing: $path" } }
if (-not (Test-Path $manifest)) { throw "MediaFlow launcher manifest is missing: $manifest" }
if (-not (Test-Path $icon)) { throw "MediaFlow icon is missing: $icon" }

$versionSource = [IO.Path]::ChangeExtension([IO.Path]::GetTempFileName(), '.cs')
try {
    @(
        'using System.Reflection;'
        ('[assembly: AssemblyInformationalVersion("{0}")]' -f $PackageVersion)
        ('[assembly: AssemblyMetadata("SourceRevision", "{0}")]' -f $SourceRevision)
    ) | Set-Content -LiteralPath $versionSource -Encoding utf8
    & $compiler /nologo /target:winexe /optimize+ /platform:x64 /win32manifest:$manifest /win32icon:$icon `
        /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll `
        /reference:$core /reference:$winforms /out:$output $source $versionSource
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $output)) { throw 'MediaFlow launcher compilation failed.' }
}
finally {
    Remove-Item -LiteralPath $versionSource -Force -ErrorAction SilentlyContinue
}

function Copy-IfChanged([string]$sourcePath, [string]$targetDirectory) {
    $targetPath = Join-Path $targetDirectory (Split-Path $sourcePath -Leaf)
    if (Test-Path -LiteralPath $targetPath) {
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePath).Hash
        $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetPath).Hash
        if ($sourceHash -eq $targetHash) { return }
    }
    Copy-Item -LiteralPath $sourcePath -Destination $targetDirectory -Force
}
Copy-IfChanged $core $projectRoot
Copy-IfChanged $winforms $projectRoot
Copy-IfChanged $loader $projectRoot
Get-Item $output, (Join-Path $projectRoot 'Microsoft.Web.WebView2.Core.dll'), `
    (Join-Path $projectRoot 'Microsoft.Web.WebView2.WinForms.dll'), `
    (Join-Path $projectRoot 'WebView2Loader.dll') | Select-Object FullName, Length, LastWriteTime
