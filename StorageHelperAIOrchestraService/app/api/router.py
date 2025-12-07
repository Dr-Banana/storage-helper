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


@api_router.post("/ingestion")
async def process_document(request: IngestRequest):
    """
    [Ingestion Pipeline]
    Process a document image uploaded by the web client.
    Pipeline flow: OCR -> Cleaning -> Metadata Extraction -> Storage -> Location Recommendation
    
    Returns complete pipeline output including all processing results.
    """
    from app.storage.pipeline_storage import PipelineStorage
    
    try:
        # Call the ingestion pipeline
        result = await ingestion.run_ingestion_pipeline(
             image_url=request.image_url, 
             owner_id=request.owner_id,
             document_id=request.document_id,
             file_type=request.file_type
        )
        
        # Get complete pipeline output using PipelineStorage
        complete_output = PipelineStorage.get_pipeline_output(result)
        
        # Return complete pipeline output
        return complete_output
        
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