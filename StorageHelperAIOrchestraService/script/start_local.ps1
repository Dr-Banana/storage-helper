# Start the service with LOCAL environment configuration
# This script sets APP_ENV=local and starts the FastAPI application

Write-Host "Starting StorageHelper AI Service with LOCAL environment..." -ForegroundColor Green
Write-Host "Loading configuration from .env.local" -ForegroundColor Cyan
Write-Host ""

# Navigate to project root directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

# Set environment variable for this session
$env:APP_ENV = "local"

# Start the application
python main.py

