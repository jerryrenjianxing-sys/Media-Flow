param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'manage-mediaflow.ps1') -Action Start -NoBrowser:$NoBrowser
