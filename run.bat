@echo off
REM Double-click this file to start Rozgar, then open http://localhost:8000
cd /d "%~dp0"
start "" http://localhost:8000
python -m uvicorn app.main:app --port 8000 --reload
pause
