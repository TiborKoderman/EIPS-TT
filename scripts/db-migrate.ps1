<#
Windows PowerShell equivalent of scripts/db-migrate.sh

What it does:
- Starts the `db` service from docker-compose
- Waits until Postgres is ready
- Creates database `crawldb` if missing
- Applies initial schema (0_initial_crawldb.sql) if crawldb.page is missing
- Applies numbered migrations db/migrations/NN_*.sql

Usage:
  PowerShell (from repo root):
    powershell -ExecutionPolicy Bypass -File .\scripts\db-migrate.ps1

Notes:
- Does NOT modify docker-compose.yml
- Uses `docker compose exec -T` to avoid TTY issues on Windows
#>

[CmdletBinding()]
param(
    [string]$DbUser = "postgres",
    [string]$DbName = "crawldb",
    [string]$SystemDb = "postgres",
    [int]$MaxWaitSeconds = 30
)

$ErrorActionPreference = "Stop"

# Move to repo root (scripts/..)
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "Starting docker compose db service..."
docker compose up -d db | Out-String | Write-Host

Write-Host "Waiting for PostgreSQL to become ready..."
$ready = $false
for ($i = 1; $i -le $MaxWaitSeconds; $i++) {
    try {
        docker compose exec -T db pg_isready -U $DbUser -d $SystemDb *> $null
        $ready = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $ready) {
    throw "PostgreSQL did not become ready in time."
}

# Create DB if missing
$dbExists = docker compose exec -T db psql -U $DbUser -d $SystemDb -tAc "SELECT 1 FROM pg_database WHERE datname='${DbName}';" | Out-String
if ($dbExists -notmatch "1") {
    Write-Host "Creating database $DbName ..."
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U $DbUser -d $SystemDb -c "CREATE DATABASE ${DbName};" | Out-String | Write-Host
}

# Apply initial schema if crawldb.page missing
$pageRegclass = docker compose exec -T db psql -U $DbUser -d $DbName -tAc "SELECT to_regclass('crawldb.page');" | Out-String
if ($pageRegclass -notmatch "crawldb\.page") {
    Write-Host "Applying initial schema (0_initial_crawldb.sql)..."
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U $DbUser -d $DbName -f /db-migrations/00_initial_crawldb.sql | Out-String | Write-Host
}

# Apply migrations NN_*.sql
$migrations = Get-ChildItem -Path (Join-Path $RepoRoot "db\migrations") -Filter "*_*.sql" -File |
    Where-Object { $_.Name -match '^[0-9][0-9]_.*\.sql$' } |
    Sort-Object Name

foreach ($m in $migrations) {
    if ($m.Name -eq "00_initial_crawldb.sql") {
        continue
    }
    Write-Host "Applying migration: $($m.Name)"
    $containerPath = "/db-migrations/$($m.Name)"
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U $DbUser -d $DbName -f $containerPath | Out-String | Write-Host
}

Write-Host "Database migrations applied."

