#Requires -Version 5.1
<#
.SYNOPSIS
    First-run setup for Katagiri: verifies uv, syncs the environment, and
    hands off to the interactive Python installer/doctor.

.DESCRIPTION
    Run this from the repository root (install.bat already does that for
    you). It does not perform Katagiri's own setup steps itself -- config,
    vendor data, dictionary import, and so on all live in
    src/katagiri/installer.py, which this script simply runs with `uv run`
    once the environment is ready.
#>

$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host ""
    Write-Host "uv was not found on PATH." -ForegroundColor Yellow
    Write-Host "Install it with:"
    Write-Host ""
    Write-Host "    winget install --id astral-sh.uv -e"
    Write-Host ""
    Write-Host "or see https://docs.astral.sh/uv/getting-started/installation/"
    Write-Host "Then re-run install.bat."
    exit 1
}

Write-Host "Found uv: $($uv.Source)"

Write-Host ""
Write-Host "Syncing the environment (uv sync)..."
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "uv sync failed (exit $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Starting the Katagiri installer..."
Write-Host ""
uv run python -m katagiri.installer
exit $LASTEXITCODE
