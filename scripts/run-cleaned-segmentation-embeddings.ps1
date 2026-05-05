<#
.SYNOPSIS
Runs the full PA2 pipeline (Segmentation + Embedding) chunk by chunk to avoid out-of-memory errors on large databases.

.DESCRIPTION
1. Runs segment_pages_to_db.py in chunks of 500 until no more HTML pages are left to process.
2. Runs compute_embeddings.py in batches of 100 to safely compute and store embedding vectors.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\run-cleaned-segmentation-embeddings.ps1
#>

[CmdletBinding()]
param(
    [int]$ChunkSize = 500,
    [int]$BatchSize = 100
)

$ErrorActionPreference = "Stop"

# Navigate to repository root
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " STEP 1: Content Cleaning & Segmentation (Chunked Mode)" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

$hasMore = $true
$totalProcessed = 0

while ($hasMore) {
    Write-Host "Fetching and segmenting next $ChunkSize pages..." -ForegroundColor Yellow

    # Run the python script and capture the output
    $output = python pa2/crawler/src/segment_pages_to_db.py --limit $ChunkSize
    $outputString = $output | Out-String

    # Print the JSON result for visibility
    Write-Host $outputString

    # Check how many were processed in this chunk
    if ($outputString -match '"processed":\s*([0-9]+)') {
        $processed = [int]$matches[1]
        $totalProcessed += $processed

        if ($processed -eq 0 -or $processed -lt $ChunkSize) {
            # If we processed 0, or fewer than ChunkSize, we reached the end of the DB
            $hasMore = $false
            Write-Host "All pages successfully segmented!" -ForegroundColor Green
        }
    } else {
        Write-Host "Warning: Could not parse output. Exiting segmentation loop to be safe." -ForegroundColor Red
        $hasMore = $false
    }
}

Write-Host "Total extracted and segmented pages: $totalProcessed" -ForegroundColor Green

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " STEP 2: Compute Vector Embeddings (Batched Mode)" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

Write-Host "Starting vector calculation (batch size: $BatchSize)..." -ForegroundColor Yellow
python pa2/crawler/src/compute_embeddings.py --batch-size $BatchSize

Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host " PA2 Pipeline Completed Successfully!" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green

