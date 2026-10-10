@echo off
chcp 65001 >nul
cd /d "%~dp0"
set SHOPAGENT_ENV=production
echo 正式本地入口 http://127.0.0.1:8001/customer
echo 这仍是本机运行，不是公网部署。没有默认管理员密码。
".venv\Scripts\python.exe" -X utf8 -m shopagent.run --environment production --port 8001
pause
