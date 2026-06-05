@echo off
cd /d "%~dp0"
echo Starting LLM Gateway Proxy...
call python -m app.main
if errorlevel 1 (
    echo.
    echo Failed to start. Make sure Python is installed and requirements are installed.
    echo Run: pip install -r requirements.txt
    pause
)
