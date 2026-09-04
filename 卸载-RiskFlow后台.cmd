@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall-riskflow-background.ps1"
if errorlevel 1 pause
