# -*- coding: utf-8 -*-
"""
Tests for per-(date, meal_time) schedule isolation in pipeline_storage.py.

Design rule
-----------
Each (date, meal_time) pair is stored in its OWN schedule_id.

  2026-03-13 breakfast → schedule A
  2026-03-13 dinner    → schedule B   ← separate, never merged into A

Consequence: confirming a dinner plan never touches the breakfast schedule,
and vice versa.  This was the bug: saving dinner for the same date overwrote
the breakfast because they shared a schedule_id.

Scenarios
---------
  1. Breakfast then dinner same date → 2 separate schedule_ids
  2. Three meal times same date → 3 separate schedule_ids
  3. Modify dinner → only the dinner schedule updated, breakfast unchanged
  4. Cross-date still isolated (date A ≠ date B → different schedules)
  5. Re-save same meal_time → finds and updates the existing schedule (no dup)
  6. Unit: meal_time matching logic
"""
from __future__ import annotations

import pytest
from typing import Any, Dict, List, Optional, Set

from app.storage.pipeline_storage import PipelineStorage


# ── Fake storage ──────────────────────────────────────────────────────────────

class FakePipelineStorage(PipelineStorage):
    """In-memory subclass — only overrides HTTP primitives."""

    def __init__(self):
        super().__init__()
        self._db: Dict[int, dict] = {}
        self._next_id = 1

    async def get_user_schedules(self, owner_id: int) -> List[dict]:
        return [s for s in self._db.values() if s.get("owner_id") == owner_id]

    async def create_schedule(self, owner_id, title, scheduled_time, event_type, metadata) -> int:
        sid = self._next_id
        self._next_id += 1
        self._db[sid] = {
            "id": sid, "owner_id": owner_id, "title": title,
            "event_type": event_type, "metadata": metadata,
        }
        return sid

    async def update_schedule(self, owner_id, schedule_id, event_type, metadata) -> bool:
        if schedule_id not in self._db:
            return False
        self._db[schedule_id]["metadata"] = metadata
        return True

    async def delete_schedule(self, schedule_id, owner_id) -> bool:
        return bool(self._db.pop(schedule_id, None))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def meal_times_for_date(self, date_str: str) -> Dict[str, List[str]]:
        """Return {meal_time: [dish, ...]} aggregated over ALL schedules for this date."""
        result: Dict[str, List[str]] = {}
        for s in self._db.values():
            _, _, _, slots = self._extract_meal_plan_from_schedule(s)
            if date_str in slots:
                result.update(slots[date_str])
        return result

    def schedule_id_for(self, date_str: str, meal_time: str) -> Optional[int]:
        """Return the schedule_id that owns (date_str, meal_time), or None."""
        for s in self._db.values():
            _, _, _, slots = self._extract_meal_plan_from_schedule(s)
            if date_str in slots and meal_time in slots[date_str]:
                return s["id"]
        return None

    def all_dates(self) -> Set[str]:
        dates: Set[str] = set()
        for s in self._db.values():
            mp, _, _, _ = self._extract_meal_plan_from_schedule(s)
            dates.update(mp.keys())
        return dates

    def schedule_count(self) -> int:
        return len(self._db)


OWNER = 7777


# ─────────────────────────────────────────────────────────────────────────────
# 1. Breakfast + dinner → two separate schedule_ids
# ─────────────────────────────────────────────────────────────────────────────

