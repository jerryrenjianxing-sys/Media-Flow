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
function Get-ScheduledTask { param($TaskName,$ErrorAction); return $global:FakeTask }
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory); return [pscustomobject]@{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory} }
function New-ScheduledTaskTrigger { [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { [pscustomobject]@{} }
function New-ScheduledTask { param($Action,$Trigger,$Principal,$Settings,$Description); return [pscustomobject]@{Actions=@($Action);Description=$Description} }
function Register-ScheduledTask { param($TaskName,$InputObject,[switch]$Force,$Xml); $global:Registrations += [pscustomobject]@{TaskName=$TaskName;Task=$InputObject;Xml=$Xml} }
$installed=& (Join-Path $ProjectRoot 'install-mediaflow-background.ps1') -NoStart -NoShortcut -AppRoot $appRoot -LauncherPath $launcher -InstanceProfilePath $ProfileFile | ConvertFrom-Json
Assert-True ($global:Registrations.Count -eq 1) 'Task was not registered exactly once.'
Assert-True ($installed.Execute -eq (Join-Path $appRoot 'runtime\python\pythonw.exe')) 'Task is attached to the GUI or system Python.'
Assert-True ($installed.Arguments -eq ('"'+(Join-Path $appRoot 'fixed_runner\background_host.py')+'" run --instance-profile "'+$ProfileFile+'"')) 'Scheduled host lost stable external profile arguments.'
Assert-True ($global:Registrations[0].TaskName -eq 'MediaFlow Background Acceptance') 'The production task was changed.'
$global:FakeTask=[pscustomobject]@{Actions=@([pscustomobject]@{Execute='C:\foreign\MediaFlow.exe';Arguments='--background-run'})}
Assert-Throws { & (Join-Path $ProjectRoot 'install-mediaflow-background.ps1') -NoStart -NoShortcut -AppRoot $appRoot -LauncherPath $launcher -InstanceProfilePath $ProfileFile } 'A foreign background task was overwritten.'
Assert-True ($global:Registrations.Count -eq 1) 'Rejected install still registered a task.'
$global:Copied=$false
function Copy-InstallerTree { param($Source,$Target);$global:Copied=$true }
Restore-InstallerTransaction ([pscustomobject]@{mutation_started=$false;program_backup=$scratch;install_root=(Join-Path $scratch 'app')})
Assert-True (-not $global:Copied) 'An idle refusal modified the running program during rollback.'
function Restore-RegistryBackup { param($Key,$Backup); throw 'A failed stop must not rewrite registry.' }
function Unregister-ScheduledTask { param($TaskName,[switch]$Confirm); $global:RemovedTask=$TaskName }
$taskXml=Join-Path $scratch 'saved-task.xml'
[IO.File]::WriteAllText($taskXml,'<saved-original-task />',[Text.UTF8Encoding]::new($false))
$state=[pscustomobject]@{mutation_started=$true;program_changed=$false;committed=$false;program_backup=$scratch;install_root=(Join-Path $scratch 'app');task_name='MediaFlow Background Acceptance';task_xml=$taskXml;profile_backup=$null;shortcuts=@();backup_root=$scratch;phase='prepared'}
$StatePath=Join-Path $scratch 'failed-stop-transaction.json'
$global:FakeTask=[pscustomobject]@{Actions=@([pscustomobject]@{Execute=(Join-Path $scratch 'app\resources\platform\runtime\python\pythonw.exe');Arguments=''})}
Restore-InstallerTransaction $state
Assert-True (-not $global:Copied) 'A stop failure overwrote still-running program files.'
Assert-True ($global:RemovedTask -eq 'MediaFlow Background Acceptance') 'A task restore affected a foreign task.'
Assert-True ($global:Registrations[-1].Xml -eq '<saved-original-task />') 'The old exact task definition was not restored.'
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
$profileSource=Get-MediaFlowInstance -AppRoot $source -InstanceProfilePath $ProfileFile
Assert-True (-not $profileSource.DevelopmentSource -and $profileSource.Acceptance) 'An explicit acceptance profile inherited source credentials.'
$pythonOnly=Join-Path $scratch 'python-folder-only'
New-Item -ItemType Directory -Force -Path (Join-Path $pythonOnly 'runtime\python') | Out-Null
[IO.File]::WriteAllBytes((Join-Path $pythonOnly 'runtime\python\python.exe'),[byte[]]@(0))
Remove-Item Env:MEDIAFLOW_INSTANCE_PROFILE -ErrorAction SilentlyContinue
$notSource=Get-MediaFlowInstance -AppRoot $pythonOnly
Assert-True (-not $notSource.DevelopmentSource) 'A Python folder alone was treated as a source checkout.'
[pscustomobject]@{ok=$true;assertions=$global:Passed;scratch=$scratch;real_tasks_registered=0;real_processes_started=0;device_actions=0} | ConvertTo-Json
