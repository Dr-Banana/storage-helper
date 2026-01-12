from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse
import logging
from typing import List, Optional
import tempfile
import os
from app.api.schemas import (
    IngestResponse, 
    FeedbackRequest, FeedbackResponse,
    IngestConfirmRequest, IngestConfirmResponse,
    SearchRequest, SearchResponse,
    CategoryConfigResponse, CategoryTypeInfo
)
from app.pipelines import ingestion, feedback
from app.modules.embedding import EmbeddingGenerator
from app.storage.pipeline_storage import PipelineStorage

logger = logging.getLogger(__name__)
api_router = APIRouter()


@api_router.post("/ingestion", response_model=IngestResponse)
async def process_document(
    files: List[UploadFile] = File(..., description="Document files to process (images or PDFs). Can upload single or multiple files."),
    owner_id: int = Form(..., description="Document owner user ID"),
    # Use string to receive, allowing frontend to pass empty string without parsing failure
    document_id: str = Form(
        "", description="Optional existing document ID (string). Empty string means 'no document_id'."
    ),
    # Similarly, file_type also allows empty string, which will be normalized to None
    file_type: str = Form(
        "", description="Optional file type override: 'image' or 'pdf' (auto-detected if not provided). Only used for single file upload."
    )
):
    """
    [Ingestion Pipeline]
    Process document(s) for AI ingestion pipeline.
    
    **API Design (receives file binary data):**
    - Frontend uploads files via `multipart/form-data` (similar to `/api/v1/documents/upload`)
    - Supports single or multiple file uploads (`files` parameter can be single or multiple `UploadFile`)
    - After AIOrchestraService receives files:
      1. Temporarily save files to temp directory (or use in-memory data)
      2. Call DataStorageService `/api/v1/documents/upload` to upload file and get `image_url`
      3. Execute full AI pipeline (OCR -> Vision -> Cleaning -> Recommendation -> Embedding)
      4. Call `/api/v1/documents/process` to save structured results and `ocr_text`
    
    Pipeline flow:
    - **Single file**:
      1. OCR -> Vision(optional) -> Cleaning
      2. Call DataStorageService `/api/v1/documents/upload` via `PipelineStorage.upload_file_only()`
         to complete file upload and get `image_url`
      3. Recommendation + Embedding (parallel)
      4. Call `/api/v1/documents/process` to save page metadata and `ocr_text`, get `document_id` / `page_id`
    - **Multiple files / Multi-page PDF**:
      1. Split PDF into pages -> Create independent page task for each page
      2. Each page: OCR -> Vision(optional) -> Cleaning -> Upload(`/documents/upload`)
      3. Aggregate text from all pages for one Recommendation + Embedding
      4. Call `/documents/process` for each page, using the same `document_id`
    
    For batch processing:
    - Multiple PDF files are split into individual pages
    - Multiple image files are treated as separate pages
    - All pages are associated with the same document
    - Page numbers are assigned sequentially across all files
    
    Returns complete pipeline output including all processing results.
    """
    from app.pipelines.ingestion import run_unified_ingestion_pipeline
    
    temp_files = []  # Track temporary files for cleanup
    
    try:
        # Validate files
        if not files or len(files) == 0:
            raise HTTPException(status_code=400, detail="files cannot be empty")
        
        # Save uploaded files to temporary directory
        # We need to save them because the pipeline expects file paths
        temp_dir = tempfile.mkdtemp(prefix="ingestion_")
        temp_files.append(temp_dir)  # Track directory for cleanup
        
        file_paths = []
        for idx, uploaded_file in enumerate(files):
            # Determine file extension from filename or content type
            filename = uploaded_file.filename or f"file_{idx}"
            file_ext = os.path.splitext(filename)[1] or ".bin"
            
            # Create temporary file path
            temp_file_path = os.path.join(temp_dir, f"uploaded_{idx}{file_ext}")
            
            # Read file content and save to temporary file
            file_content = await uploaded_file.read()
            with open(temp_file_path, "wb") as f:
                f.write(file_content)
            
            temp_files.append(temp_file_path)
            file_paths.append(temp_file_path)
            
            logger.info(f"Saved uploaded file {filename} to temporary path: {temp_file_path}")
        
        # Normalize document_id (string -> Optional[int]), allow frontend to pass empty string
        normalized_document_id: Optional[int] = None
        if document_id is not None and str(document_id).strip() != "":
            try:
                normalized_document_id = int(str(document_id).strip())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="document_id must be a valid integer or empty string"
                )
        
        # Normalize file_type (empty string treated as None)
        normalized_file_type: Optional[str] = None
        if file_type is not None and str(file_type).strip() != "":
            normalized_file_type = str(file_type).strip()
        
        # Use unified ingestion pipeline for both single and multiple files
        # Enable preview mode: process AI pipeline but skip database upload
        # User will confirm and upload via /ingestion/confirm endpoint
        logger.info(f"Processing {len(file_paths)} file(s) using unified pipeline (preview mode)")
        result = await run_unified_ingestion_pipeline(
            file_urls=file_paths,
            owner_id=owner_id,
            document_id=normalized_document_id,
            file_type=normalized_file_type,  # Pass file_type parameter for single file uploads
            preview_mode=True  # Enable preview mode - skip database upload
        )
        
        # Extract data from unified pipeline result
        recommendation_data = result.get("recommendation", {})
        embedding_save_error = result.get("embedding_save_error")
        recommendation_error = result.get("recommendation_error")
        status = result.get("status", "success")
        
        # Normalize status: map "completed" to "success" for consistency
        if status == "completed":
            status = "success"
        
        response = IngestResponse(
            status=status,
            document_id=result.get("document_id"),
            recommendation=recommendation_data,
            total_pages=result.get("total_pages"),
            successful_pages=result.get("successful_pages"),
            failed_pages=result.get("failed_pages"),
            page_results=result.get("page_results"),
            embedding_save_error=embedding_save_error,
            recommendation_error=recommendation_error,
            embedding=result.get("embedding"),  # Include embedding for confirmation
            embedding_dimension=result.get("embedding_dimension"),  # Include embedding dimension for confirmation
            preview_mode=True  # Mark as preview result
        )
        
        # Only return HTTP 500 for complete failures, not partial successes
        if status == "failed":
            return JSONResponse(
                status_code=500,
                content=response.model_dump()
            )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in ingestion pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
    finally:
        # Clean up temporary files
        import shutil
        for temp_file in temp_files:
            try:
                if os.path.isfile(temp_file):
                    os.remove(temp_file)
                elif os.path.isdir(temp_file):
                    shutil.rmtree(temp_file)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {temp_file}: {e}")


