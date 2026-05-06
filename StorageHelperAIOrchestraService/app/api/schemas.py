from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Any
from datetime import datetime

# ==========================================
# Shared / Base Models
# ==========================================

class DocumentMetadata(BaseModel):
    """
    Corresponds to SQL table: document -> metadata (JSON)
    Stores dynamic fields extracted from OCR/LLM
    """
    tax_year: Optional[int] = None
    issuer_name: Optional[str] = None
    expiry_date: Optional[str] = None
    # Allow more arbitrary fields, matching SQL JSON type
    extra_fields: Dict[str, Any] = Field(default_factory=dict)

# ==========================================
# 1. Ingestion Pipeline (Store/Process Documents)
# ==========================================

class IngestRequest(BaseModel):
    """
    Frontend request parameters for AI document processing.
    
    **Local mode and remote mode both use `file_urls` field:**
    - **Local mode (recommended)**: Pass list of **local absolute paths**, e.g.:
      - Single file: `["C:/Users/xxx/Downloads/tax-2024.pdf"]`
      - Multiple files: `["C:/docs/a.pdf", "C:/docs/b.jpg"]`
      - AIOrchestraService will read local files and complete upload and storage via DataStorageService's
        `/api/v1/documents/upload` & `/api/v1/documents/process`.
    - **Remote mode (optional)**: Can also pass HTTP/HTTPS URLs accessible by backend, e.g.:
      - `["https://example.com/file1.jpg"]`
    
    **Important:**
    - Frontend (browser) usually **cannot directly access local disk absolute paths**, so local mode is generally
      suitable for desktop apps, backend scripts, or frontend deployed on the same machine as AI service.
    - Whether local path or URL, all are placed in `file_urls` field, pipeline will automatically identify and
      use `_read_file_content()` to decide whether to read local file or download from network.
    """
    document_id: Optional[int] = Field(None, description="If storage service already created the row, pass ID here.")
    file_urls: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "List of files to process (local absolute paths or HTTP/HTTPS URLs accessible by backend), "
            "supports images and PDFs. Single file must also use a list of length 1."
        ),
    )
    owner_id: int = Field(..., description="References user.id")
    user_notes: Optional[str] = Field(None, description="User's manual input to help AI")
    file_type: Optional[str] = Field(None, description="File type: 'image' or 'pdf' (auto-detected if not provided). Only used for single file upload.")


class PageProcessingResult(BaseModel):
    """Processing result for a single page"""
    page_number: int = Field(..., description="Page number (1-indexed)")
    status: str = Field(..., description="Processing status: 'success', 'failed', 'skipped'")
    error: Optional[str] = Field(None, description="Error message if failed")
    ocr_text: Optional[str] = Field(None, description="Extracted OCR text for this page")
    file_url: Optional[str] = Field(None, description="URL of uploaded file")
    document_id: Optional[int] = Field(None, description="Document ID for this page")
    page_id: Optional[int] = Field(None, description="Page ID (if available)")


class IngestResponse(BaseModel):
    """
    Response after AI processing completes
    Supports both single file and batch processing response formats
    """
    status: str = Field(..., description="Processing status: 'success', 'partial_success', 'failed'")
    document_id: Optional[int] = Field(None, description="Document ID (integer)")
    
    # AI recommendation information (includes all recommendation data)
    recommendation: Dict[str, Any] = Field(default_factory=dict, description="Complete recommendation data including category_code, location_id, recommendation_reason, etc.")
    
    # Batch processing related fields (only used for batch processing)
    total_pages: Optional[int] = Field(None, description="Total number of pages (for batch processing)")
    successful_pages: Optional[int] = Field(None, description="Number of successfully processed pages (for batch processing)")
    failed_pages: Optional[int] = Field(None, description="Number of failed pages (for batch processing)")
    page_results: Optional[List[PageProcessingResult]] = Field(None, description="Processing results for each page (for batch processing)")
    
    # Embedding save error (if embedding save failed)
    embedding_save_error: Optional[str] = Field(None, description="Error message if embedding save failed")
    
    # Recommendation generation error (if recommendation generation failed)
    recommendation_error: Optional[str] = Field(None, description="Error message if recommendation generation failed")
    
    # Embedding information (needed in preview mode, for confirmation upload)
    embedding: Optional[List[float]] = Field(None, description="Document embedding vector (if available, needed for confirmation)")
    embedding_dimension: Optional[int] = Field(None, description="Embedding dimension (if available, needed for confirmation)")
    
    # Preview mode flag (if True, this is a preview result and requires user confirmation before uploading to database)
    preview_mode: bool = Field(False, description="If True, this is a preview result and requires user confirmation before uploading to database")


class IngestConfirmRequest(BaseModel):
    """
    User confirmation and document upload request
    Contains all necessary information from preview results and user-modified category_id, location_id
    """
    # Information from preview results
    page_results: List[PageProcessingResult] = Field(..., description="Page processing results from preview (must include file_url and ocr_text for each page)")
    recommendation: Dict[str, Any] = Field(..., description="Recommendation data from preview")
    owner_id: int = Field(..., description="Document owner user ID")
    document_id: Optional[int] = Field(None, description="Optional existing document ID")
    
    # User-modified information
    category_id: Optional[int] = Field(None, description="User-modified category ID (overrides recommendation)")
    location_id: Optional[int] = Field(None, description="User-modified location ID (overrides recommendation, use -1 for no location)")
    
    # Embedding information (if available)
    embedding: Optional[List[float]] = Field(None, description="Document embedding vector (if available from preview)")
    embedding_dimension: Optional[int] = Field(None, description="Embedding dimension (if available from preview)")


