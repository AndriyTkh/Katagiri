@echo off
setlocal

rem Katagiri setup -- the one entry point. Safe to re-run at any time.
rem
rem Checks for uv, syncs the Python environment, then hands off to the
rem interactive installer/doctor at src/katagiri/installer.py. Any arguments
rem pass straight through to the installer, so these work too:
rem
rem     setup.bat            interactive wizard (default)
rem     setup.bat --yes      accept defaults, no prompts, no scheduled tasks
rem     setup.bat --check    doctor only: report status, change nothing

cd /d "%~dp0"

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

echo Syncing the environment: uv sync ...
uv sync
if errorlevel 1 (
    echo.
    echo uv sync failed. Fix the error above, then re-run setup.bat.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting the Katagiri installer...
echo.
uv run python -m katagiri.installer %*
set "EXITCODE=%ERRORLEVEL%"

echo.
pause
exit /b %EXITCODE%
