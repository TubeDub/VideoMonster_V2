# TubeDub dev helpers (Windows PowerShell)
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "test", "lint", "run", "zip")]
    [string]$Command = "help"
)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

switch ($Command) {
    "install" {
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        pip install pytest ruff
        Write-Host "Done. Copy .env.example to .env if needed."
    }
    "test" {
        $env:VM_DEV_MODE = "1"
        $env:VM_PREPARE_WARMUP = "0"
        python -m pytest tests/ -q
    }
    "lint" {
        ruff check api engines tests
    }
    "run" {
        python app.py
    }
    "zip" {
        python scripts/run_master_checks.py
    }
    default {
        Write-Host "Usage: .\scripts\dev.ps1 [install|test|lint|run|zip]"
    }
}
