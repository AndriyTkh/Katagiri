@echo off
setlocal

rem Launches the Katagiri MCP server (katagiri-mcp) in this console.
rem
rem This is a stdio server: it speaks MCP (JSON-RPC) on stdin/stdout and is
rem normally launched by the MCP client (e.g. Claude Code), not by hand.
rem Running it standalone like this is only useful as a smoke test that the
rem server starts cleanly. Close the window (or Ctrl+C) to stop it.

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

uv run katagiri-mcp
set "EXITCODE=%ERRORLEVEL%"

echo.
echo katagiri-mcp exited with code %EXITCODE%.
pause
exit /b %EXITCODE%