@api_router.post("/ingestion/confirm", response_model=IngestConfirmResponse)
async def confirm_and_upload_document(request: IngestConfirmRequest):
    """
    [Ingestion Confirmation]
    User confirms preview results and uploads document to database.
    
    This API receives preview results and user-modified category_id, location_id,
    then executes database upload operation.
    
    Process:
    1. Call /api/v1/documents/process for each page with user-modified category_id and location_id
    2. Save embedding (if available)
    3. Return upload results
    """
    from app.pipelines.ingestion import IngestionPipeline
    from app.modules.embedding import EmbeddingResult
    
    logger.info(f"Confirming and uploading document for owner_id={request.owner_id}, document_id={request.document_id}")
    
    pipeline = IngestionPipeline()
    page_results = []
    successful_pages = 0
    failed_pages = 0
    final_document_id = request.document_id
    
    try:
        # Process each page with user-modified category_id and location_id
        for page_result in request.page_results:
            if page_result.status != "success":
                # Skip failed pages
                page_results.append({
                    "page_number": page_result.page_number,
                    "status": "failed",
                    "error": page_result.error or "Page processing failed in preview",
                    "ocr_text": page_result.ocr_text,
                    "file_url": page_result.file_url,
                    "document_id": page_result.document_id,
                    "page_id": page_result.page_id
                })
                failed_pages += 1
                continue
            
            if not page_result.file_url:
                # Missing file_url - cannot process
                page_results.append({
                    "page_number": page_result.page_number,
                    "status": "failed",
                    "error": "Missing file_url - cannot process page",
                    "ocr_text": page_result.ocr_text,
                    "file_url": None,
                    "document_id": page_result.document_id,
                    "page_id": None
                })
                failed_pages += 1
                continue
            
            try:
                # Use user-modified category_id and location_id (override recommendation)
                category_id = request.category_id
                location_id = request.location_id
                
                # If user didn't provide category_id, try to get from recommendation
                if category_id is None and request.recommendation:
                    category_id = request.recommendation.get("category_id")
                
                # If user didn't provide location_id, try to get from recommendation
                if location_id is None and request.recommendation:
                    location_id = request.recommendation.get("location_id") or request.recommendation.get("suggested_location_id")
                
                # Normalize location_id: None means no location, convert to -1
                if location_id is None:
                    location_id = -1
                
                # Process document page with user-modified data
                metadata = None
                if request.recommendation:
                    metadata = request.recommendation.get("metadata")
                process_result = await pipeline.pipeline_storage.process_document_page(
                    image_url=page_result.file_url,
                    owner_id=request.owner_id,
                    page_number=page_result.page_number,
                    ocr_text=page_result.ocr_text or "",
                    document_id=final_document_id,  # Use document_id from request or None (creates new)
                    category_id=category_id,
                    location_id=location_id,
                    metadata=metadata
                )
                
                if process_result:
                    # Update final_document_id from first successful page
                    if final_document_id is None and process_result.get("document_id"):
                        final_document_id = process_result.get("document_id")
                    
                    page_results.append({
                        "page_number": process_result.get("page_number", page_result.page_number),
                        "status": "success",
                        "error": None,
                        "ocr_text": page_result.ocr_text,
                        "file_url": process_result.get("image_url") or page_result.file_url,
                        "document_id": process_result.get("document_id") or final_document_id,
                        "page_id": process_result.get("page_id")
                    })
                    successful_pages += 1
                else:
                    # Processing failed
                    page_results.append({
                        "page_number": page_result.page_number,
                        "status": "failed",
                        "error": "Failed to process document page via API",
                        "ocr_text": page_result.ocr_text,
                        "file_url": page_result.file_url,
                        "document_id": final_document_id,
                        "page_id": None
                    })
                    failed_pages += 1
                    
            except Exception as e:
                logger.error(f"Error processing page {page_result.page_number}: {e}", exc_info=True)
                page_results.append({
                    "page_number": page_result.page_number,
                    "status": "failed",
                    "error": f"Error processing page: {str(e)}",
                    "ocr_text": page_result.ocr_text,
                    "file_url": page_result.file_url,
                    "document_id": final_document_id,
                    "page_id": None
                })
                failed_pages += 1
        
        # Save embedding if available
        embedding_save_error = None
        if final_document_id and request.embedding and request.embedding_dimension:
            try:
                # Validate embedding dimension
                if request.embedding_dimension == 768 and len(request.embedding) == 768:
                    save_success = await pipeline.pipeline_storage.save_document_embedding(
                        document_id=final_document_id,
                        embedding=request.embedding
                    )
                    if not save_success:
                        embedding_save_error = "Failed to save document embedding via API"
                else:
                    embedding_save_error = f"Invalid embedding dimension: expected 768, got {request.embedding_dimension} or length {len(request.embedding)}"
            except Exception as e:
                logger.error(f"Error saving embedding: {e}", exc_info=True)
                embedding_save_error = f"Error saving embedding: {str(e)}"
        
        # Determine status
        total_pages = len(request.page_results)
        if failed_pages == 0:
            status = "success"
        elif successful_pages > 0:
            status = "partial_success"
        else:
            status = "failed"
        
        return IngestConfirmResponse(
            status=status,
            document_id=final_document_id,
            total_pages=total_pages,
            successful_pages=successful_pages,
            failed_pages=failed_pages,
            page_results=page_results,
            embedding_save_error=embedding_save_error
        )
        
    except Exception as e:
        logger.error(f"Error in confirmation pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Confirmation failed: {str(e)}")


