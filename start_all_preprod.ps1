# PowerShell script to start all services in PREPROD mode using Docker
# For Windows users - Preprod mode runs in Docker but uses production DB

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

Write-Host "========================================" -ForegroundColor $MAGENTA
Write-Host "  StorageHelper - PREPROD Environment" -ForegroundColor $MAGENTA
Write-Host "========================================" -ForegroundColor $MAGENTA
Write-Host ""
Write-Host "Configuration:" -ForegroundColor $MAGENTA
Write-Host "  - Database: Production Supabase (cloud)" -ForegroundColor $MAGENTA
Write-Host "  - Backend: Docker containers" -ForegroundColor $MAGENTA
Write-Host "  - Storage: Production Supabase (cloud)" -ForegroundColor $MAGENTA
Write-Host "  - Config: .env.preprod" -ForegroundColor $MAGENTA
Write-Host ""
Write-Host "[WARNING] This mode operates on PRODUCTION database!" -ForegroundColor $RED
Write-Host ""

# Confirmation prompt
$response = Read-Host "Confirm starting PREPROD environment? (y/n)"
if ($response -ne "y") {
    Write-Host ""
    Write-Host "Cancelled" -ForegroundColor $YELLOW
    exit 0
}

Write-Host ""

# Check if running on Windows
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "Error: This script requires PowerShell 5.0 or higher" -ForegroundColor $RED
    exit 1
}

# Check if Docker is installed
Write-Host "[Check] Verifying Docker installation..." -ForegroundColor $BLUE
try {
    docker --version | Out-Null
} catch {
    Write-Host "Error: Docker is not installed. Please install Docker Desktop" -ForegroundColor $RED
    exit 1
}
Write-Host "  [OK] Docker verified" -ForegroundColor $GREEN
Write-Host ""

# ============================================================
# Auto-detect and stop LOCAL environment (avoid port conflicts)
# ============================================================
Write-Host "[Check] Detecting LOCAL environment..." -ForegroundColor $BLUE

# Check for all LOCAL Docker containers (any with -local suffix)
$localContainers = docker ps -a --filter "name=-local" --format "{{.Names}}" 2>$null

# Check for Web Service (npm/vite processes on port 5173)
$webProcesses = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | 
    Select-Object -ExpandProperty OwningProcess -Unique

if ($localContainers -or $webProcesses) {
    Write-Host "  [!] Other environment detected" -ForegroundColor $YELLOW
    
    if ($localContainers) {
        Write-Host "  Docker containers: $($localContainers -join ', ')" -ForegroundColor $YELLOW
        Write-Host "  Stopping LOCAL Docker containers..." -ForegroundColor $YELLOW
        
        # Stop and remove all LOCAL containers
        Push-Location $DATA_STORAGE_DIR
        $env:APP_ENV = "local"
        docker-compose down 2>$null | Out-Null
        Pop-Location
        
        Push-Location $AI_SERVICE_DIR
        $env:APP_ENV = "local"
        docker-compose down 2>$null | Out-Null
        Pop-Location
    }
    
    if ($webProcesses) {
        Write-Host "  Stopping Web Service processes..." -ForegroundColor $YELLOW
        foreach ($pid in $webProcesses) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
    
    Start-Sleep -Seconds 3
    Write-Host "  [OK] Previous environment stopped" -ForegroundColor $GREEN
} else {
    Write-Host "  [OK] No other environment detected" -ForegroundColor $GREEN
}

Write-Host ""

# Check configuration files
Write-Host "[Check] Verifying PREPROD configuration..." -ForegroundColor $BLUE

$aiPreprodEnv = Join-Path $AI_SERVICE_DIR ".env.preprod"
$dataPreprodEnv = Join-Path $DATA_STORAGE_DIR ".env.preprod"

$configOk = $true

if (-not (Test-Path $aiPreprodEnv)) {
    Write-Host "  [Error] Missing: StorageHelperAIOrchestraService/.env.preprod" -ForegroundColor $RED
    $configOk = $false
}

if (-not (Test-Path $dataPreprodEnv)) {
    Write-Host "  [Error] Missing: StorageHelperDataStorageService/.env.preprod" -ForegroundColor $RED
    $configOk = $false
}

if (-not $configOk) {
    Write-Host ""
    Write-Host "Please create missing .env.preprod configuration files" -ForegroundColor $YELLOW
    Write-Host "Reference: ENVIRONMENT_GUIDE.md" -ForegroundColor $BLUE
    exit 1
}

Write-Host "  [OK] Configuration files ready" -ForegroundColor $GREEN
Write-Host ""

# Step 1: Start Data Storage Service (PREPROD) using Docker
Write-Host "[1/3] Starting Data Storage Service (PREPROD)..." -ForegroundColor $BLUE

# Start in foreground first to ensure network is created
Push-Location $DATA_STORAGE_DIR
$env:APP_ENV = "preprod"

Write-Host "  Stopping old containers..." -ForegroundColor $YELLOW
docker-compose down 2>$null | Out-Null

Write-Host "  Building and starting service..." -ForegroundColor $YELLOW
Write-Host "  (Skipping PostgreSQL - using Supabase)" -ForegroundColor $BLUE
docker-compose up -d --build 2>&1 | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [Error] Failed to start Data Storage Service" -ForegroundColor $RED
    Pop-Location
    exit 1
}

