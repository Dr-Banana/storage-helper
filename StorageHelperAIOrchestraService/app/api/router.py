from fastapi import APIRouter, HTTPException, BackgroundTasks
import logging
from app.api.schemas import (
    IngestRequest, IngestResponse, 
    SearchRequest, SearchResponse, 
    FeedbackRequest, FeedbackResponse,
    SearchResultItem, LocationInfo
)
from app.pipelines import ingestion, search, feedback

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
    from app.storage.pipeline_storage import PipelineStorage
    from app.pipelines.ingestion import run_batch_ingestion_pipeline
    
    try:
        # Validate file_urls
        if not request.file_urls or len(request.file_urls) == 0:
            raise HTTPException(status_code=400, detail="file_urls cannot be empty")
        
        # Determine if this is a single file or batch request
        if len(request.file_urls) == 1:
            # Single file processing
            single_file_url = request.file_urls[0]
            logger.info(f"Processing single file: {single_file_url}")
            result = await ingestion.run_ingestion_pipeline(
                image_url=single_file_url, 
                owner_id=request.owner_id,
                document_id=request.document_id,
                file_type=request.file_type
            )
            
            # Get complete pipeline output using PipelineStorage
            complete_output = PipelineStorage.get_pipeline_output(result)
            
            # Convert to IngestResponse format
            # Ensure document_id is int, not string
            doc_id = complete_output.get("document_id")
            if doc_id is not None:
                if isinstance(doc_id, str):
                    try:
                        doc_id = int(doc_id)
                    except (ValueError, TypeError):
                        logger.warning(f"Could not convert document_id '{doc_id}' to int")
                        doc_id = None
            
            # Extract recommendation data from complete_output
            # recommendation_data contains all recommendation info
            recommendation_data = complete_output.get("recommendation_data", {})
            if not recommendation_data:
                # Fallback: try to construct from old fields (for backward compatibility)
                recommendation_data = {}
                # Note: category_code is not included - use category_id only
                if complete_output.get("recommended_location_id"):
                    recommendation_data["location_id"] = complete_output.get("recommended_location_id")
                if complete_output.get("recommended_location_reason"):
                    recommendation_data["recommendation_reason"] = complete_output.get("recommended_location_reason")
                if complete_output.get("extracted_metadata"):
                    recommendation_data.update(complete_output.get("extracted_metadata", {}))
            
            # Normalize recommendation: ensure location_id is used instead of location_name
            if recommendation_data:
                if "location_name" in recommendation_data:
                    recommendation_data.pop("location_name")
                if "suggested_location_name" in recommendation_data:
                    recommendation_data.pop("suggested_location_name")
                # Ensure location_id is set (prefer location_id over suggested_location_id)
                if "suggested_location_id" in recommendation_data and "location_id" not in recommendation_data:
                    recommendation_data["location_id"] = recommendation_data.pop("suggested_location_id")
                # Remove category_code (use category_id only to save space)
                recommendation_data.pop("category_code", None)
            
            return IngestResponse(
                status=complete_output.get("status", "success"),
                document_id=doc_id,
                recommendation=recommendation_data,
                total_pages=None,
                successful_pages=None,
                failed_pages=None,
                page_results=None
            )
        else:
            # Batch processing (multiple files)
            logger.info(f"Processing {len(request.file_urls)} files in batch mode")
            result = await run_batch_ingestion_pipeline(
                file_urls=request.file_urls,
                owner_id=request.owner_id,
                document_id=request.document_id
            )
            
            # Convert batch result to IngestResponse format
            # Extract recommendation data (should already be normalized in batch processing)
            recommendation_data = result.get("recommendation", {})
            
            return IngestResponse(
                status=result.get("status", "success"),
                document_id=result.get("document_id"),
                recommendation=recommendation_data,
                total_pages=result.get("total_pages"),
                successful_pages=result.get("successful_pages"),
                failed_pages=result.get("failed_pages"),
                page_results=result.get("page_results")
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in ingestion pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


@api_router.post("/search", response_model=SearchResponse)
async def search_documents(request: SearchRequest):
    """
    [Search Pipeline]
    Search for documents using natural language query from web client.
    Example query: "Where is my W2?"
    Pipeline flow: Query Normalization -> Embedding -> Similarity Search -> Result Assembly
    """
    try:
        # Call the search pipeline
        results_data = await search.run_search_pipeline(
            query=request.query, 
            owner_id=request.owner_id, 
            top_k=request.top_k
        )
        
        # Results from pipeline already have document_id as UUID string
        # Return SearchResponse
        return SearchResponse(results=results_data)
        
    except Exception as e:
        logger.error(f"Error in search pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@api_router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest):
    """
    [Feedback Handler]
    Collect user feedback to improve AI recommendations and search accuracy.
    """
    try:
        # Call the feedback handler pipeline
        await feedback.handle_feedback(request)
        return FeedbackResponse(msg="Feedback logged successfully")
    except Exception as e:
        logger.error(f"Error handling feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Feedback failed: {str(e)}")