"""
文档语义搜索：query -> embedding -> DataStorageService /api/documents/search
"""
import logging
import httpx
from typing import List, Optional

from app.modules.embedding import EmbeddingGenerator
from app.storage.pipeline_storage import PipelineStorage
from app.core.config import settings

logger = logging.getLogger(__name__)

RECEIPT_KEYWORDS = ["receipt", "receipts", "小票", "发票", "收据", "receipt document"]


async def extract_search_term(user_input: str) -> Optional[str]:
    """
    Use LLM to extract the main object/product name from a complex query.
    Example: "I have 5 tomato instead of 1" -> "tomato"
    """
    try:
        api_key = settings.GEMINI_LLM_API_KEY
        model_name = settings.GEMINI_LLM_MODEL
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        
        system_instruction = """
        You are an entity extractor. 
        Extract the main object/product name from the user's request.
        Return ONLY the object name (and maybe a few identifying words like brand if present).
        If the query is already simple (e.g., "tomato"), just return it as is.
        
        Examples:
        "I have 5 tomato instead of 1" -> "tomato"
        "Update my passport expiry date" -> "passport"
        "Where is the rice?" -> "rice"
        "show me receipts" -> "receipts"
        """
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_input}]}],
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 50
            }
        }
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(api_url, json=payload)
            if response.status_code != 200:
                return None
                
            result = response.json()
            candidates = result.get('candidates', [])
            if candidates:
                content = candidates[0].get('content', {}).get('parts', [])[0].get('text', '').strip()
                content = content.replace('"', '').replace("'", "").strip()
                # Only use extraction if it's significantly shorter than input (meaning extraction happened)
                # or if input was long. If input is short, it's likely already a keyword.
                if len(content) < len(user_input) or len(user_input.split()) > 3:
                    return content
            return None
    except Exception as e:
        logger.warning(f"Failed to extract search term: {e}")
        return None


async def perform_search(
    query: str,
    owner_id: int,
    top_k: int = 5,
    exclude_receipts: Optional[bool] = None,
) -> List[int]:
    """
    query -> embedding -> /api/documents/search，返回 document id 列表。
    exclude_receipts 为 None 时按 query 是否含小票关键词推断。
    """
    try:
        query_clean = (query or "").strip()
        if not query_clean:
            logger.warning("perform_search: empty query")
            return []

        # Optimization: Try to extract search term for complex queries
        # This helps with "I have 5 tomato" -> "tomato" matching
        if len(query_clean.split()) > 2:
            extracted = await extract_search_term(query_clean)
            if extracted:
                logger.info(f"Optimized search query: '{query_clean}' -> '{extracted}'")
                query_clean = extracted

        if exclude_receipts is None:
            ql = query_clean.lower()
            include_receipts = any(kw in ql for kw in RECEIPT_KEYWORDS)
            exclude_receipts = not include_receipts

        gen = EmbeddingGenerator(task_type="RETRIEVAL_QUERY")
        emb = await gen.generate(query_clean)
        if not emb.is_successful:
            logger.error("perform_search: embedding failed: %s", emb.error)
            return []

        search_limit = top_k * 2 if exclude_receipts else top_k
        storage = PipelineStorage()
        ids = await storage.search_documents(
            query_embedding=emb.vector,
            owner_id=owner_id,
            top_k=search_limit,
            exclude_receipts=exclude_receipts,
        )
        return (ids or [])[:top_k]
    except Exception as e:
        logger.error("perform_search failed: %s", e, exc_info=True)
        return []
