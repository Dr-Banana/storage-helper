#!/bin/bash
# Start the Data Storage Service with LOCAL environment configuration
# This script initializes the database and starts the FastAPI application

echo -e "\033[32mStarting StorageHelper Data Storage Service with LOCAL environment...\033[0m"
echo ""

# Navigate to project root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# Step 1: Initialize database if needed
echo -e "\033[36mInitializing database...\033[0m"
./scripts/init-db.sh
if [ $? -ne 0 ]; then
    echo -e "\033[31mDatabase initialization failed. Continuing anyway...\033[0m"
fi

echo ""

# Step 2: Start the API server
echo -e "\033[36mStarting API server...\033[0m"
python main.py
