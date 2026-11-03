@echo off
title 停止 DAPENG Alpha 服务
setlocal
set "FOUND="

REM 说明：按命令行特征精确匹配 PID 后 taskkill，避免误杀其它 python 进程。
REM   看门狗：pythonw.exe + 命令行含 server_watchdog
REM   服务：  python.exe  + 命令行含 uvicorn 和 server.main
REM 最后按端口兜底，覆盖 uvicorn.exe 直启等特征之外的启动方式。

echo [1/3] 停止看门狗（先停，防止它自动重启 uvicorn）...
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" ^| Where-Object { $_.CommandLine -match 'server_watchdog' } ^| ForEach-Object { $_.ProcessId }" 2^>nul') do (
    echo     停止看门狗 PID=%%p
    taskkill /PID %%p /T >nul 2>&1
    if errorlevel 1 taskkill /PID %%p /T /F >nul 2>&1
    set "FOUND=1"
)

echo [2/3] 停止 uvicorn 服务进程（按命令行特征匹配）...
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" ^| Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'server.main' } ^| ForEach-Object { $_.ProcessId }" 2^>nul') do (
    echo     停止 uvicorn PID=%%p
    taskkill /PID %%p >nul 2>&1
    if errorlevel 1 taskkill /PID %%p /F >nul 2>&1
    set "FOUND=1"
)

echo [3/3] 兜底：反查 127.0.0.1:8900 监听进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /c:"127.0.0.1:8900" ^| findstr "LISTENING"') do (
    echo     停止端口 8900 监听进程 PID=%%a
    taskkill /PID %%a >nul 2>&1
    if errorlevel 1 taskkill /PID %%a /F >nul 2>&1
    set "FOUND=1"
)

if not defined FOUND (
    echo 未发现运行中的 DAPENG 服务进程。
) else (
    echo 服务已停止。
)
echo.
timeout /t 2 >nul
endlocal
