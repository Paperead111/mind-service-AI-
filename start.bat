@echo off
chcp 65001 >nul
cd /d "%~dp0"

python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [start] Installing dependencies...
    python -m pip install --user --cache-dir .pipcache -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
    if errorlevel 1 goto :err
)

echo [start] Starting mind-service at http://127.0.0.1:8000
echo [start] Keep this window open. Close it to stop the service.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto :err
goto :eof

:err
echo.
echo [start] Failed to start. Please send me the error message above.
pause
exit /b 1