@api_router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest):
    """
    [Feedback Handler]
    Collect user feedback to improve AI recommendations.
    """
    try:
        # Call the feedback handler pipeline
        await feedback.handle_feedback(request)
        return FeedbackResponse(msg="Feedback logged successfully")
    except Exception as e:
        logger.error(f"Error handling feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Feedback failed: {str(e)}")


@api_router.post("/search", response_model=SearchResponse)
async def search_documents(request: SearchRequest):
    """
    [Search Pipeline]
    Search for documents using natural language queries.
    
    Process:
    1. Generate embedding for the query text using Gemini
    2. Call DataStorageService /api/documents/search with the embedding
    3. Return the list of matching document IDs
    """
    try:
        logger.info(f"Processing search query: '{request.query}' for owner_id={request.owner_id}")
        
        # 1. Generate embedding for the query
        # Use task_type="RETRIEVAL_QUERY" for search queries to match 
        # the "RETRIEVAL_DOCUMENT" used during ingestion.
        generator = EmbeddingGenerator(task_type="RETRIEVAL_QUERY")
        
        # Basic cleaning to match ingestion normalization
        clean_query = request.query.strip()
        
        embedding_result = await generator.generate(clean_query)
        
        if not embedding_result.is_successful:
            logger.error(f"Failed to generate embedding for query: {embedding_result.error}")
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to generate embedding: {embedding_result.error}"
            )
        
        # 2. Call DataStorageService search API
        storage = PipelineStorage()
        document_ids = await storage.search_documents(
            query_embedding=embedding_result.vector,
            owner_id=request.owner_id,
            top_k=request.top_k
        )
        
        return SearchResponse(
            query=request.query,
            document_ids=document_ids,
            count=len(document_ids)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in search pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@api_router.get("/category-config", response_model=CategoryConfigResponse)
async def get_category_config():
    """
    [Category Configuration]
    Get all available category types and their configuration.
    
    This endpoint exposes category configuration from category_config.py,
    allowing frontend to display all possible categories without hardcoding.
    
    Returns:
    - List of all allowed category codes
    - Detailed information about each category type (code, name, description, keywords, etc.)
    """
    from app.core.category_config import (
        ALLOWED_CATEGORY_TYPES,
        CATEGORY_LOCATION_KEYWORDS,
        SECURE_CATEGORIES,
        FREQUENT_ACCESS_CATEGORIES,
        COMMON_CATEGORY_SUGGESTIONS
    )
    
    try:
        category_types = []
        
        for category_code in ALLOWED_CATEGORY_TYPES:
            # Get suggestion info (name and description)
            suggestion = COMMON_CATEGORY_SUGGESTIONS.get(category_code, {})
            name = suggestion.get("name", category_code)
            description = suggestion.get("description", f"Category: {category_code}")
            
            # Get keywords
            keywords = CATEGORY_LOCATION_KEYWORDS.get(category_code, [])
            
            # Check if secure or frequent access
            is_secure = category_code in SECURE_CATEGORIES
            is_frequent_access = category_code in FREQUENT_ACCESS_CATEGORIES
            
            category_types.append(CategoryTypeInfo(
                code=category_code,
                name=name,
                description=description,
                keywords=keywords,
                is_secure=is_secure,
                is_frequent_access=is_frequent_access
            ))
        
        return CategoryConfigResponse(
            allowed_category_types=ALLOWED_CATEGORY_TYPES,
            category_types=category_types
        )
        
    except Exception as e:
        logger.error(f"Error getting category config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get category config: {str(e)}")