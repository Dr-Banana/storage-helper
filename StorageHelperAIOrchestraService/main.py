from fastapi import FastAPI
from app.api.router import api_router
import uvicorn

app = FastAPI(
    title="家用 AI 文件管家 (Orchestra Service)",
    description="处理 OCR、文件分类、搜索和位置推荐的核心服务",
    version="v1"
)

# 挂载 API 路由到 /api/v1 前缀下
app.include_router(api_router, prefix="/api/v1")

# 根路径欢迎信息
@app.get("/", include_in_schema=False)
async def root():
    return {"message": "StorageHelper AI Orchestration Service is running. Access /docs for API documentation."}

if __name__ == "__main__":
    # 使用 Uvicorn 启动服务器
    uvicorn.run(app, host="0.0.0.0", port=8000)