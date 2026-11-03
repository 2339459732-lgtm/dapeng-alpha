@echo off
setlocal
title DAPENG Alpha 策略选股系统

REM (file is GBK/936 encoded - no chcp needed; matches system codepage)
REM 检查服务是否已在运行（精确匹配本地监听地址，避免远程地址列误匹配）
netstat -ano | findstr /c:"127.0.0.1:8900" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [√] 服务已在运行 http://localhost:8900
    goto :open_browser
)

echo ╔══════════════════════════════════╗
echo ║  DAPENG Alpha 策略选股系统 v3.0  ║
echo ╚══════════════════════════════════╝
echo.

if not exist "%~dp0start-dapeng.vbs" (
    echo [X] 未找到 start-dapeng.vbs，请确认文件位于项目根目录。
    pause
    exit /b 1
)

echo [1/2] 启动服务看门狗（独立后台进程，窗口关闭不影响服务）...
wscript.exe "%~dp0start-dapeng.vbs"

echo [2/2] 等待服务就绪（最多 60 秒）...
set /a WAIT_COUNT=0
:wait_loop
netstat -ano | findstr /c:"127.0.0.1:8900" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 goto :open_browser
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 60 (
    echo.
    echo [X] 等待服务就绪超时（60 秒），服务可能启动失败。
    echo     请查看日志：logs\server.log 与 logs\watchdog.log
    pause
    exit /b 1
)
timeout /t 1 >nul
goto :wait_loop

:open_browser
echo [√] 正在打开浏览器...
start http://localhost:8900/dashboard.html
echo.
echo 提示：服务由看门狗守护，崩溃会自动重启；停止请运行 stop.bat
pause >nul
