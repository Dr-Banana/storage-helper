import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import uvicorn

_app_env = os.getenv("APP_ENV", "").lower().strip()
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

if _app_env == "local":
    logging.basicConfig(level=logging.WARNING, format=_LOG_FORMAT)
    logging.getLogger("app").setLevel(logging.DEBUG)
    _uvicorn_log_level = "debug"
else:
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
    logging.getLogger("app").setLevel(logging.INFO)
    _uvicorn_log_level = "info"

from app.api.router import api_router

app = FastAPI(
    title="StorageHelper AI Orchestration Service",
    description="Meal planning AI agent",
    version="v2",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "StorageHelper AI Orchestration Service is running. See /docs for API."}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888, log_level=_uvicorn_log_level)
