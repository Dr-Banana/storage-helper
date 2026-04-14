import asyncio
import logging
import os
from contextlib import asynccontextmanager

# 在所有 app 模块导入之前完成日志配置，确保启动阶段的日志也受控
_app_env = os.getenv("APP_ENV", "").lower().strip()
_is_local = _app_env == "local"

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

if _is_local:
    # 本地开发：app 代码输出 DEBUG，第三方库保持 WARNING
    logging.basicConfig(level=logging.WARNING, format=_LOG_FORMAT)
    logging.getLogger("app").setLevel(logging.DEBUG)
    _uvicorn_log_level = "debug"
else:
    # preprod / prod：INFO 及以上，确保 Render 等平台能看到访问日志和启动信息
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
    logging.getLogger("app").setLevel(logging.INFO)
    _uvicorn_log_level = "info"

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.router import api_router
import uvicorn


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────
    from app.services.howtocook_service import get_howtocook_service
    htc = get_howtocook_service()
    await htc.initialize()
    sync_task = asyncio.create_task(htc.start_background_sync())

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass
    htc.shutdown()


app = FastAPI(
    title="家用 AI 文件管家 (Orchestra Service)",
    description="处理 OCR、文件分类、搜索和位置推荐的核心服务",
    version="v1",
    lifespan=lifespan,
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
    uvicorn.run(app, host="0.0.0.0", port=8888, log_level=_uvicorn_log_level)