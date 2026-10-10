@echo off
chcp 65001 >nul
cd /d "%~dp0"
set SHOPAGENT_ENV=production
".venv\Scripts\python.exe" -X utf8 -m shopagent.stop
pause
