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
        """Should return default empty state for non-existent user"""
        state = get_plan_state(owner_id=999)
        assert state == {
            "meal_plan": {},
            "shopping_list": [],
            "meal_plan_slots": {},
            "dish_ingredients": {},
            "is_draft": False,
            "draft_base_db_dates": set(),
            "last_pipeline_action": None,
            "cooking_context": None,
            "pending_modify_action": None,
            "pending_overwrite": None,
            "pending_options": None,
            "draft_rejected_dishes": set(),
            "pending_planning_queue": [],
            "pending_ask_dates": [],
            "meal_planning_queue": [],
            "meal_planning_total": 0,
            "confirmation_retry_count": 0,
            "refresh_count": 0,
        }

    def test_get_existing_state(self):
        """Should return existing state with all fields"""
        owner_id = 1
        now = datetime.now(timezone.utc)
        _plan_states[owner_id] = {
            "meal_plan": {"2026-02-10": "Pasta"},
            "shopping_list": ["tomatoes", "pasta"],
            "schedule_id": 42,
            "meal_plan_slots": {"2026-02-10": {"lunch": "Pasta"}},
            "dish_ingredients": {"Pasta": ["tomato", "pasta"]},
            "updated_at": now,
        }

        state = get_plan_state(owner_id)
        assert state["meal_plan"] == {"2026-02-10": "Pasta"}
        assert state["shopping_list"] == ["tomatoes", "pasta"]
        assert state["schedule_id"] == 42
        assert state["meal_plan_slots"] == {"2026-02-10": {"lunch": "Pasta"}}
        assert state["dish_ingredients"] == {"Pasta": ["tomato", "pasta"]}
        assert state["updated_at"] == now

    def test_get_existing_state_defaults_missing_new_fields(self):
        """Should default meal_plan_slots and dish_ingredients to {} if not stored"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {"2026-02-10": "Pasta"},
            "shopping_list": [],
        }

        state = get_plan_state(owner_id)
        assert state["meal_plan_slots"] == {}
        assert state["dish_ingredients"] == {}


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

    def test_update_merge_meal_plan_slots(self):
        """Should deep-merge meal_plan_slots per date when merge=True"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {},
            "shopping_list": [],
            "meal_plan_slots": {"2026-02-10": {"lunch": "Pasta"}},
        }

        result = update_plan_state(
            owner_id=owner_id,
            meal_plan_slots={"2026-02-10": {"dinner": "Salad"}, "2026-02-11": {"lunch": "Pizza"}},
            merge=True,
        )

        assert result["meal_plan_slots"]["2026-02-10"] == {"lunch": "Pasta", "dinner": "Salad"}
        assert result["meal_plan_slots"]["2026-02-11"] == {"lunch": "Pizza"}

    def test_update_replace_meal_plan_slots(self):
        """Should replace meal_plan_slots entirely when merge=False"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {},
            "shopping_list": [],
            "meal_plan_slots": {"2026-02-10": {"lunch": "Pasta"}},
        }

        result = update_plan_state(
            owner_id=owner_id,
            meal_plan_slots={"2026-02-11": {"dinner": "Salad"}},
            merge=False,
        )

        assert result["meal_plan_slots"] == {"2026-02-11": {"dinner": "Salad"}}
        assert "2026-02-10" not in result["meal_plan_slots"]

    def test_update_merge_dish_ingredients(self):
        """Should merge dish_ingredients (deduplicated) when merge=True"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {},
            "shopping_list": [],
            "dish_ingredients": {"Pasta": ["tomato", "pasta"]},
        }

        result = update_plan_state(
            owner_id=owner_id,
            dish_ingredients={"Pasta": ["cheese", "tomato"], "Pizza": ["dough", "cheese"]},
            merge=True,
        )

        assert set(result["dish_ingredients"]["Pasta"]) == {"tomato", "pasta", "cheese"}
        assert set(result["dish_ingredients"]["Pizza"]) == {"dough", "cheese"}

    def test_update_replace_dish_ingredients(self):
        """Should replace dish_ingredients entirely when merge=False"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {},
            "shopping_list": [],
            "dish_ingredients": {"Pasta": ["tomato", "pasta"]},
        }

        result = update_plan_state(
            owner_id=owner_id,
            dish_ingredients={"Pizza": ["dough", "cheese"]},
            merge=False,
        )

        assert result["dish_ingredients"] == {"Pizza": ["dough", "cheese"]}
        assert "Pasta" not in result["dish_ingredients"]

    def test_update_schedule_id_clears_when_none(self):
        """Should remove schedule_id from state when explicitly passed None (Bug Fix #13)"""
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

        assert "schedule_id" not in result

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

    def test_update_initializes_missing_fields(self):
        """update_plan_state should auto-initialize meal_plan_slots and dish_ingredients"""
        owner_id = 1
        _plan_states[owner_id] = {
            "meal_plan": {},
            "shopping_list": [],
        }

        result = update_plan_state(owner_id=owner_id, merge=True)

        assert result["meal_plan_slots"] == {}
        assert result["dish_ingredients"] == {}


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


class TestDraftMode:
    """Tests for is_draft flag and draft_base_db_dates snapshot field."""

    def test_is_draft_defaults_false_empty_state(self):
        """Empty state returns is_draft=False."""
        assert get_plan_state(owner_id=900)["is_draft"] is False

    def test_is_draft_defaults_false_for_legacy_state_without_flag(self):
        """Old in-memory records that lack is_draft field default to False."""
        _plan_states[901] = {"meal_plan": {}, "shopping_list": []}
        assert get_plan_state(901)["is_draft"] is False

    def test_set_is_draft_true(self):
        """Updating is_draft=True persists the flag."""
        update_plan_state(owner_id=902, is_draft=True)
        assert get_plan_state(902)["is_draft"] is True

    def test_confirm_clears_is_draft(self):
        """Setting is_draft=False after True simulates the confirm step."""
        update_plan_state(owner_id=903, is_draft=True)
        update_plan_state(owner_id=903, is_draft=False)
        assert get_plan_state(903)["is_draft"] is False

    def test_is_draft_unset_does_not_change_existing_value(self):
        """Calling update without is_draft should leave the existing flag untouched."""
        update_plan_state(owner_id=904, is_draft=True)
        update_plan_state(owner_id=904, meal_plan={"2026-03-01": "Pasta"})
        assert get_plan_state(904)["is_draft"] is True

    def test_draft_base_db_dates_stored_and_retrieved(self):
        """draft_base_db_dates snapshot is persisted and returned by get_plan_state."""
        base = {"2026-02-20", "2026-02-21"}
        update_plan_state(owner_id=905, draft_base_db_dates=base)
        assert get_plan_state(905)["draft_base_db_dates"] == base

    def test_draft_base_db_dates_defaults_to_empty_set(self):
        """State without draft_base_db_dates returns an empty set."""
        _plan_states[906] = {"meal_plan": {}, "shopping_list": []}
        assert get_plan_state(906)["draft_base_db_dates"] == set()

    def test_draft_base_db_dates_not_modified_when_not_passed(self):
        """Subsequent updates that omit draft_base_db_dates leave the snapshot intact."""
        update_plan_state(owner_id=907, draft_base_db_dates={"2026-02-20"})
        update_plan_state(owner_id=907, meal_plan={"2026-02-21": "Pasta"})
        assert get_plan_state(907)["draft_base_db_dates"] == {"2026-02-20"}

    def test_full_draft_lifecycle(self):
        """Simulate recommend → modify → confirm lifecycle through state updates."""
        oid = 908

        # 1. Recommend: create draft with base snapshot
        update_plan_state(
            owner_id=oid,
            meal_plan={"2026-02-23": "Kung Pao Chicken", "2026-02-24": "Braised Pork"},
            is_draft=True,
            draft_base_db_dates={"2026-02-20"},
            merge=False,
        )
        state = get_plan_state(oid)
        assert state["is_draft"] is True
        assert len(state["meal_plan"]) == 2
        assert state["draft_base_db_dates"] == {"2026-02-20"}

        # 2. Modify: add a meal to the draft
        update_plan_state(oid, meal_plan={"2026-02-25": "Mapo Tofu"}, is_draft=True, merge=True)
        state = get_plan_state(oid)
        assert len(state["meal_plan"]) == 3
        assert state["is_draft"] is True

        # 3. Confirm: finalise (is_draft → False)
        update_plan_state(oid, is_draft=False, merge=True)
        state = get_plan_state(oid)
        assert state["is_draft"] is False
        assert len(state["meal_plan"]) == 3  # meals intact


class TestGhostDateFilter:
    """
    Tests for the ghost-date filter logic executed inside execute() at confirm time.

    The filter prevents dates deleted by the user in the Web UI from being
    "resurrected" when the draft is confirmed.  The logic is:
        user_deleted = base_db_dates - current_db_dates
        draft = {d: v for d, v in draft.items() if d not in user_deleted}
    """

    @staticmethod
    def _apply_filter(draft_plan, draft_slots, base_db_dates, current_db_dates):
        """Reproduce the ghost-date filter from plan_ahead_pipeline.execute."""
        user_deleted = set(base_db_dates) - set(current_db_dates)
        if not user_deleted:
            return draft_plan, draft_slots
        filtered_plan = {d: v for d, v in draft_plan.items() if d not in user_deleted}
        filtered_slots = {d: v for d, v in draft_slots.items() if d not in user_deleted}
        return filtered_plan, filtered_slots

    def test_no_deleted_dates_returns_draft_unchanged(self):
        """If the user deleted nothing, the entire draft is preserved."""
        plan = {"2026-02-23": "Dish A", "2026-02-24": "Dish B"}
        slots = {"2026-02-23": {"dinner": ["Dish A"]}, "2026-02-24": {"dinner": ["Dish B"]}}
        fp, fs = self._apply_filter(plan, slots, {"2026-02-23"}, {"2026-02-23"})
        assert fp == plan
        assert fs == slots

    def test_user_deleted_date_is_removed_from_draft(self):
        """A date the user deleted between recommend and confirm is excluded."""
        plan = {"2026-02-23": "Dish A", "2026-02-24": "Dish B"}
        slots = {"2026-02-23": {"dinner": ["Dish A"]}, "2026-02-24": {"dinner": ["Dish B"]}}
        # Feb 23 was in DB at draft creation but user deleted it since
        fp, fs = self._apply_filter(plan, slots, {"2026-02-23"}, {})
        assert "2026-02-23" not in fp
        assert "2026-02-24" in fp
        assert "2026-02-23" not in fs

    def test_new_ai_recommended_dates_always_kept(self):
        """Dates the AI added (never in DB) are NOT treated as deleted."""
        plan = {"2026-02-23": "Existing", "2026-02-28": "AI New Dish"}
        slots = {
            "2026-02-23": {"dinner": ["Existing"]},
            "2026-02-28": {"dinner": ["AI New Dish"]},
        }
        # Feb 23 was in DB; Feb 28 was never in DB
        fp, _ = self._apply_filter(plan, slots, {"2026-02-23"}, {})
        assert "2026-02-23" not in fp   # deleted by user
        assert "2026-02-28" in fp       # new AI recommendation → keep

    def test_empty_base_dates_keeps_entire_draft(self):
        """If nothing was in DB when the draft was created, nothing gets filtered."""
        plan = {"2026-02-23": "Dish A", "2026-02-24": "Dish B"}
        slots = {"2026-02-23": {"dinner": ["Dish A"]}, "2026-02-24": {"dinner": ["Dish B"]}}
        fp, fs = self._apply_filter(plan, slots, set(), {})
        assert fp == plan
        assert fs == slots

    def test_multiple_deleted_dates_all_removed(self):
        """When the user deleted multiple dates, all of them are stripped."""
        plan = {
            "2026-02-20": "Old 1",
            "2026-02-21": "Old 2",
            "2026-02-23": "New",
        }
        slots = {
            "2026-02-20": {"dinner": ["Old 1"]},
            "2026-02-21": {"dinner": ["Old 2"]},
            "2026-02-23": {"dinner": ["New"]},
        }
        fp, _ = self._apply_filter(plan, slots, {"2026-02-20", "2026-02-21"}, {})
        assert "2026-02-20" not in fp
        assert "2026-02-21" not in fp
        assert "2026-02-23" in fp

    def test_partial_deletion_keeps_surviving_db_dates(self):
        """If the user deleted only some old dates, the rest survive."""
        plan = {"2026-02-20": "Keep", "2026-02-21": "Deleted", "2026-02-25": "AI"}
        slots = {
            "2026-02-20": {"dinner": ["Keep"]},
            "2026-02-21": {"dinner": ["Deleted"]},
            "2026-02-25": {"dinner": ["AI"]},
        }
        # Feb 20 and Feb 21 were in DB; user only deleted Feb 21
        fp, _ = self._apply_filter(plan, slots, {"2026-02-20", "2026-02-21"}, {"2026-02-20"})
        assert "2026-02-20" in fp    # still in DB → kept
        assert "2026-02-21" not in fp  # deleted
        assert "2026-02-25" in fp    # AI recommendation → kept


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
            meal_plan_slots={"2026-02-10": {"lunch": "Pasta"}},
            dish_ingredients={"Pasta": ["tomato", "pasta"]},
            merge=False,
        )

        # Step 2: User adds another meal
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-02-11": "Pizza"},
            meal_plan_slots={"2026-02-11": {"dinner": "Pizza"}},
            dish_ingredients={"Pizza": ["dough", "cheese"]},
            merge=True,
        )

        state = get_plan_state(owner_id)
        assert len(state["meal_plan"]) == 2
        assert state["meal_plan"]["2026-02-10"] == "Pasta"
        assert state["meal_plan"]["2026-02-11"] == "Pizza"
        assert state["meal_plan_slots"]["2026-02-10"] == {"lunch": "Pasta"}
        assert state["meal_plan_slots"]["2026-02-11"] == {"dinner": "Pizza"}
        assert set(state["dish_ingredients"]["Pasta"]) == {"tomato", "pasta"}
        assert set(state["dish_ingredients"]["Pizza"]) == {"dough", "cheese"}

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
        assert state["schedule_id"] is None  # get_plan_state always returns schedule_id key (None when cleared)

        # Next write creates new schedule
        update_plan_state(
            owner_id=owner_id,
            schedule_id=7,  # New ID
            merge=True,
        )

        state = get_plan_state(owner_id)
        assert state["schedule_id"] == 7


