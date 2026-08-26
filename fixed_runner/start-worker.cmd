@echo off
title Android Fixed Worker
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-worker-secure.ps1" worker
pause
