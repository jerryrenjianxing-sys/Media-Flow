param([string]$ProjectRoot = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent))
$ErrorActionPreference='Stop'
$scratch=Join-Path $ProjectRoot ('work\electron-acceptance\helper-tests-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
$env:LOCALAPPDATA=Join-Path $scratch 'local-app-data'
$env:MEDIAFLOW_INSTANCE_PROFILE=''
. (Join-Path $PSScriptRoot 'installer-helper.ps1') -FunctionsOnly
$global:Passed=0
function Assert-True { param($Value,[string]$Message); if (-not $Value) { throw $Message }; $global:Passed++ }
function Assert-Throws { param([scriptblock]$Body,[string]$Message); $thrown=$false; try { & $Body | Out-Null } catch { $thrown=$true }; Assert-True $thrown $Message }
function Assert-OwnsTask { param($Task,$Instance,[string]$Message); Assert-MediaFlowTaskOwnership $Task $Instance; Assert-True $true $Message }
function Save-Profile { param($Profile); [IO.File]::WriteAllText($global:ProfileFile,($Profile|ConvertTo-Json),[Text.UTF8Encoding]::new($false)) }
$appRoot=Join-Path $scratch 'app\resources\platform'
$launcher=Join-Path $scratch 'app\MediaFlow.exe'
$global:ProfileFile=Join-Path $scratch 'profile.json'
$profile=[pscustomobject]@{ schema=1;id='acceptance-test';data_root=(Join-Path $scratch 'data');ui_port=31301;api_port=49338;stream_port=49339;task_name='MediaFlow Background Acceptance';acceptance=$true;install_root=(Join-Path $scratch 'app') }
Save-Profile $profile
$instance=Get-MediaFlowInstance -AppRoot $appRoot -LauncherPath $launcher -InstanceProfilePath $ProfileFile
Assert-True ($instance.Id -eq 'acceptance-test' -and $instance.TaskName -eq 'MediaFlow Background Acceptance') 'Profile identity was lost.'
Assert-True ($instance.InstallRegistryKey -eq 'Software\MediaFlow\Desktop\acceptance-test') 'Acceptance shares production registry.'
Assert-True ($instance.ShortcutName -ne 'MediaFlow') 'Acceptance shares production shortcut.'
Assert-Throws { Get-MediaFlowInstance -AppRoot $appRoot -InstanceProfilePath (Join-Path $scratch 'missing.json') } 'Missing profile fell back to production.'
foreach($field in @('ui_port','api_port','stream_port')) {
    $old=$profile.$field; $profile.$field=48138; Save-Profile $profile
    Assert-Throws { Get-MediaFlowInstance -AppRoot $appRoot -InstanceProfilePath $ProfileFile } 'Production port was accepted.'
    $profile.$field=$old
}
$profile.task_name='MediaFlow Background';Save-Profile $profile
Assert-Throws { Get-MediaFlowInstance -AppRoot $appRoot -InstanceProfilePath $ProfileFile } 'Production task was accepted.'
$profile.task_name='MediaFlow Background Acceptance'
$oldData=$profile.data_root;$profile.data_root=Join-Path $env:LOCALAPPDATA 'MediaFlow\data\nested';Save-Profile $profile
Assert-Throws { Get-MediaFlowInstance -AppRoot $appRoot -InstanceProfilePath $ProfileFile } 'Production data descendant was accepted.'
$profile.data_root=$oldData
$profile.acceptance=$false;Save-Profile $profile
Assert-Throws { Get-MediaFlowInstance -AppRoot $appRoot -InstanceProfilePath $ProfileFile } 'Nonproduction was allowed to enable live device behavior.'
$profile.acceptance=$true;Save-Profile $profile
New-Item -ItemType Directory -Force -Path (Join-Path $appRoot 'runtime\python'),(Join-Path $appRoot 'fixed_runner'),(Join-Path $appRoot 'installer\electron') | Out-Null
foreach($file in @((Join-Path $appRoot 'runtime\python\pythonw.exe'),(Join-Path $appRoot 'fixed_runner\background_host.py'),$launcher)) { [IO.File]::WriteAllBytes($file,[byte[]]@(0)) }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'instance-profile.ps1') -Destination (Join-Path $appRoot 'installer\electron\instance-profile.ps1')
$global:FakeTask=$null;$global:Registrations=@()
function Get-ScheduledTask {
    param($TaskName,$ErrorAction)
    if ($global:TaskSnapshots -and $global:TaskSnapshots.Count) { return $global:TaskSnapshots.Dequeue() }
    return $global:FakeTask
}
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory); return [pscustomobject]@{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory} }
function New-ScheduledTaskTrigger { [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { [pscustomobject]@{} }
function New-ScheduledTask { param($Action,$Trigger,$Principal,$Settings,$Description); return [pscustomobject]@{Actions=@($Action);Description=$Description} }
function Register-ScheduledTask {
    param($TaskName,$InputObject,[switch]$Force,$Xml)
    if ($global:ExpectedProfileAtRegistration -and (Get-FileHash -LiteralPath $global:ExpectedProfileAtRegistration.Path -Algorithm SHA256).Hash -ne $global:ExpectedProfileAtRegistration.Hash) { throw 'The old profile was not restored before task registration.' }
    $global:Registrations += [pscustomobject]@{TaskName=$TaskName;Task=$InputObject;Xml=$Xml;Force=$Force.IsPresent}
}
$installed=& (Join-Path $ProjectRoot 'install-mediaflow-background.ps1') -NoStart -NoShortcut -AppRoot $appRoot -LauncherPath $launcher -InstanceProfilePath $ProfileFile | ConvertFrom-Json
Assert-True ($global:Registrations.Count -eq 1) 'Task was not registered exactly once.'
Assert-True ($installed.Execute -eq (Join-Path $appRoot 'runtime\python\pythonw.exe')) 'Task is attached to the GUI or system Python.'
Assert-True ($installed.Arguments -eq ('"'+(Join-Path $appRoot 'fixed_runner\background_host.py')+'" run --instance-profile "'+$ProfileFile+'"')) 'Scheduled host lost stable external profile arguments.'
Assert-True ($global:Registrations[0].TaskName -eq 'MediaFlow Background Acceptance') 'The production task was changed.'
$ownedTask=$global:Registrations[0].Task
Assert-OwnsTask $ownedTask $instance 'The existing dev.48 task was rejected.'
$ownedScript=Join-Path $appRoot 'fixed_runner\background_host.py'
$ownedPython=Join-Path $appRoot 'runtime\python\pythonw.exe'
function New-OwnershipTask {
    param([string]$Execute=$ownedPython,[string]$Arguments=('"'+$ownedScript+'" run --instance-profile "'+$ProfileFile+'"'),[string]$WorkingDirectory=$appRoot)
    return [pscustomobject]@{Actions=@([pscustomobject]@{Execute=$Execute;Arguments=$Arguments;WorkingDirectory=$WorkingDirectory})}
}
$caseTask=New-OwnershipTask -Execute $ownedPython.ToUpperInvariant() -WorkingDirectory $appRoot.ToUpperInvariant() -Arguments ('"'+$ownedScript.ToUpperInvariant()+'" run --instance-profile "'+$ProfileFile.ToUpperInvariant()+'"')
Assert-OwnsTask $caseTask $instance 'Windows path letter case changed task ownership.'
$lowerInstance=Get-MediaFlowInstance -AppRoot $appRoot.ToLowerInvariant() -LauncherPath $launcher.ToLowerInvariant() -InstanceProfilePath $ProfileFile.ToLowerInvariant()
Assert-OwnsTask $ownedTask $lowerInstance 'The Electron lower-case caller rejected the original registered task.'
$wrongNameTask=New-OwnershipTask
$wrongNameTask|Add-Member -NotePropertyName TaskName -NotePropertyValue 'MediaFlow Background Other'
Assert-Throws { Assert-MediaFlowTaskOwnership $wrongNameTask $instance } 'A different registered task name was accepted.'
$slashTask=New-OwnershipTask -Execute $ownedPython.Replace('\','/') -WorkingDirectory ($appRoot.Replace('\','/')+'/') -Arguments ('"'+$ownedScript.Replace('\','/')+'" run --instance-profile="'+$ProfileFile.Replace('\','/')+'"')
Assert-OwnsTask $slashTask $instance 'Equivalent Windows path separators were rejected.'
$dotScript=Join-Path $appRoot 'fixed_runner\..\fixed_runner\background_host.py'
Assert-OwnsTask (New-OwnershipTask -Arguments ('"'+$dotScript+'" run --instance-profile "'+$ProfileFile+'"')) $instance 'Equivalent absolute dot segments were rejected.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Execute 'C:\foreign\pythonw.exe') $instance } 'A foreign executable with exact expected argument bait was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -WorkingDirectory (Join-Path $scratch 'foreign')) $instance } 'A foreign working directory was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -WorkingDirectory '') $instance } 'An inherited working directory was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'.bak" run --instance-profile "'+$ProfileFile+'"')) $instance } 'A script filename containing the expected script was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"C:\foreign\host.py" run --note "'+$ownedScript+'" --instance-profile "'+$ProfileFile+'"')) $instance } 'The expected script in an unrelated argument was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'" stop --instance-profile "'+$ProfileFile+'"')) $instance } 'A non-run subcommand was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'" run')) $instance } 'An installed task without its explicit profile was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'" run --instance-profile "'+$ProfileFile+'" --instance-profile "'+$ProfileFile+'"')) $instance } 'Duplicate profile arguments were accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'" run --instance-profile "'+$ProfileFile+'" --unexpected')) $instance } 'Extra unknown task arguments were accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'" run --instance-profile')) $instance } 'An empty profile argument was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'" run --instance-profile "'+$ProfileFile+'" --instance-profile="'+$ProfileFile+'"')) $instance } 'Mixed duplicate profile forms were accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'" run --INSTANCE-PROFILE "'+$ProfileFile+'"')) $instance } 'Python option names were incorrectly treated as case-insensitive.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Execute 'C:pythonw.exe') $instance } 'A drive-relative executable was accepted.'
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments (('"'+$ownedScript+'" run --instance-profile "'+$ProfileFile+'"')+"`n")) $instance } 'Control characters were accepted in task arguments.'
$multiple=[pscustomobject]@{Actions=@($ownedTask.Actions[0],$ownedTask.Actions[0])}
Assert-Throws { Assert-MediaFlowTaskOwnership $multiple $instance } 'Multiple scheduled task actions were accepted.'
$otherProfile=Join-Path $scratch 'other-profile.json'
Write-InstallerJson $otherProfile $profile
Assert-Throws { Assert-MediaFlowTaskOwnership (New-OwnershipTask -Arguments ('"'+$ownedScript+'" run --instance-profile "'+$otherProfile+'"')) $instance } 'Another profile file with identical content was accepted.'
foreach($field in @('id','data_root','ui_port','api_port','stream_port','task_name','acceptance','app_root','install_root')) {
    $changed=$profile|ConvertTo-Json|ConvertFrom-Json
    $value=switch($field) {
        'id' { 'acceptance-other' }
        'data_root' { Join-Path $scratch 'other-data' }
        'ui_port' { 31302 }
        'api_port' { 49340 }
        'stream_port' { 49341 }
        'task_name' { 'MediaFlow Background Other' }
        'acceptance' { $false }
        'app_root' { Join-Path $scratch 'other-app' }
        'install_root' { Join-Path $scratch 'other-install' }
    }
    $changed|Add-Member -NotePropertyName $field -NotePropertyValue $value -Force
    Save-Profile $changed
    Assert-Throws { Assert-MediaFlowTaskOwnership $ownedTask $instance } ('Profile field changed without being rejected: '+$field)
    Save-Profile $profile
}
$unicodeName=([string][char]0x4E2D+[char]0x6587)+' space'
$unicodeRoot=Join-Path $scratch $unicodeName
$unicodeApp=Join-Path $unicodeRoot 'installed app\resources\platform'
$unicodeProfile=Join-Path $unicodeRoot 'instance profile.json'
New-Item -ItemType Directory -Force -Path $unicodeRoot | Out-Null
$unicodePayload=$profile|ConvertTo-Json|ConvertFrom-Json
$unicodePayload.data_root=Join-Path $unicodeRoot 'user data'
$unicodePayload.install_root=Join-Path $unicodeRoot 'installed app'
$unicodePayload|Add-Member -NotePropertyName app_root -NotePropertyValue $unicodeApp -Force
Write-InstallerJson $unicodeProfile $unicodePayload
$unicodeInstance=Get-MediaFlowInstance -AppRoot $unicodeApp -LauncherPath (Join-Path $unicodePayload.install_root 'MediaFlow.exe') -InstanceProfilePath $unicodeProfile
$unicodeTask=[pscustomobject]@{Actions=@([pscustomobject]@{Execute=(Join-Path $unicodeApp 'runtime\python\pythonw.exe');WorkingDirectory=$unicodeApp;Arguments=('"'+(Join-Path $unicodeApp 'fixed_runner\background_host.py')+'" run --instance-profile "'+$unicodeProfile+'"')})}
Assert-OwnsTask $unicodeTask $unicodeInstance 'Quoted Chinese paths containing spaces were rejected.'
$parsed=@(Split-MediaFlowWindowsArguments '"path with space.py" run --instance-profile "C:\data path\profile.json"')
Assert-True ($parsed.Count -eq 4 -and $parsed[0] -eq 'path with space.py' -and $parsed[3] -eq 'C:\data path\profile.json') 'Native Windows argv parsing lost argument boundaries.'
$global:FakeTask=[pscustomobject]@{Actions=@([pscustomobject]@{Execute='C:\foreign\MediaFlow.exe';Arguments='--background-run'})}
Assert-Throws { & (Join-Path $ProjectRoot 'install-mediaflow-background.ps1') -NoStart -NoShortcut -AppRoot $appRoot -LauncherPath $launcher -InstanceProfilePath $ProfileFile } 'A foreign background task was overwritten.'
Assert-True ($global:Registrations.Count -eq 1) 'Rejected install still registered a task.'
$global:Copied=$false
function Copy-InstallerTree { param($Source,$Target);$global:Copied=$true }
Restore-InstallerTransaction ([pscustomobject]@{mutation_started=$false;program_backup=$scratch;install_root=(Join-Path $scratch 'app')})
Assert-True (-not $global:Copied) 'An idle refusal modified the running program during rollback.'
function Restore-RegistryBackup { param($Key,$Backup); throw 'A failed stop must not rewrite registry.' }
function Unregister-ScheduledTask { param($TaskName,[switch]$Confirm); $global:RemovedTask=$TaskName; $global:RemovalCount++; $global:FakeTask=$null }
function New-TestTaskXml {
    param($Task)
    $action=@($Task.Actions)[0]
    return '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Actions Context="Author"><Exec><Command>'+[Security.SecurityElement]::Escape($action.Execute)+'</Command><Arguments>'+[Security.SecurityElement]::Escape($action.Arguments)+'</Arguments><WorkingDirectory>'+[Security.SecurityElement]::Escape($action.WorkingDirectory)+'</WorkingDirectory></Exec></Actions></Task>'
}
$taskXml=Join-Path $scratch 'saved-task.xml'
$originalTaskXml=New-TestTaskXml $ownedTask
[IO.File]::WriteAllText($taskXml,$originalTaskXml,[Text.UTF8Encoding]::new($false))
$oldReceipt=Get-VerifiedInstallerTaskReceipt $ownedTask $instance $null
$newReceipt=Get-InstallerCandidateTaskReceipt $instance (Join-Path $scratch 'app')
Assert-True (Test-InstallerTaskActionReceipt $caseTask $oldReceipt) 'An equivalent action with different case did not match its verified receipt.'
Assert-True (Test-InstallerTaskActionReceipt $slashTask $oldReceipt) 'Equivalent argv/profile forms did not match their verified receipt.'
Assert-True (-not (Test-InstallerTaskActionReceipt (New-OwnershipTask -Execute 'C:\foreign\pythonw.exe') $oldReceipt)) 'A foreign executable matched an installer receipt.'
Assert-True (-not (Test-InstallerTaskActionReceipt (New-OwnershipTask -WorkingDirectory (Join-Path $scratch 'app-other')) $oldReceipt)) 'A sibling working directory matched an installer receipt.'
Assert-True (-not (Test-InstallerTaskActionReceipt (New-OwnershipTask -Arguments ('"'+$ownedScript+'" run --instance-profile "'+$ProfileFile+'" --note "'+$appRoot+'"')) $oldReceipt)) 'Extra path bait matched an installer receipt.'
Assert-Throws { Get-VerifiedInstallerTaskReceipt (New-OwnershipTask -Execute 'C:\foreign\pythonw.exe') $instance $null } 'Prepare accepted a foreign task with the expected root in arguments.'
Assert-Throws { Get-VerifiedInstallerTaskReceipt $ownedTask $instance ([pscustomobject]@{Legacy=$false;Platform=(Join-Path $scratch 'foreign-platform')}) } 'Prepare accepted a different old program directory.'
Assert-Throws { ConvertFrom-InstallerTaskXml '<!DOCTYPE Task [<!ENTITY x "bad">]><Task>&x;</Task>' $instance.TaskName } 'Task XML allowed a DTD.'
Assert-Throws { ConvertFrom-InstallerTaskXml '<Task><Actions><Exec/><Exec/></Actions></Task>' $instance.TaskName } 'Task XML accepted multiple actions.'
Assert-True (Test-InstallerTaskActionReceipt (ConvertFrom-InstallerTaskXml $originalTaskXml $instance.TaskName) $oldReceipt) 'Exported task XML lost its exact verified action.'
$savedProfile=Join-Path $scratch 'saved-profile.json'
Copy-Item -LiteralPath $ProfileFile -Destination $savedProfile
$changedProfile=$profile|ConvertTo-Json|ConvertFrom-Json
$changedProfile.data_root=Join-Path $scratch 'wrong-current-data'
Save-Profile $changedProfile
$state=[pscustomobject]@{mutation_started=$true;program_changed=$false;committed=$false;program_backup=$scratch;install_root=(Join-Path $scratch 'app');task_name='MediaFlow Background Acceptance';task_xml=$taskXml;task_xml_sha256=(Get-FileHash -LiteralPath $taskXml -Algorithm SHA256).Hash;old_task_action=$oldReceipt;new_task_action=$newReceipt;profile_backup=$savedProfile;profile_path=$ProfileFile;shortcuts=@();backup_root=$scratch;phase='prepared'}
$StatePath=Join-Path $scratch 'failed-stop-transaction.json'
$global:FakeTask=$ownedTask
$global:RemovalCount=0
$global:ExpectedProfileAtRegistration=[pscustomobject]@{Path=$ProfileFile;Hash=(Get-FileHash -LiteralPath $savedProfile -Algorithm SHA256).Hash}
Restore-InstallerTransaction $state
Assert-True (-not $global:Copied) 'A stop failure overwrote still-running program files.'
Assert-True ($global:RemovedTask -eq 'MediaFlow Background Acceptance') 'A task restore affected a foreign task.'
Assert-True ($global:Registrations[-1].Xml -eq $originalTaskXml) 'The old exact task definition was not restored.'
Assert-True (-not $global:Registrations[-1].Force) 'Rollback could overwrite a task created concurrently after the final ownership query.'
Assert-True ((Get-FileHash -LiteralPath $ProfileFile -Algorithm SHA256).Hash -eq $global:ExpectedProfileAtRegistration.Hash) 'Rollback did not restore the old profile.'
$global:ExpectedProfileAtRegistration=$null
$registrationsBefore=$global:Registrations.Count
$removalsBefore=$global:RemovalCount
$global:FakeTask=New-OwnershipTask -Execute 'C:\foreign\pythonw.exe'
$state.committed=$true;$state.program_changed=$true
Assert-Throws { Restore-InstallerTransaction $state } 'Rollback accepted a foreign task with the installation path in arguments.'
Assert-True ($global:Registrations.Count -eq $registrationsBefore -and $global:RemovalCount -eq $removalsBefore) 'Rejected rollback mutated the foreign task.'
Assert-True (-not $global:Copied -and (Test-Path -LiteralPath $state.install_root)) 'Rejected rollback moved or restored program files before validating ownership.'
$state.committed=$false;$state.program_changed=$false
$global:FakeTask=$ownedTask
$global:TaskSnapshots=New-Object System.Collections.Queue
$global:TaskSnapshots.Enqueue($ownedTask)
$global:TaskSnapshots.Enqueue((New-OwnershipTask -Execute 'C:\foreign\pythonw.exe'))
Assert-Throws { Restore-InstallerTransaction $state } 'Rollback did not re-check a task replaced while restoring files.'
Assert-True ($global:Registrations.Count -eq $registrationsBefore -and $global:RemovalCount -eq $removalsBefore) 'Rollback removed a concurrent foreign replacement.'
$global:TaskSnapshots=$null
$global:FakeTask=$null
Restore-InstallerTransaction $state
Assert-True ($global:Registrations.Count -eq $registrationsBefore+1) 'Rollback could not restore an absent original task.'
$global:FakeTask=$ownedTask
$state.task_xml_sha256='not-the-saved-hash'
$registrationsBefore=$global:Registrations.Count;$removalsBefore=$global:RemovalCount
Assert-Throws { Restore-InstallerTransaction $state } 'Rollback accepted corrupted task XML.'
Assert-True ($global:Registrations.Count -eq $registrationsBefore -and $global:RemovalCount -eq $removalsBefore) 'Invalid task XML changed task registration.'
$state.task_xml_sha256=(Get-FileHash -LiteralPath $taskXml -Algorithm SHA256).Hash
$wrongXml=New-TestTaskXml (New-OwnershipTask -Execute 'C:\foreign\pythonw.exe')
[IO.File]::WriteAllText($taskXml,$wrongXml,[Text.UTF8Encoding]::new($false))
$state.task_xml_sha256=(Get-FileHash -LiteralPath $taskXml -Algorithm SHA256).Hash
Assert-Throws { Restore-InstallerTransaction $state } 'Rollback accepted a different XML action even with a matching file hash.'
[IO.File]::WriteAllText($taskXml,$originalTaskXml,[Text.UTF8Encoding]::new($false))
$movedRoot=Join-Path $scratch 'nonexistent-moved-program'
$movedReceipt=Get-InstallerCandidateTaskReceipt $instance $movedRoot
$global:FakeTask=[pscustomobject]@{Actions=@([pscustomobject]@{Execute=$movedReceipt.execute;WorkingDirectory=$movedReceipt.working_directory;Arguments=('"'+$movedReceipt.script+'" run --instance-profile "'+$movedReceipt.profile_path+'"')})}
$movedState=[pscustomobject]@{mutation_started=$true;program_changed=$false;committed=$false;install_root=$movedRoot;task_name=$instance.TaskName;task_xml=$null;old_task_action=$null;new_task_action=$movedReceipt;profile_backup=$null;shortcuts=@();backup_root=$scratch;phase='old_removed'}
$removalsBefore=$global:RemovalCount
Restore-InstallerTransaction $movedState
Assert-True ($global:RemovalCount -eq $removalsBefore+1 -and -not (Test-Path -LiteralPath $movedRoot)) 'Rollback depended on the moved executable or profile file.'
$legacyPlatform=Join-Path $scratch 'legacy-program\current'
$legacyInstance=[pscustomobject]@{Id='production';Acceptance=$false;TaskName='MediaFlow Background'}
$legacyProgram=[pscustomobject]@{Legacy=$true;Platform=$legacyPlatform}
$legacyTask=[pscustomobject]@{Actions=@([pscustomobject]@{Execute=(Join-Path $legacyPlatform 'MediaFlow.exe');WorkingDirectory=$legacyPlatform;Arguments='--background-run'})}
$legacyReceipt=Get-VerifiedInstallerTaskReceipt $legacyTask $legacyInstance $legacyProgram
Assert-True (Test-InstallerTaskActionReceipt $legacyTask $legacyReceipt) 'The verified dev.44 launcher action lost its migration compatibility.'
$legacyForeign=[pscustomobject]@{Actions=@([pscustomobject]@{Execute='C:\foreign\MediaFlow.exe';WorkingDirectory=$legacyPlatform;Arguments='--background-run'})}
Assert-Throws { Get-VerifiedInstallerTaskReceipt $legacyForeign $legacyInstance $legacyProgram } 'Legacy migration accepted an external launcher.'
$legacyTask.Actions[0].Arguments='--background-run --note "'+$legacyPlatform+'"'
Assert-Throws { Get-VerifiedInstallerTaskReceipt $legacyTask $legacyInstance $legacyProgram } 'Legacy migration accepted extra argument path bait.'
function Get-CimInstance { param($ClassName); return @([pscustomobject]@{ExecutablePath=(Join-Path $scratch 'app\MediaFlow.exe');Name='MediaFlow.exe'},[pscustomobject]@{ExecutablePath=(Join-Path $scratch 'app-other\MediaFlow.exe');Name='MediaFlow.exe'},[pscustomobject]@{ExecutablePath='C:\other\python.exe';Name='python.exe'}) }
$owned=@(Get-OwnedInstallerProcesses (Join-Path $scratch 'app'))
Assert-True ($owned.Count -eq 1 -and $owned[0].ExecutablePath -eq (Join-Path $scratch 'app\MediaFlow.exe')) 'Process scope matched an unrelated installation.'
$localRoot=Join-Path $scratch 'local-program'
New-Item -ItemType Directory -Force -Path (Join-Path $localRoot 'resources\platform') | Out-Null
Write-InstallerJson (Join-Path $localRoot 'resources\platform\release-manifest.json') @{product='MediaFlow';runtime='win-x64';version='0.4.1-dev.45';source_dirty=$true;source_commit='test-source'}
Assert-Throws { Assert-ProgramIdentity $localRoot } 'Production accepted dirty source.'
Assert-Throws { Assert-ProgramIdentity $localRoot -AllowDirty } 'Dirty acceptance did not require a build marker.'
Write-InstallerJson (Join-Path $localRoot 'resources\local-only-build.json') @{local_only=$true;acceptance_only=$true;version='0.4.1-dev.45';source_commit='test-source'}
$localIdentity=Assert-ProgramIdentity $localRoot -AllowDirty
Assert-True ($localIdentity.Manifest.source_dirty -eq $true) 'The local dirty marker disguised a clean release.'
Assert-Throws { Assert-ProgramIdentity $localRoot } 'A local marker let production install dirty source.'
Write-InstallerJson (Join-Path $localRoot 'resources\local-only-build.json') @{local_only=$true;acceptance_only=$true;version='0.4.1-dev.46';source_commit='test-source'}
Assert-Throws { Assert-ProgramIdentity $localRoot -AllowDirty } 'A mismatching local build identity was accepted.'
Assert-Throws { Assert-ProgramIdentity (Join-Path $scratch 'unverified-program') } 'An unverified folder was accepted as an old installation.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'MediaFlow\bootstrap.json'))) 'Acceptance wrote a production bootstrap.'
$source=Join-Path $scratch 'source-checkout'
New-Item -ItemType Directory -Force -Path (Join-Path $source '.venv\Scripts'),(Join-Path $source 'fixed_runner'),(Join-Path $source 'installer\electron'),(Join-Path $source 'runtime\python') | Out-Null
foreach($name in @('PROJECT.md','fixed_runner\runtime_layout.py','fixed_runner\background_host.py','.venv\Scripts\python.exe','.venv\Scripts\pythonw.exe','runtime\python\python.exe','runtime\python\pythonw.exe')) { [IO.File]::WriteAllBytes((Join-Path $source $name),[byte[]]@(0)) }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'instance-profile.ps1') -Destination (Join-Path $source 'installer\electron\instance-profile.ps1')
$installedData=Join-Path $scratch 'existing-installed-data'
Write-InstallerJson (Join-Path $env:LOCALAPPDATA 'MediaFlow\bootstrap.json') @{data_root=$installedData}
foreach($name in @('MEDIAFLOW_INSTANCE_PROFILE','MEDIAFLOW_DATA_ROOT','RISKFLOW_DATA_ROOT','MEDIAFLOW_DEVELOPMENT_SOURCE')) { Remove-Item ('Env:'+$name) -ErrorAction SilentlyContinue }
$sourceInstance=Get-MediaFlowInstance -AppRoot $source
Assert-True ($sourceInstance.DevelopmentSource -eq $true) 'Explicit source sentinels were not detected.'
Assert-True ($sourceInstance.DataRoot -eq (Join-Path $source 'fixed_runner\runtime')) 'Source management adopted installed bootstrap data.'
Assert-True ((Get-MediaFlowPython $sourceInstance) -eq (Join-Path $source '.venv\Scripts\python.exe')) 'Source management selected a stray bundled interpreter.'
Set-MediaFlowInstanceEnvironment $sourceInstance
Assert-True ($env:MEDIAFLOW_DEVELOPMENT_SOURCE -eq '1' -and $env:MEDIAFLOW_DATA_ROOT -eq (Join-Path $source 'fixed_runner\runtime')) 'Source secret/data environment was not preserved.'
$global:FakeTask=$null
$sourceInstalled=& (Join-Path $ProjectRoot 'install-mediaflow-background.ps1') -NoStart -NoShortcut -AppRoot $source | ConvertFrom-Json
$stableSourceProfile=Get-Content -LiteralPath $sourceInstalled.InstanceProfilePath -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-True ($stableSourceProfile.development_source -eq $true -and $stableSourceProfile.data_root -eq (Join-Path $source 'fixed_runner\runtime')) 'Source scheduled task did not persist its original layout.'
Assert-True ($sourceInstalled.InstanceProfilePath -eq (Join-Path $source 'fixed_runner\runtime\instance-profile.json')) 'Source registration wrote configuration into installed data.'
Assert-True ($sourceInstalled.Execute -eq (Join-Path $source '.venv\Scripts\pythonw.exe')) 'Source registration used a bundled distribution Python.'
$sourceTask=$global:Registrations[-1].Task
Assert-OwnsTask $sourceTask $sourceInstance 'A verified existing source profile could not be re-registered.'
$legacySourceTask=[pscustomobject]@{Actions=@([pscustomobject]@{Execute=$sourceInstalled.Execute;WorkingDirectory=$source;Arguments=('"'+(Join-Path $source 'fixed_runner\background_host.py')+'" run')})}
Assert-OwnsTask $legacySourceTask $sourceInstance 'The verified pre-profile source registration lost compatibility.'
$explicitSource=Get-MediaFlowInstance -AppRoot $source -InstanceProfilePath $sourceInstalled.InstanceProfilePath
Assert-Throws { Assert-MediaFlowTaskOwnership $legacySourceTask $explicitSource } 'A task without a profile bypassed an explicit source profile.'
$sourceWithWrongPython=[pscustomobject]@{Actions=@([pscustomobject]@{Execute=(Join-Path $source 'runtime\python\pythonw.exe');WorkingDirectory=$source;Arguments=$sourceTask.Actions[0].Arguments})}
Assert-Throws { Assert-MediaFlowTaskOwnership $sourceWithWrongPython $sourceInstance } 'Source ownership accepted a stray bundled runtime.'
$profileSource=Get-MediaFlowInstance -AppRoot $source -InstanceProfilePath $ProfileFile
Assert-True (-not $profileSource.DevelopmentSource -and $profileSource.Acceptance) 'An explicit acceptance profile inherited source credentials.'
$pythonOnly=Join-Path $scratch 'python-folder-only'
New-Item -ItemType Directory -Force -Path (Join-Path $pythonOnly 'runtime\python') | Out-Null
[IO.File]::WriteAllBytes((Join-Path $pythonOnly 'runtime\python\python.exe'),[byte[]]@(0))
Remove-Item Env:MEDIAFLOW_INSTANCE_PROFILE -ErrorAction SilentlyContinue
$notSource=Get-MediaFlowInstance -AppRoot $pythonOnly
Assert-True (-not $notSource.DevelopmentSource) 'A Python folder alone was treated as a source checkout.'
$processPath=[Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
$physicalProcess=Get-MediaFlowPhysicalPath $processPath
Assert-True (Test-MediaFlowSamePath $physicalProcess $processPath) 'Native file handles did not resolve the actual PowerShell process image.'
Assert-True (Test-MediaFlowSamePath (Get-MediaFlowPhysicalPath $ProfileFile) $ProfileFile) 'Native file handles did not resolve the actual fixture profile.'
Assert-True (Test-MediaFlowSamePath (Get-MediaFlowPhysicalPath $scratch) $scratch) 'Native file handles did not resolve the actual fixture directory.'
Assert-True ((ConvertTo-MediaFlowAbsolutePath 'C:\') -eq 'C:\') 'Canonicalization changed an absolute drive root into a drive-relative path.'
$packageCache='C:\Users\Example\AppData\Local\Packages\OpenAI.Codex_example\LocalCache\Local'
Assert-True (Test-MediaFlowPackageCachePath $packageCache) 'The Codex package cache was not classified as unstable.'
Assert-True (Test-MediaFlowPackageCachePath $packageCache.ToUpperInvariant()) 'Package cache detection depended on path letter case.'
Assert-True (Test-MediaFlowPackageCachePath 'D:\Packages\Other.App_test\LocalCache\profile.json') 'Another MSIX package cache was not classified as unstable.'
Assert-True (-not (Test-MediaFlowPackageCachePath 'C:\Users\Example\AppData\Local\Programs\MediaFlow')) 'A stable installed program path was blocked.'
Assert-True (-not (Test-MediaFlowPackageCachePath 'C:\Users\Example\AppData\Local\Packages\Other.App_test\LocalCacheBackup')) 'A similar directory name was mistaken for LocalCache.'
$stableProdApp=Join-Path $scratch 'stable-production\resources\platform'
$stableProdData=Join-Path $scratch 'stable-data'
$stableProdProfile=Join-Path $stableProdData 'instance-profile.json'
New-Item -ItemType Directory -Force -Path $stableProdApp | Out-Null
$stableProdPayload=[pscustomobject]@{schema=1;id='production';acceptance=$false;install_root=(Join-Path $scratch 'stable-production');app_root=$stableProdApp;data_root=$stableProdData;ui_port=3001;api_port=48138;stream_port=48139;task_name='MediaFlow Background'}
Write-InstallerJson $stableProdProfile $stableProdPayload
$stableProd=Get-MediaFlowInstance -AppRoot $stableProdApp -InstanceProfilePath $stableProdProfile
Assert-True ($stableProd.Id -eq 'production') 'An ordinary stable production profile was rejected.'
$profileLess=$stableProd|Select-Object *
$profileLess.ProfilePath=$null;$profileLess.Profile=$null
$defaultReceipt=Get-InstallerCandidateTaskReceipt $profileLess $profileLess.InstallRoot
Assert-True (Test-MediaFlowSamePath $defaultReceipt.profile_path (Join-Path $stableProdData 'instance-profile.json')) 'A new installation still put its profile outside DATA_ROOT.'
$global:MediaFlowRealPathResolver=${function:Get-MediaFlowPhysicalPath}
$global:MediaFlowBlockedFixturePath=$null
try {
    function Get-MediaFlowPhysicalPath {
        param([string]$Path)
        if ($global:MediaFlowBlockedFixturePath -eq '!unreadable') { throw 'native fixture access failure' }
        if ($global:MediaFlowBlockedFixturePath -eq '*' -or (Test-MediaFlowSamePath $Path $global:MediaFlowBlockedFixturePath)) { return 'C:\Users\Example\AppData\Local\Packages\OpenAI.Codex_example\LocalCache\Local\virtualized-path' }
        return & $global:MediaFlowRealPathResolver $Path
    }
    foreach($blockedPath in @($stableProdApp,$stableProdData,$stableProdProfile)) {
        $global:MediaFlowBlockedFixturePath=$blockedPath
        $caught=$null
        $caughtCode=$null
        try { Get-MediaFlowInstance -AppRoot $stableProdApp -InstanceProfilePath $stableProdProfile | Out-Null } catch { $caught=$_.Exception.Message; $caughtCode=$_.Exception.Data['MediaFlowCode'] }
        Assert-True ($caught -like '*MSIX LocalCache*Windows Task Scheduler*') 'A physically virtualized production path was not rejected with an actionable reason.'
        Assert-True ($caughtCode -eq 'storage_path_unstable') 'The physical path refusal lost its structured management error code.'
    }
    $global:MediaFlowBlockedFixturePath='!unreadable'
    $caughtCode=$null
    try { Get-MediaFlowInstance -AppRoot $stableProdApp -InstanceProfilePath $stableProdProfile | Out-Null } catch { $caughtCode=$_.Exception.Data['MediaFlowCode'] }
    Assert-True ($caughtCode -eq 'storage_path_unverifiable') 'An inaccessible physical path lost its structured management error code.'
    $global:MediaFlowBlockedFixturePath='*'
    $sourceUnaffected=Get-MediaFlowInstance -AppRoot $source
    Assert-True ($sourceUnaffected.DevelopmentSource) 'The production path guard changed source checkout management.'
    $acceptanceUnaffected=Get-MediaFlowInstance -AppRoot $appRoot -InstanceProfilePath $ProfileFile
    Assert-True ($acceptanceUnaffected.Acceptance) 'The production path guard changed explicit isolated acceptance behavior.'
} finally {
    Set-Item -Path Function:Get-MediaFlowPhysicalPath -Value $global:MediaFlowRealPathResolver
    $global:MediaFlowBlockedFixturePath=$null
}
# Use fresh processes, not the cached native types above. NSIS places its native
# System.dll in cwd, which must not shadow the managed compiler reference.
$poisonRoot=Join-Path $scratch 'native System dll cwd'
New-Item -ItemType Directory -Force -Path $poisonRoot | Out-Null
$poisonDll=Join-Path $poisonRoot 'System.dll'
Copy-Item -LiteralPath (Join-Path $env:SystemRoot 'System32\version.dll') -Destination $poisonDll
$nativeProbeShells=@((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'),(Join-Path $env:SystemRoot 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'))
$coreShell=Get-Command pwsh.exe -ErrorAction SilentlyContinue
if ($coreShell) { $nativeProbeShells+= $coreShell.Source }
$escapedHelper=(Join-Path $PSScriptRoot 'instance-profile.ps1').Replace("'","''")
$escapedNativeDll=$poisonDll.Replace("'","''")
$probeSource='$ErrorActionPreference=''Stop'';$ProgressPreference=''SilentlyContinue'';. '''+$escapedHelper+''';$parts=@(Split-MediaFlowWindowsArguments ''"C:\path with space\host.py" run --instance-profile "C:\data path\instance.json"'');if($parts.Count -ne 4 -or $parts[3] -ne ''C:\data path\instance.json''){throw ''native argv failed''};$physical=Get-MediaFlowPhysicalPath '''+$escapedNativeDll+''';if(-not(Test-MediaFlowSamePath $physical '''+$escapedNativeDll+''')){throw ''native physical path failed''};[pscustomobject]@{ok=$true;pointer_size=[IntPtr]::Size}|ConvertTo-Json -Compress'
$encodedProbe=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($probeSource))
foreach($nativeShell in $nativeProbeShells) {
    $startInfo=New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName=$nativeShell
    $startInfo.Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$encodedProbe
    $startInfo.WorkingDirectory=$poisonRoot
    $startInfo.UseShellExecute=$false;$startInfo.CreateNoWindow=$true
    $startInfo.RedirectStandardOutput=$true;$startInfo.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process
    $process.StartInfo=$startInfo
    try {
        $null=$process.Start()
        $stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(20000)) { $process.Kill();throw 'Isolated native compiler probe exceeded 20 seconds.' }
        $output=$stdout.GetAwaiter().GetResult();$errorOutput=$stderr.GetAwaiter().GetResult()
        Assert-True ($process.ExitCode -eq 0) ('Native System.dll shadowed compiler references in '+$nativeShell+': '+$errorOutput)
        $result=$output|ConvertFrom-Json
        Assert-True ($result.ok -eq $true -and $result.pointer_size -in @(4,8)) 'Fresh native probes did not both execute successfully.'
    } finally { $process.Dispose() }
}
[pscustomobject]@{ok=$true;assertions=$global:Passed;scratch=$scratch;real_tasks_registered=0;real_services_started=0;native_probe_processes=$nativeProbeShells.Count;device_actions=0} | ConvertTo-Json
