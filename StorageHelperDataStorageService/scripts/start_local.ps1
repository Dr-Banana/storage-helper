# Start the Data Storage Service with LOCAL environment configuration
# This script initializes the database and starts the FastAPI application

Write-Host "Starting StorageHelper Data Storage Service with LOCAL environment..." -ForegroundColor Green
Write-Host ""

# Navigate to project root directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# Step 1: Initialize database if needed
Write-Host "Initializing database..." -ForegroundColor Cyan
& ".\scripts\init-db.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Database initialization failed. Continuing anyway..." -ForegroundColor Yellow
}

Write-Host ""

# Step 2: Start the API server
Write-Host "Starting API server..." -ForegroundColor Cyan
python main.py
