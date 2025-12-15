# Start development server
Write-Host "Starting StorageHelperWebService development server..." -ForegroundColor Green

# Check if dependencies are installed
if (-not (Test-Path "node_modules")) {
    Write-Host "node_modules not found, installing dependencies..." -ForegroundColor Yellow
    npm install
}

# Start development server
Write-Host "Starting Vite development server..." -ForegroundColor Green
npm run dev
