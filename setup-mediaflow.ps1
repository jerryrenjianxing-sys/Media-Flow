$ErrorActionPreference = 'Stop'
# One-cycle compatibility wrapper while the bootstrap implementation retains its historical file name.
& (Join-Path $PSScriptRoot 'setup-riskflow.ps1') @args
