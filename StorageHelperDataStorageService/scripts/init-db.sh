#!/bin/bash
set -e

# Database initialization script for Storage Helper
echo "================================"
echo "Storage Helper - Database Setup"
echo "================================"
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

# Check if PostgreSQL image already exists
if docker image inspect pgvector/pgvector:pg16 &> /dev/null; then
    echo "✅ PostgreSQL (pgvector) image already exists"
else
    echo "📥 Pulling PostgreSQL (pgvector) image..."
    docker pull pgvector/pgvector:pg16
    if [ $? -ne 0 ]; then
        echo "❌ Error: Failed to pull PostgreSQL image"
        exit 1
    fi
fi

# Start PostgreSQL container
echo "🚀 Starting database container..."
docker-compose up -d postgres
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

# Verify database initialization
echo "📊 Verifying database schema..."
docker-compose exec -T postgres psql -U postgres -d storage_helper -c "\dt"
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Error: Failed to verify database schema"
    echo "   The database may not have been initialized correctly"
    echo "   Check logs with: docker-compose logs postgres"
    exit 1
fi

echo ""
echo "================================"
echo "✨ Database setup completed!"
echo "================================"
echo ""
echo "Connection details:"
echo "  Host: localhost"
echo "  Port: 5432"
echo "  User: postgres"
echo "  Password: root"
echo "  Database: storage_helper"
echo ""
echo "To stop the container: docker-compose down"
echo "To view logs: docker-compose logs postgres"
