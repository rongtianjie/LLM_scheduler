@echo off
cd /d "%~dp0"
title LLM Gateway 并发测试
echo ========================================
echo   LLM Gateway 并发压力测试
echo   2 线程循环请求
echo ========================================
echo.
echo 网关: http://127.0.0.1:8001
echo 按 Ctrl+C 停止测试
echo.
python test_gateway.py --threads 2
echo.
if errorlevel 1 (
    echo 测试失败，请确认网关已启动
) else (
    echo 测试已完成
)
pause
