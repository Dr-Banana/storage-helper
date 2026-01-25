from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.router import api_router
import uvicorn
import logging

# 配置日志输出
logging.basicConfig(
    level=logging.WARNING,  # Changed from INFO to WARNING to reduce verbosity
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# Explicitly set httpx logger to WARNING to suppress HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Keep app logs at INFO level if needed, or let them inherit WARNING
# To keep app specific logs at INFO while silencing libraries:
logging.getLogger("app").setLevel(logging.INFO)

app = FastAPI(
    title="家用 AI 文件管家 (Orchestra Service)",
    description="处理 OCR、文件分类、搜索和位置推荐的核心服务",
    version="v1"
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API 路由到 /api/v1 前缀下
app.include_router(api_router, prefix="/api/v1")

# 根路径欢迎信息
@app.get("/", include_in_schema=False)
async def root():
    return {"message": "StorageHelper AI Orchestration Service is running. Access /docs for API documentation."}

if __name__ == "__main__":
    # 使用 Uvicorn 启动服务器
    uvicorn.run(app, host="0.0.0.0", port=8888)