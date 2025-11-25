"""
Similarity search engine for finding documents using vector embeddings.
Supports both low-level embedding-based search and high-level text query search.
"""
import numpy as np
from typing import List, Dict, Any, Optional
import logging

from app.storage.local_storage import get_all_embeddings, get_embedding, get_document
from app.modules.embedding import EmbeddingGenerator

logger = logging.getLogger(__name__)


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors.
    
    Cosine similarity range: -1 to 1
    - 1.0 = Perfect match (vectors are identical in direction)
    - 0.0 = No similarity (vectors are orthogonal/unrelated)
    - -1.0 = Completely opposite (vectors point in opposite directions)
    
    For text embeddings (typically normalized positive vectors), 
    the range is usually 0.0 to 1.0 in practice.
    
    :param vec1: First vector
    :param vec2: Second vector
    :return: Cosine similarity score (-1 to 1, typically 0 to 1 for text embeddings)
    """
    if not vec1 or not vec2:
        return 0.0
    
    if len(vec1) != len(vec2):
        logger.warning(f"Vector dimension mismatch: {len(vec1)} vs {len(vec2)}")
        return 0.0
    
    try:
        vec1_array = np.array(vec1)
        vec2_array = np.array(vec2)
        
        dot_product = np.dot(vec1_array, vec2_array)
        norm1 = np.linalg.norm(vec1_array)
        norm2 = np.linalg.norm(vec2_array)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return float(dot_product / (norm1 * norm2))
    except Exception as e:
        logger.error(f"Error calculating cosine similarity: {e}")
        return 0.0


class SearchEngine:
    """
    Vector similarity search engine for finding documents.
    Supports both embedding-based search and text query search.
    """
    
    def __init__(self, embedding_generator: Optional[EmbeddingGenerator] = None):
        """
        Initialize search engine.
        
        :param embedding_generator: Optional EmbeddingGenerator for text query search.
                                   If not provided, text query search will create one when needed.
        """
        self.embedding_generator = embedding_generator
    
    async def search(
        self,
        query_embedding: List[float],
        owner_id: Optional[int] = None,
        top_k: int = 5,
        min_score: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Perform similarity search using query embedding.
        
        :param query_embedding: Query vector embedding
        :param owner_id: Optional owner ID to filter documents
        :param top_k: Number of top results to return
        :param min_score: Minimum similarity score threshold (0-1)
        :return: List of search results with document IDs and scores, sorted by score (descending)
        """
        if not query_embedding:
            logger.warning("Empty query embedding provided")
            return []
        
        logger.info(f"Similarity search: owner_id={owner_id}, top_k={top_k}, min_score={min_score}")
        
        # Get all documents with embeddings
        documents = get_all_embeddings(owner_id=owner_id)
        
        if not documents:
            logger.warning("No documents with embeddings found")
            return []
        
        # Calculate similarity scores for all documents
        scored_results = []
        for doc in documents:
            doc_embedding = doc.get("embedding")
            if not doc_embedding:
                continue
            
            similarity = cosine_similarity(query_embedding, doc_embedding)
            
            if similarity >= min_score:
                scored_results.append({
                    "document_id": doc["id"],
                    "score": similarity,
                    "metadata": doc.get("metadata", {}),
                })
        
        # Sort by score (descending) and return top_k
        scored_results.sort(key=lambda x: x["score"], reverse=True)
        top_results = scored_results[:top_k]
        
        logger.info(f"Similarity search completed: found {len(top_results)} results above threshold")
        return top_results
    
    async def search_by_text(
        self,
        query: str,
        owner_id: Optional[int] = None,
        top_k: int = 5,
        min_score: float = 0.0,
        enrich_results: bool = False
    ) -> List[Dict[str, Any]]:
        """
        High-level search interface that accepts text query.
        Generates embedding from query text and performs similarity search.
        
        :param query: Search query text
        :param owner_id: Optional owner ID to filter documents
        :param top_k: Number of top results to return
        :param min_score: Minimum similarity score threshold (0-1)
        :param enrich_results: If True, enrich results with full document data
        :return: List of search results with scores and document data
        """
        logger.info(f"Text query search: query='{query[:50]}...', owner_id={owner_id}, top_k={top_k}")
        
        # Generate query embedding
        if not self.embedding_generator:
            self.embedding_generator = EmbeddingGenerator()
        
        try:
            query_embedding = await self.embedding_generator.generate(query)
            if not query_embedding:
                logger.error("Failed to generate query embedding")
                return []
        except Exception as e:
            logger.error(f"Error generating query embedding: {e}")
            return []
        
        # Perform similarity search using embedding
        results = await self.search(
            query_embedding=query_embedding,
            owner_id=owner_id,
            top_k=top_k,
            min_score=min_score
        )
        
        # Enrich results with full document data if requested
        if enrich_results:
            enriched_results = []
            for result in results:
                doc_id = result.get("document_id")
                if doc_id:
                    full_doc = get_document(str(doc_id), include_embedding=False)
                    if full_doc:
                        result.update({
                            "text_preview": full_doc.get("extracted_text", "")[:200],
                            "full_text": full_doc.get("extracted_text", ""),
                            "source": full_doc.get("source"),
                            "created_at": full_doc.get("created_at"),
                            "recommendation_data": full_doc.get("recommendation_data"),
                        })
                enriched_results.append(result)
            logger.info(f"Text query search completed: found {len(enriched_results)} enriched results")
            return enriched_results
        
        return results


# Default instance
_default_engine = SearchEngine()


async def run_similarity_search(
    embedding: List[float],
    owner_id: Optional[int] = None,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Backward compatibility wrapper for similarity search.
    Uses the default SearchEngine instance.
    
    :param embedding: Query vector embedding
    :param owner_id: Optional owner ID to filter documents
    :param top_k: Number of top results to return
    :return: List of search results with document IDs and scores
    """
    engine = SearchEngine()
    return await engine.search(embedding, owner_id=owner_id, top_k=top_k)


async def semantic_search(
    query: str,
    owner_id: Optional[int] = None,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Convenience function for semantic search using text query.
    Uses the default SearchEngine instance.
    
    :param query: Search query text
    :param owner_id: Optional owner ID to filter documents
    :param top_k: Number of top results to return
    :return: List of search results with scores and document data
    """
    engine = SearchEngine()
    return await engine.search_by_text(query, owner_id=owner_id, top_k=top_k, enrich_results=True)