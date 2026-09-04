@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-mediaflow-console.ps1"
if errorlevel 1 pause
