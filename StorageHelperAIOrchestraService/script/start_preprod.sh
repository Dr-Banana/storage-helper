#!/bin/bash
# Start the service with PREPROD environment configuration
# This mode runs locally but uses production-like configuration (same API keys, etc.)
# Useful for testing production behavior locally

echo -e "\033[32mStarting StorageHelper AI Service with PREPROD environment...\033[0m"
echo -e "\033[36mLoading configuration from .env.preprod\033[0m"
echo ""
echo -e "\033[33mNote: This mode uses production-like configuration but runs locally\033[0m"
echo ""

# Navigate to project root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# Check if .env.preprod exists
if [ ! -f ".env.preprod" ]; then
    echo -e "\033[33mWarning: .env.preprod file not found!\033[0m"
    echo -e "\033[33mPlease create .env.preprod file with your production-like configuration\033[0m"
    echo ""
    echo -e "\033[36mYou can copy from .env.prod and modify STORAGE_SERVICE_URL to point to local DataStorageService\033[0m"
    echo ""
    read -p "Do you want to continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Set environment variable and start the application
export APP_ENV=preprod

echo -e "\033[36mEnvironment: PREPROD\033[0m"
echo -e "\033[36mStorage Service: Local (http://localhost:8000)\033[0m"
echo ""

python main.py

