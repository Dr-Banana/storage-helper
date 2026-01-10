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

echo -e "${YELLOW}========== StorageHelper 所有服务启动 ==========${NC}\n"

# 检查 Docker 是否安装
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误：Docker 未安装。请先安装 Docker Desktop${NC}"
    exit 1
fi

# 检查 Docker 是否运行
if ! docker ps &> /dev/null; then
    echo -e "${RED}错误：Docker 未运行。请先启动 Docker Desktop${NC}"
    exit 1
fi

echo -e "${CYAN}✓ Docker 环境检查通过${NC}\n"

# 初始化数据库（在启动 Data Storage Service 之前）
echo -e "${BLUE}[初始化] 初始化数据库...${NC}"
bash "$DATA_STORAGE_DIR/scripts/init-db.sh"
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ 数据库初始化失败${NC}"
    exit 1
fi
echo ""
echo -e "${BLUE}[1/3] 启动 AI Orchestration Service...${NC}"
osascript - "$AI_SERVICE_DIR" <<'SCRIPT'
on run argv
    tell application "Terminal"
        activate
        do script "cd " & quoted form of item 1 of argv & " && docker-compose down && docker-compose up -d && echo '✓ AI Orchestration Service 已启动（端口 8888）' && docker-compose logs -f"
    end tell
end run
SCRIPT
sleep 2
echo -e "${GREEN}✓ AI Orchestration Service 窗口已打开${NC}\n"

# 第二步：启动 Data Storage Service（Docker）
echo -e "${BLUE}[2/3] 启动 Data Storage Service...${NC}"
osascript - "$DATA_STORAGE_DIR" <<'SCRIPT'
on run argv
    tell application "Terminal"
        activate
        do script "cd " & quoted form of item 1 of argv & " && docker-compose up -d && echo '✓ Data Storage Service 已启动（端口 8000）' && docker-compose logs -f"
    end tell
end run
SCRIPT
sleep 2
echo -e "${GREEN}✓ Data Storage Service 窗口已打开${NC}\n"

# 第三步：启动 Web Service（在新窗口中）
echo -e "${BLUE}[3/3] 启动 Web Service...${NC}"
osascript - "$WEB_SERVICE_DIR" <<'SCRIPT'
on run argv
    tell application "Terminal"
        activate
        do script "cd " & quoted form of item 1 of argv & " && if [ ! -d 'node_modules' ]; then npm install; fi && echo '✓ Web Service 依赖已安装' && npm run dev"
    end tell
end run
SCRIPT
sleep 1
echo -e "${GREEN}✓ Web Service 窗口已打开${NC}\n"

echo -e "${YELLOW}========== 所有服务已启动 ==========${NC}\n"
echo -e "${CYAN}服务访问地址：${NC}"
echo -e "  📊 AI Orchestration   → ${BLUE}http://localhost:8888${NC}"
echo -e "  💾 Data Storage API   → ${BLUE}http://localhost:8000${NC} (Swagger: /docs)"
echo -e "  🌐 Web Service        → ${BLUE}http://localhost:5173${NC}"
echo -e ""
echo -e "${YELLOW}提示：${NC}"
echo -e "  • 查看日志：docker-compose logs -f [service_name]"
echo -e "  • 停止所有：docker-compose down（在各服务目录中）"
echo -e "  • 清理资源：docker system prune"
