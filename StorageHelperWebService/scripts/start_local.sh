#!/bin/bash

# Start development server
echo "Starting StorageHelperWebService development server..."

# Check if dependencies are installed
if [ ! -d "node_modules" ]; then
    echo "node_modules not found, installing dependencies..."
    npm install
fi

# Start development server
echo "Starting Vite development server..."
npm run dev
