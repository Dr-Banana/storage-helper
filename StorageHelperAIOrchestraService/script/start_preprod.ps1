# Start the service with PREPROD environment configuration
# This mode runs locally but uses production-like configuration (same API keys, etc.)
# Useful for testing production behavior locally

Write-Host "Starting StorageHelper AI Service with PREPROD environment..." -ForegroundColor Green
Write-Host "Loading configuration from .env.preprod" -ForegroundColor Cyan
Write-Host ""
Write-Host "Note: This mode uses production-like configuration but runs locally" -ForegroundColor Yellow
Write-Host ""

# Navigate to project root directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

# Check if .env.preprod exists
if (-not (Test-Path ".env.preprod")) {
    Write-Host "Warning: .env.preprod file not found!" -ForegroundColor Yellow
    Write-Host "Please create .env.preprod file with your production-like configuration" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "You can copy from .env.prod and modify STORAGE_SERVICE_URL to point to local DataStorageService" -ForegroundColor Cyan
    Write-Host ""
    $response = Read-Host "Do you want to continue anyway? (y/n)"
    if ($response -ne "y") {
        exit 1
    }
}

# Set environment variable for this session
$env:APP_ENV = "preprod"

Write-Host "Environment: PREPROD" -ForegroundColor Cyan
Write-Host "Storage Service: Local (http://localhost:8000)" -ForegroundColor Cyan
Write-Host ""

# Start the application
python main.py

