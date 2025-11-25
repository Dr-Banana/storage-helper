import httpx
import json
import logging
import asyncio
from typing import List, Optional

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """
    Embedding generator class for converting text to vector representations.
    Uses Gemini API's embedContent endpoint with configurable model and task type.
    """
    
    def __init__(
        self,
        model_name: str = "text-embedding-004",
        api_key: str = "",
        task_type: str = "RETRIEVAL_DOCUMENT",
        max_retries: int = 3,
        timeout: float = 30.0
    ):
        """
        Initialize the EmbeddingGenerator with configuration.
        
        :param model_name: The embedding model name (e.g., "text-embedding-004").
        :param api_key: API key for Gemini API.
        :param task_type: Task type for embedding (e.g., "RETRIEVAL_DOCUMENT").
        :param max_retries: Maximum number of retry attempts.
        :param timeout: Request timeout in seconds.
        """
        self.model_name = model_name
        self.api_key = api_key
        self.task_type = task_type
        self.max_retries = max_retries
        self.timeout = timeout
        self._api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:embedContent?key={self.api_key}"
    
    async def generate(self, text: str) -> List[float]:
        """
        Generate vector representation (embedding) for the given text.
        Uses the configured model and task type to call Gemini API's embedContent endpoint.
        
        :param text: Document text to generate embedding for.
        :return: List of floats representing the embedding vector.
        :raises Exception: If API call fails or returns invalid data after all retries.
        """
        # Validate input text
        if not text or not text.strip():
            logger.warning("Attempted to generate embedding for empty or whitespace text. Returning empty vector.")
            return []
        
        # Construct request payload
        payload = {
            "content": {
                "parts": [
                    {
                        "text": text
                    }
                ]
            },
            "taskType": self.task_type
        }
        
        headers = {'Content-Type': 'application/json'}
        
        # Implement exponential backoff retry
        delay = 1
        
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    logger.info(f"Attempting to generate embedding (Attempt {attempt + 1}/{self.max_retries}). Text length: {len(text)}")
                    response = await client.post(self._api_url, headers=headers, json=payload)
                    response.raise_for_status()
                    
                    result = response.json()
                    
                    # Extract embedding values from result['embedding']['values']
                    embedding_values = result.get('embedding', {}).get('values')
                    
                    if embedding_values and isinstance(embedding_values, list):
                        logger.info(f"Embedding successful. Vector dimension: {len(embedding_values)}")
                        return embedding_values
                    else:
                        error_message = "Embedding response missing 'values' or invalid structure."
                        logger.error(error_message)
                        # Raise ValueError to trigger next retry
                        raise ValueError(error_message)
                        
            except httpx.HTTPError as e:
                logger.error(f"HTTP Error on Embedding API call (Attempt {attempt + 1}/{self.max_retries}): {e}")
            except Exception as e:
                logger.error(f"Error processing Embedding response (Attempt {attempt + 1}/{self.max_retries}): {e}")
                
            if attempt < self.max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
        
        # If all retries fail, raise fatal exception
        logger.critical("Failed to generate embedding after all retries.")
        raise Exception("Embedding generation failed due to repeated API errors.")
    
    async def generate_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts in batch.
        
        :param texts: List of document texts to generate embeddings for.
        :return: List of embedding vectors (each is a list of floats).
        """
        results = []
        for text in texts:
            try:
                embedding = await self.generate(text)
                results.append(embedding)
            except Exception as e:
                logger.error(f"Failed to generate embedding for text (length: {len(text)}): {e}")
                results.append([])  # Append empty list on failure
        return results


# Create a default instance for backward compatibility
_default_generator = EmbeddingGenerator()

# Backward compatibility: export the generate method as a module-level function
async def generate_embedding(text: str) -> List[float]:
    """
    Backward compatibility wrapper for generate_embedding function.
    Uses the default EmbeddingGenerator instance.
    
    :param text: Document text to generate embedding for.
    :return: List of floats representing the embedding vector.
    """
    return await _default_generator.generate(text)