class TestCookingContext:
    """Tests for cooking_context field — used by the RECIPE_QA / DISCUSSING_RECIPE flow."""

    def test_cooking_context_defaults_to_none(self):
        """Fresh state has no cooking context."""
        state = get_plan_state(owner_id=42)
        assert state["cooking_context"] is None

    def test_set_cooking_context(self):
        """Should store dish_name and steps in cooking_context."""
        owner_id = 1
        ctx = {
            "dish_name": "蒜泥白肉",
            "steps": [
                "将五花肉洗净，冷水入锅。",
                "调酱：2汤匙生抽 : 1汤匙香醋 : ½汤匙白糖 : 1茶匙香油。",
                "将肉切片，淋上酱汁即可。",
            ],
        }
        update_plan_state(owner_id=owner_id, cooking_context=ctx)

        state = get_plan_state(owner_id)
        assert state["cooking_context"]["dish_name"] == "蒜泥白肉"
        assert len(state["cooking_context"]["steps"]) == 3

    def test_overwrite_cooking_context(self):
        """Setting a new cooking_context replaces the previous one."""
        owner_id = 1
        update_plan_state(owner_id=owner_id, cooking_context={"dish_name": "宫保鸡丁", "steps": ["step1"]})
        update_plan_state(owner_id=owner_id, cooking_context={"dish_name": "鱼香肉丝", "steps": ["stepA", "stepB"]})

        state = get_plan_state(owner_id)
        assert state["cooking_context"]["dish_name"] == "鱼香肉丝"
        assert state["cooking_context"]["steps"] == ["stepA", "stepB"]

    def test_clear_cooking_context(self):
        """Setting cooking_context=None removes it from state."""
        owner_id = 1
        update_plan_state(owner_id=owner_id, cooking_context={"dish_name": "番茄炒蛋", "steps": ["s1"]})
        update_plan_state(owner_id=owner_id, cooking_context=None)

        state = get_plan_state(owner_id)
        assert state["cooking_context"] is None

    def test_cooking_context_unset_by_default(self):
        """update_plan_state without cooking_context kwarg leaves existing value unchanged."""
        owner_id = 1
        ctx = {"dish_name": "回锅肉", "steps": ["step1"]}
        update_plan_state(owner_id=owner_id, cooking_context=ctx)
        # Update something else — cooking_context should be untouched
        update_plan_state(owner_id=owner_id, last_pipeline_action="ask")

        state = get_plan_state(owner_id)
        assert state["cooking_context"]["dish_name"] == "回锅肉"

    def test_cooking_context_survives_plan_update(self):
        """A plan update (meal_plan_slots) should not wipe the cooking_context."""
        owner_id = 1
        update_plan_state(
            owner_id=owner_id,
            cooking_context={"dish_name": "蒜泥白肉", "steps": ["s1", "s2"]},
        )
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-03-10": "蒜泥白肉"},
            merge=True,
        )
        state = get_plan_state(owner_id)
        assert state["cooking_context"]["dish_name"] == "蒜泥白肉"
        assert state["meal_plan"]["2026-03-10"] == "蒜泥白肉"


