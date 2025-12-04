"""
Application configuration
"""
import os
from typing import Optional

class Settings:
    """Application settings"""
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root:root@localhost:3306/storage_helper"
    )
    
    # API
    API_TITLE: str = "Storage Helper Data Storage Service"
    API_VERSION: str = "1.0.0"
    API_DESCRIPTION: str = "Database backend for Home AI Paper Organizer"
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    class Config:
        env_file = ".env.local"


settings = Settings()
