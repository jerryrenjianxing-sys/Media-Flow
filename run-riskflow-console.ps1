param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'manage-riskflow.ps1') -Action Start -NoBrowser:$NoBrowser
