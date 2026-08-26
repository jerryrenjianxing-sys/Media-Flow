param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$WorkerArgs
)

$ErrorActionPreference = 'Stop'
$python = 'C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe'
$worker = Join-Path $PSScriptRoot 'worker.py'
$savedKeyPath = 'C:\Users\jerry\Documents\Codex\Tools\Open-AutoGLM\.secrets\zai-api-key.dpapi'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath $savedKeyPath)) {
    throw "Encrypted Z.AI key not found: $savedKeyPath"
}

$encryptedKey = (Get-Content -Raw -LiteralPath $savedKeyPath).Trim()
$secureKey = $encryptedKey | ConvertTo-SecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:PHONE_AGENT_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    $env:PHONE_AGENT_BASE_URL = 'https://api.z.ai/api/paas/v4'
    $env:PHONE_AGENT_MODEL = 'autoglm-phone-multilingual'
    $env:PHONE_AGENT_COMMENT_MODEL = 'glm-4.6v-flash'
    if ($WorkerArgs.Count -eq 0) {
        $WorkerArgs = @('worker')
    }
    & $python $worker @WorkerArgs
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:PHONE_AGENT_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_AGENT_COMMENT_MODEL -ErrorAction SilentlyContinue
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
}
