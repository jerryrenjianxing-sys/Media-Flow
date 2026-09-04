param(
    [Parameter(Mandatory = $true)]
    [string]$TransferPath
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$projectRoot = Split-Path $PSScriptRoot -Parent
$destinationPath = Join-Path $projectRoot '.secrets\openrouter-api-key.dpapi'
$protectedBytes = [IO.File]::ReadAllBytes($TransferPath)
$plainBytes = $null
try {
    $plainBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $protectedBytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    $plain = [Text.Encoding]::UTF8.GetString($plainBytes)
    $secure = ConvertTo-SecureString $plain -AsPlainText -Force
    $encrypted = ConvertFrom-SecureString -SecureString $secure
    [IO.File]::WriteAllText(
        $destinationPath,
        $encrypted,
        [Text.UTF8Encoding]::new($false)
    )
    Remove-Item -LiteralPath $TransferPath -Force
}
finally {
    if ($plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
    if ($protectedBytes) { [Array]::Clear($protectedBytes, 0, $protectedBytes.Length) }
    $plain = $null
    $secure = $null
    $encrypted = $null
}
