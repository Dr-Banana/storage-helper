# PowerShell script to start all services in separate windows
# For Windows users

# Set UTF-8 encoding for proper Chinese character display
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

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

Write-Host "========== 在不同窗口中启动所有服务 ==========" -ForegroundColor $YELLOW
Write-Host ""

# Check if running on Windows
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "错误：此脚本需要 PowerShell 5.0 或更高版本" -ForegroundColor $RED
    exit 1
}

# Step 1: Start AI Orchestration Service in a new window
Write-Host "[1/3] 在新 PowerShell 窗口启动 AI Orchestration Service..." -ForegroundColor $BLUE

$aiCommand = @"
cd '$AI_SERVICE_DIR'
if (-not (Test-Path 'env')) {
    python -m venv env
}
& .\env\Scripts\Activate.ps1
pip install -q -r requirements.txt
Write-Host '✓ AI Orchestration Service 虚拟环境已激活' -ForegroundColor Green

Write-Host '🔍 Running all unit tests and environment checks...' -ForegroundColor Yellow
python -m pytest tests/
`$testResult = `$LASTEXITCODE
if (`$testResult -ne 0) {
    Write-Host ''
    Write-Host '❌ Tests failed! Please fix the issues before starting services.' -ForegroundColor Red
    Write-Host "Exit code: `$testResult" -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}
Write-Host ''
Write-Host '✅ All tests passed' -ForegroundColor Green

docker-compose down
docker-compose build --no-cache
docker-compose up -d
Write-Host '✓ AI Orchestration Service 已启动' -ForegroundColor Green
Read-Host "按 Enter 键保持此窗口开放"
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $aiCommand -WindowStyle Normal
Start-Sleep -Seconds 2
Write-Host "✓ AI Orchestration Service 窗口已打开" -ForegroundColor $GREEN
Write-Host ""

# Step 2: Start Data Storage Service in a new window
Write-Host "[2/3] 在新 PowerShell 窗口启动 Data Storage Service..." -ForegroundColor $BLUE

$dataStorageCommand = @"
cd '$DATA_STORAGE_DIR'
Write-Host '🚀 启动 PostgreSQL 数据库...' -ForegroundColor Cyan
docker-compose up -d postgres
Start-Sleep -Seconds 5
if (-not (Test-Path 'venv')) {
    python -m venv venv
}
& .\venv\Scripts\Activate.ps1
pip install -q -r requirements.txt
Write-Host '✓ Data Storage Service 虚拟环境已激活' -ForegroundColor Green
& .\scripts\start_local.ps1
Read-Host "按 Enter 键保持此窗口开放"
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $dataStorageCommand -WindowStyle Normal
Start-Sleep -Seconds 2
Write-Host "✓ Data Storage Service 窗口已打开" -ForegroundColor $GREEN
Write-Host ""

# Step 3: Start Web Service in a new window
Write-Host "[3/3] 在新 PowerShell 窗口启动 Web Service..." -ForegroundColor $BLUE

$webCommand = @"
cd '$WEB_SERVICE_DIR'
Write-Host '✓ Web Service 目录已进入' -ForegroundColor Green
& .\scripts\start_local.ps1
Read-Host "按 Enter 键保持此窗口开放"
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCommand -WindowStyle Normal
Start-Sleep -Seconds 1
Write-Host "✓ Web Service 窗口已打开" -ForegroundColor $GREEN
Write-Host ""

Write-Host "========== 所有服务已在新窗口中启动 ==========" -ForegroundColor $YELLOW
Write-Host "请检查各个 PowerShell 窗口以查看服务运行情况" -ForegroundColor $YELLOW
