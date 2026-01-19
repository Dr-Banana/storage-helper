import logging
from typing import List
from app.modules.embedding import EmbeddingGenerator
from app.storage.pipeline_storage import PipelineStorage

logger = logging.getLogger(__name__)

async def perform_search(query: str, owner_id: int, top_k: int = 5) -> List[int]:
    """
    Performs semantic search for documents.
    
    By default, excludes receipt parent documents and only returns item documents.
    If the query explicitly requests receipts (e.g., "receipt", "小票", "发票"), 
    receipts will be included in results.
    """
    try:
        # Check if user explicitly wants receipts
        query_lower = query.lower()
        receipt_keywords = ['receipt', 'receipts', '小票', '发票', '收据', '发票', 'receipt document']
        include_receipts = any(keyword in query_lower for keyword in receipt_keywords)
        
        # 1. Generate embedding for the query
        generator = EmbeddingGenerator(task_type="RETRIEVAL_QUERY")
        embedding_result = await generator.generate(query.strip())
        
        if not embedding_result.is_successful:
            logger.error(f"Failed to generate embedding for search query: {embedding_result.error}")
            return []
        
        # 2. Call DataStorageService search API (search more to account for filtering)
        # If excluding receipts, we may need to search more to get enough items
        search_limit = top_k * 2 if not include_receipts else top_k
        
        storage = PipelineStorage()
        all_document_ids = await storage.search_documents(
            query_embedding=embedding_result.vector,
            owner_id=owner_id,
            top_k=search_limit,
            exclude_receipts=not include_receipts  # Exclude receipts if user didn't ask for them
        )
        
        # Limit to top_k if we searched for more
        document_ids = all_document_ids[:top_k]
        
        return document_ids
        
    except Exception as e:
        logger.error(f"Search operation failed: {e}", exc_info=True)
        return []

