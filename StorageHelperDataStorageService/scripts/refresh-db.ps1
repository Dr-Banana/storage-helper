# Database refresh script for Storage Helper
# This script drops the existing database and recreates it from schema.sql
$ErrorActionPreference = "Stop"

Write-Host "================================"
Write-Host "Storage Helper - Database Refresh"
Write-Host "================================"
Write-Host ""
Write-Host "⚠️  WARNING: This will DELETE all data in the storage_helper database!"
Write-Host "Proceeding with refresh..."
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

# Check if schema.sql exists
if (-not (Test-Path "schema.sql")) {
    Write-Host "❌ Error: schema.sql not found in $PROJECT_ROOT"
    Write-Host "   Please ensure schema.sql exists in the project root"
    exit 1
}

Write-Host "✅ Found schema.sql"
Write-Host ""

# Check if MySQL container exists and clean up if needed
Write-Host "🔍 Checking MySQL container status..."
$ErrorActionPreference = "Continue"
$containerExists = docker ps -a --filter "name=storage-helper-db" --quiet
$ErrorActionPreference = "Stop"

if ($containerExists) {
    Write-Host "ℹ️  Found existing container"
    
    $ErrorActionPreference = "Continue"
    $containerRunning = docker ps --filter "name=storage-helper-db" --quiet
    $ErrorActionPreference = "Stop"
    
    if ($containerRunning) {
        Write-Host "✅ Container is running"
    } else {
        Write-Host "🔄 Container exists but not running, removing it..."
        docker rm storage-helper-db 2>&1 | Out-Null
    }
} else {
    Write-Host "✅ No existing container found"
}

Write-Host ""

# Start MySQL container
Write-Host "🚀 Starting database container..."
$ErrorActionPreference = "Continue"
docker-compose up -d mysql 2>&1 | Out-Null
$startResult = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($startResult -ne 0) {
    Write-Host "⚠️  Could not start via docker-compose, trying to clean up and retry..."
    docker rm -f storage-helper-db 2>&1 | Out-Null
    docker-compose up -d mysql
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Error: Failed to start MySQL container"
        exit 1
    }
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

# Drop existing database
Write-Host "🗑️  Dropping existing database..."
$ErrorActionPreference = "Continue"
docker-compose exec -T mysql mysql -uroot -proot -e "DROP DATABASE IF EXISTS storage_helper;" 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
Write-Host "✅ Database dropped"
Write-Host ""

# Create new database
Write-Host "🔨 Creating new database..."
docker-compose exec -T mysql mysql -uroot -proot -e "CREATE DATABASE storage_helper CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Error: Failed to create database"
    exit 1
}
Write-Host "✅ Database created"
Write-Host ""

# Execute schema.sql
Write-Host "📝 Executing schema.sql..."
$schemaContent = Get-Content "schema.sql" -Raw
$ErrorActionPreference = "Continue"
$schemaContent | docker-compose exec -T mysql mysql -uroot -proot storage_helper 2>&1 | Out-Null
$schemaResult = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($schemaResult -ne 0) {
    Write-Host "❌ Error: Failed to execute schema.sql"
    Write-Host "   Check the schema.sql file for syntax errors"
    exit 1
}
Write-Host "✅ Schema executed successfully"
Write-Host ""

# Verify database setup
Write-Host "📊 Verifying database structure..."
docker-compose exec -T mysql mysql -uroot -proot storage_helper -e "SHOW TABLES;"
Write-Host ""

# Count tables
$ErrorActionPreference = "Continue"
$tableCount = docker-compose exec -T mysql mysql -uroot -proot storage_helper -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='storage_helper';" 2>&1
$ErrorActionPreference = "Stop"

$tableCountLine = $tableCount -split "`n" | Select-Object -Last 1
Write-Host "✅ Total tables created: $tableCountLine"
Write-Host ""

Write-Host "================================"
Write-Host "✨ Database refresh completed!"
Write-Host "================================"
Write-Host ""
Write-Host "Connection details:"
Write-Host "  Host: localhost"
Write-Host "  Port: 3306"
Write-Host "  User: root"
Write-Host "  Password: root"
Write-Host "  Database: storage_helper"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  - Verify tables: docker-compose exec mysql mysql -uroot -proot storage_helper -e ""DESCRIBE document;"""
Write-Host "  - Insert test data: add data to your tables"
Write-Host "  - Stop container: docker-compose down"
Write-Host "  - View logs: docker-compose logs mysql"
