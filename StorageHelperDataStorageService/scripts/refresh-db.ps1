# Database refresh script for Storage Helper
# This script drops the existing database and recreates it from schema.sql
$ErrorActionPreference = "Stop"

Write-Host "================================"
Write-Host "Storage Helper - Database Refresh"
Write-Host "================================"
Write-Host ""
Write-Host "[WARNING] This will DELETE all data in the storage_helper database!"
Write-Host "Proceeding with refresh..."
Write-Host ""

# Check if Docker is installed
$dockerCheck = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCheck) {
    Write-Host "[ERROR] Docker is not installed"
    Write-Host "Please install Docker: https://www.docker.com/products/docker-desktop"
    exit 1
}

# Check if Docker Compose is installed
$composeCheck = Get-Command docker-compose -ErrorAction SilentlyContinue
if (-not $composeCheck) {
    Write-Host "[ERROR] Docker Compose is not installed"
    Write-Host "Please install Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
}

$dockerVersion = docker --version
Write-Host "[OK] Docker installed: $dockerVersion"
$composeVersion = docker-compose --version
Write-Host "[OK] Docker Compose installed: $composeVersion"
Write-Host ""

# Get project root directory
$PROJECT_ROOT = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $PROJECT_ROOT

Write-Host "[INFO] Project directory: $PROJECT_ROOT"
Write-Host ""

# Check if schema.sql exists
if (-not (Test-Path "schema.sql")) {
    Write-Host "[ERROR] schema.sql not found in $PROJECT_ROOT"
    Write-Host "   Please ensure schema.sql exists in the project root"
    exit 1
}

Write-Host "[OK] Found schema.sql"
Write-Host ""

# Check if MySQL container exists and clean up if needed
Write-Host "[INFO] Checking database container status..."

# Force complete cleanup with docker-compose down -v (removes volumes)
Write-Host "[INFO] Bringing down docker-compose (will remove volumes)..."
$ErrorActionPreference = "Continue"
docker-compose down -v 2>&1 | Out-Null
$ErrorActionPreference = "Stop"

# Additional cleanup in case docker volume is still there
Write-Host "[INFO] Removing any remaining data volumes..."
$ErrorActionPreference = "Continue"
docker volume rm storage-helper-db-data 2>&1 | Out-Null
docker volume rm mysql_data 2>&1 | Out-Null
docker volume rm postgres_data 2>&1 | Out-Null
docker volume rm storage-helper-data 2>&1 | Out-Null

# Clean up any orphaned containers
Write-Host "[INFO] Cleaning up orphaned containers..."
docker rm -f storage-helper-db 2>&1 | Out-Null
$ErrorActionPreference = "Stop"

Write-Host ""

# Start PostgreSQL container
Write-Host "[INFO] Starting database container with PostgreSQL (pgvector)..."
$ErrorActionPreference = "Continue"
docker-compose up -d postgres 2>&1 | Out-Null
$startResult = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($startResult -ne 0) {
    Write-Host "[WARNING] Could not start via docker-compose, trying to clean up and retry..."
    docker rm -f storage-helper-db 2>&1 | Out-Null
    docker-compose up -d postgres
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to start PostgreSQL container"
        exit 1
    }
}

# Wait for PostgreSQL to be ready
Write-Host "[INFO] Waiting for PostgreSQL to start..."
$ErrorActionPreference = "Continue"
$postgresReady = $false
for ($i = 1; $i -le 30; $i++) {
    docker-compose exec -T postgres pg_isready -U postgres 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $postgresReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
$ErrorActionPreference = "Stop"

if (-not $postgresReady) {
    Write-Host "[ERROR] PostgreSQL failed to become ready within 30 seconds"
    Write-Host "   Check logs with: docker-compose logs postgres"
    exit 1
}

Write-Host "[OK] PostgreSQL is ready"
Write-Host ""

# Drop existing database
Write-Host "[INFO] Dropping existing database..."
$ErrorActionPreference = "Continue"
docker-compose exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS storage_helper;" 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
Write-Host "[OK] Database dropped"
Write-Host ""

# Create new database
Write-Host "[INFO] Creating new database..."
docker-compose exec -T postgres psql -U postgres -c "CREATE DATABASE storage_helper;"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to create database"
    exit 1
}
Write-Host "[OK] Database created"
Write-Host ""

# Execute schema.sql
Write-Host "[INFO] Executing schema.sql..."
docker-compose exec -T postgres psql -U postgres -d storage_helper -f /docker-entrypoint-initdb.d/schema.sql
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to execute schema.sql"
    Write-Host "   Check the schema.sql file for syntax errors"
    exit 1
}
Write-Host "[OK] Schema executed successfully"
Write-Host ""

# Verify database setup
Write-Host "[INFO] Verifying database structure..."
docker-compose exec -T postgres psql -U postgres -d storage_helper -c "\dt"
Write-Host ""

# Count tables
$ErrorActionPreference = "Continue"
$tableCount = docker-compose exec -T postgres psql -U postgres -d storage_helper -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>&1
$ErrorActionPreference = "Stop"

$tableCountLine = $tableCount -split "`n" | Select-Object -Last 1
Write-Host "[OK] Total tables created: $tableCountLine"
Write-Host ""

Write-Host "================================"
Write-Host "[SUCCESS] Database refresh completed!"
Write-Host "================================"
Write-Host ""
Write-Host "Connection details:"
Write-Host "  Host: localhost"
Write-Host "  Port: 5432"
Write-Host "  User: postgres"
Write-Host "  Password: root"
Write-Host "  Database: storage_helper"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  - Verify tables: docker-compose exec postgres psql -U postgres -d storage_helper -c '\dt'"
Write-Host "  - Insert test data: add data to your tables"
Write-Host "  - Stop container: docker-compose down"
Write-Host "  - View logs: docker-compose logs postgres"
