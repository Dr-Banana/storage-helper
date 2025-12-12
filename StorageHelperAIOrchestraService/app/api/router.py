from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import logging
from app.api.schemas import (
    IngestRequest, IngestResponse, 
    FeedbackRequest, FeedbackResponse
)
from app.pipelines import ingestion, feedback

logger = logging.getLogger(__name__)
api_router = APIRouter()


@api_router.post("/ingestion", response_model=IngestResponse)
async def process_document(request: IngestRequest):
    """
    [Ingestion Pipeline]
    Process document(s) uploaded by the web client.
    
    Unified API using 'file_urls' field:
    - Single file: file_urls = ["file1.jpg"] (list with one element)
    - Multiple files: file_urls = ["file1.jpg", "file2.pdf", "file3.jpg"]
    
    Pipeline flow:
    - Single file (list length = 1): OCR -> Cleaning -> Upload -> Recommendation -> Embedding
    - Multiple files (list length > 1): Split PDFs -> Process all pages in parallel -> Recommendation -> Embedding
    
    For batch processing:
    - Multiple PDF files are split into individual pages
    - Multiple image files are treated as separate pages
    - All pages are associated with the same document
    - Page numbers are assigned sequentially across all files
    
    Returns complete pipeline output including all processing results.
    """
    from app.pipelines.ingestion import run_unified_ingestion_pipeline
    
    try:
        # Validate file_urls
        if not request.file_urls or len(request.file_urls) == 0:
            raise HTTPException(status_code=400, detail="file_urls cannot be empty")
        
        # Use unified ingestion pipeline for both single and multiple files
        # This ensures consistent response format with total_pages, successful_pages, failed_pages, page_results
        logger.info(f"Processing {len(request.file_urls)} file(s) using unified pipeline")
        result = await run_unified_ingestion_pipeline(
            file_urls=request.file_urls,
            owner_id=request.owner_id,
            document_id=request.document_id,
            file_type=request.file_type  # Pass file_type parameter for single file uploads
        )
        
        # Extract data from unified pipeline result
        recommendation_data = result.get("recommendation", {})
        embedding_save_error = result.get("embedding_save_error")
        status = result.get("status", "success")
        
        # Normalize status: map "completed" to "success" for consistency
        if status == "completed":
            status = "success"
        
        # Adjust status based on embedding save result
        # Note: run_unified_ingestion_pipeline already handles status adjustment internally
        # We only need to handle edge cases here
        # Status from unified pipeline is already "success", "partial_success", or "failed"
        # No need to check for "recommendation_failed" or "embedding_failed" as unified pipeline doesn't return those
        
        response = IngestResponse(
            status=status,
            document_id=result.get("document_id"),
            recommendation=recommendation_data,
            total_pages=result.get("total_pages"),
            successful_pages=result.get("successful_pages"),
            failed_pages=result.get("failed_pages"),
            page_results=result.get("page_results"),
            embedding_save_error=embedding_save_error
        )
        
        # Only return HTTP 500 for complete failures, not partial successes
        # If embedding_save_error exists but status is "partial_success" or "success",
        # it means document processing succeeded but embedding save failed - this is partial success, not complete failure
        # HTTP 500 should only be returned when status is "failed" (complete failure)
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