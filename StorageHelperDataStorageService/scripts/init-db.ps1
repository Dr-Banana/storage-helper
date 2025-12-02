# Database initialization script for Storage Helper
$ErrorActionPreference = "Stop"

Write-Host "================================"
Write-Host "Storage Helper - Database Setup"
Write-Host "================================"
Write-Host ""

# Check if Docker is installed
$dockerCheck = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCheck) {
    Write-Host "❌ Error: Docker is not installed"
    Write-Host "Please install Docker: https://www.docker.com/products/docker-desktop"
    exit 1
}

# Check if Docker Compose is installed
$composeCheck = Get-Command docker-compose -ErrorAction SilentlyContinue
if (-not $composeCheck) {
    Write-Host "❌ Error: Docker Compose is not installed"
    Write-Host "Please install Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
}

$dockerVersion = docker --version
Write-Host "✅ Docker installed: $dockerVersion"
$composeVersion = docker-compose --version
Write-Host "✅ Docker Compose installed: $composeVersion"
Write-Host ""

# Get project root directory
$PROJECT_ROOT = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $PROJECT_ROOT

Write-Host "📁 Project directory: $PROJECT_ROOT"
Write-Host ""

# Check if MySQL image already exists
$ErrorActionPreference = "Continue"
docker image inspect mysql:8.0 2>&1 | Out-Null
$imageExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"

if ($imageExists) {
    Write-Host "✅ MySQL image already exists"
} else {
    Write-Host "📥 Pulling MySQL image..."
    docker pull mysql:8.0
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Error: Failed to pull MySQL image"
        exit 1
    }
}

# Start MySQL container
Write-Host "🚀 Starting database container..."
docker-compose up -d mysql
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Error: Failed to start MySQL container"
    exit 1
}

# Wait for MySQL to be ready
Write-Host "⏳ Waiting for MySQL to start..."
$ErrorActionPreference = "Continue"
$mysqlReady = $false
for ($i = 1; $i -le 30; $i++) {
    docker-compose exec -T mysql mysql -uroot -proot -e "SELECT 1" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $mysqlReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
$ErrorActionPreference = "Stop"

if (-not $mysqlReady) {
    Write-Host "❌ Error: MySQL failed to become ready within 30 seconds"
    Write-Host "   Check logs with: docker-compose logs mysql"
    exit 1
}

Write-Host "✅ MySQL is ready"
Write-Host ""

# Verify database initialization
Write-Host "📊 Verifying database schema..."
$ErrorActionPreference = "Continue"
docker-compose exec -T mysql mysql -uroot -proot storage_helper -e "SHOW TABLES;"
$schemaVerified = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"

if (-not $schemaVerified) {
    Write-Host ""
    Write-Host "❌ Error: Failed to verify database schema"
    Write-Host "   The database may not have been initialized correctly"
    Write-Host "   Check logs with: docker-compose logs mysql"
    exit 1
}

Write-Host ""
Write-Host "================================"
Write-Host "✨ Database setup completed!"
Write-Host "================================"
Write-Host ""
Write-Host "Connection details:"
Write-Host "  Host: localhost"
Write-Host "  Port: 3306"
Write-Host "  User: root"
Write-Host "  Password: root"
Write-Host "  Database: storage_helper"
Write-Host ""
Write-Host "To stop the container: docker-compose down"
Write-Host "To view logs: docker-compose logs mysql"
