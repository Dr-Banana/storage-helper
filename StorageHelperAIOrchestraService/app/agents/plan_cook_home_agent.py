"""
PlanCookHomeAgent: Sub-agent of Plan Ahead for home cooking (inventory + recipe suggestions).

Not a top-level intent; invoked by the chat pipeline when handling PLAN_AHEAD to inject
user inventory and support "cook at home" flows (e.g. suggest recipes from existing stock).
"""
import httpx
from typing import Dict, Any, List
from app.agents.base import BaseAgent
from app.storage.pipeline_storage import _get_storage_base_url


class PlanCookHomeAgent(BaseAgent):
    """
    Plan Cook Home sub-agent: Provides user inventory and recipe suggestions.
    Used under PLAN_AHEAD for cooking-at-home flows.
    """
    def __init__(self):
        super().__init__("PLAN_AHEAD_COOK_HOME")
    
    async def _get_user_inventory(self, owner_id: int) -> List[Dict[str, Any]]:
        """
        Get user's current inventory (receipt items extracted from documents)
        Only returns food items (is_food=True)
        
        Args:
            owner_id: User ID
            
        Returns:
            List of inventory items
        """
        try:
            base_url = _get_storage_base_url()
            if not base_url:
                self.logger.warning("Cannot get inventory: Invalid STORAGE_SERVICE_URL configuration")
                return []
            
            # Get all documents for the user
            url = f"{base_url}/api/users/{owner_id}/documents"
            self.logger.debug(f"Fetching user documents from: {url}")
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                result = response.json()
                documents = result.get("documents", [])
            
            # Extract food items from document metadata
            inventory_items = []
            for doc in documents:
                metadata = doc.get("metadata") or {}
                
                # Check if this is a receipt with items
                if "items" in metadata and isinstance(metadata["items"], list):
                    for item in metadata["items"]:
                        # Only include food items
                        if item.get("is_food", False):
                            inventory_items.append({
                                "product_name": item.get("product_name", "Unknown"),
                                "category": item.get("category", ""),
                                "quantity": item.get("quantity", "1"),
                                "storage_suggestion": item.get("storage_suggestion", ""),
                                "estimated_shelf_life_days": item.get("estimated_shelf_life_days", 0)
                            })
                else:
                    # Check if this is an individual item document (not a receipt)
                    product_name = metadata.get("product_name") or doc.get("title")
                    is_food = metadata.get("is_food", False)
                    
                    if is_food and product_name:
                        inventory_items.append({
                            "product_name": product_name,
                            "category": metadata.get("category", ""),
                            "quantity": metadata.get("quantity", "1"),
                            "storage_suggestion": metadata.get("storage_suggestion", ""),
                            "estimated_shelf_life_days": metadata.get("estimated_shelf_life_days", 0)
                        })
            
            self.logger.info(f"Retrieved {len(inventory_items)} food items from inventory for user {owner_id}")
            return inventory_items
            
        except httpx.ConnectError as e:
            self.logger.error(f"Cannot connect to DataStorageService to get inventory: {e}")
            return []
        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP {e.response.status_code} when fetching inventory: {e.response.text[:200]}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching user inventory: {e}", exc_info=True)
            return []
    
    async def execute(
        self,
        user_input: str,
        owner_id: int,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute home cooking meal planning operation
        
        Args:
            user_input: User input
            owner_id: User ID
            **kwargs: Other optional parameters
            
        Returns:
            Action response containing inventory information and suggestions
        """
        self.logger.info(f"Executing cook home plan for user {owner_id}: {user_input}")
        
        # Get real inventory
        inventory_items = await self._get_user_inventory(owner_id)
        
        if inventory_items:
            # Format inventory list for AI context
            inventory_list = []
            for item in inventory_items[:20]:  # Limit to 20 items to avoid too long context
                product_name = item.get("product_name", "Unknown")
                category = item.get("category", "")
                inventory_list.append(f"- {product_name}" + (f" ({category})" if category else ""))
            
            inventory_text = "\n".join(inventory_list)
            inventory_summary = f"I found {len(inventory_items)} food items in your inventory:\n{inventory_text}"
            
            return self.format_response(
                action="PLAN_COOK_HOME",
                message="Let's plan a meal at home. I can check your fridge and suggest a recipe.",
                data={
                    "suggestion": inventory_summary,
                    "inventory_items": inventory_items,
                    "inventory_count": len(inventory_items)
                }
            )
        else:
            # No inventory found
            return self.format_response(
                action="PLAN_COOK_HOME",
                message="Let's plan a meal at home. I can check your fridge and suggest a recipe.",
                data={
                    "suggestion": "I couldn't find any food items in your current inventory. Try uploading a shopping receipt first to add items to your inventory.",
                    "inventory_items": [],
                    "inventory_count": 0
                }
            )
