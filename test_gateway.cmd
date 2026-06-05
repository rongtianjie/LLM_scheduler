@echo off
cd /d "%~dp0"
title LLM Gateway Load Test
echo ========================================
echo   LLM Gateway Concurrent Load Test
echo   2 threads, loop requests
echo ========================================
echo.
echo Gateway: http://127.0.0.1:8001
echo Press Ctrl+C to stop
echo.
python test_gateway.py --threads 2
echo.
if errorlevel 1 (
    echo Test failed - is the gateway running?
) else (
    echo Test complete
)
pause
