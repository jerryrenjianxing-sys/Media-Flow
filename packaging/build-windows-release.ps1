param(
    [string]$Version = '',
    [ValidateSet('development', 'release')]
    [string]$Channel = 'development',
    [switch]$SkipVelopack,
    [string]$TemplateManifestPath = ''
)

$ErrorActionPreference = 'Stop'
if ($TemplateManifestPath -and ($Channel -ne 'development' -or $SkipVelopack)) {
    throw 'Private snapshot payloads require an explicit development installer, never a public release or stage-only build.'
}
$projectRoot = Split-Path $PSScriptRoot -Parent
$canonicalVersionPath = Join-Path $projectRoot 'packaging\version.json'
if (-not (Test-Path -LiteralPath $canonicalVersionPath)) {
    throw 'Canonical release version is missing: packaging\version.json'
}
$canonical = Get-Content -LiteralPath $canonicalVersionPath -Raw | ConvertFrom-Json
$canonicalVersion = $canonical.version
if ([string]::IsNullOrWhiteSpace($canonicalVersion)) {
    throw 'Canonical release version is empty.'
}
$developmentIteration = [int]$canonical.development_iteration
if ($developmentIteration -lt 1) {
    throw 'Canonical development_iteration must be a positive integer.'
}
$developmentBuild = $Channel -eq 'development'
$releaseBuild = $Channel -eq 'release'
$packageVersion = if ($developmentBuild) {
    "$canonicalVersion-dev.$developmentIteration"
}
else {
    $canonicalVersion
}
if (-not [string]::IsNullOrWhiteSpace($Version) -and $Version -ne $packageVersion) {
    throw "Requested version $Version must match canonical version $packageVersion. Update packaging\version.json once instead of creating a parallel release line."
}
$Version = $canonicalVersion
$sourceRevision = (& git -c core.excludesfile= -C $projectRoot rev-parse --short=12 HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceRevision)) { $sourceRevision = 'unknown' }
$sourceCommit = (& git -c core.excludesfile= -C $projectRoot rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceCommit)) { $sourceCommit = 'unknown' }
$sourceDirty = [bool](& git -c core.excludesfile= -C $projectRoot status --porcelain --untracked-files=no 2>$null)
$untrackedSource = [bool](& git -c core.excludesfile= -C $projectRoot ls-files --others --exclude-standard -- '*.py' '*.ps1' '*.cs' '*.ts' '*.tsx' '*.json' 2>$null)
$sourceDirty = $sourceDirty -or $untrackedSource
if ($sourceDirty -and -not $SkipVelopack) {
    throw 'Refusing to create an installer from a dirty working tree. Development runs may be dirty, but the single installable release must come from one committed source revision.'
}
$releaseTag = "mediaflow/v$packageVersion"
if (-not $SkipVelopack) {
    $tagCommit = (& git -c core.excludesfile= -C $projectRoot rev-list -n 1 $releaseTag 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($tagCommit)) {
        throw "Installable build tag $releaseTag is missing. Commit the source, create this immutable version tag, then build."
    }
    if ($tagCommit.Trim() -ne $sourceCommit.Trim()) {
        throw "Installable build tag $releaseTag must point to the exact source revision $($sourceCommit.Trim()). Increment development_iteration for different code."
    }
}
$assemblyVersion = "$Version.0"
foreach ($sourcePath in @(
    (Join-Path $projectRoot 'launcher\RiskFlowLauncher.cs'),
    (Join-Path $projectRoot 'installer\MediaFlowInstaller.cs')
)) {
    if (-not (Select-String -LiteralPath $sourcePath -SimpleMatch ('AssemblyFileVersion("{0}")' -f $assemblyVersion) -Quiet)) {
        throw "Executable version in $sourcePath must match canonical version $Version."
    }
}
$outRoot = Join-Path $PSScriptRoot 'out'
$stage = Join-Path $outRoot 'MediaFlow-win-x64'
$releases = Join-Path $outRoot 'Releases'
$identityPath = Join-Path $outRoot ("release-identities\{0}.json" -f $packageVersion)
if (-not $SkipVelopack -and (Test-Path -LiteralPath $identityPath)) {
    throw "Installer identity for $packageVersion is already frozen. Reuse the existing checked package or increment the development version; do not rebuild different bytes under the same version."
}
$sourcePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$sourceSitePackages = Join-Path $projectRoot '.venv\Lib\site-packages'
$dotnet = Join-Path $PSScriptRoot 'tools\dotnet-runtime\expanded\dotnet.exe'
$vpk = Join-Path $PSScriptRoot 'tools\vpk-package\expanded\tools\net8.0\any\vpk.dll'

