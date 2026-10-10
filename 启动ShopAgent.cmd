@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.13 -m venv .venv
    if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -X utf8 -c "import fastapi,uvicorn,httpx,mcp,python_a2a" >nul 2>&1
if errorlevel 1 (
    echo 正在安装项目依赖，首次运行需要联网...
    ".venv\Scripts\python.exe" -X utf8 -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)
echo 启动后打开 http://127.0.0.1:8000
echo 演示账号 alice / bob / admin，密码 demo123
echo 按 Ctrl+C 停止主程序及全部插件。
".venv\Scripts\python.exe" -X utf8 -m shopagent.run
pause
exit /b
:fail
echo 启动失败，请保留上方错误文字并查看 README.md。
pause
exit /b 1