class TestSameDateDifferentMealTimes:

    @pytest.mark.asyncio
    async def test_breakfast_and_dinner_get_separate_schedules(self):
        """
        Core invariant: (2026-03-13, breakfast) and (2026-03-13, dinner) must
        live in different schedule_ids.
        """
        storage = FakePipelineStorage()
        date = "2026-03-13"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "小笼包"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"breakfast": ["小笼包"]}},
        )
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "农家小炒肉 and 清炒豆苗 and 白米饭"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"dinner": ["农家小炒肉", "清炒豆苗", "白米饭"]}},
        )

        assert storage.schedule_count() == 2, (
            f"Expected 2 separate schedules (one per meal time), "
            f"got {storage.schedule_count()}"
        )

        sid_breakfast = storage.schedule_id_for(date, "breakfast")
        sid_dinner    = storage.schedule_id_for(date, "dinner")

        assert sid_breakfast is not None, "No schedule found for breakfast"
        assert sid_dinner    is not None, "No schedule found for dinner"
        assert sid_breakfast != sid_dinner, (
            f"breakfast and dinner share the same schedule_id={sid_breakfast}! "
            "They must be stored separately."
        )

    @pytest.mark.asyncio
    async def test_all_dishes_still_accessible_after_two_saves(self):
        """Both meal times must be retrievable even though stored separately."""
        storage = FakePipelineStorage()
        date = "2026-03-13"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "小笼包"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"breakfast": ["小笼包"]}},
        )
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "农家小炒肉"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"dinner": ["农家小炒肉", "清炒豆苗", "白米饭"]}},
        )

        all_mt = storage.meal_times_for_date(date)
        assert "breakfast" in all_mt, f"breakfast missing: {all_mt}"
        assert "dinner"    in all_mt, f"dinner missing: {all_mt}"
        assert "小笼包" in all_mt["breakfast"],    f"小笼包 not in breakfast: {all_mt}"
        assert "农家小炒肉" in all_mt["dinner"], f"农家小炒肉 not in dinner: {all_mt}"

    @pytest.mark.asyncio
    async def test_three_meal_times_produce_three_schedules(self):
        """breakfast, lunch, dinner each get their own schedule_id."""
        storage = FakePipelineStorage()
        date = "2026-03-14"

        for meal_time, dish in [
            ("breakfast", "燕麦粥"),
            ("lunch",     "宫保鸡丁"),
            ("dinner",    "清蒸鱼"),
        ]:
            await storage.create_or_update_meal_plan_schedule(
                owner_id=OWNER,
                meal_plan={date: dish},
                shopping_list=[],
                event_type="meal_plan_draft",
                meal_plan_slots={date: {meal_time: [dish]}},
            )

        assert storage.schedule_count() == 3, (
            f"Expected 3 schedules (one per meal time), got {storage.schedule_count()}"
        )
        sids = {
            meal_time: storage.schedule_id_for(date, meal_time)
            for meal_time in ("breakfast", "lunch", "dinner")
        }
        assert len(set(sids.values())) == 3, (
            f"Some meal times share a schedule_id: {sids}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Modify one meal time → only that schedule updated, other untouched
# ─────────────────────────────────────────────────────────────────────────────

class TestModifyOneMealTimeLeaveOthersAlone:

    @pytest.mark.asyncio
    async def test_modify_dinner_does_not_touch_breakfast_schedule(self):
        """
        Scenario from the bug report:
          1. Breakfast saved (小笼包)   → schedule A
          2. Dinner saved (红烧肉)      → schedule B
          3. User modifies dinner       → only schedule B updated
          4. Schedule A (breakfast) unchanged
        """
        storage = FakePipelineStorage()
        date = "2026-03-15"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "小笼包"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"breakfast": ["小笼包"]}},
        )
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "红烧肉"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"dinner": ["红烧肉"]}},
        )

        sid_breakfast_before = storage.schedule_id_for(date, "breakfast")
        sid_dinner_before    = storage.schedule_id_for(date, "dinner")
        assert sid_breakfast_before != sid_dinner_before

        # Modify dinner
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "麻婆豆腐"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"dinner": ["麻婆豆腐"]}},
        )

        # Still only 2 schedules (no new schedule created for modify)
        assert storage.schedule_count() == 2, (
            f"modify should reuse existing dinner schedule, not create a new one. "
            f"count={storage.schedule_count()}"
        )

        all_mt = storage.meal_times_for_date(date)
        assert all_mt.get("breakfast") == ["小笼包"], (
            f"Breakfast was changed/lost after modifying dinner: {all_mt}"
        )
        assert all_mt.get("dinner") == ["麻婆豆腐"], (
            f"Dinner was not updated: {all_mt}"
        )
        # Breakfast schedule_id must not have changed
        assert storage.schedule_id_for(date, "breakfast") == sid_breakfast_before, (
            "Breakfast schedule_id changed after modifying dinner!"
        )

    @pytest.mark.asyncio
    async def test_re_save_same_meal_time_updates_not_duplicates(self):
        """Saving the same meal_time again must UPDATE, not create a new schedule."""
        storage = FakePipelineStorage()
        date = "2026-03-16"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "小笼包"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"breakfast": ["小笼包"]}},
        )
        sid_first = storage.schedule_id_for(date, "breakfast")
        assert storage.schedule_count() == 1

        # Save breakfast again with a different dish
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "豆浆油条"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"breakfast": ["豆浆油条"]}},
        )

        assert storage.schedule_count() == 1, (
            "Re-saving breakfast created a duplicate schedule!"
        )
        assert storage.schedule_id_for(date, "breakfast") == sid_first, (
            "Re-save changed the breakfast schedule_id"
        )
        assert storage.meal_times_for_date(date)["breakfast"] == ["豆浆油条"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Cross-date isolation preserved
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossDateIsolation:

    @pytest.mark.asyncio
    async def test_different_dates_different_schedules(self):
        storage = FakePipelineStorage()
        date_a = "2026-03-13"
        date_b = "2026-03-14"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date_a: "小笼包"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date_a: {"breakfast": ["小笼包"]}},
        )
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date_b: "清蒸鱼"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date_b: {"dinner": ["清蒸鱼"]}},
        )

        assert storage.schedule_count() == 2
        assert storage.all_dates() == {date_a, date_b}

        sid_a = storage.schedule_id_for(date_a, "breakfast")
        sid_b = storage.schedule_id_for(date_b, "dinner")
        assert sid_a != sid_b, "Different dates share a schedule_id"

        # date_b schedule must not contain breakfast from date_a
        mt_b = storage.meal_times_for_date(date_b)
        assert "breakfast" not in mt_b, f"date_b has breakfast from date_a: {mt_b}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. is_append=True: new dishes must MERGE with existing dishes in the same
