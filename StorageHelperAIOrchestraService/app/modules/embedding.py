import httpx
import json
import logging
import asyncio
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    """
    Data structure for embedding generation results.
    Encapsulates the embedding vector along with metadata and status information.
    """
    vector: List[float]  # The embedding vector
    dimension: int  # Dimension of the embedding vector
    status: str = "success"  # Status: "success", "failed", "pending"
    model_name: Optional[str] = None  # Model used for generation
    task_type: Optional[str] = None  # Task type used (e.g., "RETRIEVAL_DOCUMENT")
    error: Optional[str] = None  # Error message if generation failed
    raw_response: Optional[Dict[str, Any]] = None  # Full API response (optional)
    
    def __post_init__(self):
        """Validate and set dimension if not provided."""
        if self.dimension is None and self.vector:
            self.dimension = len(self.vector)
        elif self.dimension is None:
            self.dimension = 0
    
    @property
    def is_successful(self) -> bool:
        """Check if embedding generation was successful."""
        return self.status == "success" and len(self.vector) > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for serialization."""
        return {
            "vector": self.vector,
            "dimension": self.dimension,
            "status": self.status,
            "model_name": self.model_name,
            "task_type": self.task_type,
            "error": self.error,
        }
    
    @classmethod
    def create_failed(cls, error: str, model_name: Optional[str] = None, task_type: Optional[str] = None) -> "EmbeddingResult":
        """Create a failed EmbeddingResult."""
        return cls(
            vector=[],
            dimension=0,
            status="failed",
            model_name=model_name,
            task_type=task_type,
            error=error
        )
    
    @classmethod
    def create_pending(cls, model_name: Optional[str] = None, task_type: Optional[str] = None) -> "EmbeddingResult":
        """Create a pending EmbeddingResult."""
        return cls(
            vector=[],
            dimension=0,
            status="pending",
            model_name=model_name,
            task_type=task_type
        )


class EmbeddingGenerator:
    """
    Embedding generator class for converting text to vector representations.
    Uses Gemini API's embedContent endpoint with configurable model and task type.
    """
    
    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        task_type: str = "RETRIEVAL_DOCUMENT",
        max_retries: int = 3,
        timeout: float = 30.0
    ):
        """
        Initialize the EmbeddingGenerator with configuration.
        
        :param model_name: The embedding model name. Defaults to settings.GEMINI_EMBEDDING_MODEL.
        :param api_key: API key for Gemini API. Defaults to settings.GEMINI_EMBEDDING_API_KEY.
        :param task_type: Task type for embedding (e.g., "RETRIEVAL_DOCUMENT").
        :param max_retries: Maximum number of retry attempts.
        :param timeout: Request timeout in seconds.
        """
        self.model_name = model_name or settings.GEMINI_EMBEDDING_MODEL
        self.api_key = api_key or settings.GEMINI_EMBEDDING_API_KEY
        self.task_type = task_type
        self.max_retries = max_retries
        self.timeout = timeout
        self._api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:embedContent"
    
    async def generate(self, text: str) -> EmbeddingResult:
        """
        Generate vector representation (embedding) for the given text.
        Uses the configured model and task type to call Gemini API's embedContent endpoint.
        
        :param text: Document text to generate embedding for.
        :return: EmbeddingResult containing the embedding vector and metadata.
        :raises Exception: If API call fails or returns invalid data after all retries.
        """
        # Validate input text
        if not text or not text.strip():
            logger.warning("Attempted to generate embedding for empty or whitespace text. Returning failed result.")
            return EmbeddingResult.create_failed(
                error="Empty or whitespace text provided",
                model_name=self.model_name,
                task_type=self.task_type
            )
        
        # Construct request payload
        payload = {
            "content": {
                "parts": [
                    {
                        "text": text
                    }
                ]
            },
            "taskType": self.task_type,
            "outputDimensionality": 768  # Force 768 dimensions for DB compatibility
        }
        
        headers = {'Content-Type': 'application/json', 'x-goog-api-key': self.api_key}
        
        # Implement exponential backoff retry
        delay = 1
        last_error = "Unknown error"
        
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    # logger.info(f"Attempting to generate embedding (Attempt {attempt + 1}/{self.max_retries}). Text length: {len(text)}")
                    response = await client.post(self._api_url, headers=headers, json=payload)
                    response.raise_for_status()
                    
                    result = response.json()
                    
                    # Extract embedding values from result['embedding']['values']
                    embedding_values = result.get('embedding', {}).get('values')
                    
                    if embedding_values and isinstance(embedding_values, list):
                        # Force truncation if API ignored outputDimensionality
                        if len(embedding_values) > 768:
                            logger.warning(f"Embedding API returned {len(embedding_values)} dimensions despite outputDimensionality=768 request. Truncating to 768.")
                            embedding_values = embedding_values[:768]
                            
                        # logger.info(f"Embedding successful. Vector dimension: {len(embedding_values)}")
                        return EmbeddingResult(
                            vector=embedding_values,
                            dimension=len(embedding_values),
                            status="success",
                            model_name=self.model_name,
                            task_type=self.task_type,
                            raw_response=result
                        )
                    else:
                        error_message = "Embedding response missing 'values' or invalid structure."
                        logger.error(error_message)
                        # Raise ValueError to trigger next retry
                        raise ValueError(error_message)
                        
            except httpx.HTTPError as e:
                logger.error(f"HTTP Error on Embedding API call (Attempt {attempt + 1}/{self.max_retries}): {e}")
                last_error = str(e)
            except Exception as e:
                logger.error(f"Error processing Embedding response (Attempt {attempt + 1}/{self.max_retries}): {e}")
                last_error = str(e)
                
            if attempt < self.max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
        
        # If all retries fail, return failed result instead of raising exception
        error_msg = f"Embedding generation failed after {self.max_retries} retries: {last_error}"
        logger.critical(error_msg)
        return EmbeddingResult.create_failed(
            error=error_msg,
            model_name=self.model_name,
            task_type=self.task_type
        )
    
    async def generate_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """
        Generate embeddings for multiple texts in batch.
        
        :param texts: List of document texts to generate embeddings for.
        :return: List of EmbeddingResult objects.
        """
        results = []
        for text in texts:
            try:
                embedding_result = await self.generate(text)
                results.append(embedding_result)
            except Exception as e:
                logger.error(f"Failed to generate embedding for text (length: {len(text)}): {e}")
                results.append(EmbeddingResult.create_failed(
                    error=str(e),
                    model_name=self.model_name,
                    task_type=self.task_type
                ))
        return results


# Create a default instance for backward compatibility
_default_generator = EmbeddingGenerator()

# Backward compatibility: export the generate method as a module-level function
async def generate_embedding(text: str) -> EmbeddingResult:
    """
    Backward compatibility wrapper for generate_embedding function.
    Uses the default EmbeddingGenerator instance.
    
    :param text: Document text to generate embedding for.
    :return: EmbeddingResult containing the embedding vector and metadata.
    """
    return await _default_generator.generate(text)