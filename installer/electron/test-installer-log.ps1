param(
    [Parameter(Mandatory=$true)][string]$CompilerPath,
    [string]$ProjectRoot=''
)
$ErrorActionPreference='Stop'
if (-not $ProjectRoot) { $ProjectRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
$script:Passed=0
function Assert-LogFixture {
    param($Condition,[string]$Message)
    if (-not $Condition) { throw $Message }
    $script:Passed++
}
function ConvertTo-NsisLiteral {
    param([string]$Value)
    return $Value.Replace('$','$$').Replace('"','$\"')
}
function Invoke-NsisStdinCompiler {
    param([string]$Text,[string]$LogPath,[string]$WorkingDirectory)
    $startInfo=New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName=$compiler
    $startInfo.Arguments='-V2 -INPUTCHARSET UTF8 -'
    $startInfo.WorkingDirectory=$WorkingDirectory
    $startInfo.UseShellExecute=$false;$startInfo.CreateNoWindow=$true
    $startInfo.RedirectStandardInput=$true;$startInfo.RedirectStandardOutput=$true;$startInfo.RedirectStandardError=$true
    $compileProcess=New-Object Diagnostics.Process
    $compileProcess.StartInfo=$startInfo
    try {
        $null=$compileProcess.Start()
        $stdout=$compileProcess.StandardOutput.ReadToEndAsync();$stderr=$compileProcess.StandardError.ReadToEndAsync()
        # Match electron-builder's UTF-8 stdin bytes, not the terminal code page
        # or a file with a leading BOM. A preamble would be inside the script.
        $inputBytes=$strictUtf8.GetBytes($Text)
        $inputStream=$compileProcess.StandardInput.BaseStream
        $inputStream.Write($inputBytes,0,$inputBytes.Length)
        $inputStream.Flush();$inputStream.Close()
        if (-not $compileProcess.WaitForExit(30000)) { $compileProcess.Kill();throw 'The isolated stdin compiler exceeded 30 seconds.' }
        $output=$stdout.GetAwaiter().GetResult()+$stderr.GetAwaiter().GetResult()
        [IO.File]::WriteAllText($LogPath,$output,$strictUtf8)
        return [pscustomobject]@{exit_code=$compileProcess.ExitCode;output=$output}
    } finally { $compileProcess.Dispose() }
}

# No compiler discovery, dependency download, installed application access,
# scheduled task registration, service start, or business data access.
$compiler=[IO.Path]::GetFullPath($CompilerPath)
Assert-LogFixture (Test-Path -LiteralPath $compiler -PathType Leaf) 'Explicit NSIS compiler does not exist.'
$sourcePath=Join-Path $PSScriptRoot 'installer.nsi'
$bytes=[IO.File]::ReadAllBytes($sourcePath)
$strictUtf8=New-Object Text.UTF8Encoding($false,$true)
$source=$strictUtf8.GetString($bytes)
Assert-LogFixture ($source.Length -gt 0) 'The actual installer must be nonempty strict UTF-8.'
Assert-LogFixture ($source.IndexOf([char]0xFEFF) -lt 0) 'A leading or embedded BOM breaks the installer when electron-builder prepends its header.'
$macros=[regex]::Matches($source,'(?ms)^!macro RequireHelperSuccess DESCRIPTION\r?\n.*?^!macroend')
Assert-LogFixture ($macros.Count -eq 1) 'Expected exactly one actual RequireHelperSuccess macro.'
$calls=@([regex]::Matches($source,'(?m)^\s*!insertmacro RequireHelperSuccess "([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
Assert-LogFixture ($calls.Count -ge 2) 'The actual installer no longer has two helper-result stages.'
$descriptions=@($calls | Select-Object -Last 2)
$preparePrefix=([string][char]0x5B89+[char]0x88C5)
$commitPrefix=([string][char]0x65B0+[char]0x7248)
Assert-LogFixture ($descriptions[0].StartsWith($preparePrefix) -and $descriptions[1].StartsWith($commitPrefix)) 'Actual installer Chinese text is missing or incorrectly decoded.'
$successMatch=[regex]::Match($macros[0].Value,'(?m)^\s*FileWriteUTF16LE \$R9 "([^\"]+) code=0')
Assert-LogFixture ($successMatch.Success -and $successMatch.Groups[1].Value.StartsWith($preparePrefix)) 'The actual macro needs a Chinese success label instead of a failure description.'
$successLabel=$successMatch.Groups[1].Value

$scratch=Join-Path ([IO.Path]::GetFullPath($ProjectRoot)) ('work\electron-acceptance\nsis-log-tests-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$chinese=([string][char]0x4E2D+[char]0x6587)
$firstResult=([ordered]@{ok=$true;phase='prepared';note=($chinese+[char]0x4E00)} | ConvertTo-Json -Compress)
$logs=@()
foreach($scenario in @('success','failure')) {
    $secondCode=if($scenario -eq 'success') { 0 } else { 1 }
    $secondResult=([ordered]@{ok=($secondCode -eq 0);phase='installed';note=($chinese+[char]0x4E8C)} | ConvertTo-Json -Compress)
    $fixtureSource=Join-Path $scratch ($scenario+'-fixture.nsi')
    $fixtureExe=Join-Path $scratch ($scenario+'-fixture.exe')
    $fixtureLog=Join-Path $scratch ($scenario+'.installation.log')
    $sentinel=Join-Path $scratch ($scenario+'.continued-after-result')
    $lines=@(
    'Unicode true',
    'RequestExecutionLevel user',
    'SilentInstall silent',
    'Name "MediaFlow log-only regression"',
    ('OutFile "'+(ConvertTo-NsisLiteral $fixtureExe)+'"'),
    '!include "LogicLib.nsh"',
    '!include "FileFunc.nsh"',
    'Var ResultCode',
    'Var ResultText',
    'Var InstallationLog',
    $macros[0].Value,
    'Section "Log only"',
    ('StrCpy $InstallationLog "'+(ConvertTo-NsisLiteral $fixtureLog)+'"'),
    ('Push "'+(ConvertTo-NsisLiteral $firstResult)+'"'),
    'Push "0"',
    ('!insertmacro RequireHelperSuccess "'+(ConvertTo-NsisLiteral $descriptions[0])+'"'),
    ('Push "'+(ConvertTo-NsisLiteral $secondResult)+'"'),
    ('Push "'+$secondCode+'"'),
    ('!insertmacro RequireHelperSuccess "'+(ConvertTo-NsisLiteral $descriptions[1])+'"'),
    ('FileOpen $R8 "'+(ConvertTo-NsisLiteral $sentinel)+'" w'),
    'FileWrite $R8 "continued"',
    'FileClose $R8',
    'SetErrorLevel 0',
    'SectionEnd'
    )
    $builderHeader='; Simulated electron-builder header before the custom script.'+"`r`n"+'!define MEDIAFLOW_STDIN_FIXTURE "1"'+"`r`n"
    $stdinText=$builderHeader+($lines -join "`r`n")+"`r`n"
    Assert-LogFixture ($stdinText.IndexOf([char]0xFEFF) -lt 0) 'The builder-style stdin fixture contains an embedded BOM.'
    [IO.File]::WriteAllText($fixtureSource,$stdinText,$strictUtf8)
    $compiled=Invoke-NsisStdinCompiler $stdinText (Join-Path $scratch ($scenario+'-compiler.log')) $scratch
    Assert-LogFixture ($compiled.exit_code -eq 0 -and (Test-Path -LiteralPath $fixtureExe -PathType Leaf)) ('The builder-style stdin fixture did not compile: '+$compiled.output)
    $process=Start-Process -FilePath $fixtureExe -ArgumentList '/S' -WorkingDirectory $scratch -WindowStyle Hidden -PassThru
    try {
        if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'The isolated log fixture exceeded 30 seconds.' }
        Assert-LogFixture ($process.ExitCode -eq $secondCode) 'The log-only fixture did not return the real helper-result exit code.'
    } finally { $process.Dispose() }
    Assert-LogFixture (Test-Path -LiteralPath $fixtureLog -PathType Leaf) 'The actual helper-result macro did not create its log.'
    $logBytes=[IO.File]::ReadAllBytes($fixtureLog)
    Assert-LogFixture ($logBytes.Length -gt 2 -and $logBytes.Length % 2 -eq 0 -and $logBytes[0] -eq 0xFF -and $logBytes[1] -eq 0xFE) 'The log is not a complete UTF-16LE file with BOM.'
    $actual=[Text.Encoding]::Unicode.GetString($logBytes,2,$logBytes.Length-2)
    $secondLabel=if($secondCode -eq 0) { $successLabel } else { $descriptions[1] }
    $expected='MediaFlow installer diagnostic'+"`r`n"+$successLabel+' code=0'+"`r`n"+$firstResult+"`r`n"+$secondLabel+' code='+$secondCode+"`r`n"+$secondResult+"`r`n"
    Assert-LogFixture ($actual -ceq $expected) 'The real macro corrupted Chinese text, inserted an extra BOM, or overwrote a previous stage.'
    Assert-LogFixture ($actual.IndexOf($firstResult,[StringComparison]::Ordinal) -ge 0 -and $actual.IndexOf($secondResult,[StringComparison]::Ordinal) -gt $actual.IndexOf($firstResult,[StringComparison]::Ordinal)) 'The two real helper results were not appended in order.'
    Assert-LogFixture ((Test-Path -LiteralPath $sentinel) -eq ($secondCode -eq 0)) 'A failure did not stop the silent fixture, or success stopped execution.'
    $logs+=[pscustomobject]@{scenario=$scenario;path=$fixtureLog;bytes=$logBytes.Length;exit_code=$secondCode}
}
[pscustomobject]@{ok=$true;assertions=$script:Passed;compiler=$compiler;compiler_sha256=(Get-FileHash -LiteralPath $compiler -Algorithm SHA256).Hash.ToLowerInvariant();compiler_input='header + custom script, UTF-8 stdin, -INPUTCHARSET UTF8';scratch=$scratch;logs=$logs;pure_executable_runs=2;real_tasks_registered=0;real_services_started=0;device_actions=0} | ConvertTo-Json -Depth 5
