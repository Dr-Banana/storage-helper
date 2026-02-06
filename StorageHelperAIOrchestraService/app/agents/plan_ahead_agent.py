"""
PlanAheadAgent: Agent for planning meals ahead (e.g. next week) and generating shopping list.
"""
from typing import Dict, Any
from app.agents.base import BaseAgent
from app.modules.plan_ahead_state import get_plan_state


class PlanAheadAgent(BaseAgent):
    """
    Plan-ahead Agent: Handles planning meals for a future period (e.g. next week),
    generating a shopping list from recipes, and optionally saving to schedule.
    """

    def __init__(self):
        super().__init__("PLAN_AHEAD")

    async def execute(
        self,
        user_input: str,
        owner_id: int,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute plan-ahead operation. Returns placeholder structure for meal_plan
        and shopping_list; chat pipeline will merge context and LLM output in Step 2.

        Args:
            user_input: User input
            owner_id: User ID
            **kwargs: Optional context (e.g. context with plan_ahead data)

        Returns:
            Action response with meal_plan and shopping_list (empty or from context)
        """
        self.logger.info(f"Executing plan ahead for user {owner_id}: {user_input}")

        # Get plan state: priority: context > server storage
        server_state = get_plan_state(owner_id)
        context = kwargs.get("context") or {}
        
        if context.get("type") == "plan_ahead" and isinstance(context.get("data"), dict):
            # Use context if provided
            data = context["data"].copy()
            data.setdefault("meal_plan", server_state.get("meal_plan", {}))
            data.setdefault("shopping_list", server_state.get("shopping_list", []))
        else:
            # Use server state if no context
            data = {
                "meal_plan": server_state.get("meal_plan", {}),
                "shopping_list": server_state.get("shopping_list", []),
            }

        return self.format_response(
            action="PLAN_AHEAD",
            message="Let's plan your meals for the coming week and build a shopping list.",
            data=data,
        )
