# Start the service with PRODUCTION environment configuration
# This script sets APP_ENV=prod and starts the FastAPI application

Write-Host "Starting StorageHelper AI Service with PRODUCTION environment..." -ForegroundColor Yellow
Write-Host "Loading configuration from .env.prod" -ForegroundColor Cyan
Write-Host ""
Write-Host "WARNING: You are running in PRODUCTION mode!" -ForegroundColor Red
Write-Host ""

# Navigate to project root directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

# Set environment variable for this session
$env:APP_ENV = "prod"

# Start the application
python main.py

