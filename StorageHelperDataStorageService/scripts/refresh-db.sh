#!/bin/bash
set -e

# Database refresh script for Storage Helper
# This script drops the existing database and recreates it from schema.sql
echo "================================"
echo "Storage Helper - Database Refresh"
echo "================================"
echo ""
echo "⚠️  WARNING: This will DELETE all data in the storage_helper database!"
echo "Proceeding with refresh..."
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed"
    echo "Please install Docker: https://www.docker.com/products/docker-desktop"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: Docker Compose is not installed"
    echo "Please install Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
fi

echo "✅ Docker installed: $(docker --version)"
echo "✅ Docker Compose installed: $(docker-compose --version)"
echo ""

# Get project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "📁 Project directory: $PROJECT_ROOT"
echo ""

# Check if schema.sql exists
if [ ! -f "schema.sql" ]; then
    echo "❌ Error: schema.sql not found in $PROJECT_ROOT"
    echo "   Please ensure schema.sql exists in the project root"
    exit 1
fi

echo "✅ Found schema.sql"
echo ""

# Check if MySQL container exists and clean up if needed
echo "🔍 Checking database container status..."

# Force complete cleanup with docker-compose down -v (removes volumes)
echo "🛑 Bringing down docker-compose (will remove volumes)..."
docker-compose down -v 2>&1 || true

# Additional cleanup in case docker volume is still there
echo "🗑️  Removing any remaining data volumes..."
docker volume rm storage-helper-db-data 2>/dev/null || true
docker volume rm mysql_data 2>/dev/null || true
docker volume rm postgres_data 2>/dev/null || true
docker volume rm storage-helper-data 2>/dev/null || true

# Clean up any orphaned containers
echo "🧹 Cleaning up orphaned containers..."
docker rm -f storage-helper-db 2>/dev/null || true

echo ""

# Start PostgreSQL container with fresh data
echo "🚀 Starting database container with PostgreSQL (pgvector)..."
docker-compose up -d postgres 2>&1 || {
    echo "⚠️  Could not start via docker-compose, trying direct docker commands..."
    docker rm -f storage-helper-db 2>/dev/null || true
    docker-compose up -d postgres
}

if [ $? -ne 0 ]; then
    echo "❌ Error: Failed to start PostgreSQL container"
    exit 1
fi

# Wait for PostgreSQL to be ready
echo "⏳ Waiting for PostgreSQL to start..."
postgres_ready=false
for i in {1..30}; do
    if docker-compose exec -T postgres pg_isready -U postgres > /dev/null 2>&1; then
        postgres_ready=true
        break
    fi
    sleep 1
done

if [ "$postgres_ready" = false ]; then
    echo "❌ Error: PostgreSQL failed to become ready within 30 seconds"
    echo "   Check logs with: docker-compose logs postgres"
    exit 1
fi

echo "✅ PostgreSQL is ready"
echo ""

# Drop existing database
echo "🗑️  Dropping existing database..."
docker-compose exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS storage_helper;" 2>&1 || true
echo "✅ Database dropped"
echo ""

# Create new database
echo "🔨 Creating new database..."
docker-compose exec -T postgres psql -U postgres -c "CREATE DATABASE storage_helper;"
if [ $? -ne 0 ]; then
    echo "❌ Error: Failed to create database"
    exit 1
fi
echo "✅ Database created"
echo ""

# Execute schema.sql
echo "📝 Executing schema.sql..."
docker-compose exec -T postgres psql -U postgres -d storage_helper -f /docker-entrypoint-initdb.d/schema.sql
if [ $? -ne 0 ]; then
    echo "❌ Error: Failed to execute schema.sql"
    echo "   Check the schema.sql file for syntax errors"
    exit 1
fi
echo "✅ Schema executed successfully"
echo ""

# Verify database setup
echo "📊 Verifying database structure..."
docker-compose exec -T postgres psql -U postgres -d storage_helper -c "\dt"
echo ""

# Count tables
table_count=$(docker-compose exec -T postgres psql -U postgres -d storage_helper -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | xargs)
echo "✅ Total tables created: $table_count"
echo ""

echo "================================"
echo "✨ Database refresh completed!"
echo "================================"
echo ""
echo "Connection details:"
echo "  Host: localhost"
echo "  Port: 5432"
echo "  User: postgres"
echo "  Password: root"
echo "  Database: storage_helper"
echo ""
echo "Next steps:"
echo "  - Verify tables: docker-compose exec postgres psql -U postgres -d storage_helper -c '\dt'"
echo "  - Insert test data: add data to your tables"
echo "  - Stop container: docker-compose down"
echo "  - View logs: docker-compose logs postgres"
