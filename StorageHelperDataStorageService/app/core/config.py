"""
Application configuration
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
import logging

logger = logging.getLogger(__name__)

def get_env_file() -> Optional[str]:
    """
    Determine which .env file to use based on APP_ENV.
    
    For prod mode, prioritizes /etc/secrets/ directory (for containerized/cloud deployments).
    For other modes, checks local .env file first, then /etc/secrets/ as fallback.
    """
    app_env = os.getenv("APP_ENV", "").lower().strip()
    
    if not app_env:
        return None
    
    env_file = f".env.{app_env}"
    
    # For prod mode, prioritize /etc/secrets/ directory
    if app_env == "prod":
        secret_path = f"/etc/secrets/{env_file}"
        if os.path.exists(secret_path):
            logger.info(f"Loading prod configuration from {secret_path}")
            return secret_path
        
        # Fallback to local file if /etc/secrets/ doesn't exist
        if os.path.exists(env_file):
            logger.info(f"Loading prod configuration from local file {env_file}")
            return env_file
        
        logger.warning(f"Prod mode: No configuration file found in /etc/secrets/ or local directory")
        return None
    
    # For other modes (local, preprod), check local file first
    if os.path.exists(env_file):
        return env_file
    
    # Fallback to /etc/secrets/ for other environments
    secret_path = f"/etc/secrets/{env_file}"
    if os.path.exists(secret_path):
        logger.info(f"Loading {app_env} configuration from {secret_path}")
        return secret_path
        
    return None

class Settings(BaseSettings):
    """Application settings using Pydantic Settings"""
    
    # Environment
    APP_ENV: str = "local"
    
    # Database
    DATABASE_URL: str = "postgresql://postgres:root@localhost:5432/storage_helper"
    
    # Supabase (for storage)
    SUPABASE_URL: Optional[str] = None
    SUPABASE_KEY: Optional[str] = None
    SUPABASE_BUCKET: str = "documents"
    
    # Local Storage
    STORAGE_LOCAL_PATH: str = "./tmp"
    
    # API
    API_TITLE: str = "Storage Helper Data Storage Service"
    API_VERSION: str = "1.0.0"
    API_DESCRIPTION: str = "Database backend for Home AI Paper Organizer"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Pydantic Configuration
    model_config = SettingsConfigDict(
        env_file=get_env_file(), 
        extra='ignore'
    )

settings = Settings()
