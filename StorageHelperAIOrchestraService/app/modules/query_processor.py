import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class QueryProcessor:
    """
    Query processing class for normalizing and cleaning user search queries.
    """
    
    @staticmethod
    def normalize(query: str) -> str:
        """
        Normalize and clean user query.
        - Remove extra whitespace
        - Normalize case (optional, can be customized)
        - Remove special characters if needed
        - Trim leading/trailing spaces
        
        :param query: Raw user query string
        :return: Normalized query string
        """
        if not query:
            return ""
        
        # Remove extra whitespace and trim
        normalized = re.sub(r'\s+', ' ', query.strip())
        
        logger.debug(f"Query normalized: '{query}' -> '{normalized}'")
        return normalized


# Default instance
_default_processor = QueryProcessor()


async def normalize_query(query: str) -> str:
    """
    Backward compatibility wrapper for normalize_query function.
    Uses the default QueryProcessor instance.
    
    :param query: Raw user query string
    :return: Normalized query string
    """
    return _default_processor.normalize(query)