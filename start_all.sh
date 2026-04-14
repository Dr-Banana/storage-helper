#!/bin/bash

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 基础路径
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_SERVICE_DIR="$BASE_DIR/StorageHelperAIOrchestraService"
DATA_STORAGE_DIR="$BASE_DIR/StorageHelperDataStorageService"
WEB_SERVICE_DIR="$BASE_DIR/StorageHelperWebService"
FOODIE_SERVICE_DIR="$BASE_DIR/FoodieService"

echo -e "${YELLOW}========== StorageHelper - Start All Services ==========${NC}\n"

# Check Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed. Please install Docker Desktop${NC}"
    exit 1
fi

# Check Docker is running
if ! docker ps &> /dev/null; then
    echo -e "${RED}Error: Docker is not running. Please start Docker Desktop${NC}"
    exit 1
fi

echo -e "${CYAN}✓ Docker check passed${NC}\n"

# Initialize database before starting Data Storage Service
echo -e "${BLUE}[Init] Initializing database...${NC}"
bash "$DATA_STORAGE_DIR/scripts/init-db.sh"
if [ $? -ne 0 ]; then
    echo -e "${RED}[Error] Database initialization failed${NC}"
    exit 1
fi
echo ""
echo -e "${BLUE}[1/4] Starting Data Storage Service...${NC}"
osascript - "$DATA_STORAGE_DIR" <<'SCRIPT'
on run argv
    tell application "Terminal"
        activate
        do script "cd " & quoted form of item 1 of argv & " && echo 'Stopping old containers...' && APP_ENV=local docker-compose down && echo 'Building and starting service...' && APP_ENV=local docker-compose --profile local up -d --build && echo '[OK] Data Storage Service started (port 8000)' && APP_ENV=local docker-compose logs -f"
    end tell
end run
SCRIPT
sleep 2
echo -e "${GREEN}✓ Data Storage Service window opened${NC}\n"

echo -e "${BLUE}[2/4] Starting FoodieService (HowToCook MCP)...${NC}"
osascript - "$FOODIE_SERVICE_DIR" <<'SCRIPT'
on run argv
    tell application "Terminal"
        activate
        do script "cd " & quoted form of item 1 of argv & " && echo 'Stopping old containers...' && APP_ENV=local docker-compose down && echo 'Building and starting service...' && APP_ENV=local docker-compose up -d --build && echo '[OK] FoodieService started (host port 3010)' && APP_ENV=local docker-compose logs -f"
    end tell
end run
SCRIPT
sleep 2
echo -e "${GREEN}✓ FoodieService window opened${NC}\n"

echo -e "${BLUE}[3/4] Starting AI Orchestration Service...${NC}"
osascript - "$AI_SERVICE_DIR" <<'SCRIPT'
on run argv
    tell application "Terminal"
        activate
        do script "cd " & quoted form of item 1 of argv & " && echo 'Stopping old containers...' && docker-compose down && echo 'Building and starting service...' && docker-compose up -d --build && echo '[OK] AI Orchestration Service started (port 8888)' && docker-compose logs -f"
    end tell
end run
SCRIPT
sleep 2
echo -e "${GREEN}✓ AI Orchestration Service window opened${NC}\n"

# Step 4: Start Web Service
echo -e "${BLUE}[4/4] Starting Web Service...${NC}"
osascript - "$WEB_SERVICE_DIR" <<'SCRIPT'
on run argv
    tell application "Terminal"
        activate
        do script "cd " & quoted form of item 1 of argv & " && if [ ! -d 'node_modules' ]; then npm install; fi && echo '[OK] Dependencies installed' && npm run dev"
    end tell
end run
SCRIPT
sleep 1
echo -e "${GREEN}✓ Web Service window opened${NC}\n"

echo -e "${YELLOW}========== All services started ==========${NC}\n"
echo -e "${CYAN}Service URLs:${NC}"
echo -e "  [DB]  Data Storage API  -> ${BLUE}http://localhost:8000${NC} (Swagger: /docs)"
echo -e "  [MCP] HowToCook MCP     -> ${BLUE}http://localhost:3010/health${NC}"
echo -e "  [AI]  AI Orchestration  -> ${BLUE}http://localhost:8888${NC}"
echo -e "  [WEB] Web Service       -> ${BLUE}http://localhost:5173${NC}"
echo -e ""
echo -e "${YELLOW}Tips:${NC}"
echo -e "  - View logs: docker-compose logs -f [service_name]"
echo -e "  - Stop all: docker-compose down (in each service directory)"
echo -e "  - Clean up: docker system prune"
