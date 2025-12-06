from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
import os
import logging
import sys

logger = logging.getLogger(__name__)


def get_env_file() -> str:
    """
    Determine which .env file to use based on APP_ENV environment variable.
    
    APP_ENV must be explicitly set to either 'local' or 'prod'.
    Available environments:
    - local: Development/testing environment (.env.local)
    - prod: Production environment (.env.prod)
    
    :return: Path to the .env file to load
    :raises SystemExit: If APP_ENV is not set or invalid
    """
    app_env = os.getenv("APP_ENV", "").lower().strip()
    
    # List of valid environments
    valid_envs = ["local", "prod"]
    
    if not app_env:
        error_msg = (
            "\n" + "="*70 + "\n"
            "ERROR: APP_ENV environment variable is not set!\n\n"
            "You must explicitly specify the environment:\n"
            "  - For local testing: APP_ENV=local\n"
            "  - For production: APP_ENV=prod\n\n"
            "Quick start:\n"
            "  Windows: .\\script\\start_local.ps1  or  .\\script\\start_prod.ps1\n"
            "  Linux/Mac: ./script/start_local.sh  or  ./script/start_prod.sh\n"
            "="*70
        )
        logger.error(error_msg)
        sys.exit(1)
    
    if app_env not in valid_envs:
        error_msg = (
            "\n" + "="*70 + "\n"
            f"ERROR: Invalid APP_ENV value: '{app_env}'\n\n"
            f"Valid environments: {', '.join(valid_envs)}\n\n"
            "Please set APP_ENV to one of the valid values:\n"
            "  - APP_ENV=local (for development/testing)\n"
            "  - APP_ENV=prod (for production)\n"
            "="*70
        )
        logger.error(error_msg)
        sys.exit(1)
    
    env_file = f".env.{app_env}"
    
    # Check if the specified env file exists
    if not os.path.exists(env_file):
        error_msg = (
            "\n" + "="*70 + "\n"
            f"ERROR: Configuration file not found: {env_file}\n\n"
            f"APP_ENV is set to '{app_env}' but {env_file} does not exist.\n\n"
            "Please create the configuration file:\n"
            f"  1. Copy .env.example to {env_file}\n"
            f"  2. Fill in your API keys and configuration\n"
            "="*70
        )
        logger.error(error_msg)
        sys.exit(1)
    
    logger.info(f"✓ Loading configuration from {env_file} (APP_ENV={app_env})")
    return env_file


def mask_sensitive_value(value: str, show_chars: int = 4) -> str:
    """
    Mask sensitive values for logging, showing only the last few characters.
    
    :param value: The sensitive value to mask
    :param show_chars: Number of characters to show at the end
    :return: Masked string like "***xyz"
    """
    if not value:
        return "[NOT SET]"
    if len(value) <= show_chars:
        return "*" * len(value)
    return "*" * (len(value) - show_chars) + value[-show_chars:]


class Settings(BaseSettings):
    # Storage Service Configuration
    STORAGE_SERVICE_URL: str = "http://localhost:8001/internal"
    
    # Gemini API Configuration
    GEMINI_EMBEDDING_API_KEY: str = ""  # Gemini API key for embedding generation
    GEMINI_LLM_API_KEY: str = ""  # Gemini API key for LLM (recommendation)
    GEMINI_EMBEDDING_MODEL: str = "text-embedding-004"  # Embedding model name
    GEMINI_LLM_MODEL: str = "gemini-2.5-flash-preview-09-2025"  # LLM model name
    
    # OCR Configuration
    TESSERACT_CMD: Optional[str] = None  # Tesseract 可执行文件路径，如果为 None 则使用系统 PATH
    TESSERACT_LANG: str = "eng"  # OCR 语言，默认英语，可以设置为 "eng+chi_sim" 中英混合或 "chi_sim" 仅简体中文
    OCR_TIMEOUT: float = 30.0  # OCR 处理的超时时间（秒）
    OCR_ENABLE_PREPROCESSING: bool = True  # 是否启用图片预处理
    OCR_MIN_CONFIDENCE: float = 0.0  # 最小置信度阈值（0-100），低于此值的文本可能被过滤
    OCR_PSM: int = 1  # Page Segmentation Mode (0-13), 1=auto with OSD (orientation/script detection), 3=fully automatic, 6=uniform block, 11=sparse text
    
    # Vision Enhancement Configuration (Multimodal Understanding)
    VISION_ENABLE: bool = True  # Enable Gemini Vision API for multimodal document understanding
    VISION_API_KEY: str = ""  # Gemini API key for vision (can reuse GEMINI_LLM_API_KEY if not set separately)
    VISION_MODEL: str = "gemini-2.0-flash-exp"  # Vision model name
    VISION_AUTO_TRIGGER_ON_LOW_OCR: bool = True  # Auto-trigger vision when OCR confidence is low
    VISION_OCR_CONFIDENCE_THRESHOLD: float = 80  # Trigger vision if OCR confidence below this (0-100, percentage)
    VISION_TIMEOUT: float = 30.0  # Vision API timeout (seconds)
    
    # 允许 Pydantic 读取 .env 文件
    model_config = SettingsConfigDict(env_file=get_env_file(), extra='ignore')
    
    def log_config_summary(self):
        """Log configuration summary with sensitive values masked."""
        current_env = os.getenv("APP_ENV", "unknown")
        
        logger.info("=" * 70)
        logger.info(f"Configuration Summary (Environment: {current_env})")
        logger.info("=" * 70)
        logger.info(f"Storage Service URL: {self.STORAGE_SERVICE_URL}")
        logger.info(f"Embedding API Key: {mask_sensitive_value(self.GEMINI_EMBEDDING_API_KEY)}")
        logger.info(f"LLM API Key: {mask_sensitive_value(self.GEMINI_LLM_API_KEY)}")
        logger.info(f"Embedding Model: {self.GEMINI_EMBEDDING_MODEL}")
        logger.info(f"LLM Model: {self.GEMINI_LLM_MODEL}")
        logger.info(f"Tesseract Language: {self.TESSERACT_LANG}")
        logger.info(f"OCR Preprocessing: {'Enabled' if self.OCR_ENABLE_PREPROCESSING else 'Disabled'}")
        logger.info(f"Vision Enhancement: {'Enabled' if self.VISION_ENABLE else 'Disabled'}")
        if self.VISION_ENABLE:
            logger.info(f"Vision Model: {self.VISION_MODEL}")
            logger.info(f"Vision Auto-trigger: {'Yes' if self.VISION_AUTO_TRIGGER_ON_LOW_OCR else 'No'} (threshold: {self.VISION_OCR_CONFIDENCE_THRESHOLD})")
        logger.info("=" * 70)


# Initialize settings singleton
settings = Settings()

# Log configuration summary (with masked sensitive values)
settings.log_config_summary()