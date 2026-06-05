@echo off
cd /d "%~dp0"
echo ========================================
echo   LLM Gateway 测试工具
echo ========================================
echo.
echo 1. 非流式请求
echo 2. 流式请求
echo.
set /p choice="请选择 (1 或 2): "

if "%choice%"=="1" (
    echo.
    echo 发送非流式请求...
    python test_gateway.py
) else if "%choice%"=="2" (
    echo.
    echo 发送流式请求...
    python test_gateway.py --stream
) else (
    echo 无效选择，默认发送非流式请求
    python test_gateway.py
)

echo.
if errorlevel 1 (
    echo 请求失败，请确认网关已启动 (http://127.0.0.1:8001)
) else (
    echo 请求完成
)
pause
