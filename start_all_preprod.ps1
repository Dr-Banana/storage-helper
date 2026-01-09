# PowerShell script to start all services in PREPROD mode in separate windows
# For Windows users - Preprod mode runs locally but uses production-like configuration

# Base paths
$BASE_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$AI_SERVICE_DIR = Join-Path $BASE_DIR "StorageHelperAIOrchestraService"
$DATA_STORAGE_DIR = Join-Path $BASE_DIR "StorageHelperDataStorageService"
$WEB_SERVICE_DIR = Join-Path $BASE_DIR "StorageHelperWebService"

# Colors
$RED = "Red"
$GREEN = "Green"
$YELLOW = "Yellow"
$BLUE = "Cyan"
$MAGENTA = "Magenta"

Write-Host "========== Starting all services in PREPROD mode ==========" -ForegroundColor Magenta
Write-Host "Preprod mode: Backends use Prod credentials but run locally" -ForegroundColor Yellow
Write-Host "Web Service: Runs in Local mode to connect to local backends" -ForegroundColor Cyan
Write-Host ""

# Check if running on Windows
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "Error: This script requires PowerShell 5.0 or higher" -ForegroundColor Red
    exit 1
}

# Step 1: Start Data Storage Service (PREPROD)
Write-Host "[1/3] Starting Data Storage Service (PREPROD)..." -ForegroundColor Cyan

$dataStorageCmd = "cd '$DATA_STORAGE_DIR'; if (-not (Test-Path 'venv')) { python -m venv venv }; & .\venv\Scripts\Activate.ps1; pip install -q -r requirements.txt; Write-Host 'Starting PREPROD mode (Cloud DB)...' -ForegroundColor Cyan; & .\scripts\start_preprod.ps1; Read-Host 'Press Enter to close this window'"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $dataStorageCmd -WindowStyle Normal
Start-Sleep -Seconds 3
Write-Host "Data Storage Service window opened" -ForegroundColor Green
Start-Sleep -Seconds 2

# Step 2: Start AI Orchestration Service (PREPROD)
Write-Host "[2/3] Starting AI Orchestration Service (PREPROD)..." -ForegroundColor Cyan

$aiCmd = @"
cd '$AI_SERVICE_DIR'
if (-not (Test-Path 'env')) {
    python -m venv env
}
& .\env\Scripts\Activate.ps1
pip install -q -r requirements.txt

Write-Host 'Running all unit tests and environment checks...' -ForegroundColor Yellow
python -m pytest tests/
`$testResult = `$LASTEXITCODE
if (`$testResult -ne 0) {
    Write-Host 'Tests failed! Please fix the issues before starting services.' -ForegroundColor Red
    Write-Host "Exit code: `$testResult" -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host 'All tests passed' -ForegroundColor Green
Write-Host 'Starting PREPROD mode...' -ForegroundColor Cyan
& .\script\start_preprod.ps1
Read-Host 'Press Enter to close this window'
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $aiCmd -WindowStyle Normal
Start-Sleep -Seconds 2
Write-Host "AI Orchestration Service window opened" -ForegroundColor Green

# Step 3: Web Service (LOCAL DEV MODE)
# In Preprod, Web Service should use local config to connect to local backends
Write-Host "[3/3] Starting Web Service (Local Dev Mode)..." -ForegroundColor Cyan

$webCommand = "cd '$WEB_SERVICE_DIR'; Write-Host 'Checking dependencies...' -ForegroundColor Cyan; if (-not (Test-Path 'node_modules')) { npm install }; Write-Host 'Starting Vite Dev Server (Local Mode)...' -ForegroundColor Cyan; npm run dev; Read-Host 'Press Enter to close this window'"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCommand -WindowStyle Normal
Start-Sleep -Seconds 1
Write-Host "Web Service window opened" -ForegroundColor Green

Write-Host ""
Write-Host "========== All service startup commands sent ==========" -ForegroundColor Magenta
Write-Host "Service URLs:" -ForegroundColor Yellow
Write-Host "  - Data Storage Service: http://localhost:8000" -ForegroundColor $GREEN
Write-Host "  - AI Orchestration Service: http://localhost:8888" -ForegroundColor $GREEN
Write-Host "  - Web Service (Local Dev): http://localhost:3000" -ForegroundColor $GREEN
Write-Host ""
Write-Host "Note: Frontend uses Local config (port 3000) to proxy requests to local backends." -ForegroundColor Gray
