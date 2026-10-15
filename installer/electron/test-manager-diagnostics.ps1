$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'manager-diagnostics.ps1')
$script:passed = 0
function Assert($Condition, [string]$Name) {
    if (-not $Condition) { throw ('FAIL: ' + $Name) }
    $script:passed += 1
}
function Refuses($Identity, $Instance, $Release, [string]$Code) {
    try { Assert-MediaFlowReadyIdentity $Identity $Instance $Release; throw 'Expected identity refusal' }
    catch { Assert ($_.Exception.Data['MediaFlowCode'] -eq $Code) ('identity refuses ' + $Code) }
}
$instance = [pscustomobject]@{ Id='acceptance-manager'; DataRoot='C:\MediaFlow Test\数据'; AppRoot='C:\MediaFlow App'; ApiPort=49101; UiPort=31101; StreamPort=49102; Acceptance=$true; TaskName='MediaFlow Background Manager Test' }
$release = [pscustomobject]@{ version='0.4.1-dev.49'; source_revision='123456789abc' }
$identity = @{ ok=$true; id=$instance.Id; data_root='c:\MEDIAFLOW TEST\数据\'; app_root='c:\mediaflow app\'; api_port=49101; ui_port=31101; stream_port=49102; acceptance=$true; product_version=@{ version=$release.version; source_revision=('123456789abc' + ('0' * 28)) } }
Assert-MediaFlowReadyIdentity ([pscustomobject]$identity) $instance $release
Assert $true 'case insensitive complete identity'
Assert (-not (Test-MediaFlowPathEqual 'C:\app' 'C:\app-other')) 'similar paths remain different'
Assert (-not (Test-MediaFlowPathEqual 'C:\app' 'relative')) 'relative identity cannot match'
foreach ($key in @('id','data_root','app_root','api_port','ui_port','stream_port','acceptance')) {
    $changed = $identity.Clone()
    $changed[$key] = switch ($key) { 'acceptance' { $false } { $_ -like '*port' } { 49199 } 'id' { 'acceptance-other' } default { 'C:\wrong' } }
    Refuses ([pscustomobject]$changed) $instance $release 'wrong_instance'
}
foreach ($metadata in @(@{ version='0.4.1-dev.48'; source_revision=$release.source_revision }, @{ version=$release.version; source_revision='abcdefabcdef' }, @{ version=$release.version; source_revision='unknown' })) {
    $changed = $identity.Clone(); $changed.product_version=$metadata
    Refuses ([pscustomobject]$changed) $instance $release 'release_mismatch'
}
$start = [DateTimeOffset]::Now
$task = [pscustomobject]@{ State='Ready' }
$info = [pscustomobject]@{ LastTaskResult=1; LastRunTime=$start.LocalDateTime }
$bootstrap = [pscustomobject]@{ instance_id=$instance.Id; started_at=$start.ToString('o'); state='failed'; phase='imports'; error_code='bootstrap_import_failed'; attempt_id='attempt-new' }
$failure = Get-MediaFlowStartupFailure $instance $start $task $info $bootstrap
Assert ($failure.code -eq 'service_early_exit' -and $failure.phase -eq 'imports') 'fresh bootstrap exit detected'
Assert ($failure.message -ceq '后台组件加载失败，请检查安装完整性和启动日志。') 'concrete startup phase uses exact Chinese reason'
Assert ($null -eq (Get-MediaFlowStartupFailure $instance $start $task $info $bootstrap 'attempt-new' $info.LastRunTime)) 'repeated failed attempt is not attributed to retry'
$bootstrap.started_at=$start.AddMilliseconds(-100).ToString('o')
Assert ($null -eq (Get-MediaFlowStartupFailure $instance $start $task $info $bootstrap '' $info.LastRunTime)) 'subsecond old failure ignored'
$bootstrap.started_at=$start.AddMinutes(-5).ToString('o'); $info.LastRunTime=$start.AddMinutes(-5).LocalDateTime
Assert ($null -eq (Get-MediaFlowStartupFailure $instance $start $task $info $bootstrap)) 'old failed attempt ignored'
$info.LastRunTime=$start.LocalDateTime
Assert ((Get-MediaFlowStartupFailure $instance $start $task $info $bootstrap).phase -eq 'scheduled_task') 'fresh task result detects early exit'
Assert ($null -eq (Get-MediaFlowStartupFailure $instance $start $task $info $bootstrap '' $info.LastRunTime)) 'unchanged task timestamp ignored on rapid retry'
$info.LastTaskResult=267009
Assert ($null -eq (Get-MediaFlowStartupFailure $instance $start $task $info $null)) 'running task result is not failure'
$info.LastTaskResult=1; $task.State='Running'
Assert ($null -eq (Get-MediaFlowStartupFailure $instance $start $task $info $null)) 'stale previous result while running ignored'
try { throw 'raw traceback and credentials' } catch { $safe = Get-MediaFlowManagementFailure $_ 'configuration' $instance }
Assert ($safe.code -eq 'management_failed' -and $safe.message -notmatch 'traceback|credentials') 'unknown errors sanitized'
try { throw (New-MediaFlowManagementError 'task_disabled' '后台已禁用。' 'registration') } catch { $safe = Get-MediaFlowManagementFailure $_ 'configuration' $instance }
Assert ($safe.code -eq 'task_disabled' -and $safe.message -eq '后台已禁用。') 'structured reasons preserved'
foreach($pathCode in @('storage_path_unstable','storage_path_unverifiable')) {
    try { throw (New-MediaFlowManagementError $pathCode 'internal native path reason' 'configuration' @{recovery='choose_stable_path'}) } catch { $safe=Get-MediaFlowManagementFailure $_ 'configuration' $instance }
    Assert ($safe.code -eq $pathCode -and $safe.message -like '*稳定*' -and $safe.phase -eq 'configuration' -and $safe.diagnostic.recovery -eq 'choose_stable_path') ('structured physical path recovery '+$pathCode)
}
Write-Output ("manager diagnostics: {0} assertions passed" -f $script:passed)

# Exercise the real Windows PowerShell 5 entrypoint without registering, starting
# or stopping any task. These branches must return one UTF-8 JSON value, exit 1,
# and preserve the user-facing reason rather than a PowerShell stack.
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('mediaflow-manager-test-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixtureRoot
$profilePath = Join-Path $fixtureRoot 'profile.json'
$profile = @{ schema=1; id='acceptance-manager'; acceptance=$true; data_root=(Join-Path $fixtureRoot 'data'); install_root=$fixtureRoot; app_root=$root; ui_port=31101; api_port=49101; stream_port=49102; task_name='MediaFlow Manager Offline Test' }
[IO.File]::WriteAllText($profilePath, ($profile | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$originalLocalAppData = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = Join-Path $fixtureRoot 'local'
    $fallbackInstance = [pscustomobject]@{ Id='acceptance-manager-fallback'; DataRoot=(Join-Path $fixtureRoot 'fallback-data'); TaskName='Unused' }
    $fallbackRoot = Join-Path $env:LOCALAPPDATA 'MediaFlow\instances\acceptance-manager-fallback\startup-diagnostics'
    $null = New-Item -ItemType Directory -Path $fallbackRoot -Force
    $fallbackState = @{ instance_id=$fallbackInstance.Id; attempt_id='fallback-attempt'; started_at=$start.AddSeconds(1).ToString('o'); state='failed'; phase='heartbeat'; error_code='background_startup_heartbeat_failed'; log_path='C:\Private\not-a-startup-log.txt' }
    $fallbackJson = Join-Path $fallbackRoot 'background-startup.json'
    [IO.File]::WriteAllText($fallbackJson, ($fallbackState | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    $snapshot = Get-MediaFlowStartupSnapshot $fallbackInstance
    Assert ($snapshot.attempt_id -eq 'fallback-attempt') 'fallback diagnostics read without primary'
    $trustedFallbackLog = Join-Path $fallbackRoot 'background-startup.log'
    Assert ($snapshot.log_path -ceq $trustedFallbackLog) 'untrusted log path replaced by known diagnostic location'
    $fallbackFailure = Get-MediaFlowStartupFailure $fallbackInstance $start $null $null $snapshot
    Assert ($fallbackFailure.phase -eq 'heartbeat' -and $fallbackFailure.diagnostic.bootstrap_log -ceq $trustedFallbackLog) 'fallback phase and actual log reach failure result'
    $fallbackState.log_path='C:\Private\not-a-startup-log.txt'
    $directFailure = Get-MediaFlowStartupFailure $fallbackInstance $start $null $null ([pscustomobject]$fallbackState)
    Assert ($directFailure.diagnostic.bootstrap_log -ceq (Join-Path $fallbackInstance.DataRoot 'background-startup.log')) 'failure builder independently rejects untrusted log path'
    $null = New-Item -ItemType Directory -Path $fallbackInstance.DataRoot -Force
    $primaryState = $fallbackState.Clone(); $primaryState.attempt_id='new-primary'; $primaryState.started_at=$start.AddSeconds(2).ToString('o'); $primaryState.state='ready'
    [IO.File]::WriteAllText((Join-Path $fallbackInstance.DataRoot 'background-startup.json'), ($primaryState | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    $snapshot = Get-MediaFlowStartupSnapshot $fallbackInstance
    Assert ($snapshot.attempt_id -eq 'new-primary' -and $snapshot.state -eq 'ready') 'newer primary replaces older fallback failure'
    Assert ($null -eq (Get-MediaFlowStartupFailure $fallbackInstance $start $null $null $snapshot)) 'first heartbeat ready is not a failure or API readiness claim'
    $fallbackState.instance_id='production'; $fallbackState.started_at=$start.AddSeconds(3).ToString('o')
    [IO.File]::WriteAllText($fallbackJson, ($fallbackState | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Assert ((Get-MediaFlowStartupSnapshot $fallbackInstance).attempt_id -eq 'new-primary') 'fallback from another instance ignored'
    $env:LOCALAPPDATA = $originalLocalAppData
    $expectedMessages = @{
        task_missing='MediaFlow 后台尚未登记，请修复后台注册后重试。'
        task_disabled='MediaFlow 后台计划任务已被禁用，请修复后台注册后重试。'
        wrong_instance='后台登记属于其他程序或实例，未启动它。请修复此实例安装。'
    }
    foreach ($scenario in @('task_missing','task_disabled','wrong_instance')) {
        $escapedRoot = $root.Replace("'", "''"); $escapedProfile = $profilePath.Replace("'", "''")
        $mock = if ($scenario -eq 'task_missing') { 'function Get-ScheduledTask { return $null }' } else {
            '$mockRoot=''' + $escapedRoot + '''; $mockProfile=''' + $escapedProfile + ''';' +
            'function Get-ScheduledTask { [pscustomobject]@{ TaskName=''MediaFlow Manager Offline Test''; State=''Disabled''; Actions=@([pscustomobject]@{ Execute=(Join-Path $mockRoot ''runtime\python\pythonw.exe''); WorkingDirectory=$mockRoot; Arguments=(''"'' + (Join-Path $mockRoot ''fixed_runner\background_host.py'') + ''" run --instance-profile "'' + $mockProfile + ''"'') }) } }'
        }
        if ($scenario -eq 'wrong_instance') { $mock = $mock.Replace("runtime\python\pythonw.exe", "runtime\python\other.exe") }
        $source = '$ProgressPreference=''SilentlyContinue''; ' + $mock + '; & ''' + (Join-Path $root 'manage-mediaflow.ps1').Replace("'", "''") + ''' -Action Start -NoBrowser -InstanceProfilePath ''' + $escapedProfile + ''''
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($source))
        $raw = & $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded
        $exitCode = $LASTEXITCODE
        $result = ($raw -join "`n") | ConvertFrom-Json
        Assert ($exitCode -eq 1 -and $result.ok -eq $false -and $result.code -eq $scenario) ('real entrypoint JSON ' + $scenario)
        Assert ($result.message -ceq $expectedMessages[$scenario]) ('real entrypoint exact UTF8 Chinese ' + $scenario)
    }
} finally {
    $env:LOCALAPPDATA = $originalLocalAppData
    $resolved = [IO.Path]::GetFullPath($fixtureRoot)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\mediaflow-manager-test-'
    if (-not $resolved.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing unexpected test cleanup path.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Write-Output ("manager diagnostics total: {0} assertions passed" -f $script:passed)
