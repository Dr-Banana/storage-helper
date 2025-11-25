from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # 假设 StorageHelperDataStorageService 运行在本地的某个端口
    # 如果未来部署在云端，这里改为相应的 URL
    STORAGE_SERVICE_URL: str = "http://localhost:8001/internal" 
    
    # OCR 相关配置
    TESSERACT_CMD: Optional[str] = None  # Tesseract 可执行文件路径，如果为 None 则使用系统 PATH
    TESSERACT_LANG: str = "eng"  # OCR 语言，默认英语，可以设置为 "eng+chi_sim" 中英混合或 "chi_sim" 仅简体中文
    OCR_TIMEOUT: float = 30.0  # OCR 处理的超时时间（秒）
    OCR_ENABLE_PREPROCESSING: bool = True  # 是否启用图片预处理
    OCR_MIN_CONFIDENCE: float = 0.0  # 最小置信度阈值（0-100），低于此值的文本可能被过滤
    OCR_PSM: int = 1  # Page Segmentation Mode (0-13), 1=auto with OSD (orientation/script detection), 3=fully automatic, 6=uniform block, 11=sparse text
    
    # 允许 Pydantic 读取 .env 文件
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

settings = Settings()