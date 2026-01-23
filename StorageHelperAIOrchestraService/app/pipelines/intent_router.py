"""
Intent Router: Routes requests to appropriate agents based on intent
Refactored to use agent architecture for better modularity
"""
import logging
from typing import Dict, Any
from app.modules.intent_classifier import Intent
from app.agents.agent_factory import agent_factory

logger = logging.getLogger(__name__)


async def route_by_intent(intent: Intent, user_input: str, owner_id: int) -> Dict[str, Any]:
    """
    Route user request to the appropriate agent based on detected intent
    
    Args:
        intent: Detected intent
        user_input: User input
        owner_id: User ID
        
    Returns:
        Dictionary containing action, message, and data
    """
    logger.info(f"Routing intent: {intent} for user: {owner_id}")

    # Use agent factory to get the corresponding agent
    agent = agent_factory.get_agent(intent)
    
    # Execute agent logic
    try:
        result = await agent.execute(
            user_input=user_input,
            owner_id=owner_id
        )
        return result
    except Exception as e:
        logger.error(f"Error executing agent {agent.name}: {e}", exc_info=True)
        # Return general response on error
        general_agent = agent_factory.get_agent(Intent.GENERAL)
        return await general_agent.execute(user_input, owner_id)

