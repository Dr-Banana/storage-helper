#!/bin/bash

# StorageHelper Docker Logs Script
# View real-time logs from services

echo "📊 StorageHelper Service Logs"
echo "Press Ctrl+C to exit"
echo ""

docker-compose logs -f
