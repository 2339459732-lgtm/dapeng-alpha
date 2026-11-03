@echo off
REM DAPENG Alpha - silent launcher (no console window, logs to server.log)
REM ASCII only: avoids GBK/UTF-8 codepage issues on Windows.

for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8900 ^| findstr LISTENING 2^>nul') do taskkill /f /pid %%a >nul 2>&1
timeout /t 2 >nul

cd /d "%~dp0"

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
    echo [DAPENG] [ERROR] No usable Python interpreter found ^(needs fastapi + uvicorn^). >> "%~dp0server.log"
    echo [DAPENG] Tried: .venv, USERPROFILE\.workbuddy envs, Python311, versions\*, PATH >> "%~dp0server.log"
    exit /b 1
)

"%PY%" -m uvicorn server.main:app --host 127.0.0.1 --port 8900 >> "%~dp0server.log" 2>&1