function Copy-Tree {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy.exe $Source $Destination /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "Directory copy failed with robocopy exit $LASTEXITCODE."
    }
}

if (-not (Test-Path -LiteralPath $sourcePython)) {
    throw 'Project Python is missing. Run setup-mediaflow.ps1 first.'
}

Push-Location (Join-Path $projectRoot 'control_console')
try {
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend standalone build failed.' }
}
finally {
    Pop-Location
}

# Packaging is deliberately side-effect free for a running installation. The
# newly built assets are copied into the staging directory below; an already
# running developer or installed UI is never restarted by the build itself.

& (Join-Path $projectRoot 'launcher\build-launcher.ps1') `
    -PackageVersion $packageVersion -SourceRevision $sourceRevision.Trim()
if ($LASTEXITCODE -ne 0) { throw 'MediaFlow launcher build failed.' }

if (Test-Path -LiteralPath $stage) {
    $resolved = Resolve-Path -LiteralPath $stage
    if (-not $resolved.Path.StartsWith($outRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to clean a release directory outside packaging/out.'
    }
    Remove-Item -LiteralPath $resolved.Path -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stage | Out-Null

$rootFiles = @(
    'MediaFlow.exe',
    'Microsoft.Web.WebView2.Core.dll',
    'Microsoft.Web.WebView2.WinForms.dll',
    'WebView2Loader.dll',
    'manage-mediaflow.ps1',
    'run-mediaflow-console.ps1',
    'install-mediaflow-background.ps1',
    'uninstall-mediaflow-background.ps1'
)
foreach ($relative in $rootFiles) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $relative) -Destination $stage
}

$fixedTarget = Join-Path $stage 'fixed_runner'
New-Item -ItemType Directory -Force -Path $fixedTarget | Out-Null
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'fixed_runner') -File | Where-Object {
    $_.Name -notlike 'test_*' -and
    $_.Name -ne 'device_profiles.json' -and
    ($_.Extension -eq '.py' -or $_.Name -in @(
        'requirements.txt',
        'read-openrouter-key.ps1',
        'set-openrouter-key-from-stdin.ps1',
        'import-background-secret.ps1'
    ))
} | Copy-Item -Destination $fixedTarget
Copy-Tree (Join-Path $projectRoot 'fixed_runner\topic_policies') `
    (Join-Path $fixedTarget 'topic_policies')

$virtualProfilesTarget = Join-Path $stage 'assets\profiles\virtual'
Copy-Tree (Join-Path $projectRoot 'assets\profiles\virtual') $virtualProfilesTarget
$invalidVirtualProfiles = Get-ChildItem -LiteralPath $virtualProfilesTarget -File | Where-Object {
    $rawProfile = Get-Content -LiteralPath $_.FullName -Raw
    try {
        $profile = $rawProfile | ConvertFrom-Json
    }
    catch {
        return $true
    }
    $declaresSafeContent =
        $profile.contains_account_data -eq $false -and
        $profile.contains_device_identity -eq $false
    $containsForbiddenField = $rawProfile -match '(?i)"(device_id|adb_endpoint|android_identity|account|coordinates)"\s*:'
    $containsCredential = $rawProfile -match 'sk-or-v1-[A-Za-z0-9_-]{32,}'
    -not $declaresSafeContent -or $containsForbiddenField -or $containsCredential
}
if ($invalidVirtualProfiles) {
    throw 'Shared virtual profile contains local identity, account data, coordinates, or credentials.'
}

$consoleTarget = Join-Path $stage 'control_console\standalone'
Copy-Tree (Join-Path $projectRoot 'control_console\dist\standalone') $consoleTarget
# Vinext's standalone output currently assumes these peer/runtime packages can
# be resolved from the source tree. Copy them explicitly so an installed build
# has no hidden dependency on control_console/node_modules.
$consoleModules = Join-Path $consoleTarget 'node_modules'
foreach ($moduleName in @('react', 'react-dom', 'scheduler', 'react-server-dom-webpack')) {
    $moduleSource = Join-Path (Join-Path $projectRoot 'control_console\node_modules') $moduleName
    if (-not (Test-Path -LiteralPath $moduleSource)) {
        throw "Frontend runtime dependency is missing: $moduleName"
    }
    Copy-Tree $moduleSource (Join-Path $consoleModules $moduleName)
}

