@echo off
title Android Fixed Worker - OpenRouter
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-worker-openrouter-secure.ps1" worker
pause
