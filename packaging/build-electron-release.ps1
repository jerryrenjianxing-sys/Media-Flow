param(
    [ValidateSet('development','release')][string]$Channel = 'development',
    [switch]$Unpacked,
    [switch]$LocalAcceptanceInstaller,
    [string]$PlatformStage = '',
    [switch]$ReuseVerifiedStage,
    [string]$PrepackagedRoot = ''
)
$ErrorActionPreference = 'Stop'
if ($Unpacked -and $LocalAcceptanceInstaller) { throw 'Select one local build mode.' }
$projectRoot = Split-Path $PSScriptRoot -Parent
$canonical = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'version.json') -Raw | ConvertFrom-Json
$version = if ($Channel -eq 'development') { '{0}-dev.{1}' -f $canonical.version,$canonical.development_iteration } else { [string]$canonical.version }
if ($version -notmatch '^0\.4\.1(?:-dev\.[1-9][0-9]*)?$') { throw 'Canonical MediaFlow version is invalid.' }
$commit = (& git -c core.excludesfile= -C $projectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'A source commit is required.' }
$dirty = [bool](& git -c core.excludesfile= -C $projectRoot status --porcelain --untracked-files=normal)
$localBuild = $Unpacked -or $LocalAcceptanceInstaller
if ($PrepackagedRoot) {
    if (-not $LocalAcceptanceInstaller -or -not $ReuseVerifiedStage) { throw 'PrepackagedRoot is only for a disposable local NSIS recompile with an already verified stage.' }
    $PrepackagedRoot=[IO.Path]::GetFullPath($PrepackagedRoot)
    $acceptanceRoot=[IO.Path]::GetFullPath((Join-Path $projectRoot 'work\electron-acceptance')).TrimEnd('\')+'\'
    if (-not $PrepackagedRoot.StartsWith($acceptanceRoot,[StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath (Join-Path $PrepackagedRoot 'resources\local-only-build.json'))) { throw 'The prepackaged program must be an existing local acceptance build.' }
}
$releaseTag = 'v'+$version
$identityPath = Join-Path $PSScriptRoot ('out\release-identities\'+$version+'.electron.json')
$releaseDirectory = Join-Path $PSScriptRoot ('out\electron-releases\'+$version)
if (-not $localBuild) {
    if ($dirty) { throw 'Installable releases require a clean committed working tree.' }
    $tagCommit = (& git -c core.excludesfile= -C $projectRoot rev-list -n 1 $releaseTag 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $tagCommit -or $tagCommit.Trim() -ne $commit) { throw "Immutable pure version tag $releaseTag must identify this exact clean commit." }
    if ((Test-Path -LiteralPath $identityPath) -or (Test-Path -LiteralPath $releaseDirectory)) { throw 'This release identity is already frozen. Increment development_iteration; no old assets will be overwritten.' }
}
if (-not $PlatformStage) { $PlatformStage = Join-Path $PSScriptRoot 'out\electron-platform' }
$PlatformStage = [IO.Path]::GetFullPath($PlatformStage)
if (-not $ReuseVerifiedStage) {
    & (Join-Path $PSScriptRoot 'build-windows-release.ps1') -PayloadOnly -StagePath $PlatformStage -Channel $Channel
}
$stageManifestPath = Join-Path $PlatformStage 'release-manifest.json'
if (-not (Test-Path -LiteralPath $stageManifestPath)) { throw 'The assembled platform manifest is missing.' }
$stageManifest = Get-Content -LiteralPath $stageManifestPath -Raw | ConvertFrom-Json
if ($stageManifest.version -ne $version -or $stageManifest.source_commit -ne $commit -or $stageManifest.desktop_shell -ne 'Electron') { throw 'Platform stage identity does not match this source/version/Electron build.' }
if (-not $localBuild -and $stageManifest.source_dirty) { throw 'A dirty local payload cannot become a release.' }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'verify-electron-payload.py') $PlatformStage
if ($LASTEXITCODE -ne 0) { throw 'Electron payload audit failed.' }
$buildDirectory = if ($localBuild) { Join-Path $projectRoot ('work\electron-acceptance\build-'+[guid]::NewGuid().ToString('N')) } else { Join-Path $PSScriptRoot ('out\electron-builds\'+$version+'-'+[guid]::NewGuid().ToString('N')) }
New-Item -ItemType Directory -Force -Path $buildDirectory | Out-Null
$environmentNames = @('MEDIAFLOW_PLATFORM_STAGE','MEDIAFLOW_RELEASE_VERSION','MEDIAFLOW_SOURCE_COMMIT','MEDIAFLOW_ELECTRON_OUTPUT','MEDIAFLOW_LOCAL_ONLY_MANIFEST','CSC_IDENTITY_AUTO_DISCOVERY')
$priorEnvironment = @{}
foreach ($name in $environmentNames) { $priorEnvironment[$name] = [Environment]::GetEnvironmentVariable($name,'Process') }
try {
    $env:MEDIAFLOW_PLATFORM_STAGE=$PlatformStage; $env:MEDIAFLOW_RELEASE_VERSION=$version
    $env:MEDIAFLOW_SOURCE_COMMIT=$commit; $env:MEDIAFLOW_ELECTRON_OUTPUT=$buildDirectory
    $env:MEDIAFLOW_LOCAL_ONLY_MANIFEST=''
    if ($localBuild) {
        $localManifest=Join-Path $buildDirectory 'local-only-build.json'
        [IO.File]::WriteAllText($localManifest,(@{local_only=$true;acceptance_only=$true;source_commit=$commit;version=$version}|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
        $env:MEDIAFLOW_LOCAL_ONLY_MANIFEST=$localManifest
    }
    $env:CSC_IDENTITY_AUTO_DISCOVERY='false'
    Push-Location (Join-Path $projectRoot 'desktop')
    try {
        $builderArguments = @('--config','electron-builder.cjs','--win')
        if (-not $Unpacked) { $builderArguments += 'nsis' }
        $builderArguments += @('--x64','--publish','never')
        if ($PrepackagedRoot) { $builderArguments += @('--prepackaged',$PrepackagedRoot) }
        if ($Unpacked) { $builderArguments += '--dir' }
        & (Join-Path $projectRoot 'desktop\node_modules\.bin\electron-builder.cmd') @builderArguments
        if ($LASTEXITCODE -ne 0) { throw 'Electron x64 build failed.' }
    } finally { Pop-Location }
} finally {
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name,$priorEnvironment[$name],'Process') }
}
if ($localBuild) {
    [pscustomobject]@{ Version=$version; SourceCommit=$commit; SourceDirty=$dirty; LocalOnly=$true; Published=$false; BuildDirectory=$buildDirectory; PlatformStage=$PlatformStage } | ConvertTo-Json
    return
}
$installerName = 'MediaFlow-'+$version+'-win-x64-Setup.exe'
$installer = Join-Path $buildDirectory $installerName
$blockmap = $installer+'.blockmap'
$updateManifest = Join-Path $buildDirectory 'dev.yml'
if ($Channel -eq 'release') { $updateManifest = Join-Path $buildDirectory 'latest.yml' }
foreach ($file in @($installer,$blockmap,$updateManifest)) { if (-not (Test-Path -LiteralPath $file)) { throw ('Release asset is missing: '+$file) } }
$licenseDirectory = Join-Path $buildDirectory 'licenses'
New-Item -ItemType Directory -Force -Path $licenseDirectory | Out-Null
Copy-Item -LiteralPath (Join-Path $PlatformStage 'licenses') -Destination (Join-Path $licenseDirectory 'platform') -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination (Join-Path $licenseDirectory 'MediaFlow-MIT.txt')
foreach ($name in @('LICENSE.electron.txt','LICENSES.chromium.html')) {
    $file=Join-Path $buildDirectory ('win-unpacked\'+$name)
    if (-not (Test-Path -LiteralPath $file)) { throw ('Electron licensing asset missing: '+$name) }
    Copy-Item -LiteralPath $file -Destination $licenseDirectory
}
$licenseZip = Join-Path $buildDirectory ('MediaFlow-'+$version+'-licenses.zip')
Compress-Archive -LiteralPath $licenseDirectory -DestinationPath $licenseZip -CompressionLevel Optimal
$manifestName='MediaFlow-'+$version+'-manifest.json'
$manifest=[ordered]@{ product='MediaFlow';app_id='com.mediaflow.desktop';version=$version;channel=$Channel;tag=$releaseTag;source_commit=$commit;source_dirty=$false;runtime='win-x64';desktop_shell='Electron';code_signed=$false;contains_local_state=$false;embedded_agent=$false;assets=@();runtime_lock_sha256=(Get-FileHash -LiteralPath (Join-Path $PlatformStage 'runtime-lock.json') -Algorithm SHA256).Hash.ToLowerInvariant() }
foreach ($file in @($installer,$blockmap,$updateManifest,$licenseZip)) { $manifest.assets += [ordered]@{name=(Split-Path $file -Leaf);bytes=(Get-Item -LiteralPath $file).Length;sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()} }
$manifestPath=Join-Path $buildDirectory $manifestName
[IO.File]::WriteAllText($manifestPath,($manifest | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
$checksumName='MediaFlow-'+$version+'-SHA256SUMS.txt'
$assets=@($installer,$blockmap,$updateManifest,$licenseZip,$manifestPath)
$checksums=@($assets | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+(Split-Path $_ -Leaf) })
$checksum=Join-Path $buildDirectory $checksumName
[IO.File]::WriteAllLines($checksum,$checksums,[Text.UTF8Encoding]::new($false))
$assets += $checksum
$readmeName='MediaFlow-'+$version+'-INSTALL.txt'
$readme=Join-Path $buildDirectory $readmeName
$instructions=@("MediaFlow $version — Windows x64 安装包",'','请运行同目录的 Setup.exe，按界面选择程序安装目录。用户数据保存在程序目录外，更新和卸载默认保留数据。','应用启动后会检查 GitHub 开发版更新；点击更新后显示下载、校验、等待空闲和安装进度。','旧 dev.44 的迁移会先核验安装身份、保存程序/托管/安装器回退资料，安全停止并卸载旧版后才登记新版。','此 ZIP 包含安装器和验证资源，不是免安装运行目录。','内含 SHA256SUMS 文件可核对各文件；ZIP 自身的哈希在发行页面另附的 SHA256SUMS 中。',"源码提交：$commit",'许可：MediaFlow 为 MIT；第三方许可包含在 licenses.zip 中。')
[IO.File]::WriteAllLines($readme,$instructions,[Text.UTF8Encoding]::new($false))
$checksums += ((Get-FileHash -LiteralPath $readme -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+$readmeName)
[IO.File]::WriteAllLines($checksum,$checksums,[Text.UTF8Encoding]::new($false))
$assets += $readme
$deliveryZip=Join-Path $buildDirectory ('MediaFlow-'+$version+'-Windows-x64.zip')
Compress-Archive -LiteralPath $assets -DestinationPath $deliveryZip -CompressionLevel Optimal
$checksums += ((Get-FileHash -LiteralPath $deliveryZip -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+(Split-Path $deliveryZip -Leaf))
[IO.File]::WriteAllLines($checksum,$checksums,[Text.UTF8Encoding]::new($false))
$assets += $deliveryZip
# Copy only after every asset exists, using CreateNew for the identity and a new version directory.
New-Item -ItemType Directory -Path $releaseDirectory | Out-Null
foreach ($file in $assets) { Copy-Item -LiteralPath $file -Destination $releaseDirectory }
New-Item -ItemType Directory -Force -Path (Split-Path $identityPath -Parent) | Out-Null
$identity=[ordered]@{version=$version;tag=$releaseTag;source_revision=$commit;installer_sha256=(Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant();manifest_sha256=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant();delivery_zip_sha256=(Get-FileHash -LiteralPath $deliveryZip -Algorithm SHA256).Hash.ToLowerInvariant();assets=$manifest.assets}
$bytes=[Text.UTF8Encoding]::new($false).GetBytes(($identity | ConvertTo-Json -Depth 10))
$stream=[IO.File]::Open($identityPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try { $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
[pscustomobject]@{Version=$version;SourceCommit=$commit;Tag=$releaseTag;ReleaseDirectory=$releaseDirectory;Identity=$identityPath;Installer=(Join-Path $releaseDirectory $installerName);Published=$false} | ConvertTo-Json