# Wait for network to be created
Write-Host "  Waiting for network..." -ForegroundColor $YELLOW
$maxRetries = 20
$retryCount = 0
$networkName = "storagehelperdatastorageservice_storage-network-preprod"

while ($retryCount -lt $maxRetries) {
    $network = docker network ls --filter "name=$networkName" --format "{{.Name}}" 2>$null
    if ($network -eq $networkName) {
        Write-Host "  [OK] Network ready: $networkName" -ForegroundColor $GREEN
        break
    }
    Start-Sleep -Seconds 1
    $retryCount++
}

if ($retryCount -eq $maxRetries) {
    Write-Host "  [Error] Network creation timeout" -ForegroundColor $RED
    docker-compose ps
    Pop-Location
    exit 1
}

# Wait for container to be healthy
Write-Host "  Waiting for service to be ready..." -ForegroundColor $YELLOW
Start-Sleep -Seconds 3

Pop-Location
Write-Host "  [OK] Data Storage Service started" -ForegroundColor $GREEN
Write-Host ""

# Open logs in new window
$dataStorageLogsCmd = "cd '$DATA_STORAGE_DIR'; `$env:APP_ENV='preprod'; docker-compose logs -f"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $dataStorageLogsCmd -WindowStyle Normal
Write-Host "  [OK] Logs window opened" -ForegroundColor $GREEN
Write-Host ""

# Step 2: Start AI Orchestration Service (PREPROD) using Docker
Write-Host "[2/3] Starting AI Orchestration Service (PREPROD)..." -ForegroundColor $BLUE

# Start in foreground
Push-Location $AI_SERVICE_DIR
$env:APP_ENV = "preprod"

Write-Host "  Stopping old containers..." -ForegroundColor $YELLOW
docker-compose down 2>$null | Out-Null

Write-Host "  Building and starting service..." -ForegroundColor $YELLOW
docker-compose up -d --build 2>&1 | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [Error] Failed to start AI Service" -ForegroundColor $RED
    Pop-Location
    exit 1
}

Write-Host "  Waiting for service to be ready..." -ForegroundColor $YELLOW
Start-Sleep -Seconds 3

Pop-Location
Write-Host "  [OK] AI Orchestration Service started" -ForegroundColor $GREEN
Write-Host ""

# Open logs in new window
$aiLogsCmd = "cd '$AI_SERVICE_DIR'; `$env:APP_ENV='preprod'; docker-compose logs -f"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $aiLogsCmd -WindowStyle Normal
Write-Host "  [OK] Logs window opened" -ForegroundColor $GREEN
Write-Host ""

# Step 3: Start Web Service (same for both LOCAL and PREPROD)
Write-Host "[3/3] Starting Web Service..." -ForegroundColor $BLUE

$webCommand = "cd '$WEB_SERVICE_DIR'; if (-not (Test-Path 'node_modules')) { Write-Host '[Install] Installing dependencies...' -ForegroundColor Cyan; npm install }; Write-Host '[OK] Dependencies installed' -ForegroundColor Green; npm run dev"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCommand -WindowStyle Normal
Start-Sleep -Seconds 1
Write-Host "  [OK] Web Service window opened" -ForegroundColor $GREEN
Write-Host ""

Write-Host ""
Write-Host "========================================" -ForegroundColor $MAGENTA
Write-Host "  All services started (PREPROD mode)" -ForegroundColor $MAGENTA
Write-Host "========================================" -ForegroundColor $MAGENTA
Write-Host ""
Write-Host "Environment: PREPROD" -ForegroundColor $MAGENTA
Write-Host "  - Database: Supabase Production DB" -ForegroundColor $MAGENTA
Write-Host "  - Storage: Supabase Production Storage" -ForegroundColor $MAGENTA
Write-Host "  - Backend: Docker containers" -ForegroundColor $MAGENTA
Write-Host ""
Write-Host "Service URLs:" -ForegroundColor $YELLOW
Write-Host "  [AI]  AI Orchestration -> http://localhost:8888" -ForegroundColor $YELLOW
Write-Host "  [DB]  Data Storage API -> http://localhost:8000 (Swagger: /docs)" -ForegroundColor $YELLOW
Write-Host "  [WEB] Web Service -> http://localhost:5173" -ForegroundColor $YELLOW
Write-Host ""
Write-Host "Tips:" -ForegroundColor $YELLOW
Write-Host "  - View logs: `$env:APP_ENV='preprod'; docker-compose logs -f" -ForegroundColor $YELLOW
Write-Host "  - Stop all: `$env:APP_ENV='preprod'; docker-compose down (in each service dir)" -ForegroundColor $YELLOW
Write-Host ""
Write-Host "[WARNING] Currently operating on PRODUCTION database!" -ForegroundColor $RED
Write-Host ""
Write-Host "Switch to LOCAL: .\start_all.ps1 (auto-stops PREPROD)" -ForegroundColor $YELLOW
