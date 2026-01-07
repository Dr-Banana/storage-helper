# Start the Data Storage Service with PREPROD environment configuration
# This mode runs locally but uses Supabase storage (same as production)

Write-Host "Starting StorageHelper Data Storage Service with PREPROD environment..." -ForegroundColor Green
Write-Host "Note: This mode uses Supabase storage (same as production)" -ForegroundColor Yellow
Write-Host ""

# Navigate to project root directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# Check if .env.preprod exists
if (-not (Test-Path ".env.preprod")) {
    Write-Host "Warning: .env.preprod file not found!" -ForegroundColor Yellow
    Write-Host "Please create .env.preprod file from .env.preprod.example" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "You can copy the example file:" -ForegroundColor Cyan
    Write-Host "  Copy-Item .env.preprod.example .env.preprod" -ForegroundColor Cyan
    Write-Host ""
    $response = Read-Host "Do you want to continue anyway? (y/n)"
    if ($response -ne "y") {
        exit 1
    }
}

# Set APP_ENV environment variable
$env:APP_ENV = "preprod"

Write-Host "Environment: PREPROD" -ForegroundColor Cyan
Write-Host "Storage: Supabase (production storage)" -ForegroundColor Cyan
Write-Host ""

# Step 1: Initialize database if needed (optional, can use Supabase DB)
Write-Host "Note: Database initialization skipped in preprod mode" -ForegroundColor Yellow
Write-Host "If you want to use local database, run: .\scripts\init-db.ps1" -ForegroundColor Yellow
Write-Host ""

# Step 2: Start the API server
Write-Host "Starting API server..." -ForegroundColor Cyan
python main.py