class IngestConfirmResponse(BaseModel):
    """
    Response after confirmation upload
    """
    status: str = Field(..., description="Upload status: 'success', 'partial_success', 'failed'")
    document_id: Optional[int] = Field(None, description="Document ID after upload")
    total_pages: Optional[int] = Field(None, description="Total number of pages")
    successful_pages: Optional[int] = Field(None, description="Number of successfully uploaded pages")
    failed_pages: Optional[int] = Field(None, description="Number of failed pages")
    page_results: Optional[List[PageProcessingResult]] = Field(None, description="Upload results for each page")
    embedding_save_error: Optional[str] = Field(None, description="Error message if embedding save failed")

# ==========================================
# 2. Feedback (User Feedback)
# ==========================================

class FeedbackRequest(BaseModel):
    """
    Corresponds to SQL table: feedback_message
    """
    document_id: str  # Changed to str to support UUID format
    feedback_type: str = Field(..., description="e.g. 'location_error', 'type_fix'")
    note: Optional[str] = None

class FeedbackResponse(BaseModel):
    msg: str = "Feedback received"


# ==========================================
# 3. Search Pipeline (Semantic Search)
# ==========================================

class SearchRequest(BaseModel):
    """
    Request for semantic search.
    """
    query: str = Field(..., description="Natural language search query")
    owner_id: int = Field(..., description="User ID to search documents for")
    top_k: int = Field(5, description="Number of results to return")


class SearchResponse(BaseModel):
    """
    Response for semantic search.
    """
    query: str = Field(..., description="The original search query")
    document_ids: List[int] = Field(..., description="List of matching document IDs")
    count: int = Field(..., description="Number of results found")


# ==========================================
# 4. Category Configuration
# ==========================================

class CategoryTypeInfo(BaseModel):
    """Information about a category type"""
    code: str = Field(..., description="Category code (e.g., 'TAX', 'MED')")
    name: str = Field(..., description="Category display name")
    description: str = Field(..., description="Category description")
    keywords: List[str] = Field(default_factory=list, description="Location keywords for this category")
    is_secure: bool = Field(False, description="Whether this category requires secure storage")
    is_frequent_access: bool = Field(False, description="Whether this category is frequently accessed")


class CategoryConfigResponse(BaseModel):
    """
    Response containing all available category types and their configuration.
    This allows frontend to display all possible categories without hardcoding.
    """
    allowed_category_types: List[str] = Field(..., description="List of all allowed category codes")
    category_types: List[CategoryTypeInfo] = Field(..., description="Detailed information about each category type")


# ==========================================
# 5. Chat Agent (AI Conversation)
# ==========================================

class ChatMessage(BaseModel):
    """A single message in a chat history"""
    role: str = Field(..., description="'user' or 'model'")
    content: str = Field(..., description="Message text content")

class ChatRequest(BaseModel):
    """Request for AI chat agent"""
    message: str = Field(..., description="User's current input message")
    history: List[ChatMessage] = Field(default_factory=list, description="Previous messages in the conversation")
    owner_id: int = Field(..., description="User ID associated with the chat")
    context: Optional[Dict[str, Any]] = Field(None, description="Optional context data (e.g. current document items for correction)")
    user_timezone: Optional[str] = Field(None, description="User's IANA timezone e.g. America/Los_Angeles for local date/time")
    cooking_level: Optional[str] = Field("beginner", description="User cooking skill level: beginner | intermediate | expert")
    language: Optional[str] = Field("en", description="Preferred AI response language: zh | en | ja | ko")

class ChatResponse(BaseModel):
    """Response from AI chat agent"""
    response: str = Field(..., description="AI's natural language response")
    intent: str = Field(..., description="Detected user intent (SEARCH, PLAN_EAT_OUT, etc.)")
    confidence: float = Field(..., description="Intent classification confidence")
    reasoning: Optional[str] = Field(None, description="AI's reasoning for intent classification")
    action: str = Field(..., description="The action to perform (SEARCH, GENERAL, etc.)")
    action_data: Dict[str, Any] = Field(default_factory=dict, description="Metadata for the action")
    thinking: Optional[str] = Field(
        None,
        description=(
            "Intermediate reasoning or tool-invocation text stripped from the response. "
            "Frontend should render this in a collapsible 'thinking' section, "
            "never as the main answer."
        ),
    )
    # Token usage for this request
    token_usage: Optional[int] = Field(None, description="Number of LLM tokens consumed by this request")
    # Free-tier limit enforcement
    error_code: Optional[str] = Field(None, description="'LIMIT_EXCEEDED' when free tier limit is reached")
    error_detail: Optional[str] = Field(None, description="Human-readable reason for the error")
    limit_info: Optional[Dict[str, Any]] = Field(None, description="Usage counters: sessions_used, sessions_limit, tokens_used, tokens_limit")


# ==========================================
# 6. List Correction (Natural Language Edit)
# ==========================================

class CorrectionRequest(BaseModel):
    """Request for AI list correction"""
    user_input: str = Field(..., description="User's natural language correction instruction")
    items: List[Dict[str, Any]] = Field(..., description="The current list of items to be corrected")

class CorrectionResponse(BaseModel):
    """Response for AI list correction"""
    corrected_items: List[Dict[str, Any]] = Field(..., description="The corrected list of items")
    changes_summary: List[str] = Field(..., description="List of changes made in human readable format")
