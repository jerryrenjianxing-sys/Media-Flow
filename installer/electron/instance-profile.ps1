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
    if ($id -eq 'production' -and -not $acceptance -and -not $developmentSource) {
        Assert-MediaFlowStableProductionPath $AppRoot 'application directory' | Out-Null
        Assert-MediaFlowStableProductionPath $dataRoot 'data directory' | Out-Null
        if ($InstanceProfilePath) { Assert-MediaFlowStableProductionPath $InstanceProfilePath 'instance profile' | Out-Null }
    }
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
function ConvertTo-MediaFlowAbsolutePath {
    param([string]$Value)
    # IsPathRooted also accepts drive-relative paths (C:foo) on .NET Framework.
    # Task identities must never depend on a caller's current drive/directory.
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\x00-\x1f]' -or
        $Value -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') {
        throw 'MediaFlow task identity requires an absolute Windows path.'
    }
    $full=[IO.Path]::GetFullPath($Value)
    if ([string]::Equals($full,[IO.Path]::GetPathRoot($full),[StringComparison]::OrdinalIgnoreCase)) { return [IO.Path]::GetPathRoot($full) }
    return $full.TrimEnd([char[]]@('\','/'))
}
function Test-MediaFlowSamePath {
    param([string]$Actual, [string]$Expected)
    try {
        $left = ConvertTo-MediaFlowAbsolutePath $Actual
        $right = ConvertTo-MediaFlowAbsolutePath $Expected
        return [string]::Equals($left, $right, [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}
function Get-MediaFlowNativeCompilerReferences {
    # NSIS's working directory contains its native System.dll plugin. Never let
    # Add-Type resolve a managed System.dll reference relative to that directory.
    if ($PSVersionTable.PSEdition -eq 'Core') {
        $referenceRoot=Join-Path $PSHOME 'ref'
        if (Test-Path -LiteralPath $referenceRoot -PathType Container) {
            return @(Get-ChildItem -LiteralPath $referenceRoot -Filter '*.dll' -File | ForEach-Object FullName)
        }
        $trusted=[string][AppContext]::GetData('TRUSTED_PLATFORM_ASSEMBLIES')
        $references=@($trusted.Split([IO.Path]::PathSeparator) | Where-Object { $_ -and [IO.Path]::IsPathRooted($_) })
        if (-not $references.Count) { throw 'PowerShell runtime reference assemblies are unavailable.' }
        return $references
    }
    return @([System.ComponentModel.Win32Exception].Assembly.Location,[System.Linq.Enumerable].Assembly.Location)
}
function Get-MediaFlowPhysicalPath {
    param([string]$Path)
    $absolute=ConvertTo-MediaFlowAbsolutePath $Path
    if (-not ('MediaFlow.NativeFilePaths' -as [type])) {
        Add-Type -ReferencedAssemblies @(Get-MediaFlowNativeCompilerReferences) -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace MediaFlow {
    public static class NativeFilePaths {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
        private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
        private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
        public static string Resolve(string path) {
            // FILE_FLAG_BACKUP_SEMANTICS also allows directory handles. Opening
            // with no write access follows reparse points and MSIX redirection.
            using (SafeFileHandle handle = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
                if (handle.IsInvalid) throw new System.ComponentModel.Win32Exception();
                StringBuilder buffer = new StringBuilder(1024);
                uint count = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
                if (count >= buffer.Capacity) {
                    if (count > 32768) throw new InvalidOperationException("Resolved Windows path is too long.");
                    buffer = new StringBuilder((int)count + 1);
                    count = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
                }
                if (count == 0 || count >= buffer.Capacity) throw new System.ComponentModel.Win32Exception();
                string result = buffer.ToString();
                if (result.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) return @"\\" + result.Substring(8);
                if (result.StartsWith(@"\\?\", StringComparison.Ordinal)) return result.Substring(4);
                return result;
            }
        }
    }
}
'@ | Out-Null
    }
    return [MediaFlow.NativeFilePaths]::Resolve($absolute)
}
function Test-MediaFlowPackageCachePath {
    param([string]$Path)
    return $Path -match '(?i)[\\/]Packages[\\/][^\\/]+[\\/]LocalCache(?:[\\/]|$)'
}
function New-MediaFlowStoragePathError {
    param([string]$Code, [string]$Message, [string]$Role, [string]$Path, [string]$PhysicalPath='')
    # This helper is also imported by the installer without manager diagnostics.
    $failure=New-Object System.Exception($Message)
    $failure.Data['MediaFlowCode']=$Code
    $failure.Data['MediaFlowPhase']='configuration'
    $failure.Data['MediaFlowDiagnostic']=@{path_role=$Role;path=$Path;physical_path=$PhysicalPath;recovery='choose_stable_path'}
    return $failure
}
function Assert-MediaFlowStableProductionPath {
    param([string]$Path, [string]$Role='path')
    try {
        $probe=ConvertTo-MediaFlowAbsolutePath $Path
        # A not-yet-created destination inherits its nearest existing directory.
        # This check never creates a profile, directory, task, or service.
        while (-not (Test-Path -LiteralPath $probe)) {
            $parent=Split-Path $probe -Parent
            if (-not $parent -or [string]::Equals($parent,$probe,[StringComparison]::OrdinalIgnoreCase)) { throw 'No existing parent directory.' }
            $probe=$parent
        }
        $physical=Get-MediaFlowPhysicalPath $probe
    } catch {
        throw (New-MediaFlowStoragePathError 'storage_path_unverifiable' ('Cannot verify the physical MediaFlow '+$Role+'. Select a readable stable path before registering its background task.') $Role $Path)
    }
    if (Test-MediaFlowPackageCachePath $physical) {
        throw (New-MediaFlowStoragePathError 'storage_path_unstable' ('MediaFlow production '+$Role+' resolves to an MSIX LocalCache directory, which Windows Task Scheduler cannot reliably access. Select a stable non-package path; the background task was not registered.') $Role $Path $physical)
    }
    return $physical
}
function Split-MediaFlowWindowsArguments {
    param([AllowEmptyString()][string]$Arguments)
    if ($Arguments -match '[\x00-\x1f]') { throw 'Invalid control character in background task arguments.' }
    if (-not ('MediaFlow.NativeCommandLine' -as [type])) {
        Add-Type -ReferencedAssemblies @(Get-MediaFlowNativeCompilerReferences) -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace MediaFlow {
    public static class NativeCommandLine {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CommandLineToArgvW(string commandLine, out int count);
        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr pointer);
        public static string[] ParseArguments(string arguments) {
            int count;
            // The first argv item has different quoting rules. Supply a harmless
            // executable so Task Scheduler's Arguments are parsed as arguments.
            IntPtr pointer = CommandLineToArgvW("mediaflow-task.exe " + arguments, out count);
            if (pointer == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
            try {
                string[] result = new string[count - 1];
                for (int index = 1; index < count; index++)
                    result[index - 1] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(pointer, index * IntPtr.Size));
                return result;
            } finally { LocalFree(pointer); }
        }
    }
}
'@ | Out-Null
    }
    return [MediaFlow.NativeCommandLine]::ParseArguments($Arguments)
}
function Assert-MediaFlowProfileIdentity {
    param([string]$ProfilePath, $Instance)
    $expectedProfile = $Instance.ProfilePath
    if (-not $expectedProfile) {
        $expectedProfile = Join-Path $Instance.DataRoot 'instance-profile.json'
    }
    if (-not (Test-MediaFlowSamePath $ProfilePath $expectedProfile)) {
        throw 'The existing task belongs to another instance profile.'
    }
    # Re-read the actual file as well as comparing its name. A profile changed
    # after the caller resolved its instance cannot silently redirect the task.
    $actual = Get-MediaFlowInstance -AppRoot $Instance.AppRoot -LauncherPath $Instance.LauncherPath -InstanceProfilePath $ProfilePath
    if (-not [string]::Equals($actual.Id, $Instance.Id, [StringComparison]::Ordinal) -or
        -not (Test-MediaFlowSamePath $actual.DataRoot $Instance.DataRoot) -or
        -not [string]::Equals($actual.TaskName, $Instance.TaskName, [StringComparison]::OrdinalIgnoreCase) -or
        $actual.UiPort -ne $Instance.UiPort -or $actual.ApiPort -ne $Instance.ApiPort -or
        $actual.StreamPort -ne $Instance.StreamPort -or $actual.Acceptance -ne $Instance.Acceptance -or
        $actual.DevelopmentSource -ne $Instance.DevelopmentSource) {
        throw 'The background task profile identity changed. Repair its registration before starting.'
    }
    if ($actual.Profile.app_root -and -not (Test-MediaFlowSamePath ([string]$actual.Profile.app_root) $Instance.AppRoot)) {
        throw 'The background task profile belongs to another application directory.'
    }
    if ($actual.Profile.install_root -and -not (Test-MediaFlowSamePath $actual.InstallRoot $Instance.InstallRoot)) {
        throw 'The background task profile belongs to another installation directory.'
    }
}
function Assert-MediaFlowTaskOwnership {
    param($Task, $Instance)
    if (-not $Task) { return }
    if ($Task.TaskName -and -not [string]::Equals([string]$Task.TaskName, $Instance.TaskName, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The existing task name belongs to another instance.'
    }
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1) { throw 'The existing background task must have exactly one verified action.' }
    $action = $actions[0]
    $executable = if ($Instance.DevelopmentSource) { Join-Path $Instance.AppRoot '.venv\Scripts\pythonw.exe' }
        else { Join-Path $Instance.AppRoot 'runtime\python\pythonw.exe' }
    if (-not (Test-MediaFlowSamePath ([string]$action.Execute) $executable) -or
        -not (Test-MediaFlowSamePath ([string]$action.WorkingDirectory) $Instance.AppRoot)) {
        throw 'The existing background task executable or working directory belongs to another installation.'
    }
    $arguments = @(Split-MediaFlowWindowsArguments ([string]$action.Arguments))
    $script = Join-Path $Instance.AppRoot 'fixed_runner\background_host.py'
    if ($arguments.Count -lt 2 -or -not (Test-MediaFlowSamePath $arguments[0] $script) -or
        -not [string]::Equals($arguments[1], 'run', [StringComparison]::Ordinal)) {
        throw 'The existing background task does not run the verified MediaFlow host.'
    }
    if ($arguments.Count -eq 2 -and $Instance.DevelopmentSource -and -not $Instance.ProfilePath) {
        # One compatibility path for the verified pre-profile source registration.
        # Explicit profiles and all installed instances still require their file.
        return
    }
    $profilePath = $null
    if ($arguments.Count -eq 4 -and [string]::Equals($arguments[2], '--instance-profile', [StringComparison]::Ordinal)) {
        $profilePath = $arguments[3]
    } elseif ($arguments.Count -eq 3 -and $arguments[2].StartsWith('--instance-profile=', [StringComparison]::Ordinal)) {
        $profilePath = $arguments[2].Substring('--instance-profile='.Length)
    }
    if (-not $profilePath) { throw 'The existing background task needs one exact instance profile argument and no extra arguments.' }
    Assert-MediaFlowProfileIdentity $profilePath $Instance
}
