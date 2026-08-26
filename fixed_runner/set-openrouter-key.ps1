$ErrorActionPreference = 'Stop'
$savedKeyPath = 'C:\Users\jerry\Documents\Codex\Tools\Open-AutoGLM\.secrets\openrouter-api-key.dpapi'
$secretDirectory = Split-Path -Parent $savedKeyPath

New-Item -ItemType Directory -Force -Path $secretDirectory | Out-Null
$secureKey = Read-Host 'Paste the OpenRouter API key (input is hidden)' -AsSecureString
$encryptedKey = ConvertFrom-SecureString -SecureString $secureKey
[IO.File]::WriteAllText(
    $savedKeyPath,
    $encryptedKey,
    [Text.UTF8Encoding]::new($false)
)
Write-Host 'OpenRouter key encrypted for the current Windows user.'
Write-Host $savedKeyPath
