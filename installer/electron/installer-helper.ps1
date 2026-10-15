param(
    [ValidateSet('Resolve','Prepare','Commit','Rollback','Remove')][string]$Action = 'Resolve',
    [string]$InstallRoot = '', [string]$InstanceProfilePath = '',
    [string]$CandidateRoot = '', [string]$StatePath = '', [string]$IniPath = '',
    [string]$LegacyInstallerPath = '', [string]$InstallerPath = '', [string]$Version = '',
    [switch]$FunctionsOnly
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'instance-profile.ps1')

function Write-InstallerJson {
    param([string]$Path, $Value)
    New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent) | Out-Null
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
}
function Get-InstallerInstance {
    if ($Action -eq 'Resolve' -and -not $InstanceProfilePath -and $InstallRoot -eq (Join-Path $env:LOCALAPPDATA 'Programs\MediaFlow') -and -not (Test-Path -LiteralPath (Join-Path $InstallRoot 'resources\instance-profile.path'))) {
        $legacyRoots=@()
        foreach($registration in @(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue)) {
            if ($registration.DisplayVersion -ne '0.4.1-dev.44') { continue }
            $match=[regex]::Match([string]$registration.UninstallString,'(?i)^\s*"?(.+?\\Update\.exe)"?\s+uninstall(?:\s.*)?$')
            if ($match.Success) {
                $root=Split-Path $match.Groups[1].Value -Parent
                try { $verified=Assert-ProgramIdentity $root; if ($verified.Legacy) { $legacyRoots+=$root } } catch { continue }
            }
        }
        $legacyRoots=@($legacyRoots|Select-Object -Unique)
        if ($legacyRoots.Count -gt 1) { throw '发现多套已登记的旧版程序，请明确选择要升级的安装目录。' }
        if ($legacyRoots.Count -eq 1) { $script:InstallRoot=$legacyRoots[0] }
    }
    if (-not $InstanceProfilePath -and $InstallRoot) {
        $marker = Join-Path $InstallRoot 'resources\instance-profile.path'
        if (Test-Path -LiteralPath $marker) { $script:InstanceProfilePath = (Get-Content -LiteralPath $marker -Raw -Encoding UTF8).Trim() }
    }
    # Installer arguments/marker are authoritative; a caller's environment cannot change identity.
    Remove-Item Env:MEDIAFLOW_INSTANCE_PROFILE -ErrorAction SilentlyContinue
    if (-not $InstallRoot) { $script:InstallRoot = Join-Path $env:LOCALAPPDATA 'Programs\MediaFlow' }
    $instance = Get-MediaFlowInstance -AppRoot (Join-Path $InstallRoot 'resources\platform') -LauncherPath (Join-Path $InstallRoot 'MediaFlow.exe') -InstanceProfilePath $InstanceProfilePath
    if ($instance.Id -ne 'production') {
        if (-not $instance.Profile.install_root) { throw 'Isolated installation requires explicit profile install_root.' }
        $script:InstallRoot = $instance.InstallRoot
        $instance.AppRoot = Join-Path $InstallRoot 'resources\platform'
        $instance.LauncherPath = Join-Path $InstallRoot 'MediaFlow.exe'
    }
    $full = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    if ($full -eq [IO.Path]::GetPathRoot($full).TrimEnd('\') -or $full -eq $env:USERPROFILE -or $full -eq [Environment]::GetFolderPath('MyDocuments')) { throw 'Unsafe installation directory.' }
    if ($instance.DataRoot.Equals($full,[StringComparison]::OrdinalIgnoreCase) -or $instance.DataRoot.StartsWith($full+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'User data must remain outside the program directory.' }
    if ($instance.ProfilePath -and $instance.ProfilePath.StartsWith($full+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Instance profile must remain outside the program directory.' }
    if ($instance.Id -ne 'production') {
        $production = Get-ItemProperty 'HKCU:\Software\MediaFlow\Desktop\production' -ErrorAction SilentlyContinue
        $protected = @((Join-Path $env:LOCALAPPDATA 'Programs\MediaFlow'), $production.InstallLocation) | Where-Object { $_ }
        foreach ($path in $protected) {
            $path = [IO.Path]::GetFullPath([string]$path).TrimEnd('\')
            if ($full.Equals($path,[StringComparison]::OrdinalIgnoreCase) -or $full.StartsWith($path+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Isolated installation cannot replace the production program.' }
        }
    }
    return $instance
}
function Copy-InstallerTree {
    param([string]$Source, [string]$Target)
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    & robocopy.exe $Source $Target /E /XJ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw 'Program backup/restore failed.' }
}
function Get-OwnedInstallerProcesses {
    param([string]$Root)
    $prefix = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ([string]$_.ExecutablePath).StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) })
}
function Assert-ProgramIdentity {
    param([string]$Root,[switch]$AllowDirty)
    $platform = Join-Path $Root 'resources\platform'
    $legacy = Test-Path -LiteralPath (Join-Path $Root 'Update.exe')
    if ($legacy) { $platform = Join-Path $Root 'current' }
    $manifest = Join-Path $platform 'release-manifest.json'
    if (-not (Test-Path -LiteralPath $manifest)) { throw 'Existing folder has no verified MediaFlow release identity.' }
    $identity = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($identity.product -ne 'MediaFlow' -or $identity.runtime -ne 'win-x64') { throw '程序版本身份无效。' }
    if ($identity.source_dirty -eq $true) {
        $marker=Join-Path $Root 'resources\local-only-build.json'
        if (-not $AllowDirty -or -not (Test-Path -LiteralPath $marker)) { throw '未提交的本地构建只能安装到显式隔离验收实例。' }
        $local=Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($local.local_only -ne $true -or $local.acceptance_only -ne $true -or $local.version -ne $identity.version -or $local.source_commit -ne $identity.source_commit) { throw '本地验收构建标记无效。' }
    }
    if ($legacy) {
        if ($identity.version -ne '0.4.1-dev.44' -or $identity.source_revision -ne 'cf7c6161ee3f') { throw 'Only the preserved verified dev.44 Velopack identity supports this migration.' }
        $updater = [IO.Path]::GetFullPath((Join-Path $Root 'Update.exe'))
        if ((Get-FileHash -LiteralPath $updater -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'e22c411c7d5392141a4f962dcd52bf98fe7c39689d3a8b328b7916d3428b875c') { throw '旧安装目录中的 Update.exe 与 dev.44 的已核验 Velopack 组件不一致。' }
        $registrations = @(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object {
            $_.UninstallString -and ([string]$_.UninstallString).IndexOf($updater,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and $_.DisplayVersion -eq '0.4.1-dev.44'
        })
        if ($registrations.Count -ne 1) { throw 'The legacy root and Update.exe do not match exactly one registered dev.44 installation.' }
    }
    [pscustomobject]@{ Platform=$platform; Legacy=$legacy; Manifest=$identity }
}
function Save-LegacyRollbackInstaller {
    param([string]$BackupRoot, [string]$DataRoot)
    $name='MediaFlow-0.4.1-dev.44-Installer.exe'
    $expected='94de26d7df0b070af4a4fcaf32dc0d17d0f0015b5b79f361995712ef8d6836e7'
    $target=Join-Path $BackupRoot $name
    $candidates=@()
    if ($LegacyInstallerPath) { $candidates += $LegacyInstallerPath }
    if ($InstallerPath) { $candidates += (Join-Path (Split-Path $InstallerPath -Parent) $name) }
    $previous=Join-Path (Split-Path $DataRoot -Parent) 'installer-backups'
    if (Test-Path -LiteralPath $previous) { $candidates += @(Get-ChildItem -LiteralPath $previous -Filter $name -File -Recurse | ForEach-Object FullName) }
    foreach ($path in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $hash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne $expected) { if ($path -eq $LegacyInstallerPath) { throw '指定的旧版回退安装包校验失败，旧程序保持原状。' }; continue }
        if ([IO.Path]::GetFullPath($path) -ne [IO.Path]::GetFullPath($target)) { Copy-Item -LiteralPath $path -Destination $target }
        return $target
    }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/jerryrenjianxing-sys/Media-Flow/releases/download/mediaflow/v0.4.1-dev.44/MediaFlow-0.4.1-dev.44-Installer.exe' -OutFile $target -TimeoutSec 180
    } catch { throw '无法取得已发布的 dev.44 回退安装包。请检查网络后重试；旧程序和后台未变更。' }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw '已下载的 dev.44 回退安装包校验失败，旧程序保持原状。' }
    return $target
}
function Invoke-SafeInstallerStop {
    param($Instance, [string]$ProgramRoot, [string]$HelperPlatform)
    Set-MediaFlowInstanceEnvironment $Instance
    $env:MEDIAFLOW_APP_ROOT = $ProgramRoot
    $env:RISKFLOW_APP_ROOT = $ProgramRoot
    $python = Join-Path $HelperPlatform 'runtime\python\python.exe'
    $stop = Join-Path $HelperPlatform 'fixed_runner\background_host.py'
    $profileArgs = @(Get-MediaFlowProfileArguments $Instance)
    $result = & $python $stop stop --timeout-seconds 30 @profileArgs
    if ($LASTEXITCODE -ne 0) { throw ('The backend refused installation or did not fully stop: ' + ($result -join "`n")) }
    $parsed = ($result -join "`n") | ConvertFrom-Json
    if ($parsed.ok -ne $true) { throw 'The backend did not confirm a safe stop.' }
}
function Get-RegistryBackup {
    param([string]$Key)
    $item = Get-ItemProperty ('HKCU:\' + $Key) -ErrorAction SilentlyContinue
    if (-not $item) { return $null }
    $backup = [ordered]@{}
    $item.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { $backup[$_.Name] = $_.Value }
    return $backup
}
function Restore-RegistryBackup {
    param([string]$Key, $Backup)
    $path = 'HKCU:\' + $Key
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    if ($Backup) {
        New-Item -Path $path -Force | Out-Null
        $Backup.PSObject.Properties | ForEach-Object { New-ItemProperty -LiteralPath $path -Name $_.Name -Value $_.Value -Force | Out-Null }
    }
}
function Get-InstallerTaskActionReceipt {
    param($Task, [ValidateSet('python_host','legacy_launcher')][string]$Kind, [string]$TaskName)
    if (-not $Task -or @($Task.Actions).Count -ne 1) { throw 'A task receipt requires exactly one action.' }
    if ($Task.TaskName -and -not [string]::Equals([string]$Task.TaskName,$TaskName,[StringComparison]::OrdinalIgnoreCase)) { throw 'Task name does not match its installation receipt.' }
    $action=@($Task.Actions)[0]
    $arguments=@(Split-MediaFlowWindowsArguments ([string]$action.Arguments))
    $receipt=[ordered]@{ schema=1; kind=$Kind; task_name=$TaskName; execute=(ConvertTo-MediaFlowAbsolutePath ([string]$action.Execute)); working_directory=(ConvertTo-MediaFlowAbsolutePath ([string]$action.WorkingDirectory)); script=$null; profile_path=$null }
    if ($Kind -eq 'legacy_launcher') {
        if ($arguments.Count -ne 1 -or -not [string]::Equals($arguments[0],'--background-run',[StringComparison]::Ordinal)) { throw 'Legacy task does not run the verified background launcher.' }
    } else {
        if ($arguments.Count -lt 2 -or -not [string]::Equals($arguments[1],'run',[StringComparison]::Ordinal)) { throw 'Task does not run the background host.' }
        $receipt.script=ConvertTo-MediaFlowAbsolutePath $arguments[0]
        if ($arguments.Count -eq 4 -and [string]::Equals($arguments[2],'--instance-profile',[StringComparison]::Ordinal)) {
            $receipt.profile_path=ConvertTo-MediaFlowAbsolutePath $arguments[3]
        } elseif ($arguments.Count -eq 3 -and $arguments[2].StartsWith('--instance-profile=',[StringComparison]::Ordinal)) {
            $receipt.profile_path=ConvertTo-MediaFlowAbsolutePath $arguments[2].Substring('--instance-profile='.Length)
        } elseif ($arguments.Count -ne 2) { throw 'Task has unsupported or duplicate arguments.' }
    }
    return [pscustomobject]$receipt
}
function Test-InstallerTaskActionReceipt {
    param($Task, $Receipt)
    if (-not $Task -or -not $Receipt -or $Receipt.schema -ne 1) { return $false }
    try {
        $actual=Get-InstallerTaskActionReceipt $Task $Receipt.kind $Receipt.task_name
        foreach($field in @('execute','working_directory','script','profile_path')) {
            if ([string]::IsNullOrEmpty([string]$actual.$field) -and [string]::IsNullOrEmpty([string]$Receipt.$field)) { continue }
            if (-not (Test-MediaFlowSamePath ([string]$actual.$field) ([string]$Receipt.$field))) { return $false }
        }
        return $true
    } catch { return $false }
}
function Get-VerifiedInstallerTaskReceipt {
    param($Task, $Instance, $OldProgram)
    if (-not $Task) { return $null }
    if ($OldProgram -and $OldProgram.Legacy) {
        # This path is used only after dev.44 manifest/updater/registration
        # verification. It does not turn an arbitrary launcher into an owner.
        if ($Instance.Id -ne 'production' -or $Instance.Acceptance) { throw 'Legacy task migration requires the verified production installation.' }
        $receipt=Get-InstallerTaskActionReceipt $Task 'legacy_launcher' $Instance.TaskName
        if (-not (Test-MediaFlowSamePath $receipt.execute (Join-Path $OldProgram.Platform 'MediaFlow.exe')) -or
            -not (Test-MediaFlowSamePath $receipt.working_directory $OldProgram.Platform)) { throw 'Legacy task belongs to another installation.' }
        return $receipt
    }
    if ($OldProgram -and -not (Test-MediaFlowSamePath $OldProgram.Platform $Instance.AppRoot)) { throw 'The previous program directory does not match the instance.' }
    Assert-MediaFlowTaskOwnership $Task $Instance
    return Get-InstallerTaskActionReceipt $Task 'python_host' $Instance.TaskName
}
function Get-InstallerCandidateTaskReceipt {
    param($Instance, [string]$TargetRoot)
    $platform=Join-Path $TargetRoot 'resources\platform'
    $profilePath=$Instance.ProfilePath
    if (-not $profilePath) { $profilePath=Join-Path $Instance.DataRoot 'instance-profile.json' }
    $task=[pscustomobject]@{Actions=@([pscustomobject]@{
        Execute=(Join-Path $platform 'runtime\python\pythonw.exe'); WorkingDirectory=$platform
        Arguments=('"'+(Join-Path $platform 'fixed_runner\background_host.py')+'" run --instance-profile "'+$profilePath+'"')
    })}
    return Get-InstallerTaskActionReceipt $task 'python_host' $Instance.TaskName
}
function ConvertFrom-InstallerTaskXml {
    param([string]$Text, [string]$TaskName)
    if ([string]::IsNullOrWhiteSpace($Text) -or $Text.Length -gt 1048576) { throw 'Invalid saved background task XML.' }
    $settings=New-Object System.Xml.XmlReaderSettings
    $settings.DtdProcessing=[System.Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver=$null
    $inputText=New-Object IO.StringReader($Text)
    $reader=[System.Xml.XmlReader]::Create($inputText,$settings)
    try {
        $document=New-Object System.Xml.XmlDocument
        $document.XmlResolver=$null
        $document.Load($reader)
    } finally { $reader.Dispose(); $inputText.Dispose() }
    $actions=@($document.SelectNodes('/*[local-name()="Task"]/*[local-name()="Actions"]/*'))
    if ($actions.Count -ne 1 -or $actions[0].LocalName -ne 'Exec') { throw 'Saved task XML must contain exactly one executable action.' }
    $action=$actions[0]
    return [pscustomobject]@{TaskName=$TaskName;Actions=@([pscustomobject]@{
        Execute=[string]$action.SelectSingleNode('*[local-name()="Command"]').InnerText
        Arguments=[string]$action.SelectSingleNode('*[local-name()="Arguments"]').InnerText
        WorkingDirectory=[string]$action.SelectSingleNode('*[local-name()="WorkingDirectory"]').InnerText
    })}
}
function Assert-InstallerRollbackTaskOwnership {
    param($State)
    $registered=Get-ScheduledTask -TaskName $State.task_name -ErrorAction SilentlyContinue
    if ($registered -and -not (Test-InstallerTaskActionReceipt $registered $State.old_task_action) -and
        -not (Test-InstallerTaskActionReceipt $registered $State.new_task_action)) {
        throw 'The current background task is not an action verified by this installation transaction. It was not removed or replaced.'
    }
    return $registered
}
function Restore-InstallerTransaction {
    param($State)
    if (-not $State.mutation_started) { return }
    # Inspect task ownership before moving programs or restoring configuration.
    # Exact persisted receipts also work after the executable/profile was moved.
    $registered=Assert-InstallerRollbackTaskOwnership $State
    $savedTaskXml=$null
    if ($State.task_xml) {
        if (-not $State.old_task_action -or -not $State.task_xml_sha256 -or
            -not (Test-Path -LiteralPath $State.task_xml -PathType Leaf) -or
            (Get-FileHash -LiteralPath $State.task_xml -Algorithm SHA256).Hash -ne $State.task_xml_sha256) { throw 'The saved background task has no valid transaction receipt. Restore was not attempted.' }
        $savedTaskXml=Get-Content -LiteralPath $State.task_xml -Raw -Encoding UTF8
        $savedTask=ConvertFrom-InstallerTaskXml $savedTaskXml $State.task_name
        if (-not (Test-InstallerTaskActionReceipt $savedTask $State.old_task_action)) { throw 'The saved task XML does not match its verified action.' }
    }
    $target = [IO.Path]::GetFullPath($State.install_root)
    if ($State.committed -and (Test-Path -LiteralPath $target)) {
        $failed = $target + '.failed-' + [guid]::NewGuid().ToString('N')
        Move-Item -LiteralPath $target -Destination $failed
    }
    if ($State.program_changed -and $State.program_backup -and (Test-Path -LiteralPath $State.program_backup)) { Copy-InstallerTree $State.program_backup $target }
    if ($State.committed) { Restore-RegistryBackup $State.install_key $State.install_registry; Restore-RegistryBackup $State.uninstall_key $State.uninstall_registry }
    if ($State.profile_backup -and (Test-Path -LiteralPath $State.profile_backup)) { Copy-Item -LiteralPath $State.profile_backup -Destination $State.profile_path -Force }
    # Re-check immediately before task mutation; never overwrite a task that
    # appeared or changed while the program/profile was being restored.
    $registered=Assert-InstallerRollbackTaskOwnership $State
    if ($registered) { Unregister-ScheduledTask -TaskName $State.task_name -Confirm:$false }
    if ($savedTaskXml) {
        if (Assert-InstallerRollbackTaskOwnership $State) { throw 'A background task appeared during rollback. It was not overwritten.' }
        # No -Force: if another task appears after the final query, registration
        # must fail rather than overwrite an unverified concurrent replacement.
        Register-ScheduledTask -TaskName $State.task_name -Xml $savedTaskXml | Out-Null
    }
    foreach ($shortcut in @($State.shortcuts)) {
        if (Test-Path -LiteralPath $shortcut.backup) { Copy-Item -LiteralPath $shortcut.backup -Destination $shortcut.path -Force }
        elseif ($State.committed -and (Test-Path -LiteralPath $shortcut.path)) { Remove-Item -LiteralPath $shortcut.path -Force }
    }
    $State.phase = 'rolled_back'
    Write-InstallerJson $StatePath $State
    Write-InstallerJson (Join-Path $State.backup_root 'transaction.json') $State
}

if ($FunctionsOnly) { return }
try {
    if ($Action -eq 'Rollback') {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        Restore-InstallerTransaction $state
        [pscustomobject]@{ ok=$true; phase='rolled_back'; recovery=$state.backup_root } | ConvertTo-Json
        exit 0
    }
    $instance = Get-InstallerInstance
    if ($Action -eq 'Resolve') {
        if ($IniPath) {
            $lines = @('[instance]', ('id='+$instance.Id), ('install_root='+$InstallRoot), ('profile='+$instance.ProfilePath), ('install_key='+$instance.InstallRegistryKey), ('uninstall_key='+$instance.UninstallRegistryKey))
            [IO.File]::WriteAllLines($IniPath,$lines,[Text.Encoding]::Unicode)
        }
        $instance | Select-Object Id,InstallRoot,ProfilePath,TaskName,DataRoot | ConvertTo-Json
        exit 0
    }
    if ($Action -eq 'Prepare') {
        if (-not $CandidateRoot -or -not $StatePath) { throw 'CandidateRoot and StatePath are required.' }
        $candidate = [IO.Path]::GetFullPath($CandidateRoot)
        if (-not $candidate.StartsWith($InstallRoot + '.candidate-',[StringComparison]::OrdinalIgnoreCase)) { throw 'Candidate program must be the installation sibling prepared by NSIS.' }
        $candidatePlatform = Join-Path $candidate 'resources\platform'
        $candidateIdentity = Assert-ProgramIdentity $candidate -AllowDirty:$instance.Acceptance
        foreach ($file in @('MediaFlow.exe','resources\platform\runtime\python\pythonw.exe','resources\platform\runtime\node\node.exe','resources\platform\runtime\platform-tools\adb.exe')) {
            if (-not (Test-Path -LiteralPath (Join-Path $candidate $file))) { throw ('Candidate is incomplete: '+$file) }
        }
        $backup = Join-Path (Split-Path $instance.DataRoot -Parent) ('installer-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $backup | Out-Null
        $state = [ordered]@{ phase='prepared'; committed=$false; mutation_started=$false; program_changed=$false; install_root=$InstallRoot; candidate_root=$candidate; backup_root=$backup; program_backup=$null; task_name=$instance.TaskName; task_xml=$null; task_xml_sha256=$null; old_task_action=$null; new_task_action=(Get-InstallerCandidateTaskReceipt $instance $InstallRoot); install_key=$instance.InstallRegistryKey; uninstall_key=$instance.UninstallRegistryKey; install_registry=(Get-RegistryBackup $instance.InstallRegistryKey); uninstall_registry=(Get-RegistryBackup $instance.UninstallRegistryKey); shortcuts=@(); profile_path=$instance.ProfilePath; profile_backup=$null; legacy=$false }
        if ($instance.ProfilePath -and (Test-Path -LiteralPath $instance.ProfilePath)) { $state.profile_backup=Join-Path $backup 'instance-profile.json'; Copy-Item -LiteralPath $instance.ProfilePath -Destination $state.profile_backup }
        foreach ($folder in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))) {
            $path = Join-Path $folder ($instance.ShortcutName+'.lnk')
            $copy = Join-Path $backup ((Split-Path $folder -Leaf)+'-'+$instance.ShortcutName+'.lnk')
            if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination $copy }
            $state.shortcuts += @{ path=$path; backup=$copy }
        }
        $old = $null
        if (Test-Path -LiteralPath $InstallRoot) {
            $old = Assert-ProgramIdentity $InstallRoot -AllowDirty:$instance.Acceptance
            $state.legacy = $old.Legacy
            if ($old.Legacy -and $instance.Id -ne 'production') { throw 'Dev.44 cannot be migrated as an isolated instance; its executable ignores profiles.' }
            $state.program_backup = Join-Path $backup 'program'
            Copy-InstallerTree $InstallRoot $state.program_backup
            if ($old.Legacy) { $rollbackInstaller=Save-LegacyRollbackInstaller $backup $instance.DataRoot }
        }
        $task = Get-ScheduledTask -TaskName $instance.TaskName -ErrorAction SilentlyContinue
        if ($task) {
            $state.old_task_action=Get-VerifiedInstallerTaskReceipt $task $instance $old
            $exportedTaskXml=Export-ScheduledTask -TaskName $instance.TaskName
            if (-not (Test-InstallerTaskActionReceipt (ConvertFrom-InstallerTaskXml $exportedTaskXml $instance.TaskName) $state.old_task_action)) { throw 'The exported background task changed during verification.' }
            $state.task_xml = Join-Path $backup 'background-task.xml'
            [IO.File]::WriteAllText($state.task_xml,$exportedTaskXml,[Text.UTF8Encoding]::new($false))
            $state.task_xml_sha256=(Get-FileHash -LiteralPath $state.task_xml -Algorithm SHA256).Hash
        }
        Write-InstallerJson $StatePath $state
        Write-InstallerJson (Join-Path $backup 'transaction.json') $state
        $state.mutation_started = $true
        Write-InstallerJson $StatePath $state
        if ($task) {
            $currentTask=Get-ScheduledTask -TaskName $instance.TaskName -ErrorAction SilentlyContinue
            if (-not (Test-InstallerTaskActionReceipt $currentTask $state.old_task_action)) { throw 'The background task changed before safe installation; it was not disabled.' }
            Disable-ScheduledTask -TaskName $instance.TaskName | Out-Null
        }
        if ($old) {
            Invoke-SafeInstallerStop $instance $old.Platform $candidatePlatform
            if (@(Get-OwnedInstallerProcesses $InstallRoot).Count) { throw 'Existing desktop/program processes are still open. Close this instance and retry.' }
        }
        if ($task) {
            $currentTask=Get-ScheduledTask -TaskName $instance.TaskName -ErrorAction SilentlyContinue
            if ($currentTask -and -not (Test-InstallerTaskActionReceipt $currentTask $state.old_task_action)) { throw 'The background task changed during safe installation; it was not removed.' }
            if ($currentTask) { Unregister-ScheduledTask -TaskName $instance.TaskName -Confirm:$false }
        }
        if ($old -and $old.Legacy) {
            $state.program_changed=$true
            Write-InstallerJson $StatePath $state
            $updater = Join-Path $InstallRoot 'Update.exe'
            $process = Start-Process -FilePath $updater -ArgumentList 'uninstall --silent' -PassThru -WindowStyle Hidden
            if (-not $process.WaitForExit(120000) -or $process.ExitCode -ne 0) { throw 'The verified old uninstaller did not finish successfully.' }
            if (Test-Path -LiteralPath (Join-Path $InstallRoot 'current')) { throw 'Legacy current directory remains; no new task or shortcuts were registered.' }
        }
        $state.phase = 'old_removed'
        Write-InstallerJson $StatePath $state
        Write-InstallerJson (Join-Path $backup 'transaction.json') $state
        [pscustomobject]@{ ok=$true; phase=$state.phase; recovery=$backup } | ConvertTo-Json
        exit 0
    }
    if ($Action -eq 'Commit') {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($state.install_root -ne $InstallRoot -or $state.phase -ne 'old_removed') { throw 'Install transaction identity/order mismatch.' }
        $state.program_changed=$true
        Write-InstallerJson $StatePath $state
        if (Test-Path -LiteralPath $InstallRoot) {
            $retired = Join-Path $state.backup_root 'retired-program'
            Move-Item -LiteralPath $InstallRoot -Destination $retired
        }
        Move-Item -LiteralPath $state.candidate_root -Destination $InstallRoot
        $state.committed = $true
        Write-InstallerJson $StatePath $state
        if (-not $instance.ProfilePath) { $instance.ProfilePath = Join-Path $instance.DataRoot 'instance-profile.json' }
        $profile = if ($instance.Profile) { $instance.Profile } else { [pscustomobject]@{schema=1;id=$instance.Id;data_root=$instance.DataRoot;ui_port=$instance.UiPort;api_port=$instance.ApiPort;stream_port=$instance.StreamPort;task_name=$instance.TaskName;acceptance=$false} }
        $profile | Add-Member -NotePropertyName app_root -NotePropertyValue (Join-Path $InstallRoot 'resources\platform') -Force
        $profile | Add-Member -NotePropertyName install_root -NotePropertyValue $InstallRoot -Force
        Write-InstallerJson $instance.ProfilePath $profile
        [IO.File]::WriteAllText((Join-Path $InstallRoot 'resources\instance-profile.path'),$instance.ProfilePath,[Text.UTF8Encoding]::new($false))
        & (Join-Path $InstallRoot 'resources\platform\install-mediaflow-background.ps1') -NoStart -AppRoot (Join-Path $InstallRoot 'resources\platform') -LauncherPath (Join-Path $InstallRoot 'MediaFlow.exe') -InstanceProfilePath $instance.ProfilePath | Out-Null
        $installKey = 'HKCU:\'+$instance.InstallRegistryKey
        $uninstallKey = 'HKCU:\'+$instance.UninstallRegistryKey
        New-Item -Path $installKey -Force | Out-Null
        New-Item -Path $uninstallKey -Force | Out-Null
        foreach ($pair in @{InstallLocation=$InstallRoot;InstanceProfilePath=$instance.ProfilePath;Version=$Version}.GetEnumerator()) { New-ItemProperty -LiteralPath $installKey -Name $pair.Key -Value $pair.Value -PropertyType String -Force | Out-Null }
        $uninstaller = Join-Path $InstallRoot 'Uninstall MediaFlow.exe'
        $values = @{ DisplayName=$instance.ShortcutName; DisplayVersion=$Version; Publisher='MediaFlow'; InstallLocation=$InstallRoot; DisplayIcon=(Join-Path $InstallRoot 'MediaFlow.exe'); UninstallString=('"'+$uninstaller+'"'); QuietUninstallString=('"'+$uninstaller+'" /S') }
        foreach ($pair in $values.GetEnumerator()) { New-ItemProperty -LiteralPath $uninstallKey -Name $pair.Key -Value $pair.Value -PropertyType String -Force | Out-Null }
        # Match the profile-aware updater adapter. Seed the old installer needed
        # for the first actual differential update; never use a shared cache.
        if ($InstallerPath -and (Test-Path -LiteralPath $InstallerPath)) {
            $updateConfig=Get-Content -LiteralPath (Join-Path $InstallRoot 'resources\app-update.yml') -Raw -Encoding UTF8
            $match=[regex]::Match($updateConfig,'(?m)^updaterCacheDirName:\s*([^\r\n]+)')
            if (-not $match.Success) { throw 'Packaged updater cache identity is missing.' }
            $cacheName=$match.Groups[1].Value.Trim().Trim('"').Trim("'")
            if ($cacheName -notmatch '^[A-Za-z0-9._-]+$') { throw 'Invalid packaged updater cache identity.' }
            $cache=Join-Path $instance.DataRoot ('desktop\'+$instance.Id+'\update-cache\'+$cacheName)
            New-Item -ItemType Directory -Force -Path $cache | Out-Null
            Copy-Item -LiteralPath $InstallerPath -Destination (Join-Path $cache 'installer.exe') -Force
            $blockmap=Join-Path $cache 'current.blockmap'
            $pendingBlockmap=Join-Path $cache 'pending\current.blockmap'
            if (Test-Path -LiteralPath ($InstallerPath+'.blockmap')) { Copy-Item -LiteralPath ($InstallerPath+'.blockmap') -Destination $blockmap -Force }
            elseif (Test-Path -LiteralPath $pendingBlockmap) { Copy-Item -LiteralPath $pendingBlockmap -Destination $blockmap -Force }
            elseif (Test-Path -LiteralPath $blockmap) { Remove-Item -LiteralPath $blockmap -Force }
        }
        $state.phase='installed'; $state.profile_path=$instance.ProfilePath
        Write-InstallerJson $StatePath $state
        Write-InstallerJson (Join-Path $state.backup_root 'transaction.json') $state
        [pscustomobject]@{ok=$true;phase='installed';recovery=$state.backup_root;instance=$instance.Id} | ConvertTo-Json
        exit 0
    }
    if ($Action -eq 'Remove') {
        $identity = Assert-ProgramIdentity $InstallRoot -AllowDirty:$instance.Acceptance
        if ($identity.Legacy) { throw 'Use the verified Velopack uninstaller for a legacy installation.' }
        $uiProcesses = @(Get-OwnedInstallerProcesses $InstallRoot | Where-Object { $_.Name -eq 'MediaFlow.exe' })
        if ($uiProcesses.Count) { throw 'Close this MediaFlow interface before uninstalling.' }
        & (Join-Path $instance.AppRoot 'uninstall-mediaflow-background.ps1') -AppRoot $instance.AppRoot -LauncherPath $instance.LauncherPath -InstanceProfilePath $instance.ProfilePath | Out-Null
        if (@(Get-OwnedInstallerProcesses $InstallRoot).Count) { throw 'Close this instance before uninstalling; its program is still open.' }
        foreach ($key in @($instance.InstallRegistryKey,$instance.UninstallRegistryKey)) {
            $path='HKCU:\'+$key
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
        }
        [pscustomobject]@{ok=$true;data_preserved=$true} | ConvertTo-Json
        exit 0
    }
} catch {
    $message = $_.Exception.Message
    if ($Action -in @('Prepare','Commit') -and $StatePath -and (Test-Path -LiteralPath $StatePath)) {
        try {
            $failure=Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
            $failure | Add-Member -NotePropertyName last_error -NotePropertyValue $message -Force
            $failure | Add-Member -NotePropertyName failure_action -NotePropertyValue $Action -Force
            Write-InstallerJson $StatePath $failure
            Write-InstallerJson (Join-Path $failure.backup_root 'transaction.json') $failure
            Restore-InstallerTransaction $failure
        } catch { $message += '; automatic restore failed: '+$_.Exception.Message }
    }
    [Console]::Error.WriteLine($message)
    exit 1
}
