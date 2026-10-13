# Shared by the installer and service tools. Reading a profile never starts a service.
function Get-MediaFlowInstance {
    param([string]$AppRoot, [string]$LauncherPath, [string]$InstanceProfilePath)
    if (-not $AppRoot) { $AppRoot = $PSScriptRoot }
    $AppRoot = [IO.Path]::GetFullPath($AppRoot)
    # A checkout is identified by source/development sentinels, never merely by
    # an interpreter directory. Installed bootstrap does not configure a checkout.
    $sourceCheckout = (Test-Path -LiteralPath (Join-Path $AppRoot 'PROJECT.md') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $AppRoot 'fixed_runner\runtime_layout.py') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $AppRoot '.venv\Scripts\python.exe') -PathType Leaf) -and
        -not (Test-Path -LiteralPath (Join-Path $AppRoot 'release-manifest.json')) -and
        -not (Test-Path -LiteralPath (Join-Path $AppRoot 'resources\platform\release-manifest.json'))
    if (-not $InstanceProfilePath -and $env:MEDIAFLOW_INSTANCE_PROFILE) { $InstanceProfilePath = $env:MEDIAFLOW_INSTANCE_PROFILE }
    if (-not $InstanceProfilePath -and -not $sourceCheckout) {
        $marker = Join-Path (Split-Path $AppRoot -Parent) 'instance-profile.path'
        if (Test-Path -LiteralPath $marker) { $InstanceProfilePath = (Get-Content -LiteralPath $marker -Raw -Encoding UTF8).Trim() }
    }
    $id = 'production'
    $dataRoot = Join-Path $env:LOCALAPPDATA 'MediaFlow\data'
    if ($sourceCheckout -and -not $InstanceProfilePath) {
        $dataRoot = if ($env:MEDIAFLOW_DATA_ROOT) { [IO.Path]::GetFullPath($env:MEDIAFLOW_DATA_ROOT) } elseif ($env:RISKFLOW_DATA_ROOT) { [IO.Path]::GetFullPath($env:RISKFLOW_DATA_ROOT) } else { Join-Path $AppRoot 'fixed_runner\runtime' }
    }
    $ports = @(3001, 48138, 48139)
    $taskName = 'MediaFlow Background'
    $acceptance = $false
    $installRoot = if ($LauncherPath) { Split-Path ([IO.Path]::GetFullPath($LauncherPath)) -Parent } else { Split-Path (Split-Path $AppRoot -Parent) -Parent }
    $profile = $null
    if ($InstanceProfilePath) {
        if (-not [IO.Path]::IsPathRooted($InstanceProfilePath)) { throw 'InstanceProfilePath must be absolute.' }
        $InstanceProfilePath = [IO.Path]::GetFullPath($InstanceProfilePath)
        if (-not (Test-Path -LiteralPath $InstanceProfilePath -PathType Leaf)) { throw 'The requested instance profile does not exist; refusing production fallback.' }
        $profile = Get-Content -LiteralPath $InstanceProfilePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([int]$profile.schema -ne 1 -or [string]$profile.id -notmatch '^[a-z][a-z0-9_-]{0,63}$') { throw 'Unsupported instance profile identity/schema.' }
        $id = [string]$profile.id
        if (-not $profile.data_root -or -not [IO.Path]::IsPathRooted([string]$profile.data_root)) { throw 'Profile data_root must be absolute.' }
        $dataRoot = [IO.Path]::GetFullPath([string]$profile.data_root)
        $ports = @([int]$profile.ui_port, [int]$profile.api_port, [int]$profile.stream_port)
        if (($ports | Where-Object { $_ -lt 1024 -or $_ -gt 65535 }) -or (@($ports | Select-Object -Unique).Count -ne 3)) { throw 'Instance ports must be three distinct valid ports.' }
        $taskName = [string]$profile.task_name
        if ([string]::IsNullOrWhiteSpace($taskName) -or $taskName -match '[\\/:*?"<>|]') { throw 'Invalid instance task_name.' }
        $acceptance = $profile.acceptance -eq $true
        if ($profile.install_root) {
            if (-not [IO.Path]::IsPathRooted([string]$profile.install_root)) { throw 'Profile install_root must be absolute.' }
            $installRoot = [IO.Path]::GetFullPath([string]$profile.install_root)
        }
        if ($id -ne 'production') {
            if (-not $acceptance) { throw 'A nonproduction installation must explicitly enable acceptance isolation.' }
            $productionData = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'MediaFlow\data')).TrimEnd('\')
            $legacyData = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'RiskFlow\data')).TrimEnd('\')
            foreach ($protected in @($productionData, $legacyData)) {
                if ($dataRoot.TrimEnd('\').Equals($protected, [StringComparison]::OrdinalIgnoreCase) -or $dataRoot.StartsWith($protected + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'An isolated instance cannot use production data.' }
            }
            if ($taskName -in @('MediaFlow Background', 'RiskFlow Background') -or ($ports | Where-Object { $_ -in @(3001, 48138, 48139) })) { throw 'An isolated instance cannot use production tasks or ports.' }
        }
        elseif ($acceptance) { throw 'Acceptance requires a nonproduction identity.' }
    }
    elseif (-not $sourceCheckout -and (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'MediaFlow\bootstrap.json'))) {
        $bootstrap = Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'MediaFlow\bootstrap.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($bootstrap.data_root) { $dataRoot = [IO.Path]::GetFullPath([string]$bootstrap.data_root) }
    }
    $shortcut = if ($id -eq 'production') { 'MediaFlow' } elseif ($id -eq 'acceptance') { 'MediaFlow Acceptance' } else { 'MediaFlow ' + $id }
    $uninstallKey='Software\Microsoft\Windows\CurrentVersion\Uninstall\MediaFlow.Desktop.'+$id
    if ($id -ne 'production' -and $profile.shortcut_name) {
        $requestedShortcut=[string]$profile.shortcut_name
        if ($requestedShortcut -in @('MediaFlow','RiskFlow') -or $requestedShortcut -match '[\\/:*?"<>|\r\n]') { throw 'An isolated shortcut cannot use a production name or a path.' }
        $shortcut=$requestedShortcut
    }
    if ($id -ne 'production' -and $profile.registry_key) {
        $requestedKey=[string]$profile.registry_key
        if ($requestedKey -notmatch '^Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MediaFlow[._-]Acceptance(?:[._-][A-Za-z0-9._-]+)?$') { throw 'An isolated uninstall registry key must use the MediaFlow.Acceptance namespace.' }
        $uninstallKey=$requestedKey
    }
    $key = 'Software\MediaFlow\Desktop\' + $id
    $developmentSource = $sourceCheckout -and $id -eq 'production' -and -not $acceptance
    [pscustomobject]@{
        Id=$id; AppRoot=$AppRoot; LauncherPath=$LauncherPath; ProfilePath=$InstanceProfilePath
        DataRoot=$dataRoot; UiPort=$ports[0]; ApiPort=$ports[1]; StreamPort=$ports[2]
        TaskName=$taskName; Acceptance=$acceptance; InstallRoot=$installRoot
        DevelopmentSource=$developmentSource
        ShortcutName=$shortcut; InstallRegistryKey=$key
        UninstallRegistryKey=$uninstallKey
        InstallerStore=('MediaFlow\Desktop\installers\' + $id + '\installer.exe'); Profile=$profile
    }
}
function Set-MediaFlowInstanceEnvironment {
    param($Instance)
    $env:MEDIAFLOW_APP_ROOT = $Instance.AppRoot
    $env:MEDIAFLOW_DATA_ROOT = $Instance.DataRoot
    $env:MEDIAFLOW_INSTANCE_ID = $Instance.Id
    $env:MEDIAFLOW_UI_PORT = [string]$Instance.UiPort
    $env:MEDIAFLOW_API_PORT = [string]$Instance.ApiPort
    $env:MEDIAFLOW_STREAM_PORT = [string]$Instance.StreamPort
    $env:MEDIAFLOW_ACCEPTANCE = if ($Instance.Acceptance) { '1' } else { '0' }
    $env:MEDIAFLOW_DEVELOPMENT_SOURCE = if ($Instance.DevelopmentSource) { '1' } else { '0' }
    $env:RISKFLOW_APP_ROOT = $Instance.AppRoot
    $env:RISKFLOW_DATA_ROOT = $Instance.DataRoot
    if ($Instance.ProfilePath) { $env:MEDIAFLOW_INSTANCE_PROFILE = $Instance.ProfilePath }
    else { Remove-Item Env:MEDIAFLOW_INSTANCE_PROFILE -ErrorAction SilentlyContinue }
    $env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
    $env:PATH = (Join-Path $Instance.AppRoot 'runtime\platform-tools') + ';' + (Join-Path $Instance.AppRoot 'runtime\node') + ';' + $env:PATH
}
function Get-MediaFlowPython {
    param($Instance, [switch]$Windowless)
    $name = if ($Windowless) { 'pythonw.exe' } else { 'python.exe' }
    $development = Join-Path $Instance.AppRoot ('.venv\Scripts\' + $name)
    if ($Instance.DevelopmentSource -and (Test-Path -LiteralPath $development -PathType Leaf)) { return $development }
    $bundled = Join-Path $Instance.AppRoot ('runtime\python\' + $name)
    if (Test-Path -LiteralPath $bundled -PathType Leaf) { return $bundled }
    if (Test-Path -LiteralPath $development -PathType Leaf) { return $development }
    throw 'MediaFlow Python runtime is missing; refusing a system runtime fallback.'
}
function Get-MediaFlowProfileArguments {
    param($Instance)
    if ($Instance.ProfilePath) { return @('--instance-profile', $Instance.ProfilePath) }
    return @()
}
function Assert-MediaFlowTaskOwnership {
    param($Task, $Instance)
    if (-not $Task) { return }
    $expected = Join-Path $Instance.AppRoot 'fixed_runner\background_host.py'
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1 -or -not ([string]$actions[0].Arguments).Contains($expected)) { throw ('The existing task ' + $Instance.TaskName + ' belongs to another installation. Verified migration must remove it before registration.') }
    if ($Instance.ProfilePath -and -not ([string]$actions[0].Arguments).Contains($Instance.ProfilePath)) { throw 'The existing task belongs to another instance profile.' }
}
