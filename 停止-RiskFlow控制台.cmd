@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-riskflow-console.ps1"
if errorlevel 1 pause

