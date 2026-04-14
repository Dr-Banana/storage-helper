# PowerShell script to start all services using Docker
# For Windows users

# Colors
$RED = "Red"
$GREEN = "Green"
$YELLOW = "Yellow"
$BLUE = "Cyan"

# Base paths
$BASE_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$AI_SERVICE_DIR = Join-Path $BASE_DIR "StorageHelperAIOrchestraService"
$DATA_STORAGE_DIR = Join-Path $BASE_DIR "StorageHelperDataStorageService"
$WEB_SERVICE_DIR = Join-Path $BASE_DIR "StorageHelperWebService"
$FOODIE_SERVICE_DIR = Join-Path $BASE_DIR "FoodieService"

Write-Host "========================================" -ForegroundColor $YELLOW
Write-Host "  StorageHelper - LOCAL Environment" -ForegroundColor $YELLOW
Write-Host "========================================" -ForegroundColor $YELLOW
Write-Host ""
Write-Host "Configuration:" -ForegroundColor $BLUE
Write-Host "  - Database: Local PostgreSQL (Docker)" -ForegroundColor $BLUE
Write-Host "  - Backend: Docker containers" -ForegroundColor $BLUE
Write-Host "  - Storage: Local filesystem" -ForegroundColor $BLUE
Write-Host "  - Config: .env.local" -ForegroundColor $BLUE
Write-Host ""

# Check if running on Windows
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "Error: This script requires PowerShell 5.0 or higher" -ForegroundColor $RED
    exit 1
}

# ============================================================
# Auto-detect and stop PREPROD environment (avoid port conflicts)
# ============================================================
Write-Host "[Check] Detecting PREPROD environment..." -ForegroundColor $BLUE

# Check for all PREPROD Docker containers (any with -preprod suffix)
$preprodContainers = docker ps -a --filter "name=-preprod" --format "{{.Names}}" 2>$null

# Check for Web Service (npm/vite processes on port 5173)
$webProcesses = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | 
    Select-Object -ExpandProperty OwningProcess -Unique

if ($preprodContainers -or $webProcesses) {
    Write-Host "  [!] Other environment detected" -ForegroundColor $YELLOW
    
    if ($preprodContainers) {
        Write-Host "  Docker containers: $($preprodContainers -join ', ')" -ForegroundColor $YELLOW
        Write-Host "  Stopping PREPROD Docker containers..." -ForegroundColor $YELLOW

        # Stop and remove all PREPROD containers
        Push-Location $DATA_STORAGE_DIR
        $env:APP_ENV = "preprod"
        docker-compose down 2>$null | Out-Null
        Pop-Location

        Push-Location $AI_SERVICE_DIR
        $env:APP_ENV = "preprod"
        docker-compose down 2>$null | Out-Null
        Pop-Location

        Push-Location $FOODIE_SERVICE_DIR
        $env:APP_ENV = "preprod"
        docker-compose down 2>$null | Out-Null
        Pop-Location
    }
    
    if ($webProcesses) {
        Write-Host "  Stopping Web Service processes..." -ForegroundColor $YELLOW
        foreach ($pid in $webProcesses) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
    
    Start-Sleep -Seconds 2
    Write-Host "  [OK] Previous environment stopped" -ForegroundColor $GREEN
} else {

    Write-Host "  [OK] No other environment detected" -ForegroundColor $GREEN
}

Write-Host ""

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
# Vercel Build Check (Frontend)
# ============================================================
Write-Host "[Check] Verifying Web Service build (Vercel simulation)..." -ForegroundColor $BLUE
Push-Location $WEB_SERVICE_DIR
try {
    if (-not (Test-Path 'node_modules')) {
        Write-Host "  Installing dependencies..." -ForegroundColor $YELLOW
        npm install | Out-Null
    }
    
    Write-Host "  Running 'npm run build'..." -ForegroundColor $YELLOW
    $buildProcess = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "build" -PassThru -NoNewWindow -Wait
    
    if ($buildProcess.ExitCode -ne 0) {
        Write-Host "  [Error] Web Service build failed! Fix errors before starting." -ForegroundColor $RED
        Pop-Location
        exit 1
    }
    Write-Host "  [OK] Build verification passed" -ForegroundColor $GREEN
} catch {
    Write-Host "  [Error] Build process failed: $_" -ForegroundColor $RED
    Pop-Location
    exit 1
}
Pop-Location
Write-Host ""

# Initialize database before starting services
Write-Host "[Init] Initializing database..." -ForegroundColor $BLUE
$initDbScript = Join-Path $DATA_STORAGE_DIR "scripts\init-db.ps1"
& $initDbScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "[Error] Database initialization failed" -ForegroundColor $RED
    exit 1
}
Write-Host ""

# Step 1: Start Data Storage Service
Write-Host "[1/3] Starting Data Storage Service..." -ForegroundColor $BLUE

# Start in foreground first to ensure network is created
Push-Location $DATA_STORAGE_DIR
$env:APP_ENV = "local"

Write-Host "  Stopping old containers..." -ForegroundColor $YELLOW
docker-compose down 2>$null | Out-Null

