@echo off
"C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe" "%~dp0worker.py" submit healthcheck --count 10 --interval-seconds 60
pause
