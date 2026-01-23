"""
PlanEatOutAgent: Agent for handling eat-out meal planning
"""
from typing import Dict, Any
from app.agents.base import BaseAgent


class PlanEatOutAgent(BaseAgent):
    """
    Eat-out meal planning Agent: Handles restaurant search and reservation requests
    This is a V2 feature, currently provides basic responses
    """
    
    def __init__(self):
        super().__init__("PLAN_EAT_OUT")
    
    async def execute(
        self,
        user_input: str,
        owner_id: int,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute eat-out meal planning operation
        
        Args:
            user_input: User input
            owner_id: User ID
            **kwargs: Other optional parameters
            
        Returns:
            Action response containing suggestions
        """
        self.logger.info(f"Executing eat out plan for user {owner_id}: {user_input}")
        
        return self.format_response(
            action="PLAN_EAT_OUT",
            message="I see you want to eat out. I can help you find restaurants or make a reservation.",
            data={
                "suggestion": "Would you like me to look for Italian or Japanese restaurants nearby?"
            }
        )