Write-Host "  Building and starting service..." -ForegroundColor $YELLOW
docker-compose --profile local up -d --build 2>&1 | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [Error] Failed to start Data Storage Service" -ForegroundColor $RED
    Pop-Location
    exit 1
}

# Wait for network to be created
Write-Host "  Waiting for network..." -ForegroundColor $YELLOW
$maxRetries = 20
$retryCount = 0
$networkName = "storagehelperdatastorageservice_storage-network-local"

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

# Wait for PostgreSQL to be healthy
Write-Host "  Waiting for PostgreSQL..." -ForegroundColor $YELLOW
Start-Sleep -Seconds 5

Pop-Location
Write-Host "  [OK] Data Storage Service started" -ForegroundColor $GREEN
Write-Host ""

# Open logs in new window
$dataStorageLogsCmd = "cd '$DATA_STORAGE_DIR'; `$env:APP_ENV='local'; docker-compose logs -f"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $dataStorageLogsCmd -WindowStyle Normal
Write-Host "  [OK] Logs window opened" -ForegroundColor $GREEN
Write-Host ""

# Step 2: Start FoodieService (howtocook-mcp)
Write-Host "[2/4] Starting FoodieService (HowToCook MCP)..." -ForegroundColor $BLUE

Push-Location $FOODIE_SERVICE_DIR
$env:APP_ENV = "local"

Write-Host "  Stopping old containers..." -ForegroundColor $YELLOW
docker-compose down 2>$null | Out-Null

Write-Host "  Building and starting service..." -ForegroundColor $YELLOW
docker-compose up -d --build 2>&1 | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [Error] Failed to start FoodieService" -ForegroundColor $RED
    Pop-Location
    exit 1
}

Write-Host "  Waiting for howtocook-mcp to be ready..." -ForegroundColor $YELLOW
Start-Sleep -Seconds 5

Pop-Location
Write-Host "  [OK] FoodieService started" -ForegroundColor $GREEN
Write-Host ""

# Open logs in new window
$foodieLogsCmd = "cd '$FOODIE_SERVICE_DIR'; `$env:APP_ENV='local'; docker-compose logs -f"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $foodieLogsCmd -WindowStyle Normal
Write-Host "  [OK] Logs window opened" -ForegroundColor $GREEN
Write-Host ""

# Step 3: Start AI Orchestration Service
Write-Host "[3/4] Starting AI Orchestration Service..." -ForegroundColor $BLUE

# Start in foreground
Push-Location $AI_SERVICE_DIR
$env:APP_ENV = "local"

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
$aiLogsCmd = "cd '$AI_SERVICE_DIR'; `$env:APP_ENV='local'; docker-compose logs -f"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $aiLogsCmd -WindowStyle Normal
Write-Host "  [OK] Logs window opened" -ForegroundColor $GREEN
Write-Host ""

# Step 4: Start Web Service in a new window
Write-Host "[4/4] Starting Web Service..." -ForegroundColor $BLUE

$webCommand = "cd '$WEB_SERVICE_DIR'; if (-not (Test-Path 'node_modules')) { Write-Host '[Install] Installing dependencies...' -ForegroundColor Cyan; npm install }; Write-Host '[OK] Dependencies installed' -ForegroundColor Green; npm run dev"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCommand -WindowStyle Normal
Start-Sleep -Seconds 1
Write-Host "  [OK] Web Service window opened" -ForegroundColor $GREEN
Write-Host ""

Write-Host "========================================" -ForegroundColor $YELLOW
Write-Host "  All services started (LOCAL mode)" -ForegroundColor $YELLOW
Write-Host "========================================" -ForegroundColor $YELLOW
Write-Host ""
Write-Host "Environment: LOCAL" -ForegroundColor $GREEN
Write-Host "  - Database: Local PostgreSQL (localhost:5432)" -ForegroundColor $GREEN
Write-Host "  - Storage: Local filesystem (./tmp)" -ForegroundColor $GREEN
Write-Host "  - Config: .env.local" -ForegroundColor $GREEN
Write-Host ""
Write-Host "Service URLs:" -ForegroundColor $BLUE
Write-Host "  [AI]  AI Orchestration  -> http://localhost:8888" -ForegroundColor $BLUE
Write-Host "  [DB]  Data Storage API  -> http://localhost:8000 (Swagger: /docs)" -ForegroundColor $BLUE
Write-Host "  [MCP] HowToCook MCP     -> http://localhost:3010/health" -ForegroundColor $BLUE
Write-Host "  [WEB] Web Service       -> http://localhost:5173" -ForegroundColor $BLUE
Write-Host ""
Write-Host "Tips:" -ForegroundColor $YELLOW
Write-Host "  - View logs: docker-compose logs -f [service_name]" -ForegroundColor $YELLOW
Write-Host "  - Stop all: docker-compose down (in each service directory)" -ForegroundColor $YELLOW
Write-Host "  - Clean up: docker system prune" -ForegroundColor $YELLOW
Write-Host ""
Write-Host "Switch to PREPROD: .\start_all_preprod.ps1 (auto-stops LOCAL)" -ForegroundColor $YELLOW
