@echo off
setlocal

rem Double-clickable first-run setup for Katagiri.
rem Checks for uv, then runs install.ps1 (which does the real work) via
rem PowerShell with an unblocked execution policy for this process only.

where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo uv was not found on PATH.
    echo Install it with:
    echo.
    echo     winget install --id astral-sh.uv -e
    echo.
    echo or see https://docs.astral.sh/uv/getting-started/installation/
    echo Then re-run this script.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "EXITCODE=%ERRORLEVEL%"

echo.
pause
exit /b %EXITCODE%
