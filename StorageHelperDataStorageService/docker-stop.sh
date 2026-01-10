#!/bin/bash

# StorageHelper Docker Stop Script
# Stops all services

echo "🛑 Stopping StorageHelper services..."

docker-compose down

echo "✅ All services stopped"
