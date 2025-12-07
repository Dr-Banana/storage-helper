#!/bin/bash
# Start the service with LOCAL environment configuration
# This script sets APP_ENV=local and starts the FastAPI application

echo -e "\033[32mStarting StorageHelper AI Service with LOCAL environment...\033[0m"
echo -e "\033[36mLoading configuration from .env.local\033[0m"
echo ""

# Navigate to project root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# Set environment variable and start the application
export APP_ENV=local
python main.py

