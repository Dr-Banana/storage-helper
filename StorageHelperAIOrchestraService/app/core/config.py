from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional
import os
import logging
import sys

logger = logging.getLogger(__name__)


def get_env_file() -> Optional[str]:
    """
    Determine which .env file to use based on APP_ENV environment variable.
    
    APP_ENV must be explicitly set to 'local', 'preprod', or 'prod'.
    Available environments:
    - local: Development/testing environment (.env.local)
    - preprod: Local testing with production-like configuration (.env.preprod)
    - prod: Production environment (.env.prod or /etc/secrets/.env.prod)
    
    :return: Path to the .env file to load, or None if no file should be loaded
    :raises SystemExit: If APP_ENV is not set or invalid
    """
    app_env = os.getenv("APP_ENV", "").lower().strip()
    
    # 自动检测是否处于测试环境
    if not app_env and ("pytest" in sys.modules or "pytest" in sys.argv[0]):
        app_env = "prod"  # 测试环境下默认使用 prod 模式（不强制依赖 .env 文件）
    
    # List of valid environments
    valid_envs = ["local", "preprod", "prod"]
    
    if not app_env:
        error_msg = (
            "\n" + "="*70 + "\n"
            "ERROR: APP_ENV environment variable is not set!\n\n"
            "You must explicitly specify the environment:\n"
            "  - For local testing: APP_ENV=local\n"
            "  - For preprod (local with prod config): APP_ENV=preprod\n"
            "  - For production: APP_ENV=prod\n\n"
            "Quick start:\n"
            "  Windows: .\\script\\start_local.ps1  or  .\\script\\start_preprod.ps1  or  .\\script\\start_prod.ps1\n"
            "  Linux/Mac: ./script/start_local.sh  or  ./script/start_preprod.sh  or  ./script/start_prod.sh\n"
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
            "  - APP_ENV=preprod (for local testing with production-like config)\n"
            "  - APP_ENV=prod (for production)\n"
            "="*70
        )
        logger.error(error_msg)
        sys.exit(1)
    
    env_file = f".env.{app_env}"
    render_secret_path = f"/etc/secrets/{env_file}"
    
    # For prod mode, prioritize /etc/secrets/ directory
    if app_env == "prod":
        if os.path.exists(render_secret_path):
            return render_secret_path
        elif os.path.exists(env_file):
            return env_file
        else:
            return None
    
    # For other modes (local, preprod), check local file first
    if os.path.exists(env_file):
        return env_file
    
    # Fallback to /etc/secrets/ for other environments
    if os.path.exists(render_secret_path):
        return render_secret_path
    
    # In local/preprod, we still require the file
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
    # Base URL for DataStorageService microservice (HTTP API communication)
    # Default: http://localhost:8000 (local) or configure via environment variable
    # Note: This service communicates with DataStorageService via HTTP API only
    STORAGE_SERVICE_URL: str = "http://localhost:8000/internal"
    
    # Gemini API Configuration
    GEMINI_EMBEDDING_API_KEY: str = ""  # Gemini API key for embedding generation
    GEMINI_LLM_API_KEY: str = ""  # Gemini API key for LLM (recommendation)
    GEMINI_METADATA_API_KEY: str = ""  # Gemini API key for metadata extraction (can reuse GEMINI_LLM_API_KEY if not set separately)
    GEMINI_EMBEDDING_MODEL: str = "text-embedding-004"  # Embedding model name (legacy, consider updating to gemini-embedding-001)
    GEMINI_LLM_MODEL: str = "gemini-2.5-flash"  # LLM model name (use stable ID; preview IDs can 404)
    GEMINI_METADATA_MODEL: str = "gemini-2.5-flash"  # Model for metadata extraction
    
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

    # HowToCook Recipe Integration (via HowToCook-MCP server)
    # StreamableHTTP endpoint of the howtocook-mcp service
    # Local:  http://howtocook-mcp:3000/mcp  (docker-compose service name)
    # Render: full URL of the deployed howtocook-mcp Render service + /mcp
    HOWTOCOOK_MCP_URL: str = "http://howtocook-mcp:3000/mcp"
    # How often the background task refreshes the dish catalog (hours)
    HOWTOCOOK_SYNC_INTERVAL_HOURS: int = 24
    
    # 允许 Pydantic 读取 .env 文件
    model_config = SettingsConfigDict(env_file=get_env_file(), extra='ignore')
    
    def log_config_summary(self):
        """Log configuration summary with sensitive values masked."""
        pass


# Initialize settings singleton
settings = Settings()
