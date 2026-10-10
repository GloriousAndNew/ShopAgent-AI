@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 本项目虚拟环境尚未创建。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m shopagent.stop
pause
