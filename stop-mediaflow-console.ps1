$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'manage-mediaflow.ps1') -Action Stop
