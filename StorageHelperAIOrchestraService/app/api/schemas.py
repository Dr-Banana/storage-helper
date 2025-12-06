from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

# ==========================================
# Shared / Base Models
# ==========================================

class LocationInfo(BaseModel):
    """
    对应 SQL 表: storage_location
    前端展示位置时的核心信息
    """
    id: int
    name: str = Field(..., description="e.g. 'Bedroom desk, left drawer #2'")
    description: Optional[str] = None
    photo_url: Optional[str] = Field(None, description="URL to the photo of the cabinet/drawer")

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
    """
    document_id: Optional[int] = Field(None, description="If storage service already created the row, pass ID here.")
    image_url: str = Field(..., description="The source file to process (image or PDF)")
    owner_id: int = Field(..., description="References user.id")
    user_notes: Optional[str] = Field(None, description="User's manual input to help AI")
    file_type: Optional[str] = Field(None, description="File type: 'image' or 'pdf' (auto-detected if not provided)")

class IngestResponse(BaseModel):
    """
    AI 处理完成后的返回结果
    """
    status: str = "success"
    document_id: str  # Changed to str to support UUID format
    
    # AI 识别出的信息 (对应 document_type 和 document.metadata)
    detected_type_code: Optional[str] = Field(None, description="e.g. 'TAX_W2'")
    extracted_metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # AI 推荐的位置 (对应 storage_location)
    recommended_location_id: Optional[int] = None
    recommended_location_reason: Optional[str] = None

# ==========================================
# 2. Search Pipeline (搜索文档)
# ==========================================

class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural language query, e.g. 'Where is my W2?'")
    owner_id: Optional[int] = Field(None, description="Filter by user.id")
    top_k: int = 5
    filters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="e.g. {'year': 2024, 'type': 'TAX_W2'}")

class SearchResultItem(BaseModel):
    """
    单个搜索结果，聚合了 document + storage_location 的信息
    """
    document_id: str  # Changed to str to support UUID format
    title: Optional[str] = None
    
    # 关键信息
    snippet: Optional[str] = Field(None, description="Relevant text snippet from OCR")
    preview_image_url: str = Field(..., description="Thumbnail of the document")
    file_type: Optional[str] = Field("image", description="File type: 'image' or 'pdf'")
    
    # 物理位置信息 (MVP 核心目标：告诉用户在哪里)
    location: Optional[LocationInfo] = None
    
    score: float = Field(..., description="Similarity score 0-1")
    created_at: Optional[datetime] = None

class SearchResponse(BaseModel):
    results: List[SearchResultItem]

# ==========================================
# 3. Feedback (用户反馈)
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