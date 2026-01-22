"""
文档语义搜索：query -> embedding -> DataStorageService /api/documents/search
"""
import logging
from typing import List, Optional

from app.modules.embedding import EmbeddingGenerator
from app.storage.pipeline_storage import PipelineStorage

logger = logging.getLogger(__name__)

RECEIPT_KEYWORDS = ["receipt", "receipts", "小票", "发票", "收据", "receipt document"]


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