#    meal_time rather than overwriting them.
#
# This fixes the regression where:
#   1. User adds 照烧鸡排 to lunch  → schedule 78, lunch=[照烧鸡排]
#   2. User asks to "再加个冻豆腐"  → AI clarifies which meal
#   3. User answers "今天午饭"       → action="add", slot={lunch:[冻豆腐]}
#   Bug: storage wrote lunch=[冻豆腐], LOSING 照烧鸡排
#   Fix: storage must write lunch=[照烧鸡排, 冻豆腐]
# ─────────────────────────────────────────────────────────────────────────────

class TestAppendMergesDishesInSameMealTime:

    @pytest.mark.asyncio
    async def test_append_adds_dish_to_existing_lunch(self):
        """
        Exact reproduction of the bug:
          Round 1 (add): 照烧鸡排 → lunch
          Round 2 (add, is_append=True): 冻豆腐 → lunch
          Expected: lunch = [照烧鸡排, 冻豆腐]  (NOT just [冻豆腐])
        """
        storage = FakePipelineStorage()
        date = "2026-03-22"

        # Round 1: add 照烧鸡排
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "照烧鸡排"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["照烧鸡排"]}},
            is_append=True,
        )
        assert storage.meal_times_for_date(date)["lunch"] == ["照烧鸡排"]

        # Round 2: add 冻豆腐 to the same lunch
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "冻豆腐"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["冻豆腐"]}},
            is_append=True,
        )

        mt = storage.meal_times_for_date(date)
        assert "lunch" in mt, f"lunch slot missing: {mt}"
        assert "照烧鸡排" in mt["lunch"], (
            f"照烧鸡排 was LOST after appending 冻豆腐 — overwrite bug regression! "
            f"lunch={mt['lunch']}"
        )
        assert "冻豆腐" in mt["lunch"], (
            f"冻豆腐 was not added to lunch: {mt['lunch']}"
        )
        assert storage.schedule_count() == 1, (
            "Should reuse the same schedule, not create a new one"
        )

    @pytest.mark.asyncio
    async def test_append_deduplicates_same_dish(self):
        """Appending a dish that already exists must not create a duplicate."""
        storage = FakePipelineStorage()
        date = "2026-03-22"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "照烧鸡排"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["照烧鸡排"]}},
            is_append=True,
        )
        # Append the same dish again
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "照烧鸡排"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["照烧鸡排"]}},
            is_append=True,
        )

        mt = storage.meal_times_for_date(date)
        assert mt["lunch"].count("照烧鸡排") == 1, (
            f"Duplicate dish found after append: {mt['lunch']}"
        )

    @pytest.mark.asyncio
    async def test_append_multi_dish_slot_merges_all(self):
        """
        Existing lunch=[A].  Append slot has [B, C].
        Result must be [A, B, C] with no duplicates.
        """
        storage = FakePipelineStorage()
        date = "2026-03-22"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "宫保鸡丁"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["宫保鸡丁"]}},
            is_append=True,
        )
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "米饭 and 汤"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["米饭", "汤"]}},
            is_append=True,
        )

        mt = storage.meal_times_for_date(date)
        lunch = mt["lunch"]
        for dish in ("宫保鸡丁", "米饭", "汤"):
            assert dish in lunch, f"{dish} missing after multi-dish append: {lunch}"
        assert len(lunch) == 3, f"Expected 3 dishes, got: {lunch}"

    @pytest.mark.asyncio
    async def test_modify_without_append_replaces_dishes(self):
        """
        When is_append=False (default, used for modify), the new dishes
        REPLACE the existing ones — merge must NOT happen.
        """
        storage = FakePipelineStorage()
        date = "2026-03-22"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "照烧鸡排"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["照烧鸡排"]}},
            is_append=False,
        )
        # Modify (replace) — user wants ONLY 麻婆豆腐 for lunch
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "麻婆豆腐"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["麻婆豆腐"]}},
            is_append=False,
        )

        mt = storage.meal_times_for_date(date)
        assert mt["lunch"] == ["麻婆豆腐"], (
            f"modify should replace dishes, not merge. Got: {mt['lunch']}"
        )
        assert "照烧鸡排" not in mt["lunch"], (
            f"旧菜 照烧鸡排 should have been replaced, not preserved: {mt['lunch']}"
        )

    @pytest.mark.asyncio
    async def test_append_to_different_meal_time_no_interference(self):
        """
        Lunch=[照烧鸡排]. Append 冻豆腐 to DINNER (not lunch).
        Lunch must be unchanged.
        """
        storage = FakePipelineStorage()
        date = "2026-03-22"

        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "照烧鸡排"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"lunch": ["照烧鸡排"]}},
            is_append=True,
        )
        # Append 冻豆腐 to DINNER
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "冻豆腐"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"dinner": ["冻豆腐"]}},
            is_append=True,
        )

        mt = storage.meal_times_for_date(date)
        assert mt.get("lunch") == ["照烧鸡排"], (
            f"Lunch was modified when appending to dinner: {mt}"
        )
        assert "冻豆腐" in mt.get("dinner", []), (
            f"冻豆腐 not added to dinner: {mt}"
        )
        # Different meal_times → 2 separate schedules
        assert storage.schedule_count() == 2

    @pytest.mark.asyncio
    async def test_append_to_new_date_creates_schedule(self):
        """Appending to a date with no existing schedule creates one."""
        storage = FakePipelineStorage()
        date = "2026-03-25"

        assert storage.schedule_count() == 0
        await storage.create_or_update_meal_plan_schedule(
            owner_id=OWNER,
            meal_plan={date: "糖醋排骨"},
            shopping_list=[],
            event_type="meal_plan_draft",
            meal_plan_slots={date: {"dinner": ["糖醋排骨"]}},
            is_append=True,
        )

        assert storage.schedule_count() == 1
        mt = storage.meal_times_for_date(date)
        assert mt.get("dinner") == ["糖醋排骨"]

    @pytest.mark.asyncio
    async def test_append_three_dishes_one_by_one_all_present(self):
        """
        Add 3 dishes to lunch one at a time via separate append calls.
        All 3 must be present in lunch at the end.
        """
        storage = FakePipelineStorage()
        date = "2026-03-22"

        for dish in ("照烧鸡排", "冻豆腐", "味噌汤"):
            await storage.create_or_update_meal_plan_schedule(
                owner_id=OWNER,
                meal_plan={date: dish},
                shopping_list=[],
                event_type="meal_plan_draft",
                meal_plan_slots={date: {"lunch": [dish]}},
                is_append=True,
            )

        mt = storage.meal_times_for_date(date)
        lunch = mt.get("lunch", [])
        for dish in ("照烧鸡排", "冻豆腐", "味噌汤"):
            assert dish in lunch, f"{dish} missing after sequential appends: {lunch}"
        assert len(lunch) == 3, f"Expected exactly 3 dishes: {lunch}"
        # All three live in the same schedule (same date+meal_time)
        assert storage.schedule_count() == 1
