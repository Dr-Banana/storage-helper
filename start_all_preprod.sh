#!/bin/bash

# Base paths
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
AI_SERVICE_DIR="$BASE_DIR/StorageHelperAIOrchestraService"
DATA_STORAGE_DIR="$BASE_DIR/StorageHelperDataStorageService"
WEB_SERVICE_DIR="$BASE_DIR/StorageHelperWebService"

# Colors
MAGENTA='\033[0;35m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${MAGENTA}========== Starting all services in PREPROD mode ==========${NC}"
echo -e "${YELLOW}Preprod mode: Backends use Prod credentials but run locally${NC}"
echo -e "${CYAN}Web Service: Runs in Local mode to connect to local backends${NC}"
echo ""

# Function to open a new terminal window and run a command
# This works on macOS (Terminal.app) and Linux (gnome-terminal, xterm, etc.)
run_in_new_window() {
    local title=$1
    local cmd=$2
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        osascript -e "tell application \"Terminal\" to do script \"$cmd\""
    elif command -v gnome-terminal &>/dev/null; then
        # Linux with GNOME
        gnome-terminal --title="$title" -- bash -c "$cmd; exec bash"
    elif command -v xterm &>/dev/null; then
        # Generic Linux xterm
        xterm -T "$title" -e "bash -c \"$cmd; exec bash\"" &
    else
        echo -e "${RED}Error: No supported terminal emulator found (gnome-terminal, xterm).${NC}"
        echo -e "Please run this command manually in a new window:"
        echo -e "  $cmd"
    fi
}

# Step 1: Start Data Storage Service (PREPROD)
echo -e "${CYAN}[1/3] Starting Data Storage Service (PREPROD)...${NC}"

data_storage_cmd="cd '$DATA_STORAGE_DIR'; if [ ! -d 'venv' ]; then python3 -m venv venv; fi; source venv/bin/activate; pip install -q -r requirements.txt; echo 'Starting PREPROD mode (Cloud DB)...'; ./scripts/start_preprod.sh"

run_in_new_window "Data Storage Service" "$data_storage_cmd"
sleep 3
echo -e "${GREEN}Data Storage Service window opened${NC}"
sleep 5

# Step 2: Start AI Orchestration Service (PREPROD)
echo -e "${CYAN}[2/3] Starting AI Orchestration Service (PREPROD)...${NC}"

ai_service_cmd="cd '$AI_SERVICE_DIR'; if [ ! -d 'env' ]; then python3 -m venv env; fi; source env/bin/activate; pip install -q -r requirements.txt; echo 'Starting PREPROD mode...'; ./script/start_preprod.sh"

run_in_new_window "AI Service" "$ai_service_cmd"
sleep 2
echo -e "${GREEN}AI Orchestration Service window opened${NC}"

# Step 3: Web Service (LOCAL DEV MODE)
echo -e "${CYAN}[3/3] Starting Web Service (Local Dev Mode)...${NC}"

web_service_cmd="cd '$WEB_SERVICE_DIR'; echo 'Checking dependencies...'; if [ ! -d 'node_modules' ]; then npm install; fi; echo 'Starting Vite Dev Server (Local Mode)...'; npm run dev"

run_in_new_window "Web Service" "$web_service_cmd"
sleep 1
echo -e "${GREEN}Web Service window opened${NC}"

echo ""
echo -e "${MAGENTA}========== All service startup commands sent ==========${NC}"
echo -e "${YELLOW}Service URLs:${NC}"
echo -e "  - Data Storage Service: ${GREEN}http://localhost:8000${NC}"
echo -e "  - AI Orchestration Service: ${GREEN}http://localhost:8888${NC}"
echo -e "  - Web Service (Local Dev): ${GREEN}http://localhost:3000${NC}"
echo ""
echo -e "Note: Frontend uses Local config (port 3000) to proxy requests to local backends."

