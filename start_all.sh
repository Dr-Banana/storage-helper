#!/bin/bash

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 基础路径
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_SERVICE_DIR="$BASE_DIR/StorageHelperAIOrchestraService"
DATA_STORAGE_DIR="$BASE_DIR/StorageHelperDataStorageService"
WEB_SERVICE_DIR="$BASE_DIR/StorageHelperWebService"

echo -e "${YELLOW}========== 在不同窗口中启动所有服务 ==========${NC}\n"

# 检查依赖
if ! command -v osascript &> /dev/null; then
    echo -e "${RED}错误：此脚本仅在 macOS 上运行${NC}"
    exit 1
fi

# 第一步：启动 AI Orchestration Service（在新窗口中）
echo -e "${BLUE}[1/3] 在新终端窗口启动 AI Orchestration Service...${NC}"
osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$AI_SERVICE_DIR' && if [ ! -d 'env' ]; then python3 -m venv env; fi && source env/bin/activate && pip install -q -r requirements.txt && echo '✓ AI Orchestration Service 虚拟环境已激活' && docker-compose down && docker-compose build --no-cache && docker-compose up -d && echo '✓ AI Orchestration Service 已启动'"
end tell
EOF
sleep 2
echo -e "${GREEN}✓ AI Orchestration Service 窗口已打开${NC}\n"

# 第二步：启动 Data Storage Service（在新窗口中）
echo -e "${BLUE}[2/3] 在新终端窗口启动 Data Storage Service...${NC}"
osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$DATA_STORAGE_DIR' && echo '🚀 启动 PostgreSQL 数据库...' && docker-compose up -d postgres && sleep 5 && if [ ! -d 'venv' ]; then python3 -m venv venv; fi && source venv/bin/activate && pip install -q -r requirements.txt && echo '✓ Data Storage Service 虚拟环境已激活' && bash scripts/start_local.sh"
end tell
EOF
sleep 2
echo -e "${GREEN}✓ Data Storage Service 窗口已打开${NC}\n"

# 第三步：启动 Web Service（在新窗口中）
echo -e "${BLUE}[3/3] 在新终端窗口启动 Web Service...${NC}"
osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$WEB_SERVICE_DIR' && echo '✓ Web Service 目录已进入' && bash scripts/start_local.sh"
end tell
EOF
sleep 1
echo -e "${GREEN}✓ Web Service 窗口已打开${NC}\n"

echo -e "${YELLOW}========== 所有服务已在新窗口中启动 ==========${NC}"
echo -e "${YELLOW}请检查各个终端窗口以查看服务运行情况${NC}"
