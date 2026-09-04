param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$WorkerArgs
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command ConvertTo-SecureString -ErrorAction SilentlyContinue)) {
    $securityModule = Join-Path $env:windir 'System32\WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
    Import-Module -Name $securityModule -Force
}
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$worker = Join-Path $PSScriptRoot 'worker.py'
$savedKeyPath = Join-Path $projectRoot '.secrets\openrouter-api-key.dpapi'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath $savedKeyPath)) {
    throw "Encrypted OpenRouter key not found. Run set-openrouter-key.cmd first."
}

$encryptedKey = (Get-Content -Raw -LiteralPath $savedKeyPath).Trim()
$secureKey = $encryptedKey | ConvertTo-SecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:PHONE_AGENT_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    $env:PHONE_AGENT_BASE_URL = 'https://openrouter.ai/api/v1'
    $env:PHONE_AGENT_COMMENT_MODEL = 'z-ai/glm-5.3-flash'
    $env:PHONE_AGENT_COMMENT_FALLBACK_MODELS = ''
    # Test-NetConnection can throw an internal Write-Debug null reference on
    # some Windows PowerShell / NetTCPIP combinations when the local proxy is
    # simply not listening.  A bounded TcpClient probe treats that ordinary
    # state as false without aborting the worker before it reaches the device.
    $proxyAvailable = $false
    $proxyProbe = New-Object System.Net.Sockets.TcpClient
    try {
        $proxyAttempt = $proxyProbe.BeginConnect('127.0.0.1', 7890, $null, $null)
        if ($proxyAttempt.AsyncWaitHandle.WaitOne(500)) {
            $proxyProbe.EndConnect($proxyAttempt)
            $proxyAvailable = $proxyProbe.Connected
        }
    }
    catch {
        $proxyAvailable = $false
    }
    finally {
        $proxyProbe.Dispose()
    }
    if ($proxyAvailable) {
        $env:PHONE_AGENT_PROXY_URL = 'http://127.0.0.1:7890'
    }
    else {
        Remove-Item Env:PHONE_AGENT_PROXY_URL -ErrorAction SilentlyContinue
    }
    if ($WorkerArgs.Count -eq 0) {
        $WorkerArgs = @('worker')
    }
    & $python $worker @WorkerArgs
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:PHONE_AGENT_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_AGENT_BASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_AGENT_COMMENT_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_AGENT_COMMENT_FALLBACK_MODELS -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_AGENT_PROXY_URL -ErrorAction SilentlyContinue
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
}
