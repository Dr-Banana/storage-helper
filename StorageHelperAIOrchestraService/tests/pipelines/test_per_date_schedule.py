# -*- coding: utf-8 -*-
"""
Per-date schedule isolation tests.

Rule: Every calendar date must live in exactly ONE schedule.
      Two different dates must NEVER share the same schedule_id.
      Adding a meal on Date B must NOT touch the schedule for Date A.

Tests run without a live API: all HTTP calls are mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from app.storage.pipeline_storage import PipelineStorage

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_schedule(
    sid: int,
    date_str: str,
    meal_time: str = "dinner",
    dish: str = "回锅肉",
) -> dict:
    """Return a minimal schedule dict as returned by the storage API."""
    return {
        "id": sid,
        "owner_id": 1,
        "title": dish,
        "event_type": "meal_plan_draft",
        "metadata": {
            "features": [
                {
                    "type": "meal_plan",
                    "plans": [
                        {
                            "date": date_str,
                            "meals": [
                                {
                                    "mealTime": meal_time,
                                    "dishes": [{"name": dish}],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    }


def _storage() -> PipelineStorage:
    return PipelineStorage()


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestPerDateScheduleIsolation:
    """
    Verify that `create_or_update_meal_plan_schedule` stores each date in its
    own schedule and never merges different dates into one schedule.
    """

    # ── 1. Fresh add: no existing schedule → create new ───────────────────────

    @pytest.mark.asyncio
    async def test_new_date_creates_new_schedule(self):
        """When no schedule exists for a date, a new one is created."""
        created_ids = []

        async def fake_get_schedules(owner_id):
            return []  # nothing in DB yet

        async def fake_create(owner_id, title, scheduled_time, event_type, metadata):
            new_id = 100
            created_ids.append(new_id)
            return new_id

        storage = _storage()
        storage.get_user_schedules = AsyncMock(side_effect=fake_get_schedules)
        storage.create_schedule = AsyncMock(side_effect=fake_create)
        storage.update_schedule = AsyncMock(return_value=True)

        sid = await storage.create_or_update_meal_plan_schedule(
            owner_id=1,
            meal_plan={"2026-03-20": "红烧肉"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={"2026-03-20": {"dinner": ["红烧肉"]}},
        )

        assert sid == 100
        storage.create_schedule.assert_called_once()
        storage.update_schedule.assert_not_called()

    # ── 2. Update: schedule already exists for this date → update it ─────────

    @pytest.mark.asyncio
    async def test_existing_date_is_updated_not_duplicated(self):
        """When a schedule for the same date already exists, it is updated."""
        existing = _make_schedule(sid=42, date_str="2026-03-20", dish="回锅肉")

        async def fake_get_schedules(owner_id):
            return [existing]

        storage = _storage()
        storage.get_user_schedules = AsyncMock(side_effect=fake_get_schedules)
        storage.update_schedule = AsyncMock(return_value=True)
        storage.create_schedule = AsyncMock()

        sid = await storage.create_or_update_meal_plan_schedule(
            owner_id=1,
            meal_plan={"2026-03-20": "麻婆豆腐"},  # same date, new dish
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={"2026-03-20": {"dinner": ["麻婆豆腐"]}},
        )

        assert sid == 42
        storage.update_schedule.assert_called_once()
        storage.create_schedule.assert_not_called()

    # ── 3. Two dates → two separate schedules ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_two_dates_produce_two_distinct_schedules(self):
        """Adding meals for two different dates must create two separate schedules."""
        next_id = [200]

        async def fake_get_schedules(owner_id):
            return []

        async def fake_create(owner_id, title, scheduled_time, event_type, metadata):
            sid = next_id[0]
            next_id[0] += 1
            return sid

        storage = _storage()
        storage.get_user_schedules = AsyncMock(side_effect=fake_get_schedules)
        storage.create_schedule = AsyncMock(side_effect=fake_create)
        storage.update_schedule = AsyncMock(return_value=True)

        # The storage method returns only the FIRST schedule_id created, but we
        # care that create_schedule was called TWICE with different dates.
        await storage.create_or_update_meal_plan_schedule(
            owner_id=1,
            meal_plan={
                "2026-03-20": "宫保鸡丁",
                "2026-03-21": "清蒸鱼",
            },
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={
                "2026-03-20": {"lunch": ["宫保鸡丁"]},
                "2026-03-21": {"dinner": ["清蒸鱼"]},
            },
        )

        assert storage.create_schedule.call_count == 2
        # Extract the dates from the metadata passed to each create_schedule call
        calls_metadata = [c.kwargs.get("metadata") or c.args[4] if len(c.args) > 4 else c.kwargs.get("metadata")
                          for c in storage.create_schedule.call_args_list]
        dates_found = set()
        for m in calls_metadata:
            if m and isinstance(m.get("features"), list):
                for feat in m["features"]:
                    if feat.get("type") == "meal_plan":
                        for plan in feat.get("plans", []):
                            dates_found.add(plan.get("date"))
        assert "2026-03-20" in dates_found
        assert "2026-03-21" in dates_found

    # ── 4. Adding Date B must NOT touch schedule for Date A ───────────────────

    @pytest.mark.asyncio
    async def test_adding_new_date_does_not_update_other_date_schedule(self):
        """
        Scenario: schedule 42 covers 2026-03-16 (already saved).
        User adds a meal for 2026-03-17 (a new date).
        Storage must CREATE a new schedule for 2026-03-17 and NOT call
        update_schedule on schedule 42.
        """
        existing_16 = _make_schedule(sid=42, date_str="2026-03-16", dish="红烧肉")

        async def fake_get_schedules(owner_id):
            return [existing_16]

        async def fake_create(**kwargs):
            return 99

        storage = _storage()
        storage.get_user_schedules = AsyncMock(side_effect=fake_get_schedules)
        storage.create_schedule = AsyncMock(side_effect=fake_create)
        storage.update_schedule = AsyncMock(return_value=True)

        await storage.create_or_update_meal_plan_schedule(
            owner_id=1,
            meal_plan={"2026-03-17": "小笼包"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={"2026-03-17": {"breakfast": ["小笼包"]}},
        )

        # Schedule 42 (for 2026-03-16) must NOT be touched
        update_ids = [c.kwargs.get("schedule_id") or (c.args[1] if len(c.args) > 1 else None)
                      for c in storage.update_schedule.call_args_list]
        assert 42 not in update_ids, (
            "update_schedule was called on schedule 42 (2026-03-16) "
            "when only 2026-03-17 should be affected."
        )
        storage.create_schedule.assert_called_once()

    # ── 5. existing_schedule_id hint only accepted for same date ─────────────

    @pytest.mark.asyncio
    async def test_existing_schedule_id_hint_ignored_if_date_mismatch(self):
        """
        If `existing_schedule_id` points to a schedule for a DIFFERENT date,
        the hint is ignored and a new schedule is created for the requested date.
        """
        # schedule 55 covers 2026-03-16 only
        existing_16 = _make_schedule(sid=55, date_str="2026-03-16", dish="红烧肉")

        async def fake_get_schedules(owner_id):
            return [existing_16]

        storage = _storage()
        storage.get_user_schedules = AsyncMock(side_effect=fake_get_schedules)
        storage.create_schedule = AsyncMock(return_value=77)
        storage.update_schedule = AsyncMock(return_value=True)

        sid = await storage.create_or_update_meal_plan_schedule(
            owner_id=1,
            meal_plan={"2026-03-17": "牛肉面"},
            shopping_list=[],
            event_type="meal_plan_draft",
            existing_schedule_id=55,  # hint for a DIFFERENT date
            meal_plan_slots={"2026-03-17": {"dinner": ["牛肉面"]}},
        )

        # Must NOT update schedule 55
        update_ids = [
            c.kwargs.get("schedule_id") or (c.args[1] if len(c.args) > 1 else None)
            for c in storage.update_schedule.call_args_list
        ]
        assert 55 not in update_ids
        storage.create_schedule.assert_called_once()
        assert sid == 77

    # ── 6. Modify same date reuses existing schedule ─────────────────────────

    @pytest.mark.asyncio
    async def test_modify_same_date_reuses_schedule(self):
        """
        Modifying the dish for a date that already has a schedule must update
        that schedule, not create a new one.
        """
        existing = _make_schedule(sid=30, date_str="2026-03-15", dish="炒面")

        async def fake_get_schedules(owner_id):
            return [existing]

        storage = _storage()
        storage.get_user_schedules = AsyncMock(side_effect=fake_get_schedules)
        storage.update_schedule = AsyncMock(return_value=True)
        storage.create_schedule = AsyncMock()

        sid = await storage.create_or_update_meal_plan_schedule(
            owner_id=1,
            meal_plan={"2026-03-15": "荞麦面"},  # same date, changed dish
            shopping_list=[],
            event_type="meal_plan_draft",
            existing_schedule_id=30,  # hint — same date → accepted
            meal_plan_slots={"2026-03-15": {"dinner": ["荞麦面"]}},
        )

        assert sid == 30
        storage.update_schedule.assert_called_once()
        storage.create_schedule.assert_not_called()
