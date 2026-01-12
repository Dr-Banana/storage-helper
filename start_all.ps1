# PowerShell script to start all services using Docker
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

Write-Host "========== StorageHelper 所有服务启动 ==========" -ForegroundColor $YELLOW
Write-Host ""

# Check if running on Windows
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "错误：此脚本需要 PowerShell 5.0 或更高版本" -ForegroundColor $RED
    exit 1
}

# Check if Docker is installed
Write-Host "检查 Docker 环境..." -ForegroundColor $BLUE
try {
    docker --version | Out-Null
} catch {
    Write-Host "错误：Docker 未安装。请先安装 Docker Desktop" -ForegroundColor $RED
    exit 1
}

Write-Host "✓ Docker 环境检查通过" -ForegroundColor $GREEN
Write-Host ""

# Initialize database before starting services
Write-Host "[初始化] 初始化数据库..." -ForegroundColor $BLUE
$initDbScript = Join-Path $DATA_STORAGE_DIR "scripts\\init-db.ps1"
& $initDbScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 数据库初始化失败" -ForegroundColor $RED
    exit 1
}
Write-Host ""

# Step 1: Start AI Orchestration Service in a new window
Write-Host "[1/3] 启动 AI Orchestration Service..." -ForegroundColor $BLUE

$aiCommand = @"
cd '$AI_SERVICE_DIR'
Write-Host '停止并清理旧容器...' -ForegroundColor Cyan
docker-compose down
Write-Host '重新构建并启动服务...' -ForegroundColor Cyan
docker-compose up -d --build
Write-Host ''
Write-Host '✓ AI Orchestration Service 已启动（端口 8888）' -ForegroundColor Green
docker-compose logs -f
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $aiCommand -WindowStyle Normal
Start-Sleep -Seconds 2
Write-Host "✓ AI Orchestration Service 窗口已打开" -ForegroundColor $GREEN
Write-Host ""

# ... (Step 1.5 保持不变)

# Step 2: Start Data Storage Service in a new window
Write-Host "[2/3] 启动 Data Storage Service..." -ForegroundColor $BLUE

$dataStorageCommand = @"
cd '$DATA_STORAGE_DIR'
Write-Host '停止并清理旧容器...' -ForegroundColor Cyan
docker-compose down
Write-Host '重新构建并启动服务...' -ForegroundColor Cyan
docker-compose up -d --build
Write-Host ''
Write-Host '✓ Data Storage Service 已启动（端口 8000）' -ForegroundColor Green
docker-compose logs -f
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $dataStorageCommand -WindowStyle Normal
Start-Sleep -Seconds 2
Write-Host "✓ Data Storage Service 窗口已打开" -ForegroundColor $GREEN
Write-Host ""

# Step 3: Start Web Service in a new window
Write-Host "[3/3] 启动 Web Service..." -ForegroundColor $BLUE

$webCommand = @"
cd '$WEB_SERVICE_DIR'
if (-not (Test-Path 'node_modules')) {
    Write-Host '📦 安装 Web Service 依赖...' -ForegroundColor Cyan
    npm install
}
Write-Host '✓ Web Service 依赖已安装' -ForegroundColor Green
npm run dev
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCommand -WindowStyle Normal
Start-Sleep -Seconds 1
Write-Host "✓ Web Service 窗口已打开" -ForegroundColor $GREEN
Write-Host ""

Write-Host "========== 所有服务已启动 ==========" -ForegroundColor $YELLOW
Write-Host ""
Write-Host "服务访问地址：" -ForegroundColor $BLUE
Write-Host "  📊 AI Orchestration   → http://localhost:8888" -ForegroundColor $BLUE
Write-Host "  💾 Data Storage API   → http://localhost:8000 (Swagger: /docs)" -ForegroundColor $BLUE
Write-Host "  🌐 Web Service        → http://localhost:5173" -ForegroundColor $BLUE
Write-Host ""
Write-Host "提示：" -ForegroundColor $YELLOW
Write-Host "  • 查看日志：docker-compose logs -f [service_name]" -ForegroundColor $YELLOW
Write-Host "  • 停止所有：docker-compose down（在各服务目录中）" -ForegroundColor $YELLOW
Write-Host "  • 清理资源：docker system prune" -ForegroundColor $YELLOW
