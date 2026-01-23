"""
SearchAgent: Agent for handling search intent
"""
from typing import Dict, Any
from app.agents.base import BaseAgent
from app.pipelines.search import perform_search


class SearchAgent(BaseAgent):
    """
    Search Agent: Handles document and item search requests
    """
    
    def __init__(self):
        super().__init__("SEARCH")
    
    async def execute(
        self,
        user_input: str,
        owner_id: int,
        top_k: int = 5,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute search operation
        
        Args:
            user_input: User search query
            owner_id: User ID
            top_k: Number of results to return
            **kwargs: Other optional parameters
            
        Returns:
            Action response containing search results
        """
        self.logger.info(f"Executing search for user {owner_id}: {user_input}")
        
        # Perform search
        document_ids = await perform_search(
            user_input,
            owner_id,
            top_k=top_k
        )
        
        if document_ids:
            return self.format_response(
                action="SEARCH",
                message=f"I found {len(document_ids)} document(s) that might match your request.",
                data={
                    "query": user_input,
                    "document_ids": document_ids
                }
            )
        else:
            return self.format_response(
                action="SEARCH",
                message="I searched for your documents but couldn't find anything relevant.",
                data={
                    "query": user_input,
                    "document_ids": []
                }
            )
