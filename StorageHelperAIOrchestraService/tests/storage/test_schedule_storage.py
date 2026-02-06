"""
Tests for schedule storage operations - Core functionality only
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.storage.pipeline_storage import PipelineStorage


class TestScheduleStorage:
    """Tests for schedule-related storage operations"""

    @pytest.mark.asyncio
    async def test_create_schedule_success(self):
        """Should create schedule and return ID"""
        storage = PipelineStorage()
        
        with patch.object(storage, 'create_schedule', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {"id": 7}
            
            result = await storage.create_schedule(
                user_id=1,
                schedule_data={
                    "title": "Meal Plan",
                    "event_type": "meal_planning",
                    "metadata": {"meal_plan": {"2026-02-10": "Pasta"}},
                }
            )
            
            assert result["id"] == 7
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_schedules_success(self):
        """Should fetch schedules for user"""
        storage = PipelineStorage()
        
        with patch.object(storage, 'get_user_schedules', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [
                {"id": 7, "title": "Meal Plan"},
                {"id": 8, "title": "Grocery List"},
            ]
            
            result = await storage.get_user_schedules(user_id=1)
            
            assert len(result) == 2
            assert result[0]["id"] == 7
            mock_get.assert_called_once_with(user_id=1)

    @pytest.mark.asyncio
    async def test_update_schedule_success(self):
        """Should update existing schedule"""
        storage = PipelineStorage()
        
        with patch.object(storage, 'update_schedule', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = {"id": 7, "title": "Updated Plan"}
            
            result = await storage.update_schedule(
                schedule_id=7,
                user_id=1,
                schedule_data={"title": "Updated Plan"}
            )
            
            assert result["id"] == 7
            assert result["title"] == "Updated Plan"
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_or_update_meal_plan_schedule(self):
        """Should create or update meal plan schedule correctly"""
        storage = PipelineStorage()
        
        # Mock the methods
        with patch.object(storage, 'update_schedule', new_callable=AsyncMock) as mock_update, \
             patch.object(storage, 'create_schedule', new_callable=AsyncMock) as mock_create:
            
            # Test update path
            mock_update.return_value = {"id": 7}
            
            result = await storage.create_or_update_meal_plan_schedule(
                owner_id=1,
                meal_plan={"2026-02-10": "Pasta"},
                shopping_list=["tomatoes"],
                existing_schedule_id=7
            )
            
            assert result == 7
            mock_update.assert_called_once()
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_existing_schedule(self):
        """Should update existing schedule when ID provided"""
        storage = PipelineStorage()
        
        with patch.object(storage, 'update_schedule', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = {"id": 7}
            
            result = await storage.create_or_update_meal_plan_schedule(
                owner_id=1,
                meal_plan={"2026-02-10": "Pasta"},
                shopping_list=["tomatoes"],
                existing_schedule_id=7
            )
            
            assert result == 7
            mock_update.assert_called_once()
