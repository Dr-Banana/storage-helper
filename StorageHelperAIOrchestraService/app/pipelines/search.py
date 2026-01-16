import logging
from typing import List
from app.modules.embedding import EmbeddingGenerator
from app.storage.pipeline_storage import PipelineStorage

logger = logging.getLogger(__name__)

async def perform_search(query: str, owner_id: int, top_k: int = 5) -> List[int]:
    """
    Performs semantic search for documents.
    """
    try:
        logger.info(f"Performing search for query: '{query}' for owner_id={owner_id}")
        
        # 1. Generate embedding for the query
        generator = EmbeddingGenerator(task_type="RETRIEVAL_QUERY")
        embedding_result = await generator.generate(query.strip())
        
        if not embedding_result.is_successful:
            logger.error(f"Failed to generate embedding for search query: {embedding_result.error}")
            return []
        
        # 2. Call DataStorageService search API
        storage = PipelineStorage()
        document_ids = await storage.search_documents(
            query_embedding=embedding_result.vector,
            owner_id=owner_id,
            top_k=top_k
        )
        
        return document_ids
        
    except Exception as e:
        logger.error(f"Search operation failed: {e}", exc_info=True)
        return []

