param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command ConvertTo-SecureString -ErrorAction SilentlyContinue)) {
    $securityModule = Join-Path $env:windir 'System32\WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
    Import-Module -Name $securityModule -Force
}
$encryptedKey = (Get-Content -Raw -LiteralPath $Path).Trim()
$secureKey = $encryptedKey | ConvertTo-SecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    [Console]::Out.Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer))
}
finally {
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
}

