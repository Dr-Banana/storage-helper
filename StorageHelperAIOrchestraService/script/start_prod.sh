#!/bin/bash
# Start the service with PRODUCTION environment configuration
# This script sets APP_ENV=prod and starts the FastAPI application

echo -e "\033[33mStarting StorageHelper AI Service with PRODUCTION environment...\033[0m"
echo -e "\033[36mLoading configuration from .env.prod\033[0m"
echo ""
echo -e "\033[31mWARNING: You are running in PRODUCTION mode!\033[0m"
echo ""

# Navigate to project root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# Set environment variable and start the application
export APP_ENV=prod
python main.py

