#!/bin/bash

# StorageHelper Docker Startup Script
# Starts the complete stack: PostgreSQL + Storage Helper API

set -e

echo "🚀 Starting StorageHelper services..."
echo ""
echo "Building and starting services..."

# Build and start containers
docker-compose up -d --build

echo ""
echo "✅ Services are starting up..."
echo ""
echo "📋 Service Status:"
docker-compose ps
echo ""
echo "🔗 API Available at: http://localhost:8000"
echo "📊 API Docs at: http://localhost:8000/docs"
echo "🗄️  Database: localhost:5432 (postgres/root)"
echo ""
echo "📝 To view logs:"
echo "   docker-compose logs -f storage-helper-api"
echo ""
echo "❌ To stop services:"
echo "   docker-compose down"
echo ""
