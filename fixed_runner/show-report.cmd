@echo off
"C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe" "%~dp0worker.py" report
start "" notepad.exe "%~dp0runtime\latest-report.md"