# ---------------------------------------------------------------------------
# pending_modify_action and pending_overwrite fields
# ---------------------------------------------------------------------------

class TestPendingModifyAction:
    """Tests for the pending_modify_action deferred-confirmation field."""

    def setup_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def test_default_is_none(self):
        state = get_plan_state(owner_id=1)
        assert state["pending_modify_action"] is None

    def test_set_pending_modify_action(self):
        action = {"dish": "宫保鸡丁", "step_idx": 2, "ingredient": "酱油", "delta": "+10ml"}
        update_plan_state(owner_id=1, pending_modify_action=action)
        assert get_plan_state(owner_id=1)["pending_modify_action"] == action

    def test_clear_pending_modify_action_with_none(self):
        update_plan_state(owner_id=1, pending_modify_action={"dish": "x"})
        update_plan_state(owner_id=1, pending_modify_action=None)
        assert get_plan_state(owner_id=1)["pending_modify_action"] is None

    def test_pending_modify_action_not_affected_by_other_updates(self):
        """Updating unrelated fields must not clear pending_modify_action."""
        action = {"dish": "番茄炒蛋", "delta": "-5g"}
        update_plan_state(owner_id=1, pending_modify_action=action)
        update_plan_state(owner_id=1, last_pipeline_action="ask")
        assert get_plan_state(owner_id=1)["pending_modify_action"] == action

    def test_pending_modify_action_cleared_on_clear_plan_state(self):
        update_plan_state(owner_id=1, pending_modify_action={"dish": "x"})
        clear_plan_state(owner_id=1)
        assert get_plan_state(owner_id=1)["pending_modify_action"] is None

    def test_overwrite_pending_modify_action(self):
        """A second set should replace the first."""
        update_plan_state(owner_id=1, pending_modify_action={"dish": "A"})
        update_plan_state(owner_id=1, pending_modify_action={"dish": "B"})
        assert get_plan_state(owner_id=1)["pending_modify_action"]["dish"] == "B"


