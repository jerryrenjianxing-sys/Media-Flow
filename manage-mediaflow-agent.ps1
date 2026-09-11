param(
    [ValidateSet('Register', 'Start', 'Stop', 'Restart', 'Status', 'Run')]
    [string]$Action = 'Status',
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
if ($Action -ne 'Status') {
    throw 'MediaFlow now uses an external Skill. Embedded Agent management is retired; no process or data was changed. Open http://127.0.0.1:3001/.'
}
[pscustomobject]@{
    state = 'retired'
    reason_code = 'external_skill_home'
    management_url = 'http://127.0.0.1:3001/'
    message = 'MediaFlow uses an external Skill; existing data is retained.'
} | ConvertTo-Json -Compress
