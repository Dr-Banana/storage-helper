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
    Process a document image uploaded by the web client.
    Pipeline flow: OCR -> Cleaning -> Metadata Extraction -> Storage -> Location Recommendation
    """
    try:
        # Call the ingestion pipeline
        result = await ingestion.run_ingestion_pipeline(
             image_url=request.image_url, 
             owner_id=request.owner_id,
             document_id=request.document_id
        )
        
        # Validate pipeline status
        pipeline_status = result.get("status")
        recommendation_error = result.get("recommendation_error")
        recommendation_data = result.get("recommendation_data")

        if pipeline_status != "completed" or recommendation_error or not recommendation_data:
            error_detail = recommendation_error or result.get("error") or "Recommendation step failed."
            logger.error(f"Ingestion pipeline failed: {error_detail}")
            raise HTTPException(status_code=500, detail=error_detail)

        # Extract location_id and category_code from recommendation
        recommended_location_id = recommendation_data.get("location_id")
        detected_type_code = recommendation_data.get("category_code")
        
        # Get document_id from result (should be UUID string from local storage)
        document_id = result.get("document_id")
        if document_id is None:
            # Fallback: try to get from saved document file if available
            # This handles cases where document_id wasn't properly set in state
            logger.warning("document_id not found in result, attempting to retrieve from saved document")
            # For now, use empty string as fallback
            document_id = ""
        
        # Ensure document_id is a string (UUID format)
        document_id_str = str(document_id) if document_id else ""
        
        # Return response matching IngestResponse schema
        return IngestResponse(
            status="completed",
            document_id=document_id_str,
            detected_type_code=detected_type_code,
            extracted_metadata=result.get("extracted_metadata", {}),
            recommended_location_id=recommended_location_id,
            recommended_location_reason=recommendation_data.get("recommendation_reason") if recommendation_data else None
        )
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