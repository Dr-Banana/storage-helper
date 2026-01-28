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

# Load environment variables from .env.local (only if exists, for local development)
# Use override=False to ensure system environment variables (from Render) take precedence
if os.path.exists(".env.local"):
    load_dotenv(".env.local", override=False)

from app.core.config import settings
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

# Log if .env.local was loaded (for local development)
if os.path.exists(".env.local"):
    logger.info("Loaded .env.local for local development")

# Create all tables
Base.metadata.create_all(bind=engine)

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
