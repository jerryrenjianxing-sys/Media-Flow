@echo off
echo This task may send one model-approved comment in the authorized test environment.
"C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe" "%~dp0worker.py" submit douyin_comment --comment-dwell 10 --max-gate-skips 3
pause
