# run_pc_agent.ps1 - set up venv + deps and launch the BMO PC Agent on :8200
# Usage:  powershell -ExecutionPolicy Bypass -File .\run_pc_agent.ps1
#
# ANTHROPIC_API_KEY must be set in the environment for /task to work.
# /health works without it. This script never hardcodes a key.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$venv = Join-Path $here ".venv"
$py = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "[pc_agent] Creating virtualenv..." -ForegroundColor Cyan
    py -m venv $venv
}

Write-Host "[pc_agent] Installing dependencies..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r (Join-Path $here "requirements.txt")

Write-Host "[pc_agent] Installing Chromium for Playwright..." -ForegroundColor Cyan
& $py -m playwright install chromium

if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host "[pc_agent] WARNING: ANTHROPIC_API_KEY is not set." -ForegroundColor Yellow
    Write-Host "           /health will work, but /task will return a 'set ANTHROPIC_API_KEY' error." -ForegroundColor Yellow
    Write-Host '           Set it with:  $env:ANTHROPIC_API_KEY = "sk-ant-..."' -ForegroundColor Yellow
}

Write-Host "[pc_agent] Starting on http://0.0.0.0:8200  (Ctrl+C to stop)" -ForegroundColor Green
& $py (Join-Path $here "pc_agent.py")