class TestPendingOverwrite:
    """Tests for the pending_overwrite recipe-diff confirmation field."""

    def setup_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def test_default_is_none(self):
        state = get_plan_state(owner_id=2)
        assert state["pending_overwrite"] is None

    def test_set_pending_overwrite(self):
        payload = {"dish_name": "红烧肉", "new_steps": ["s1", "s2", "s3"]}
        update_plan_state(owner_id=2, pending_overwrite=payload)
        assert get_plan_state(owner_id=2)["pending_overwrite"] == payload

    def test_clear_pending_overwrite_with_none(self):
        update_plan_state(owner_id=2, pending_overwrite={"dish_name": "x"})
        update_plan_state(owner_id=2, pending_overwrite=None)
        assert get_plan_state(owner_id=2)["pending_overwrite"] is None

    def test_pending_overwrite_not_affected_by_other_updates(self):
        payload = {"dish_name": "鱼香肉丝", "new_steps": ["s1"]}
        update_plan_state(owner_id=2, pending_overwrite=payload)
        update_plan_state(owner_id=2, cooking_context={"dish_name": "蒜泥白肉", "steps": []})
        assert get_plan_state(owner_id=2)["pending_overwrite"] == payload

    def test_pending_overwrite_cleared_on_clear_plan_state(self):
        update_plan_state(owner_id=2, pending_overwrite={"dish_name": "y"})
        clear_plan_state(owner_id=2)
        assert get_plan_state(owner_id=2)["pending_overwrite"] is None

    def test_both_pending_fields_independent(self):
        """pending_modify_action and pending_overwrite are stored independently."""
        action = {"dish": "A", "delta": "+5g"}
        overwrite = {"dish_name": "B", "new_steps": ["s1"]}
        update_plan_state(owner_id=3, pending_modify_action=action, pending_overwrite=overwrite)
        state = get_plan_state(owner_id=3)
        assert state["pending_modify_action"] == action
        assert state["pending_overwrite"] == overwrite

    def test_clear_one_does_not_affect_other(self):
        action = {"dish": "A"}
        overwrite = {"dish_name": "B"}
        update_plan_state(owner_id=3, pending_modify_action=action, pending_overwrite=overwrite)
        update_plan_state(owner_id=3, pending_modify_action=None)
        state = get_plan_state(owner_id=3)
        assert state["pending_modify_action"] is None
        assert state["pending_overwrite"] == overwrite


