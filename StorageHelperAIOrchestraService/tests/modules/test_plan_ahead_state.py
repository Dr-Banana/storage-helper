"""
Tests for plan_ahead_state module
"""
import pytest
from datetime import datetime, timezone
from app.modules.plan_ahead_state import (
    get_plan_state,
    update_plan_state,
    clear_plan_state,
    _plan_states,
    _UNSET,
)


@pytest.fixture(autouse=True)
def clear_global_state():
    """Clear global state before and after each test"""
    _plan_states.clear()
    yield
    _plan_states.clear()


class TestGetPlanState:
    """Tests for get_plan_state function"""

    def test_get_empty_state(self):
        """Should return empty dict for non-existent user"""
        state = get_plan_state(owner_id=999)
        assert state == {"meal_plan": {}, "shopping_list": []}

    def test_get_existing_state(self):
        """Should return existing state with all fields"""
        owner_id = 1
        now = datetime.now(timezone.utc)
        _plan_states[owner_id] = {
            "meal_plan": {"2026-02-10": "Pasta"},
            "shopping_list": ["tomatoes", "pasta"],
            "schedule_id": 42,
            "updated_at": now,
        }
        
        state = get_plan_state(owner_id)
        assert state["meal_plan"] == {"2026-02-10": "Pasta"}
        assert state["shopping_list"] == ["tomatoes", "pasta"]
        assert state["schedule_id"] == 42
        assert state["updated_at"] == now


class TestUpdatePlanState:
    """Tests for update_plan_state function"""

    def test_update_merge_meal_plan(self):
        """Should merge meal_plan when merge=True"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {"2026-02-10": "Pasta"},
            "shopping_list": [],
        }
        
        result = update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-11": "Pizza"},
            merge=True,
        )
        
        assert result["meal_plan"] == {
            "2026-02-10": "Pasta",
            "2026-02-11": "Pizza",  # Merged
        }

    def test_update_replace_meal_plan(self):
        """Should replace meal_plan when merge=False"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {"2026-02-10": "Pasta"},
            "shopping_list": [],
        }
        
        result = update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-11": "Pizza"},
            merge=False,
        )
        
        assert result["meal_plan"] == {"2026-02-11": "Pizza"}  # Replaced
        assert "2026-02-10" not in result["meal_plan"]

    def test_update_schedule_id_clears_when_none(self):
        """Should clear schedule_id when explicitly passed None (Bug Fix #13)"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {"2026-02-10": "Pasta"},
            "shopping_list": [],
            "schedule_id": 123,
        }
        
        result = update_plan_state(
            owner_id=owner_id,
            schedule_id=None,  # Explicitly clear
            merge=True,
        )
        
        assert "schedule_id" not in result or result["schedule_id"] is None

    def test_update_schedule_id_keeps_existing_when_unset(self):
        """Should keep existing schedule_id when not provided"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {},
            "shopping_list": [],
            "schedule_id": 123,
        }
        
        result = update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-10": "Pizza"},
            # schedule_id not provided - should keep existing
        )
        
        assert result["schedule_id"] == 123  # Kept existing


class TestClearPlanState:
    """Tests for clear_plan_state function"""

    def test_clear_existing_state(self):
        """Should clear state and return True"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {"2026-02-10": "Pasta"},
            "shopping_list": ["tomatoes"],
        }
        
        result = clear_plan_state(owner_id)
        assert result is True
        assert owner_id not in _plan_states

    def test_clear_non_existent_state(self):
        """Should return False when state doesn't exist"""
        result = clear_plan_state(owner_id=999)
        assert result is False


class TestIntegrationScenarios:
    """Integration tests for common usage patterns"""

    def test_meal_planning_workflow(self):
        """Test complete meal planning workflow"""
        owner_id = 1
        
        # Step 1: User starts planning
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-10": "Pasta"},
            shopping_list=["tomatoes"],
            merge=False,
        )
        
        # Step 2: User adds another meal
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-11": "Pizza"},
            merge=True,
        )
        
        state = get_plan_state(owner_id)
        assert len(state["meal_plan"]) == 2
        assert state["meal_plan"]["2026-02-10"] == "Pasta"
        assert state["meal_plan"]["2026-02-11"] == "Pizza"
        
        # Step 3: User saves to schedule
        update_plan_state(
            owner_id=owner_id,
            schedule_id=7,
            merge=True,
        )
        
        state = get_plan_state(owner_id)
        assert state["schedule_id"] == 7
        
        # Step 4: User removes a meal
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-11": "Pizza"},  # Only keep Pizza
            merge=False,  # Replace
        )
        
        state = get_plan_state(owner_id)
        assert len(state["meal_plan"]) == 1
        assert "2026-02-10" not in state["meal_plan"]

    def test_schedule_failure_recovery(self):
        """Test recovery when schedule write fails"""
        owner_id = 1
        
        # User has a plan with schedule_id
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-10": "Pasta"},
            schedule_id=6,
            merge=False,
        )
        
        state = get_plan_state(owner_id)
        assert state["schedule_id"] == 6
        
        # Schedule write fails (e.g., 404), clear stale ID
        update_plan_state(
            owner_id=owner_id,
            schedule_id=None,  # Explicitly clear
            merge=True,
        )
        
        state = get_plan_state(owner_id)
        assert "schedule_id" not in state or state["schedule_id"] is None
        
        # Next write creates new schedule
        update_plan_state(
            owner_id=owner_id,
            schedule_id=7,  # New ID
            merge=True,
        )
        
        state = get_plan_state(owner_id)
        assert state["schedule_id"] == 7
