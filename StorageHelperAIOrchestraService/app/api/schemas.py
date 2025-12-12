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
    前端请求 AI 处理文档的入参
    统一使用 file_urls 字段，支持单个文件或多文件上传：
    - 单个文件：file_urls 为长度为1的列表，例如 ["file1.jpg"]
    - 多个文件：file_urls 为多个文件的列表，例如 ["file1.jpg", "file2.pdf", "file3.jpg"]
    """
    document_id: Optional[int] = Field(None, description="If storage service already created the row, pass ID here.")
    file_urls: List[str] = Field(..., min_length=1, description="List of file URLs to process (images or PDFs). For single file, use a list with one element.")
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