"""
Tests for PLAN_AHEAD pipeline methods - Core functionality only
"""
import pytest
from datetime import datetime, timezone
from app.pipelines.chat import ChatPipeline


class TestApplyPlanModification:
    """Tests for _apply_plan_modification method"""

    def test_remove_existing_date(self):
        """Should remove meal from existing date"""
        pipeline = ChatPipeline()
        meal_plan = {"2026-02-10": "Pasta", "2026-02-11": "Pizza"}
        
        intent = {
            "operation": "remove",
            "date": "2026-02-10"
        }
        
        result = pipeline._apply_plan_modification(meal_plan, [], intent)
        
        assert "2026-02-10" not in result
        assert result == {"2026-02-11": "Pizza"}

    def test_modify_existing_date(self):
        """Should modify meal for existing date"""
        pipeline = ChatPipeline()
        meal_plan = {"2026-02-10": "Pasta"}
        
        intent = {
            "operation": "modify",
            "date": "2026-02-10",
            "meal": "Lasagna"
        }
        
        result = pipeline._apply_plan_modification(meal_plan, [], intent)
        assert result["2026-02-10"] == "Lasagna"

    def test_modify_non_existent_date_adds_meal(self):
        """Should add meal when modifying non-existent date (Bug Fix #14)"""
        pipeline = ChatPipeline()
        meal_plan = {"2026-02-10": "Pasta"}
        
        intent = {
            "operation": "modify",
            "date": "2026-02-12",  # Date doesn't exist
            "meal": "Pizza"
        }
        
        result = pipeline._apply_plan_modification(meal_plan, [], intent)
        assert result["2026-02-12"] == "Pizza"

    def test_add_new_meal(self):
        """Should add new meal to plan"""
        pipeline = ChatPipeline()
        meal_plan = {"2026-02-10": "Pasta"}
        
        intent = {
            "operation": "add",
            "date": "2026-02-11",
            "meal": "Pizza"
        }
        
        result = pipeline._apply_plan_modification(meal_plan, [], intent)
        assert result["2026-02-11"] == "Pizza"
        assert result["2026-02-10"] == "Pasta"  # Existing meal preserved


class TestPlanAheadIntegration:
    """Integration tests for PLAN_AHEAD workflows"""

    @pytest.mark.asyncio
    async def test_plan_ahead_full_workflow_add_remove(self):
        """Test complete add + remove workflow"""
        pipeline = ChatPipeline()
        
        # Start with empty plan
        meal_plan = {}
        
        # Add meal
        add_intent = {
            "operation": "add",
            "date": "2026-02-10",
            "meal": "Pasta"
        }
        meal_plan = pipeline._apply_plan_modification(meal_plan, [], add_intent)
        assert meal_plan["2026-02-10"] == "Pasta"
        
        # Remove meal
        remove_intent = {
            "operation": "remove",
            "date": "2026-02-10"
        }
        meal_plan = pipeline._apply_plan_modification(meal_plan, [], remove_intent)
        assert "2026-02-10" not in meal_plan

    @pytest.mark.asyncio
    async def test_plan_ahead_modify_workflow(self):
        """Test modify workflow"""
        pipeline = ChatPipeline()
        
        meal_plan = {"2026-02-10": "Pasta", "2026-02-11": "Pizza"}
        
        # Modify existing
        modify_intent = {
            "operation": "modify",
            "date": "2026-02-10",
            "meal": "Lasagna"
        }
        meal_plan = pipeline._apply_plan_modification(meal_plan, [], modify_intent)
        assert meal_plan["2026-02-10"] == "Lasagna"
        assert meal_plan["2026-02-11"] == "Pizza"  # Unchanged

    @pytest.mark.asyncio
    async def test_empty_plan_operations(self):
        """Test operations on empty plan"""
        pipeline = ChatPipeline()
        
        meal_plan = {}
        
        # Remove from empty plan (should not error)
        remove_intent = {"operation": "remove", "date": "2026-02-10"}
        result = pipeline._apply_plan_modification(meal_plan, [], remove_intent)
        assert result == {}
        
        # Add to empty plan
        add_intent = {
            "operation": "add",
            "date": "2026-02-10",
            "meal": "Pasta"
        }
        result = pipeline._apply_plan_modification(meal_plan, [], add_intent)
        assert result["2026-02-10"] == "Pasta"
