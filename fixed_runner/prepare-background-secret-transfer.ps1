param(
    [Parameter(Mandatory = $true)]
    [string]$TargetUser
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$projectRoot = Split-Path $PSScriptRoot -Parent
$sourcePath = Join-Path $projectRoot '.secrets\openrouter-api-key.dpapi'
$transferPath = Join-Path $projectRoot '.secrets\openrouter-transfer.dpapi-machine'
$encryptedKey = (Get-Content -Raw -LiteralPath $sourcePath).Trim()
$secureKey = $encryptedKey | ConvertTo-SecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
$plainBytes = $null
$protectedBytes = $null
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $plainBytes = [Text.Encoding]::UTF8.GetBytes($plain)
    $protectedBytes = [System.Security.Cryptography.ProtectedData]::Protect(
        $plainBytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    [IO.File]::WriteAllBytes($transferPath, $protectedBytes)

    $targetSid = ([System.Security.Principal.NTAccount]$TargetUser).Translate(
        [System.Security.Principal.SecurityIdentifier]
    )
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sidValue in @($targetSid.Value, 'S-1-5-18', 'S-1-5-32-544')) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier($sidValue)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $transferPath -AclObject $acl
}
finally {
    if ($plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
    if ($protectedBytes) { [Array]::Clear($protectedBytes, 0, $protectedBytes.Length) }
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $plain = $null
    $secureKey = $null
    $encryptedKey = $null
}

Write-Host 'MediaFlow 后台密钥迁移载荷已安全生成。'
