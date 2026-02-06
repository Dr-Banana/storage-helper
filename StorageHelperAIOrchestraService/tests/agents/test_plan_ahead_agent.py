"""
Tests for PlanAheadAgent - Core functionality only
"""
import pytest
from app.agents.plan_ahead_agent import PlanAheadAgent
from app.modules.plan_ahead_state import update_plan_state, _plan_states


@pytest.fixture(autouse=True)
def clear_global_state():
    """Clear global state before and after each test"""
    _plan_states.clear()
    yield
    _plan_states.clear()


class TestPlanAheadAgent:
    """Tests for PlanAheadAgent execution"""

    @pytest.mark.asyncio
    async def test_execute_empty_state(self):
        """Should return empty meal_plan and shopping_list when no state exists"""
        agent = PlanAheadAgent()
        
        result = await agent.execute(
            user_input="Help me plan next week",
            owner_id=1,
        )
        
        assert result["action"] == "PLAN_AHEAD"
        assert result["data"]["meal_plan"] == {}
        assert result["data"]["shopping_list"] == []

    @pytest.mark.asyncio
    async def test_execute_with_server_state(self):
        """Should return server state when no context provided"""
        owner_id = 1
        agent = PlanAheadAgent()
        
        # Populate server state
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-10": "Pasta"},
            shopping_list=["tomatoes"],
            schedule_id=7,
            merge=False,
        )
        
        result = await agent.execute(
            user_input="What's my plan?",
            owner_id=owner_id,
        )
        
        assert result["action"] == "PLAN_AHEAD"
        assert result["data"]["meal_plan"] == {"2026-02-10": "Pasta"}
        assert result["data"]["shopping_list"] == ["tomatoes"]

    @pytest.mark.asyncio
    async def test_execute_with_context(self):
        """Should prioritize context over server state"""
        owner_id = 1
        agent = PlanAheadAgent()
        
        # Populate server state
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-10": "Pasta"},
            shopping_list=["tomatoes"],
            merge=False,
        )
        
        # Execute with context (should override server state)
        context = {
            "type": "plan_ahead",
            "data": {
                "meal_plan": {"2026-02-11": "Pizza"},
                "shopping_list": ["cheese"],
            },
        }
        
        result = await agent.execute(
            user_input="Add more meals",
            owner_id=owner_id,
            context=context,
        )
        
        # Should use context data
        assert result["data"]["meal_plan"] == {"2026-02-11": "Pizza"}
        assert result["data"]["shopping_list"] == ["cheese"]
