@echo off
REM DAPENG Alpha - local launcher
REM ASCII only on purpose: avoids GBK/UTF-8 codepage issues on Windows.
REM Resolves a Python interpreter that can import fastapi + uvicorn, then starts uvicorn.

cd /d "%~dp0"

REM Free port 8900 if a previous instance is still listening
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8900 ^| findstr LISTENING 2^>nul') do (
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 2 >nul

set PORT=8900
netstat -ano | findstr ":8900.*LISTENING" >nul 2>&1
if %errorlevel%==0 (
    set PORT=8901
    echo [DAPENG] Port 8900 busy, falling back to 8901
) else (
    echo [DAPENG] Using port 8900
)

REM Interpreter candidates: local venv -> user-level runtimes -> system Python
set "PY="
for %%P in (
    "%~dp0.venv\Scripts\python.exe"
    "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) do (
    if not defined PY if exist "%%~P" (
        "%%~P" -c "import fastapi, uvicorn" >nul 2>&1 && set "PY=%%~P"
    )
)
if not defined PY (
    for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
        if not defined PY if exist "%%~D\python.exe" (
            "%%~D\python.exe" -c "import fastapi, uvicorn" >nul 2>&1 && set "PY=%%~D\python.exe"
        )
    )
)
if not defined PY (
    where python >nul 2>&1 && python -c "import fastapi, uvicorn" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [DAPENG] [ERROR] No usable Python interpreter found ^(needs fastapi + uvicorn^).
    echo [DAPENG] Tried: .venv, %%USERPROFILE%%\.workbuddy envs, Python311, versions\*, PATH
    pause
    exit /b 1
)
echo [DAPENG] Using Python: %PY%

echo [DAPENG Alpha] starting on port %PORT% ...
start "" /B "%PY%" -m uvicorn server.main:app --host 127.0.0.1 --port %PORT%
timeout /t 5 >nul

curl -s http://localhost:%PORT%/api/health >nul 2>&1
if %errorlevel%==0 (
    echo [DAPENG Alpha] started: http://localhost:%PORT%/dashboard.html
    start http://localhost:%PORT%/dashboard.html
) else (
    echo [DAPENG Alpha] startup may have failed, check server.log
)

echo %PORT% > "%~dp0.current_port"

cmd /k