# ---------------------------------------------------------------------------
# TestPendingAskDates — pending_ask_dates state field
# ---------------------------------------------------------------------------

class TestPendingAskDates:
    """
    Tests for the pending_ask_dates state field, which stores the dates captured by
    the Fresh-plan guard when it forces action=ask so the next turn can resolve the
    user's meal-type reply without losing date context.
    """

    def test_default_is_empty_list(self):
        """pending_ask_dates defaults to [] for a new user."""
        assert get_plan_state(owner_id=1)["pending_ask_dates"] == []

    def test_set_pending_ask_dates(self):
        """Setting pending_ask_dates stores the list correctly."""
        dates = ["2026-03-27", "2026-03-28"]
        update_plan_state(owner_id=1, pending_ask_dates=dates)
        assert get_plan_state(owner_id=1)["pending_ask_dates"] == dates

    def test_clear_pending_ask_dates(self):
        """Setting pending_ask_dates=None removes the field (reads as [])."""
        update_plan_state(owner_id=1, pending_ask_dates=["2026-03-27"])
        update_plan_state(owner_id=1, pending_ask_dates=None)
        assert get_plan_state(owner_id=1)["pending_ask_dates"] == []

    def test_overwrite_pending_ask_dates(self):
        """A second update replaces the previous value entirely."""
        update_plan_state(owner_id=1, pending_ask_dates=["2026-03-27"])
        update_plan_state(owner_id=1, pending_ask_dates=["2026-04-01", "2026-04-02"])
        assert get_plan_state(owner_id=1)["pending_ask_dates"] == ["2026-04-01", "2026-04-02"]

    def test_independent_of_other_fields(self):
        """Updating pending_ask_dates must not disturb other state fields."""
        update_plan_state(owner_id=1, last_pipeline_action="ask", meal_plan={"2026-03-18": "烤鸡"})
        update_plan_state(owner_id=1, pending_ask_dates=["2026-03-27", "2026-03-28"])
        state = get_plan_state(owner_id=1)
        assert state["last_pipeline_action"] == "ask"
        assert state["meal_plan"] == {"2026-03-18": "烤鸡"}
        assert state["pending_ask_dates"] == ["2026-03-27", "2026-03-28"]

    def test_clear_plan_state_removes_pending_ask_dates(self):
        """clear_plan_state removes pending_ask_dates along with everything else."""
        update_plan_state(owner_id=1, pending_ask_dates=["2026-03-27"])
        clear_plan_state(owner_id=1)
        assert get_plan_state(owner_id=1)["pending_ask_dates"] == []
