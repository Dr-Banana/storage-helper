"""
FastAPI application entry point

Minimal setup with only essential routes:
- /api/users - User management (create user)
- /api/auth/google - Google OAuth authentication
- /api/v1 - Public API for AI Service (high-level operations)
"""
import logging
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# IMPORTANT: Load settings FIRST to get APP_ENV, then conditionally load .env.local
# This ensures APP_ENV from environment or .env.{APP_ENV} takes precedence
from app.core.config import settings

# Only load .env.local if APP_ENV is explicitly set to "local"
# This prevents .env.local from overriding preprod/prod configurations
if settings.APP_ENV == "local" and os.path.exists(".env.local"):
    load_dotenv(".env.local", override=False)
    logger = logging.getLogger(__name__)
    logger.info("Loaded .env.local for local development")
from app.core.database import engine, Base
from app.routes import users, public_api, documents, location_images, google_auth, auth, schedule
from app.services.google_auth_service import GoogleAuthService
# Import all models to register them with SQLAlchemy
from app.models import (
    User, DocumentCategory, StorageLocation, Event, 
    Document, DocumentPage, DocumentEmbedding, FeedbackMessage, Schedule
)

# Configure logging
logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

# Log current environment configuration
logger.info(f"Running in {settings.APP_ENV.upper()} mode")
logger.info(f"APP_ENV from environment: {os.getenv('APP_ENV', 'NOT SET')}")
logger.info(f"APP_ENV from settings: {settings.APP_ENV}")
if settings.APP_ENV in ("preprod", "prod"):
    logger.info(f"Storage: Supabase ({settings.SUPABASE_URL or 'Not configured'})")
    logger.info(f"Supabase Bucket: {settings.SUPABASE_BUCKET}")
    logger.info(f"Supabase Key: {'configured' if settings.SUPABASE_KEY else 'NOT configured'}")
    logger.info(f"Database: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else 'Local'}")
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        logger.error("WARNING: APP_ENV is preprod/prod but Supabase is not configured! Will use local storage.")
else:
    logger.info(f"Storage: Local filesystem ({settings.STORAGE_LOCAL_PATH})")

# Create all tables
Base.metadata.create_all(bind=engine)

# Column-level migrations for existing databases (create_all only adds NEW tables)
try:
    from sqlalchemy import text as _sql_text
    with engine.connect() as _conn:
        _conn.execute(_sql_text(
            "ALTER TABLE storage_location ADD COLUMN IF NOT EXISTS content_analysis JSON"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS cooking_level VARCHAR(20) NOT NULL DEFAULT 'beginner'"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS language VARCHAR(10) NOT NULL DEFAULT 'zh'"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS default_servings INTEGER NOT NULL DEFAULT 1"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS meat_veg_ratio VARCHAR(20) NOT NULL DEFAULT '1:1:1'"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS include_soup BOOLEAN NOT NULL DEFAULT true"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS calorie_target INTEGER"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS disliked_ingredients JSON DEFAULT '[]'::json"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS cuisine_weights JSON DEFAULT '{\"Chinese\": 50, \"Western\": 20, \"Japanese\": 15, \"Korean\": 10, \"Other\": 5}'::json"
        ))
        _conn.execute(_sql_text(
            "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS recent_dishes JSON DEFAULT '[]'::json"
        ))
        _conn.commit()
    logger.info("DB migration: user preference columns (disliked_ingredients, cuisine_weights, recent_dishes) ensured")
except Exception as _e:
    logger.warning(f"DB migration check skipped: {_e}")

# Initialize Google OAuth service with client ID from environment
# Try multiple sources: system env, then settings
google_client_id = os.getenv("GOOGLE_CLIENT_ID") or getattr(settings, "GOOGLE_CLIENT_ID", None)
if google_client_id:
    GoogleAuthService.set_client_id(google_client_id)
    logger.info(f"Google OAuth configured with Client ID: {google_client_id[:20]}...")
else:
    logger.error("GOOGLE_CLIENT_ID not set - Google OAuth will not work")
    logger.error("Please set GOOGLE_CLIENT_ID environment variable in Render dashboard or .env file")

# Initialize FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    description=settings.API_DESCRIPTION,
    version=settings.API_VERSION
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(users.router, prefix="/api", tags=["users"])
app.include_router(documents.router, prefix="/api", tags=["documents"])
app.include_router(schedule.router, prefix="/api", tags=["schedule"])
app.include_router(location_images.router, tags=["location-images"])
app.include_router(public_api.router, tags=["public-api"])
app.include_router(google_auth.router, prefix="/api", tags=["authentication"])
app.include_router(auth.router, prefix="/api", tags=["authentication"])


@app.get("/", tags=["root"])
def root():
    """Root endpoint"""
    return {
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
        "status": "running"
    }


@app.get("/health", tags=["health"])
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "database": "connected"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level=settings.LOG_LEVEL.lower()
    )
