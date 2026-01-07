#!/bin/bash
# Start the Data Storage Service with PREPROD environment configuration
# This mode runs locally but uses Supabase storage (same as production)

echo -e "\033[32mStarting StorageHelper Data Storage Service with PREPROD environment...\033[0m"
echo -e "\033[33mNote: This mode uses Supabase storage (same as production)\033[0m"
echo ""

# Navigate to project root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# Check if .env.preprod exists
if [ ! -f ".env.preprod" ]; then
    echo -e "\033[33mWarning: .env.preprod file not found!\033[0m"
    echo -e "\033[33mPlease create .env.preprod file from .env.preprod.example\033[0m"
    echo ""
    echo -e "\033[36mYou can copy the example file:\033[0m"
    echo "  cp .env.preprod.example .env.preprod"
    echo ""
    read -p "Do you want to continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Set APP_ENV environment variable
export APP_ENV=preprod

echo -e "\033[36mEnvironment: PREPROD\033[0m"
echo -e "\033[36mStorage: Supabase (production storage)\033[0m"
echo ""

# Step 1: Initialize database if needed (optional, can use Supabase DB)
echo -e "\033[33mNote: Database initialization skipped in preprod mode\033[0m"
echo -e "\033[33mIf you want to use local database, run: ./scripts/init-db.sh\033[0m"
echo ""

# Step 2: Start the API server
echo -e "\033[36mStarting API server...\033[0m"
python main.py