$streamSource = Join-Path $projectRoot 'device_stream_host'
$streamTarget = Join-Path $stage 'device_stream_host'
& node (Join-Path $streamSource 'scripts\check-versions.mjs')
if ($LASTEXITCODE -ne 0) { throw 'Device stream dependency/version verification failed.' }
New-Item -ItemType Directory -Force -Path $streamTarget | Out-Null
foreach ($streamFile in @('package.json', 'package-lock.json', 'runtime-manifest.json')) {
    Copy-Item -LiteralPath (Join-Path $streamSource $streamFile) -Destination $streamTarget
}
foreach ($streamDirectory in @('src', 'scripts', 'vendor', 'node_modules')) {
    Copy-Tree (Join-Path $streamSource $streamDirectory) (Join-Path $streamTarget $streamDirectory)
}

$pythonBase = (& $sourcePython -c 'import sys; print(sys.base_prefix)').Trim()
if (-not (Test-Path -LiteralPath (Join-Path $pythonBase 'python.exe'))) {
    throw 'Portable Python base could not be located.'
}
$pythonTarget = Join-Path $stage 'runtime\python'
Copy-Tree $pythonBase $pythonTarget
$sitePackages = Join-Path $pythonTarget 'Lib\site-packages'
if (Test-Path -LiteralPath $sitePackages) {
    Remove-Item -LiteralPath $sitePackages -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
# Reuse the project's already-tested virtual-environment payload.  A release
# build must not hang on network/package-index resolution or silently package
# dependency versions different from the test run.
if (-not (Test-Path -LiteralPath $sourceSitePackages)) {
    throw 'Project Python dependencies are missing. Run setup-riskflow.ps1 first.'
}
Copy-Tree $sourceSitePackages $sitePackages
& (Join-Path $pythonTarget 'python.exe') -I -c `
    "import adbutils, PIL, requests, uiautomator2; print('portable-python-ok')"
if ($LASTEXITCODE -ne 0) { throw 'Portable Python dependency import check failed.' }

# Standalone device initialization depends on uiautomator2's bundled server and
# FastInputIME assets.  Python import success alone does not prove these binary
# resources survived packaging.
$u2Assets = Join-Path $sitePackages 'uiautomator2\assets'
foreach ($assetName in @('u2.jar', 'app-uiautomator.apk')) {
    $assetPath = Join-Path $u2Assets $assetName
    if (-not (Test-Path -LiteralPath $assetPath) -or (Get-Item -LiteralPath $assetPath).Length -le 0) {
        throw "Portable uiautomator2 asset is missing: $assetName"
    }
}

$node = (Get-Command node -ErrorAction Stop).Source
$nodeTarget = Join-Path $stage 'runtime\node'
New-Item -ItemType Directory -Force -Path $nodeTarget | Out-Null
Copy-Item -LiteralPath $node -Destination (Join-Path $nodeTarget 'node.exe')

$adb = (Get-Command adb -ErrorAction Stop).Source
$adbRoot = Split-Path $adb -Parent
Copy-Tree $adbRoot (Join-Path $stage 'runtime\platform-tools')

$forbiddenNames = @(
    'tasks.db',
    'openrouter-api-key.dpapi',
    'openrouter-api-key.pending.dpapi',
    'model-connection.json',
    'device_profiles.json',
    'platform_profiles.json',
    'installation.json'
)
$forbidden = Get-ChildItem -LiteralPath $stage -Recurse -File | Where-Object {
    $_.Name -in $forbiddenNames -or
    $_.FullName -match '[\\/]fixed_runner[\\/]runtime[\\/]'
}
if ($forbidden) {
    throw 'Release audit found local state or credentials in the package.'
}

$secretMatches = & rg -l 'sk-or-v1-[A-Za-z0-9_-]{32,}' $stage `
    -g '*.py' -g '*.ps1' -g '*.json' -g '*.md' 2>$null
if ($LASTEXITCODE -eq 0 -and $secretMatches) {
    throw 'Release audit found an OpenRouter key-like value.'
}

$developmentDeviceMatches = & rg -l 'P7HUDEKF4XVODY4D|emulator-5556|127\.0\.0\.1:16448|127\.0\.0\.1:16480|127\.0\.0\.1:16512|127\.0\.0\.1:16544' $stage `
    -g '*.py' -g '*.ps1' -g '*.json' -g '*.md' 2>$null
if ($LASTEXITCODE -eq 0 -and $developmentDeviceMatches) {
    throw 'Release audit found a development ADB serial or port.'
}

$legacyBrandAllowlist = @(
    'install-mediaflow-background.ps1',
    'fixed_runner\background_host.py',
    'fixed_runner\brand.py',
    'fixed_runner\control_api.py',
    'fixed_runner\runtime_control.py',
    'fixed_runner\runtime_layout.py',
    'fixed_runner\storage_setup.py'
)
$visibleLegacyBrand = @()
Get-ChildItem -LiteralPath $stage -Recurse -File | Where-Object { $_.Extension -in @('.py','.ps1','.json','.js','.mjs','.html','.css','.md','.txt') } | ForEach-Object {
    $relative = $_.FullName.Substring($stage.Length + 1)
    if ($relative -notin $legacyBrandAllowlist -and (Select-String -LiteralPath $_.FullName -SimpleMatch 'RiskFlow' -CaseSensitive -Quiet)) {
        $visibleLegacyBrand += $relative
    }
}
if ($visibleLegacyBrand) {
    throw "Release audit found a user-visible legacy brand reference: $($visibleLegacyBrand -join ', ')"
}

if ($releaseBuild -and $SkipVelopack) {
    throw 'A release-channel build must include the installable artifacts.'
}
if ($releaseBuild -and $sourceDirty) {
    throw 'A release-channel build must come from one clean committed source revision.'
}
$displayVersion = if ($developmentBuild) {
    "$packageVersion+$($sourceRevision.Trim())$(if ($sourceDirty) { '.dirty' } else { '' })"
}
else {
    $packageVersion
}
$manifest = [ordered]@{
    product = 'MediaFlow'
    version = $packageVersion
    target_version = $Version
    development_iteration = if ($developmentBuild) { $developmentIteration } else { $null }
    channel = if ($developmentBuild) { 'development' } else { 'release' }
    display_version = $displayVersion
    runtime = 'win-x64'
    built_at = (Get-Date).ToUniversalTime().ToString('o')
    data_root = '%LocalAppData%\MediaFlow\data'
    legacy_data_root = '%LocalAppData%\RiskFlow\data'
    contains_local_state = $false
    standalone_device_initialization = $true
    mumu_realtime_stream = 'experimental'
    stream_protocol = 1
    scrcpy_server = '3.3.3'
    desktop_shell = 'WinForms WebView2 Evergreen'
    uiautomator2_assets = @('u2.jar', 'app-uiautomator.apk')
    distribution_label = if ($developmentBuild) { 'development-stage' } else { 'internal-test' }
    source_revision = $sourceRevision.Trim()
    source_dirty = $sourceDirty
    build_identity = $displayVersion
    code_signed = $false
    user_visible_brand = 'MediaFlow 媒体自动化平台'
}
if ($TemplateManifestPath) {
    $privateTemplate = Get-Content -LiteralPath $TemplateManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $manifest['private_installer_payload'] = [ordered]@{
        source = 'private_snapshot'
        template_version = $privateTemplate.template_version
        sha256 = $privateTemplate.sha256
        size_bytes = $privateTemplate.size_bytes
        private_data_possible = $true
        public_redistribution = $false
    }
    $manifest['distribution_label'] = 'private-test-only'
    $manifest['contains_local_state_scope'] = 'program payload only; private snapshot is a separate overlay'
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage 'release-manifest.json') -Encoding utf8

if (-not $SkipVelopack) {
    if (-not (Test-Path -LiteralPath $vpk) -or -not (Test-Path -LiteralPath $dotnet)) {
        throw 'Velopack is missing. Run packaging/bootstrap-velopack.ps1 first.'
    }
    New-Item -ItemType Directory -Force -Path $releases | Out-Null
    & $dotnet $vpk pack --packId RiskFlow.Internal --packVersion $packageVersion `
        --packDir $stage --mainExe MediaFlow.exe --packTitle MediaFlow `
        --packAuthors MediaFlow --runtime win-x64 --outputDir $releases `
        --icon (Join-Path $projectRoot 'assets\brand\mediaflow.ico') `
        --splashImage (Join-Path $projectRoot 'assets\brand\mediaflow-installer-splash.png') `
        --splashProgressColor '#5E6AD2' --msi --instLocation Either `
        --msiBanner (Join-Path $projectRoot 'assets\brand\mediaflow-msi-banner.bmp') `
        --msiLogo (Join-Path $projectRoot 'assets\brand\mediaflow-msi-logo.bmp') `
        --instWelcome (Join-Path $PSScriptRoot 'installer-welcome.txt') `
        --instReadme (Join-Path $PSScriptRoot 'installer-readme.txt') `
        --instConclusion (Join-Path $PSScriptRoot 'installer-conclusion.txt') `
        --skipVeloAppCheck
    if ($LASTEXITCODE -ne 0) { throw 'Velopack packaging failed.' }

    $generatedSetup = Get-ChildItem -LiteralPath $releases -Filter '*Setup.exe' -File | Where-Object { $_.Name -ne 'MediaFlow-Setup.exe' -and $_.Name -ne 'MediaFlow-Installer.exe' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $generatedSetup) { throw 'Velopack did not produce a setup executable.' }
    $brandedSetup = Join-Path $releases 'MediaFlow-Setup.exe'
    Copy-Item -LiteralPath $generatedSetup.FullName -Destination $brandedSetup -Force
    $generatedMsi = Get-ChildItem -LiteralPath $releases -Filter '*.msi' -File | Where-Object { $_.Name -ne 'MediaFlow-x64.msi' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($generatedMsi) { Copy-Item -LiteralPath $generatedMsi.FullName -Destination (Join-Path $releases 'MediaFlow-x64.msi') -Force }
    $generatedPortable = Get-ChildItem -LiteralPath $releases -Filter '*Portable.zip' -File | Where-Object { $_.Name -ne 'MediaFlow-Portable-x64.zip' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($generatedPortable) { Copy-Item -LiteralPath $generatedPortable.FullName -Destination (Join-Path $releases 'MediaFlow-Portable-x64.zip') -Force }
    & (Join-Path $projectRoot 'installer\build-installer.ps1') -SetupPath $brandedSetup `
        -OutputPath (Join-Path $releases 'MediaFlow-Installer.exe') `
        -PackageVersion $packageVersion -SourceRevision $sourceRevision.Trim() -PrivateTemplate:([bool]$TemplateManifestPath)
    if ($LASTEXITCODE -ne 0) { throw 'MediaFlow branded installer build failed.' }
    $installerPath = Join-Path $releases 'MediaFlow-Installer.exe'
    if ($TemplateManifestPath) {
        & $sourcePython (Join-Path $projectRoot 'fixed_runner\private_template_payload.py') --append $installerPath --manifest $TemplateManifestPath
        if ($LASTEXITCODE -ne 0) { throw 'Private template overlay verification failed; installer must not be distributed.' }
        $privateNotice = Join-Path $releases 'PRIVATE-TEST-ONLY.txt'
        [IO.File]::WriteAllText($privateNotice, 'PRIVATE TEST ONLY. The appended snapshot can contain account identifiers and caches. Do not upload to GitHub or publicly redistribute. Program payload and snapshot are audited separately.', [Text.UTF8Encoding]::new($false))
    }
    $installerChecksumPath = Join-Path $releases 'MediaFlow-Installer.exe.sha256'
    $installerSha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(
        $installerChecksumPath,
        "$installerSha256  MediaFlow-Installer.exe`r`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    New-Item -ItemType Directory -Force -Path (Split-Path $identityPath -Parent) | Out-Null
    $identity = [ordered]@{ version=$packageVersion; source_revision=$sourceCommit.Trim(); installer_sha256=$installerSha256; private_template=[bool]$TemplateManifestPath }
    $identityBytes = [Text.UTF8Encoding]::new($false).GetBytes(($identity | ConvertTo-Json))
    $identityStream = [IO.File]::Open($identityPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $identityStream.Write($identityBytes, 0, $identityBytes.Length) } finally { $identityStream.Dispose() }
}

$stageSize = Get-ChildItem -LiteralPath $stage -Recurse -File | Measure-Object Length -Sum
[pscustomobject]@{
    Stage = $stage
    StageFiles = $stageSize.Count
    StageMB = [math]::Round($stageSize.Sum / 1MB, 1)
    Releases = if ($SkipVelopack) { $null } else { $releases }
}
