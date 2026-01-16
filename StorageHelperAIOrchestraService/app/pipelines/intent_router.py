import logging
from typing import Dict, Any, Optional
from app.modules.intent_classifier import Intent
from app.pipelines.search import perform_search

logger = logging.getLogger(__name__)

async def route_by_intent(intent: Intent, user_input: str, owner_id: int) -> Dict[str, Any]:
    """
    Routes the user request to the appropriate service based on detected intent.
    Currently implements mock responses for the V2 features.
    """
    logger.info(f"Routing intent: {intent} for user: {owner_id}")

    if intent == Intent.SEARCH:
        # Perform actual search
        document_ids = await perform_search(user_input, owner_id)
        
        if document_ids:
            return {
                "action": "SEARCH",
                "message": f"I found {len(document_ids)} document(s) that might match your request.",
                "data": {
                    "query": user_input,
                    "document_ids": document_ids
                }
            }
        else:
            return {
                "action": "SEARCH",
                "message": "I searched for your documents but couldn't find anything relevant.",
                "data": {"query": user_input, "document_ids": []}
            }
    
    elif intent == Intent.PLAN_EAT_OUT:
        # This is a V2 feature
        return {
            "action": "PLAN_EAT_OUT",
            "message": "I see you want to eat out. I can help you find restaurants or make a reservation.",
            "data": {"suggestion": "Would you like me to look for Italian or Japanese restaurants nearby?"}
        }
    
    elif intent == Intent.PLAN_COOK_HOME:
        # This is a V2 feature
        return {
            "action": "PLAN_COOK_HOME",
            "message": "Let's plan a meal at home. I can check your fridge and suggest a recipe.",
            "data": {"suggestion": "I see you have tomatoes and pasta. Should we make a quick Pomodoro?"}
        }
    
    else:
        return {
            "action": "GENERAL",
            "message": "How else can I help you today?",
            "data": {}
        }

