"""
UpdateAgent: Agent for handling update intent
"""
from typing import Dict, Any, List
import httpx
from app.agents.base import BaseAgent
from app.pipelines.search import perform_search
from app.storage.pipeline_storage import PipelineStorage
from app.core.config import settings
import asyncio


class UpdateAgent(BaseAgent):
    """
    Update Agent: Handles document and item update requests
    Currently implemented as search functionality, can be extended to actual update operations in the future
    """
    
    def __init__(self):
        super().__init__("UPDATE")
        self.storage = PipelineStorage()
    
    async def _extract_update_details(self, user_input: str) -> Dict[str, Any]:
        """
        Use LLM to extract the specific field updates from the request.
        Example: "I have 5 tomato instead of 1" -> {"quantity": "5"}
        Example: "Move rice to pantry" -> {"location": "pantry"}
        """
        api_key = settings.GEMINI_LLM_API_KEY
        model_name = settings.GEMINI_LLM_MODEL
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        
        system_instruction = """
        You are an information extractor. 
        Extract the fields that the user wants to update and their new values.
        Return valid JSON only.
        
        Fields to look for:
        - quantity (number or string)
        - expiry_date (YYYY-MM-DD or string description)
        - storage_condition (freezer, fridge, pantry, etc.)
        - location (if mentioned)
        - category (if mentioned)
        
        Examples:
        "I have 5 tomato instead of 1" -> {"quantity": "5"}
        "Update my passport expiry date to 2030-01-01" -> {"expiry_date": "2030-01-01"}
        "Move rice to pantry" -> {"location": "pantry"}
        "This is actually a Fruit" -> {"category": "Fruit"}
        """
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_input}]}],
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json"
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(api_url, json=payload)
                response.raise_for_status()
                result = response.json()
                
                candidates = result.get('candidates', [])
                if candidates:
                    content = candidates[0].get('content', {}).get('parts', [])[0].get('text', '').strip()
                    import json
                    # Clean up code blocks if present
                    if content.startswith("```json"):
                        content = content[7:]
                    if content.endswith("```"):
                        content = content[:-3]
                    
                    try:
                        changes = json.loads(content)
                        self.logger.info(f"Extracted update details: {changes} from '{user_input}'")
                        return changes
                    except json.JSONDecodeError:
                        self.logger.warning(f"Failed to parse JSON from LLM: {content}")
                        return {}
                return {}
        except Exception as e:
            self.logger.error(f"Failed to extract update details: {e}")
            return {}

    async def execute(
        self,
        user_input: str,
        owner_id: int,
        top_k: int = 5,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute update operation
        Current implementation: Search first to find items to update
        
        Args:
            user_input: User update query
            owner_id: User ID
            top_k: Number of results to return
            **kwargs: Other optional parameters
            
        Returns:
            Action response containing search results (used to find items to update)
        """
        self.logger.info(f"Executing update search for user {owner_id}: {user_input}")
        
        # 1. Try to extract a specific search term from the complex user input
        # Note: Logic moved to perform_search, but we can call it here if we want 
        # explicit separation, but perform_search handles it now.
        
        # 2. Extract proposed changes (WHAT to update)
        proposed_changes = await self._extract_update_details(user_input)
        
        # 3. Perform search (perform_search handles extracting search term internally)
        document_ids = await perform_search(
            user_input,
            owner_id,
            top_k=top_k
        )
        
        # Fetch document details
        documents = []
        if document_ids:
            try:
                # Fetch documents concurrently
                tasks = [self.storage.get_document(str(doc_id), owner_id=owner_id) for doc_id in document_ids]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for res in results:
                    if isinstance(res, dict):
                        documents.append(res)
                    elif isinstance(res, Exception):
                        self.logger.error(f"Error fetching document: {res}")
            except Exception as e:
                self.logger.error(f"Error fetching document details: {e}")
        
        if document_ids:
            return self.format_response(
                action="UPDATE",
                message=f"I found {len(document_ids)} document(s) that might match your update request.",
                data={
                    "query": user_input,
                    "document_ids": document_ids,
                    "documents": documents,
                    "proposed_changes": proposed_changes
                }
            )
        else:
            return self.format_response(
                action="UPDATE",
                message="I searched for your documents to update but couldn't find anything relevant.",
                data={
                    "query": user_input,
                    "document_ids": [],
                    "documents": [],
                    "proposed_changes": proposed_changes
                }
            )
