<#
Bootstrap script for Windows PowerShell (equivalent of scripts/bootstrap.sh)

What it does:
- Restores Python virtual environment (.venv) using scripts\venv-restore.sh (via bash)
- Creates pa1\report\.out directory
- Runs database migrations using scripts\db-migrate.ps1

Usage (from repo root):
  powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1

Notes:
- Requires Git Bash / WSL / any bash in PATH for venv-restore.sh.
  If you don't have bash, run the equivalent steps manually.
- Does NOT modify docker-compose.yml
#>

[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = 'Stop'
Set-Location $RepoRoot

Write-Host "Restoring Python virtual environment (.venv)..."
# Prefer bash if available (Git Bash typically provides it)
$bash = Get-Command bash -ErrorAction SilentlyContinue
if (-not $bash) {
    throw "'bash' was not found in PATH. Install Git for Windows (Git Bash) or WSL, or restore the venv manually."
}

bash scripts/venv-restore.sh | Out-String | Write-Host

Write-Host "Ensuring report output directory exists..."
New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot 'pa1\report\.out') | Out-Null

Write-Host "Running database migrations..."
powershell -ExecutionPolicy Bypass -File .\scripts\db-migrate.ps1 | Out-String | Write-Host

Write-Host "Bootstrap complete (.venv + postgres + migration)."

