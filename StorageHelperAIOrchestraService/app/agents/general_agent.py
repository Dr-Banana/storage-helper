"""
GeneralAgent: Agent for handling general conversations
"""
from typing import Dict, Any
from app.agents.base import BaseAgent


class GeneralAgent(BaseAgent):
    """
    General Agent: Handles greetings, general conversations, and other general intents
    """
    
    def __init__(self):
        super().__init__("GENERAL")
    
    async def execute(
        self,
        user_input: str,
        owner_id: int,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute general conversation operation
        
        Args:
            user_input: User input
            owner_id: User ID
            **kwargs: Other optional parameters
            
        Returns:
            Action response containing general response
        """
        self.logger.info(f"Executing general conversation for user {owner_id}: {user_input}")
        
        return self.format_response(
            action="GENERAL",
            message="How else can I help you today?",
            data={}
        )
