$ErrorActionPreference = 'Stop'
$python = 'C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe'
$evaluator = Join-Path $PSScriptRoot 'topic_evaluation.py'
$savedKeyPath = 'C:\Users\jerry\Documents\Codex\Tools\Open-AutoGLM\.secrets\openrouter-api-key.dpapi'

$encryptedKey = (Get-Content -Raw -LiteralPath $savedKeyPath).Trim()
$secureKey = $encryptedKey | ConvertTo-SecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:PHONE_AGENT_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    $env:PHONE_AGENT_BASE_URL = 'https://openrouter.ai/api/v1'
    $env:PHONE_AGENT_COMMENT_FALLBACK_MODELS = ''
    $proxyAvailable = Test-NetConnection -ComputerName '127.0.0.1' -Port 7890 -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($proxyAvailable) {
        $env:PHONE_AGENT_PROXY_URL = 'http://127.0.0.1:7890'
    }
    else {
        Remove-Item Env:PHONE_AGENT_PROXY_URL -ErrorAction SilentlyContinue
    }
    & $python $evaluator @args
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:PHONE_AGENT_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_AGENT_BASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_AGENT_COMMENT_FALLBACK_MODELS -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_AGENT_PROXY_URL -ErrorAction SilentlyContinue
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
}
