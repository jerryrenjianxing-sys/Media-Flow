$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$savedKeyPath = Join-Path $projectRoot '.secrets\openrouter-api-key.dpapi'
$secretDirectory = Split-Path -Parent $savedKeyPath
$plainKey = [Console]::In.ReadToEnd().Trim()

if ([string]::IsNullOrWhiteSpace($plainKey)) {
    throw 'OpenRouter API key is empty.'
}

try {
    New-Item -ItemType Directory -Force -Path $secretDirectory | Out-Null
    $secureKey = ConvertTo-SecureString $plainKey -AsPlainText -Force
    $encryptedKey = ConvertFrom-SecureString -SecureString $secureKey
    [IO.File]::WriteAllText(
        $savedKeyPath,
        $encryptedKey,
        [Text.UTF8Encoding]::new($false)
    )
}
finally {
    $plainKey = $null
    $secureKey = $null
    $encryptedKey = $null
}
