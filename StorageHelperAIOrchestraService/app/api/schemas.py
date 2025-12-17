from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Any
from datetime import datetime

# ==========================================
# Shared / Base Models
# ==========================================

class DocumentMetadata(BaseModel):
    """
    对应 SQL 表: document -> metadata (JSON)
    这里存放从 OCR/LLM 提取出来的动态字段
    """
    tax_year: Optional[int] = None
    issuer_name: Optional[str] = None
    expiry_date: Optional[str] = None
    # 允许更多任意字段，匹配 SQL 的 JSON 类型
    extra_fields: Dict[str, Any] = Field(default_factory=dict)

# ==========================================
# 1. Ingestion Pipeline (存入/处理文档)
# ==========================================

class IngestRequest(BaseModel):
    """
    前端请求 AI 处理文档的入参。
    
    🔑 **本地模式与远程模式统一使用 `file_urls` 字段：**
    - **本地模式（推荐）**：传入 **本地绝对路径** 列表，例如：
      - 单个文件：`["C:/Users/xxx/Downloads/tax-2024.pdf"]`
      - 多个文件：`["C:/docs/a.pdf", "C:/docs/b.jpg"]`
      - AIOrchestraService 会自行读取本地文件，并通过 DataStorageService 的
        `/api/v1/documents/upload` & `/api/v1/documents/process` 完成上传与入库。
    - **远程模式（可选）**：也可以传入后端可访问的 HTTP/HTTPS URL，例如：
      - `["https://example.com/file1.jpg"]`
    
    ❗**重要：**
    - 前端（浏览器）通常 **不能直接访问本地磁盘绝对路径**，因此本地模式一般适用于
      桌面应用、后端脚本或与 AI 服务部署在同一台机器的前端。
    - 无论是本地路径还是 URL，统一放在 `file_urls` 字段中，pipeline 会自动识别并通过
      `_read_file_content()` 决定是走本地文件读取还是网络下载。
    """
    document_id: Optional[int] = Field(None, description="If storage service already created the row, pass ID here.")
    file_urls: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "待处理文件的列表（本地绝对路径 或 后端可访问的 HTTP/HTTPS URL），"
            "支持图片和 PDF。单文件也必须使用长度为 1 的列表。"
        ),
    )
    owner_id: int = Field(..., description="References user.id")
    user_notes: Optional[str] = Field(None, description="User's manual input to help AI")
    file_type: Optional[str] = Field(None, description="File type: 'image' or 'pdf' (auto-detected if not provided). Only used for single file upload.")


class PageProcessingResult(BaseModel):
    """单个页面的处理结果"""
    page_number: int = Field(..., description="Page number (1-indexed)")
    status: str = Field(..., description="Processing status: 'success', 'failed', 'skipped'")
    error: Optional[str] = Field(None, description="Error message if failed")
    ocr_text: Optional[str] = Field(None, description="Extracted OCR text for this page")
    file_url: Optional[str] = Field(None, description="URL of uploaded file")
    document_id: Optional[int] = Field(None, description="Document ID for this page")
    page_id: Optional[int] = Field(None, description="Page ID (if available)")


class IngestResponse(BaseModel):
    """
    AI 处理完成后的返回结果
    支持单个文件和批量处理的响应格式
    """
    status: str = Field(..., description="Processing status: 'success', 'partial_success', 'failed'")
    document_id: Optional[int] = Field(None, description="Document ID (integer)")
    
    # AI 推荐信息 (包含所有recommendation数据)
    recommendation: Dict[str, Any] = Field(default_factory=dict, description="Complete recommendation data including category_code, location_id, recommendation_reason, etc.")
    
    # 批量处理相关字段（仅在批量处理时使用）
    total_pages: Optional[int] = Field(None, description="Total number of pages (for batch processing)")
    successful_pages: Optional[int] = Field(None, description="Number of successfully processed pages (for batch processing)")
    failed_pages: Optional[int] = Field(None, description="Number of failed pages (for batch processing)")
    page_results: Optional[List[PageProcessingResult]] = Field(None, description="Processing results for each page (for batch processing)")
    
    # Embedding保存错误（如果embedding保存失败）
    embedding_save_error: Optional[str] = Field(None, description="Error message if embedding save failed")

# ==========================================
# 2. Feedback (用户反馈)
# ==========================================

class FeedbackRequest(BaseModel):
    """
    对应 SQL 表: feedback_message
    """
    document_id: str  # Changed to str to support UUID format
    feedback_type: str = Field(..., description="e.g. 'location_error', 'type_fix'")
    note: Optional[str] = None

class FeedbackResponse(BaseModel):
    msg: str = "Feedback received"