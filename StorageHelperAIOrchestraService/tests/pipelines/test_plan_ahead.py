"""
Tests for PLAN_AHEAD pipeline — new schema-based architecture.

Covers:
- compute_shopping_list: deterministic ingredient aggregation
- parse_structured_response: structured LLM JSON → internal state
- ingredient format compatibility: string vs {"name": ...} dict
- apply_plan_modification: split-safety for dish names containing " and "
- inventory sorting: expiring items surface before condiments
- partial meal_time removal: "remove tonight's dinner" keeps breakfast intact
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.agents.scheduling_agent import compute_shopping_list, PlanAheadAgent
from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline


def _names(items) -> list:
    """Extract ingredient names from a list of dicts or strings."""
    return [i["name"] if isinstance(i, dict) else i for i in items]


def _name_set(items) -> set:
    return set(_names(items))


class TestComputeShoppingList:
    """Tests for the deterministic compute_shopping_list function.

    compute_shopping_list now returns List[Dict] with keys name/category/quantity.
    """

    def test_basic_aggregation(self):
        """Ingredients from multiple dishes are merged into a sorted unique list."""
        dish_ingredients = {
            "Pasta": ["pasta", "tomato", "garlic"],
            "Pizza": ["flour", "tomato", "cheese"],
        }
        result = compute_shopping_list(dish_ingredients)
        assert _names(result) == sorted({"pasta", "tomato", "garlic", "flour", "cheese"})

    def test_deduplication(self):
        """Duplicate ingredients across dishes appear only once."""
        dish_ingredients = {
            "DishA": ["onion", "garlic"],
            "DishB": ["garlic", "ginger"],
        }
        result = compute_shopping_list(dish_ingredients)
        assert _names(result).count("garlic") == 1
        assert _name_set(result) == {"onion", "garlic", "ginger"}

    def test_empty_input(self):
        """Empty dish_ingredients returns empty list."""
        assert compute_shopping_list({}) == []

    def test_dish_with_no_ingredients(self):
        """Dishes with empty ingredient lists are handled gracefully."""
        dish_ingredients = {
            "EmptyDish": [],
            "Salad": ["lettuce", "tomato"],
        }
        result = compute_shopping_list(dish_ingredients)
        assert _names(result) == ["lettuce", "tomato"]

    def test_output_is_sorted(self):
        """Output is always alphabetically sorted."""
        dish_ingredients = {
            "Dish": ["zucchini", "apple", "mango"],
        }
        result = compute_shopping_list(dish_ingredients)
        assert _names(result) == ["apple", "mango", "zucchini"]

    def test_single_dish(self):
        """Single dish returns its own ingredient list sorted."""
        dish_ingredients = {"回锅肉": ["猪肉", "豆瓣酱", "大蒜", "青椒"]}
        result = compute_shopping_list(dish_ingredients)
        assert _name_set(result) == {"猪肉", "豆瓣酱", "大蒜", "青椒"}
        assert len(result) == 4


class TestParseStructuredResponse:
    """Tests for PlanAheadAgent.parse_structured_response."""

    def _agent(self):
        return PlanAheadAgent(gemini_api_url="http://fake")

    def _make_response(self, action="add", meal_entries=None, user_message="OK",
                       target_date=None, meal_time=None):
        payload = {
            "action": action,
            "user_message": user_message,
            "meal_entries": meal_entries or [],
        }
        if target_date:
            payload["target_date"] = target_date
        if meal_time:
            payload["meal_time"] = meal_time
        return json.dumps(payload)

    def test_basic_parse(self):
        """A valid structured response is parsed into internal state."""
        agent = self._agent()
        raw = self._make_response(
            action="add",
            target_date="2026-02-27",
            meal_time="lunch",
            meal_entries=[
                {
                    "date": "2026-02-27",
                    "meal_time": "lunch",
                    "dishes": [
                        {"name": "回锅肉", "ingredients": [{"name": "猪肉"}, {"name": "豆瓣酱"}]}
                    ],
                }
            ],
        )
        result = agent.parse_structured_response(raw)
        assert result is not None
        assert result["action"] == "add"
        assert "2026-02-27" in result["meal_plan"]
        assert "回锅肉" in result["meal_plan_slots"]["2026-02-27"]["lunch"]
        assert "回锅肉" in result["dish_ingredients"]
        assert _name_set(result["dish_ingredients"]["回锅肉"]) == {"猪肉", "豆瓣酱"}

    def test_shopping_list_is_computed_programmatically(self):
        """shopping_list is computed from dish_ingredients, not taken from LLM output."""
        agent = self._agent()
        raw = self._make_response(
            action="add",
            meal_entries=[
                {
                    "date": "2026-02-27",
                    "meal_time": "dinner",
                    "dishes": [
                        {"name": "Pasta", "ingredients": [{"name": "pasta"}, {"name": "tomato"}]}
                    ],
                }
            ],
        )
        result = agent.parse_structured_response(raw)
        assert result is not None
        assert _name_set(result["shopping_list"]) == {"pasta", "tomato"}

    def test_multiple_dates_and_dishes(self):
        """Multiple meal_entries across dates are all captured."""
        agent = self._agent()
        raw = self._make_response(
            action="add",
            meal_entries=[
                {
                    "date": "2026-02-20",
                    "meal_time": "dinner",
                    "dishes": [{"name": "DishA", "ingredients": [{"name": "ing1"}]}],
                },
                {
                    "date": "2026-02-21",
                    "meal_time": "lunch",
                    "dishes": [{"name": "DishB", "ingredients": [{"name": "ing2"}]}],
                },
            ],
        )
        result = agent.parse_structured_response(raw)
        assert result is not None
        assert "2026-02-20" in result["meal_plan"]
        assert "2026-02-21" in result["meal_plan"]
        assert _name_set(result["shopping_list"]) == {"ing1", "ing2"}

    def test_invalid_json_returns_none(self):
        """Non-JSON response returns None gracefully."""
        agent = self._agent()
        result = agent.parse_structured_response("not valid json {{{")
        assert result is None

    def test_missing_meal_entries_returns_none(self):
        """Response without meal_entries returns None."""
        agent = self._agent()
        raw = json.dumps({"action": "add", "user_message": "OK"})
        result = agent.parse_structured_response(raw)
        assert result is None

    def test_view_action_preserved(self):
        """action=view is preserved in the parsed result."""
        agent = self._agent()
        raw = self._make_response(
            action="view",
            meal_entries=[
                {
                    "date": "2026-02-20",
                    "meal_time": "dinner",
                    "dishes": [{"name": "Stir Fry", "ingredients": [{"name": "beef"}]}],
                }
            ],
        )
        result = agent.parse_structured_response(raw)
        assert result is not None
        assert result["action"] == "view"


class TestIngredientFormatCompat:
    """
    parse_structured_response must accept BOTH ingredient formats:
      - Plain strings:  ["pasta", "tomato"]            (schema-correct)
      - Name objects:   [{"name": "pasta"}, ...]       (some LLM variants)
      - Mixed          (defensive guard)
    """

    def _agent(self):
        return PlanAheadAgent(gemini_api_url="http://fake")

    def _entry(self, ingredients):
        return {
            "date": "2026-03-01",
            "meal_time": "dinner",
            "dishes": [{"name": "TestDish", "ingredients": ingredients}],
        }

    def _parse(self, ingredients):
        raw = json.dumps({
            "action": "add",
            "user_message": "OK",
            "meal_entries": [self._entry(ingredients)],
        })
        return self._agent().parse_structured_response(raw)

    def test_plain_string_ingredients(self):
        """Standard string array format is parsed into dish_ingredients."""
        result = self._parse(["pasta", "tomato"])
        assert result is not None
        assert _name_set(result["dish_ingredients"]["TestDish"]) == {"pasta", "tomato"}
        assert _name_set(result["shopping_list"]) == {"pasta", "tomato"}

    def test_name_dict_ingredients(self):
        """Object format [{'name': 'x'}] is parsed identically to string format."""
        result = self._parse([{"name": "pasta"}, {"name": "tomato"}])
        assert result is not None
        assert _name_set(result["dish_ingredients"]["TestDish"]) == {"pasta", "tomato"}
        assert _name_set(result["shopping_list"]) == {"pasta", "tomato"}

    def test_mixed_format_ingredients(self):
        """A mix of strings and dicts in the same list is handled gracefully."""
        result = self._parse(["lettuce", {"name": "tomato"}, "olive oil"])
        assert result is not None
        assert _name_set(result["dish_ingredients"]["TestDish"]) == {"lettuce", "tomato", "olive oil"}

    def test_empty_name_dict_skipped(self):
        """Dict objects with empty or missing 'name' field are skipped."""
        result = self._parse([{"name": ""}, {"name": "garlic"}, {}])
        assert result is not None
        assert _names(result["dish_ingredients"]["TestDish"]) == ["garlic"]

    def test_chinese_ingredients_both_formats(self):
        """Chinese ingredient names work in both formats."""
        result_str = self._parse(["猪肉", "豆瓣酱"])
        result_dict = self._parse([{"name": "猪肉"}, {"name": "豆瓣酱"}])
        assert _name_set(result_str["dish_ingredients"]["TestDish"]) == {"猪肉", "豆瓣酱"}
        assert _name_set(result_dict["dish_ingredients"]["TestDish"]) == {"猪肉", "豆瓣酱"}


class TestApplyPlanModification:
    """
    Tests for PlanAheadAgent.apply_plan_modification focusing on split-safety.

    Key regression: dish names containing ' and ' (e.g. "Fish and Chips",
    "Mac and Cheese") must NOT be split into multiple dishes.
    """

    def _agent(self):
        return PlanAheadAgent(gemini_api_url="http://fake")

    def test_dish_name_with_and_not_split_in_slots(self):
        """A slot value that is already a list with 'and' in the name stays intact."""
        agent = self._agent()
        slots = {"2026-03-01": {"dinner": ["Fish and Chips"]}}
        intent = {
            "operation": "add",
            "date": "2026-03-02",
            "meal": "Salad",
            "meal_time": "dinner",
            "append": False,
        }
        _, result_slots = agent.apply_plan_modification({}, [], intent, meal_plan_slots=slots)
        assert result_slots["2026-03-01"]["dinner"] == ["Fish and Chips"]

    def test_legacy_string_slot_with_and_treated_as_single_dish(self):
        """Legacy string slot value 'Mac and Cheese' is kept as one dish, not split."""
        agent = self._agent()
        slots = {"2026-03-01": {"dinner": "Mac and Cheese"}}
        intent = {
            "operation": "add",
            "date": "2026-03-02",
            "meal": "Soup",
            "meal_time": "dinner",
            "append": False,
        }
        _, result_slots = agent.apply_plan_modification({}, [], intent, meal_plan_slots=slots)
        assert result_slots["2026-03-01"]["dinner"] == ["Mac and Cheese"]

    def test_comma_separated_meal_plan_string_splits_by_comma(self):
        """Legacy comma-joined meal_plan string is split by comma (not by ' and ')."""
        agent = self._agent()
        meal_plan = {"2026-03-01": "Pasta, Salad"}
        intent = {
            "operation": "add",
            "date": "2026-03-02",
            "meal": "Steak",
            "meal_time": "dinner",
            "append": False,
        }
        _, result_slots = agent.apply_plan_modification(meal_plan, [], intent, meal_plan_slots=None)
        # 2026-03-01 should be split by comma into two separate dishes
        assert set(result_slots["2026-03-01"]["dinner"]) == {"Pasta", "Salad"}

    def test_intent_meal_with_and_not_split(self):
        """The 'meal' field in an add/modify intent keeps 'and' in the name intact."""
        agent = self._agent()
        intent = {
            "operation": "add",
            "date": "2026-03-01",
            "meal": "Fish and Chips",
            "meal_time": "dinner",
            "append": False,
        }
        _, result_slots = agent.apply_plan_modification({}, [], intent, meal_plan_slots=None)
        assert result_slots["2026-03-01"]["dinner"] == ["Fish and Chips"]

    def test_modify_replaces_existing_dish(self):
        """A 'modify' operation replaces the dish list for the slot."""
        agent = self._agent()
        slots = {"2026-03-01": {"dinner": ["Old Dish"]}}
        intent = {
            "operation": "modify",
            "date": "2026-03-01",
            "meal": "New Dish",
            "meal_time": "dinner",
            "append": False,
        }
        _, result_slots = agent.apply_plan_modification({}, [], intent, meal_plan_slots=slots)
        assert result_slots["2026-03-01"]["dinner"] == ["New Dish"]

    def test_remove_specific_dish_from_slot(self):
        """A 'remove' operation with a meal name removes only that dish."""
        agent = self._agent()
        slots = {"2026-03-01": {"dinner": ["Dish A", "Dish B"]}}
        intent = {
            "operation": "remove",
            "date": "2026-03-01",
            "meal": "Dish A",
            "meal_time": "dinner",
        }
        _, result_slots = agent.apply_plan_modification({}, [], intent, meal_plan_slots=slots)
        assert result_slots["2026-03-01"]["dinner"] == ["Dish B"]

    def test_remove_entire_date(self):
        """A 'remove' operation without meal name removes the entire date."""
        agent = self._agent()
        slots = {"2026-03-01": {"dinner": ["Dish A"]}, "2026-03-02": {"dinner": ["Dish B"]}}
        intent = {
            "operation": "remove",
            "date": "2026-03-01",
            "meal": None,
            "meal_time": "dinner",
        }
        _, result_slots = agent.apply_plan_modification({}, [], intent, meal_plan_slots=slots)
        assert "2026-03-01" not in result_slots
        assert "2026-03-02" in result_slots


class TestInventorySorting:
    """
    _build_context must surface the most urgent inventory items first so the LLM
    sees expiring proteins/produce before non-perishable condiments.
    """

    def _pipeline(self):
        return PlanAheadPipeline(gemini_api_url="http://fake")

    def _empty_state(self):
        return {"meal_plan_slots": {}, "dish_ingredients": {}, "meal_plan": {}}

    def test_expiring_items_appear_before_non_expiring(self):
        """Items with a short shelf life come first in the context string."""
        pipeline = self._pipeline()
        inventory = [
            {"product_name": "Soy Sauce", "quantity": "1", "estimated_shelf_life_days": 0},
            {"product_name": "Beef", "quantity": "500g", "estimated_shelf_life_days": 2},
            {"product_name": "Salt", "quantity": "1", "estimated_shelf_life_days": 0},
            {"product_name": "Spinach", "quantity": "100g", "estimated_shelf_life_days": 1},
        ]
        ctx = pipeline._build_context(self._empty_state(), None, inventory_items=inventory)
        beef_pos = ctx.find("Beef")
        spinach_pos = ctx.find("Spinach")
        soy_pos = ctx.find("Soy Sauce")
        # Perishables (Spinach 1d, Beef 2d) must appear before non-perishables (Soy Sauce 0d)
        assert spinach_pos < soy_pos
        assert beef_pos < soy_pos

    def test_most_urgent_item_is_first(self):
        """Item with the shortest shelf life appears first in the inventory section."""
        pipeline = self._pipeline()
        inventory = [
            {"product_name": "Chicken", "quantity": "300g", "estimated_shelf_life_days": 3},
            {"product_name": "Milk", "quantity": "1L", "estimated_shelf_life_days": 1},
            {"product_name": "Rice", "quantity": "2kg", "estimated_shelf_life_days": 0},
        ]
        ctx = pipeline._build_context(self._empty_state(), None, inventory_items=inventory)
        milk_pos = ctx.find("Milk")
        chicken_pos = ctx.find("Chicken")
        # Milk (1d) should come before Chicken (3d)
        assert milk_pos < chicken_pos

    def test_use_soon_label_on_items_expiring_within_3_days(self):
        """Items with shelf life ≤ 3 days are tagged [USE SOON]."""
        pipeline = self._pipeline()
        inventory = [
            {"product_name": "Yogurt", "quantity": "1", "estimated_shelf_life_days": 2},
            {"product_name": "Onion", "quantity": "3", "estimated_shelf_life_days": 14},
        ]
        ctx = pipeline._build_context(self._empty_state(), None, inventory_items=inventory)
        assert "Yogurt" in ctx and "[USE SOON]" in ctx
        yogurt_section = ctx[ctx.find("Yogurt"):]
        use_soon_pos = yogurt_section.find("[USE SOON]")
        shelf_label_pos = yogurt_section.find("[~14d shelf life]")
        # [USE SOON] should appear near Yogurt
        assert use_soon_pos < 50
        # Onion should have shelf life label, not USE SOON
        assert "~14d shelf life" in ctx

    def test_draft_mode_label_in_context(self):
        """When is_draft=True, context contains DRAFT label."""
        pipeline = self._pipeline()
        ctx_draft = pipeline._build_context(self._empty_state(), None, is_draft=True)
        ctx_saved = pipeline._build_context(self._empty_state(), None, is_draft=False)
        assert "DRAFT MEAL PLAN" in ctx_draft
        assert "CURRENT MEAL PLAN" in ctx_saved


class TestBuildContextCookingLevelAndLanguage:
    """Tests for the cooking_level and language sections added to _build_context."""

    def _pipeline(self):
        return PlanAheadPipeline(gemini_api_url="http://fake")

    def _empty_state(self):
        return {"meal_plan_slots": {}, "dish_ingredients": {}, "meal_plan": {}}

    def test_beginner_section_present(self):
        ctx = self._pipeline()._build_context(self._empty_state(), None, cooking_level="beginner")
        assert "COOKING LEVEL" in ctx.upper() or "USER COOKING LEVEL" in ctx
        assert "beginner" in ctx.lower() or "Beginner" in ctx

    def test_intermediate_section_present(self):
        ctx = self._pipeline()._build_context(self._empty_state(), None, cooking_level="intermediate")
        assert "intermediate" in ctx.lower() or "Intermediate" in ctx or "Some Experience" in ctx

    def test_expert_section_present(self):
        ctx = self._pipeline()._build_context(self._empty_state(), None, cooking_level="expert")
        assert "expert" in ctx.lower() or "Experienced Cook" in ctx

    def test_unknown_level_falls_back_to_beginner(self):
        """An unrecognised cooking_level should be treated as beginner."""
        ctx = self._pipeline()._build_context(self._empty_state(), None, cooking_level="ninja")
        assert "Beginner" in ctx or "beginner" in ctx.lower()

    def test_language_zh_in_context(self):
        ctx = self._pipeline()._build_context(self._empty_state(), None, language="zh")
        assert "LANGUAGE REQUIREMENT" in ctx.upper() or "Simplified Chinese" in ctx

    def test_language_en_in_context(self):
        ctx = self._pipeline()._build_context(self._empty_state(), None, language="en")
        assert "English" in ctx

    def test_language_ja_in_context(self):
        ctx = self._pipeline()._build_context(self._empty_state(), None, language="ja")
        assert "Japanese" in ctx or "日本語" in ctx

    def test_language_critical_rule_present(self):
        """CRITICAL language rule must be present regardless of language chosen."""
        for lang in ("zh", "en", "ja", "ko"):
            ctx = self._pipeline()._build_context(self._empty_state(), None, language=lang)
            assert "CRITICAL" in ctx or "MUST" in ctx

    def test_scheduling_context_injected(self):
        """When scheduling_context is provided, it appears in the output."""
        ctx = self._pipeline()._build_context(
            self._empty_state(), None,
            scheduling_context="占用: 周三午餐 — 公司聚餐",
        )
        assert "公司聚餐" in ctx
        assert "LIVE CALENDAR" in ctx.upper() or "SchedulingAgent" in ctx

    def test_no_scheduling_context_adds_anti_dup_rule(self):
        """Without scheduling_context, the single-source anti-duplication rule is present."""
        ctx = self._pipeline()._build_context(self._empty_state(), None)
        assert "ANTI-DUPLICATION" in ctx.upper() or "Do NOT add" in ctx


class TestPlanAheadPipelineStaleStateCleanup:
    """Tests for the stale in-memory state cleanup when DB has no meal plan."""

    def setup_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def test_stale_state_cleared_when_db_empty(self):
        """If DB returns no plan but in-memory has a stale schedule_id, it should be cleared."""
        from app.modules.plan_ahead_state import update_plan_state, get_plan_state

        owner_id = 42
        # Seed in-memory state with stale data
        update_plan_state(
            owner_id=owner_id,
            meal_plan={"2026-03-10": "宫保鸡丁"},
            schedule_id=999,
            meal_plan_slots={"2026-03-10": {"dinner": ["宫保鸡丁"]}},
            merge=False,
        )
        assert get_plan_state(owner_id)["schedule_id"] == 999

        # Simulate the pipeline receiving an empty DB state (user deleted the plan on Web UI)
        from app.modules.plan_ahead_state import update_plan_state as _upd
        _upd(
            owner_id=owner_id,
            meal_plan={},
            shopping_list=[],
            schedule_id=None,
            meal_plan_slots={},
            dish_ingredients={},
            is_draft=False,
            last_pipeline_action=None,
            merge=False,
        )

        state = get_plan_state(owner_id)
        assert state["schedule_id"] is None
        assert state["meal_plan"] == {}
        assert state["meal_plan_slots"] == {}

    def test_fresh_state_not_affected_by_empty_db(self):
        """A user with no prior state should still have clean defaults."""
        from app.modules.plan_ahead_state import get_plan_state

        state = get_plan_state(owner_id=99)
        # schedule_id is absent (or None) from a brand-new state
        assert state.get("schedule_id") is None
        assert state["meal_plan"] == {}
        assert state["is_draft"] is False


# ---------------------------------------------------------------------------
# Regression: partial meal_time removal
# Bug: "去掉今天晚上的计划" was removing the whole day (including breakfast)
#      instead of only the dinner slot.
# ---------------------------------------------------------------------------

def _make_storage_client_mock() -> MagicMock:
    """Return a minimal storage_client mock suitable for execute() tests."""
    sc = MagicMock()
    sc.get_user_schedules = AsyncMock(return_value=[])
    sc.update_schedule = AsyncMock(return_value=True)
    sc.delete_schedule = AsyncMock()
    sc.get_user_subscription = AsyncMock(return_value={"is_active": True})
    sc.check_usage_limits = AsyncMock(return_value={"allowed": True})
    sc.increment_meal_plan_session = AsyncMock(return_value=None)
    sc.add_token_usage = AsyncMock(return_value=None)
    sc._extract_meal_plan_from_schedule = MagicMock(return_value=({}, [], {}, {}))
    sc._extract_existing_dish_data = MagicMock(return_value={})
    sc._convert_to_feature_format = MagicMock(return_value={})
    return sc


def _state_with_breakfast_and_dinner() -> dict:
    """Plan where 2026-03-14 has BOTH breakfast and dinner slots."""
    return {
        "meal_plan": {
            "2026-03-13": "农家小炒肉",
            "2026-03-14": "煮鸡蛋 and 粥 and 香煎鳕鱼 and 白米饭",
            "2026-03-15": "清炒豆苗",
        },
        "meal_plan_slots": {
            "2026-03-13": {"dinner": ["农家小炒肉"]},
            "2026-03-14": {
                "breakfast": ["煮鸡蛋", "粥"],   # must survive after remove-dinner
                "dinner": ["香煎鳕鱼", "白米饭"],  # must be removed
            },
            "2026-03-15": {"dinner": ["清炒豆苗"]},
        },
        "dish_ingredients": {
            "煮鸡蛋": [{"name": "鸡蛋", "category": "protein", "quantity": "2"}],
            "粥": [{"name": "大米", "category": "grain", "quantity": "50g"}],
            "香煎鳕鱼": [{"name": "鳕鱼", "category": "protein", "quantity": "200g"}],
            "白米饭": [{"name": "大米", "category": "grain", "quantity": "150g"}],
        },
        "shopping_list": [],
        "schedule_id": 41,
        "is_draft": False,
        "last_pipeline_action": None,
    }


def _state_dinner_only() -> dict:
    """Plan where 2026-03-14 has ONLY a dinner slot."""
    return {
        "meal_plan": {
            "2026-03-13": "农家小炒肉",
            "2026-03-14": "香煎鳕鱼 and 白米饭",
            "2026-03-15": "清炒豆苗",
        },
        "meal_plan_slots": {
            "2026-03-13": {"dinner": ["农家小炒肉"]},
            "2026-03-14": {"dinner": ["香煎鳕鱼", "白米饭"]},
            "2026-03-15": {"dinner": ["清炒豆苗"]},
        },
        "dish_ingredients": {
            "香煎鳕鱼": [{"name": "鳕鱼", "category": "protein", "quantity": "200g"}],
            "白米饭": [{"name": "大米", "category": "grain", "quantity": "150g"}],
        },
        "shopping_list": [],
        "schedule_id": 41,
        "is_draft": False,
        "last_pipeline_action": None,
    }


class TestParseMealTimeDefault:
    """parse_structured_response: meal_time must be None when absent (not 'dinner')."""

    def _agent(self):
        return PlanAheadAgent(gemini_api_url="http://fake")

    def test_meal_time_is_none_when_absent(self):
        """meal_time defaults to None, NOT 'dinner', when the LLM omits it."""
        raw = json.dumps({
            "action": "remove",
            "user_message": "Done",
            "meal_entries": [],
            "target_date": "2026-03-14",
        })
        result = self._agent().parse_structured_response(raw)
        assert result is not None
        assert result["meal_time"] is None, (
            "meal_time should be None when absent — defaulting to 'dinner' caused "
            "partial-day removes to behave like whole-day removes."
        )

    def test_meal_time_dinner_when_explicitly_set(self):
        """meal_time='dinner' is returned when the LLM explicitly sets it."""
        raw = json.dumps({
            "action": "remove",
            "user_message": "Done",
            "meal_entries": [],
            "target_date": "2026-03-14",
            "meal_time": "dinner",
        })
        result = self._agent().parse_structured_response(raw)
        assert result is not None
        assert result["meal_time"] == "dinner"

    def test_meal_time_breakfast_when_explicitly_set(self):
        raw = json.dumps({
            "action": "remove",
            "user_message": "Done",
            "meal_entries": [],
            "target_date": "2026-03-14",
            "meal_time": "breakfast",
        })
        result = self._agent().parse_structured_response(raw)
        assert result is not None
        assert result["meal_time"] == "breakfast"


class TestRemovePartialMealTime:
    """Regression: 'remove tonight's dinner' must NOT remove today's breakfast.

    Before the fix, the code removed the ENTIRE target_date from the plan whenever
    meal_entries was empty, regardless of meal_time.  The fix uses meal_time to
    decide whether to do a partial slot removal or a full-day removal.
    """

    def setup_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def _pipeline(self):
        return PlanAheadPipeline(gemini_api_url="http://fake")

    def _llm_remove_dinner_parsed(self) -> dict:
        """Parsed LLM result: remove dinner for 2026-03-14, meal_entries is empty."""
        raw = json.dumps({
            "action": "remove",
            "target_date": "2026-03-14",
            "meal_time": "dinner",
            "user_message": "好的，已移除今天晚餐。",
            "meal_entries": [],
        })
        return PlanAheadAgent(gemini_api_url="http://fake").parse_structured_response(raw)

    def _run_execute(self, old_state: dict, llm_parsed: dict) -> dict:
        pipeline = self._pipeline()
        sc = _make_storage_client_mock()

        async def _run():
            with patch.object(
                pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                AsyncMock(return_value=old_state),
            ), patch.object(
                pipeline, "_call_llm",
                AsyncMock(return_value=llm_parsed),
            ), patch.object(
                pipeline.plan_ahead_agent, "persist_meal_plan",
                AsyncMock(return_value=old_state.get("schedule_id")),
            ):
                return await pipeline.execute(
                    owner_id=1,
                    user_input="去掉今天晚上的计划",
                    history=[],
                    user_timezone="America/Los_Angeles",
                    storage_client=sc,
                )

        return asyncio.run(_run())

    def _ad(self, result: dict) -> dict:
        """Shortcut to result['action_data']."""
        return result["action_data"]

    def test_breakfast_survives_when_removing_dinner(self):
        """The regression case: day has breakfast + dinner; removing dinner keeps breakfast."""
        result = self._run_execute(
            old_state=_state_with_breakfast_and_dinner(),
            llm_parsed=self._llm_remove_dinner_parsed(),
        )
        ad = self._ad(result)
        slots = ad["meal_plan_slots"]
        mp = ad["meal_plan"]

        assert "2026-03-14" in mp, (
            "2026-03-14 should remain in plan — breakfast is still there"
        )
        assert "breakfast" in slots.get("2026-03-14", {}), (
            "Breakfast slot for 2026-03-14 should survive"
        )
        assert set(slots["2026-03-14"]["breakfast"]) == {"煮鸡蛋", "粥"}
        assert "dinner" not in slots.get("2026-03-14", {}), (
            "Dinner slot for 2026-03-14 should have been removed"
        )
        assert "煮鸡蛋" in mp["2026-03-14"] or "粥" in mp["2026-03-14"]
        assert "香煎鳕鱼" not in mp["2026-03-14"]

    def test_dinner_dish_ingredients_removed_from_shopping_list(self):
        """After removing dinner, dinner-only ingredients are dropped from shopping list."""
        result = self._run_execute(
            old_state=_state_with_breakfast_and_dinner(),
            llm_parsed=self._llm_remove_dinner_parsed(),
        )
        di = self._ad(result)["dish_ingredients"]
        assert "香煎鳕鱼" not in di, "Dinner dish ingredients should be cleaned up"
        assert "白米饭" not in di, "Shared staple dish from dinner should be cleaned up"
        assert "煮鸡蛋" in di, "Breakfast dish ingredients must remain"
        assert "粥" in di, "Breakfast dish ingredients must remain"

    def test_other_dates_unaffected_by_partial_removal(self):
        """Removing dinner on 2026-03-14 must not touch 2026-03-13 or 2026-03-15."""
        result = self._run_execute(
            old_state=_state_with_breakfast_and_dinner(),
            llm_parsed=self._llm_remove_dinner_parsed(),
        )
        mp = self._ad(result)["meal_plan"]
        assert "2026-03-13" in mp
        assert "2026-03-15" in mp

    def test_whole_day_removed_when_dinner_is_only_slot(self):
        """If dinner is the ONLY slot on that day, removing it removes the entire date."""
        result = self._run_execute(
            old_state=_state_dinner_only(),
            llm_parsed=self._llm_remove_dinner_parsed(),
        )
        mp = self._ad(result)["meal_plan"]
        assert "2026-03-14" not in mp, (
            "2026-03-14 should be fully removed when dinner was the only slot"
        )
        assert "2026-03-13" in mp
        assert "2026-03-15" in mp

    def test_whole_day_removed_when_no_meal_time_specified(self):
        """If meal_time is absent (None), the entire day should be removed."""
        agent = PlanAheadAgent(gemini_api_url="http://fake")
        llm_whole_day_parsed = agent.parse_structured_response(json.dumps({
            "action": "remove",
            "target_date": "2026-03-14",
            "user_message": "好的，已移除今天全部计划。",
            "meal_entries": [],
        }))
        result = self._run_execute(
            old_state=_state_with_breakfast_and_dinner(),
            llm_parsed=llm_whole_day_parsed,
        )
        mp = self._ad(result)["meal_plan"]
        assert "2026-03-14" not in mp, (
            "Entire date should be removed when no specific meal_time is given"
        )


class TestPastMealRecordingPipeline:
    """
    Regression tests for the past-meal recording bug.

    Before the fix: saying "昨天吃的是麻婆豆腐辣椒炒肉和米饭" caused the LLM
    to respond with "好的，我知道了" and an empty meal_entries — the meal was
    never added to the schedule.

    After the fix: past-date meal reports must be parsed and persisted just
    like any other action='add' operation.
    """

    def _agent(self):
        return PlanAheadAgent(gemini_api_url="http://fake")

    def _make_past_meal_response(self, date_str: str, meal_time: str, dishes: list):
        """Build a structured LLM response for adding past meals."""
        return json.dumps({
            "action": "add",
            "target_date": date_str,
            "meal_time": meal_time,
            "user_message": f"好的，已为您记录 {date_str} {meal_time} 的用餐记录。",
            "meal_entries": [
                {
                    "date": date_str,
                    "meal_time": meal_time,
                    "dishes": [
                        {"name": d, "ingredients": [{"name": "食材", "category": "other"}]}
                        for d in dishes
                    ],
                }
            ],
        })

    def test_past_date_meal_parsed_correctly(self):
        """A past-date meal (e.g. yesterday) is parsed the same as a future meal."""
        agent = self._agent()
        # Use a clearly past date
        raw = self._make_past_meal_response("2026-04-04", "dinner", ["麻婆豆腐", "辣椒炒肉", "米饭"])
        result = agent.parse_structured_response(raw)
        assert result is not None
        assert result["action"] == "add"
        assert "2026-04-04" in result["meal_plan"]
        assert "2026-04-04" in result["meal_plan_slots"]
        dinner_dishes = result["meal_plan_slots"]["2026-04-04"].get("dinner", [])
        assert "麻婆豆腐" in dinner_dishes
        assert "辣椒炒肉" in dinner_dishes
        assert "米饭" in dinner_dishes

    def test_past_date_multiple_dishes_all_captured(self):
        """All dishes in a past-meal report end up in dish_ingredients."""
        agent = self._agent()
        raw = self._make_past_meal_response("2026-04-03", "lunch", ["宫保鸡丁", "白米饭"])
        result = agent.parse_structured_response(raw)
        assert result is not None
        assert "宫保鸡丁" in result["dish_ingredients"]
        assert "白米饭" in result["dish_ingredients"]

    def test_past_meal_shopping_list_computed(self):
        """shopping_list is computed from ingredients even for past-date meals."""
        agent = self._agent()
        raw = json.dumps({
            "action": "add",
            "target_date": "2026-04-04",
            "meal_time": "dinner",
            "user_message": "已记录。",
            "meal_entries": [
                {
                    "date": "2026-04-04",
                    "meal_time": "dinner",
                    "dishes": [
                        {
                            "name": "麻婆豆腐",
                            "ingredients": [
                                {"name": "豆腐", "category": "protein"},
                                {"name": "豆瓣酱", "category": "spice"},
                            ],
                        }
                    ],
                }
            ],
        })
        result = agent.parse_structured_response(raw)
        assert result is not None
        names = {i["name"] if isinstance(i, dict) else i for i in result["shopping_list"]}
        assert "豆腐" in names
        assert "豆瓣酱" in names

    def test_past_breakfast_meal_type_preserved(self):
        """Past breakfast reports use meal_time='breakfast', not defaulting to dinner."""
        agent = self._agent()
        raw = self._make_past_meal_response("2026-04-04", "breakfast", ["煮鸡蛋", "粥"])
        result = agent.parse_structured_response(raw)
        assert result is not None
        assert "breakfast" in result["meal_plan_slots"].get("2026-04-04", {})
        assert "煮鸡蛋" in result["meal_plan_slots"]["2026-04-04"]["breakfast"]

    def test_acknowledge_only_response_has_empty_meal_plan_slots(self):
        """
        Documents the bug condition: if the LLM returns an acknowledgement with
        empty meal_entries, parse_structured_response produces a result with empty
        meal_plan_slots — so nothing is actually saved to the schedule.

        The PAST MEAL RECORDING RULE in the prompt prevents this by instructing
        the LLM to always populate meal_entries for past-date reports.
        """
        agent = self._agent()
        raw = json.dumps({
            "action": "add",
            "target_date": "2026-04-04",
            "meal_time": "dinner",
            "user_message": "好的，我知道了。",
            "meal_entries": [],
        })
        result = agent.parse_structured_response(raw)
        # parse_structured_response succeeds but produces empty meal data
        assert result is not None
        assert result["meal_plan_slots"] == {}, (
            "Empty meal_entries → empty meal_plan_slots → nothing persisted to schedule (the bug)"
        )
        assert result["meal_plan"] == {}


class TestUpdateIngredientsPreservesSlot:
    """
    Regression: "辣椒炒肉是牛肉" (ingredient clarification) must NOT wipe
    sibling dishes from the same slot.

    Root cause: for action=update_ingredients the LLM returns only the updated
    dish in meal_entries (e.g. ['辣椒炒肉']).  The persist call uses
    is_append=False, so a PUT with that single-dish slot OVERWRITES the full
    ['麻婆豆腐', '辣椒炒肉', '米饭'] dinner.

    Fix (Step 4e in plan_ahead_pipeline.py): restore the full existing dish
    list from DB state before persisting; merge updated dish_ingredients on top.
    """

    def setup_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def _pipeline(self):
        return PlanAheadPipeline(gemini_api_url="http://fake")

    def _state_three_dish_dinner(self) -> dict:
        """2026-04-04 dinner has 麻婆豆腐, 辣椒炒肉, 米饭."""
        return {
            "meal_plan": {"2026-04-04": "麻婆豆腐, 辣椒炒肉, 米饭"},
            "meal_plan_slots": {
                "2026-04-04": {"dinner": ["麻婆豆腐", "辣椒炒肉", "米饭"]},
            },
            "dish_ingredients": {
                "麻婆豆腐": [{"name": "豆腐", "category": "protein"}],
                "辣椒炒肉": [{"name": "猪肉", "category": "protein"}],
                "米饭": [{"name": "大米", "category": "grain"}],
            },
            "shopping_list": [],
            "schedule_id": 100,
            "is_draft": False,
            "last_pipeline_action": None,
        }

    def _llm_update_ingredients_parsed(self) -> dict:
        """LLM returns update_ingredients for 辣椒炒肉 only (beef replaces pork)."""
        raw = json.dumps({
            "action": "update_ingredients",
            "target_date": "2026-04-04",
            "meal_time": "dinner",
            "user_message": "好的，已将辣椒炒肉的猪肉更新为牛肉。",
            "meal_entries": [
                {
                    "date": "2026-04-04",
                    "meal_time": "dinner",
                    "dishes": [
                        {
                            "name": "辣椒炒肉",
                            "ingredients": [
                                {"name": "牛肉", "category": "protein"},
                                {"name": "辣椒", "category": "vegetable"},
                            ],
                        }
                    ],
                }
            ],
        })
        return PlanAheadAgent(gemini_api_url="http://fake").parse_structured_response(raw)

    def _run_execute(self, old_state: dict, llm_parsed: dict,
                     user_input: str = "辣椒炒肉是牛肉") -> dict:
        pipeline = self._pipeline()
        sc = _make_storage_client_mock()

        async def _run():
            with patch.object(
                pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                AsyncMock(return_value=old_state),
            ), patch.object(
                pipeline, "_call_llm",
                AsyncMock(return_value=llm_parsed),
            ), patch.object(
                pipeline.plan_ahead_agent, "persist_meal_plan",
                AsyncMock(return_value=old_state.get("schedule_id")),
            ):
                return await pipeline.execute(
                    owner_id=1,
                    user_input=user_input,
                    history=[],
                    user_timezone="Asia/Shanghai",
                    storage_client=sc,
                )

        return asyncio.run(_run())

    def _ad(self, result: dict) -> dict:
        return result["action_data"]

    def test_sibling_dishes_preserved_after_update_ingredients(self):
        """
        The regression: update_ingredients for 辣椒炒肉 must NOT wipe 麻婆豆腐 and 米饭.
        """
        result = self._run_execute(
            old_state=self._state_three_dish_dinner(),
            llm_parsed=self._llm_update_ingredients_parsed(),
        )
        slots = self._ad(result)["meal_plan_slots"]
        dinner = slots.get("2026-04-04", {}).get("dinner", [])
        assert "麻婆豆腐" in dinner, "麻婆豆腐 must be preserved after update_ingredients"
        assert "辣椒炒肉" in dinner, "辣椒炒肉 must remain in the slot"
        assert "米饭" in dinner, "米饭 must be preserved after update_ingredients"

    def test_updated_ingredient_reflected_in_dish_ingredients(self):
        """The ingredient update for 辣椒炒肉 must take effect (牛肉, not 猪肉)."""
        result = self._run_execute(
            old_state=self._state_three_dish_dinner(),
            llm_parsed=self._llm_update_ingredients_parsed(),
        )
        di = self._ad(result)["dish_ingredients"]
        assert "辣椒炒肉" in di
        ing_names = {
            i["name"] if isinstance(i, dict) else i
            for i in di["辣椒炒肉"]
        }
        assert "牛肉" in ing_names, "Updated ingredient 牛肉 must appear"
        assert "猪肉" not in ing_names, "Old ingredient 猪肉 must be replaced"

    def test_unrelated_dishes_ingredients_preserved(self):
        """Ingredients for 麻婆豆腐 and 米饭 must remain untouched."""
        result = self._run_execute(
            old_state=self._state_three_dish_dinner(),
            llm_parsed=self._llm_update_ingredients_parsed(),
        )
        di = self._ad(result)["dish_ingredients"]
        assert "麻婆豆腐" in di
        assert "米饭" in di
        mapo_names = {
            i["name"] if isinstance(i, dict) else i for i in di["麻婆豆腐"]
        }
        assert "豆腐" in mapo_names

    def test_meal_plan_string_contains_all_three_dishes(self):
        """The meal_plan summary string must still contain all 3 dishes."""
        result = self._run_execute(
            old_state=self._state_three_dish_dinner(),
            llm_parsed=self._llm_update_ingredients_parsed(),
        )
        mp = self._ad(result)["meal_plan"]
        plan_str = mp.get("2026-04-04", "")
        assert "麻婆豆腐" in plan_str
        assert "辣椒炒肉" in plan_str
        assert "米饭" in plan_str


class TestModifyDishSwapPreservesSiblings:
    """
    Regression: "将蒜蓉炒蒜苔换成蒜苔炒鸡蛋" must NOT wipe sibling dishes
    from the same slot (清炖牛骨汤, 杂粮饭).

    Root cause: the explicit-dish filter (Step 4c) runs for action=modify and
    strips any dish not matching the explicitly-named replacement dish.  The
    filter correctly strips truly extra LLM hallucinations, but sibling dishes
    that were already in the DB slot are legitimate — they should be preserved.

    Fix: for non-draft modify, add the existing DB dishes to the
    _pre_draft_dishes protection set inside the filter.
    """

    def setup_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def _pipeline(self):
        return PlanAheadPipeline(gemini_api_url="http://fake")

    def _state_three_dish_dinner(self) -> dict:
        """DB state: 2026-04-06 dinner = [清炖牛骨汤, 蒜蓉炒蒜苔, 杂粮饭]."""
        return {
            "meal_plan": {"2026-04-06": "清炖牛骨汤, 蒜蓉炒蒜苔, 杂粮饭"},
            "meal_plan_slots": {
                "2026-04-06": {"dinner": ["清炖牛骨汤", "蒜蓉炒蒜苔", "杂粮饭"]},
            },
            "dish_ingredients": {
                "清炖牛骨汤": [{"name": "牛骨", "category": "protein"}],
                "蒜蓉炒蒜苔": [{"name": "蒜苔", "category": "vegetable"}],
                "杂粮饭": [{"name": "杂粮", "category": "grain"}],
            },
            "shopping_list": [],
            "schedule_id": 102,
            "is_draft": False,
            "last_pipeline_action": None,
        }

    def _llm_modify_swap_parsed(self) -> dict:
        """
        LLM correctly returns the full updated slot after replacing 蒜蓉炒蒜苔 → 蒜苔炒鸡蛋.
        The siblings 清炖牛骨汤 and 杂粮饭 are carried over.
        """
        raw = json.dumps({
            "action": "modify",
            "target_date": "2026-04-06",
            "meal_time": "dinner",
            "user_message": "好的，已将蒜蓉炒蒜苔调整为蒜苔炒鸡蛋。",
            "meal_entries": [
                {
                    "date": "2026-04-06",
                    "meal_time": "dinner",
                    "dishes": [
                        {
                            "name": "清炖牛骨汤",
                            "ingredients": [{"name": "牛骨", "category": "protein"}],
                        },
                        {
                            "name": "蒜苔炒鸡蛋",
                            "ingredients": [
                                {"name": "蒜苔", "category": "vegetable"},
                                {"name": "鸡蛋", "category": "protein"},
                            ],
                        },
                        {
                            "name": "杂粮饭",
                            "ingredients": [{"name": "杂粮", "category": "grain"}],
                        },
                    ],
                }
            ],
        })
        return PlanAheadAgent(gemini_api_url="http://fake").parse_structured_response(raw)

    def _run_execute(self, old_state: dict, llm_parsed: dict) -> dict:
        pipeline = self._pipeline()
        sc = _make_storage_client_mock()

        async def _run():
            with patch.object(
                pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                AsyncMock(return_value=old_state),
            ), patch.object(
                pipeline, "_call_llm",
                AsyncMock(return_value=llm_parsed),
            ), patch.object(
                pipeline.plan_ahead_agent, "persist_meal_plan",
                AsyncMock(return_value=old_state.get("schedule_id")),
            ):
                return await pipeline.execute(
                    owner_id=1,
                    user_input="换成蒜苔炒鸡蛋",
                    history=[],
                    user_timezone="Asia/Shanghai",
                    storage_client=sc,
                )

        return asyncio.run(_run())

    def _ad(self, result: dict) -> dict:
        return result["action_data"]

    def test_sibling_dishes_preserved_after_swap(self):
        """
        The regression: explicit-dish filter must NOT strip 清炖牛骨汤 and 杂粮饭
        when the user replaces only 蒜蓉炒蒜苔.
        """
        result = self._run_execute(
            old_state=self._state_three_dish_dinner(),
            llm_parsed=self._llm_modify_swap_parsed(),
        )
        slots = self._ad(result)["meal_plan_slots"]
        dinner = slots.get("2026-04-06", {}).get("dinner", [])
        assert "清炖牛骨汤" in dinner, "清炖牛骨汤 must be preserved after swap"
        assert "蒜苔炒鸡蛋" in dinner, "replacement dish 蒜苔炒鸡蛋 must be present"
        assert "杂粮饭" in dinner, "杂粮饭 must be preserved after swap"
        assert "蒜蓉炒蒜苔" not in dinner, "replaced dish 蒜蓉炒蒜苔 must be removed"

    def test_new_dish_ingredients_saved(self):
        """Ingredients for the replacement dish 蒜苔炒鸡蛋 must be in dish_ingredients."""
        result = self._run_execute(
            old_state=self._state_three_dish_dinner(),
            llm_parsed=self._llm_modify_swap_parsed(),
        )
        di = self._ad(result)["dish_ingredients"]
        assert "蒜苔炒鸡蛋" in di
        ing_names = {i["name"] if isinstance(i, dict) else i for i in di["蒜苔炒鸡蛋"]}
        assert "鸡蛋" in ing_names

    def test_replaced_dish_removed_from_ingredients(self):
        """蒜蓉炒蒜苔 must no longer be in dish_ingredients after swap."""
        result = self._run_execute(
            old_state=self._state_three_dish_dinner(),
            llm_parsed=self._llm_modify_swap_parsed(),
        )
        di = self._ad(result)["dish_ingredients"]
        assert "蒜蓉炒蒜苔" not in di

    def test_meal_plan_string_has_three_dishes(self):
        """meal_plan summary string must contain all 3 dishes after swap."""
        result = self._run_execute(
            old_state=self._state_three_dish_dinner(),
            llm_parsed=self._llm_modify_swap_parsed(),
        )
        mp = self._ad(result)["meal_plan"]
        plan_str = mp.get("2026-04-06", "")
        assert "清炖牛骨汤" in plan_str
        assert "蒜苔炒鸡蛋" in plan_str
        assert "杂粮饭" in plan_str
        assert "蒜蓉炒蒜苔" not in plan_str


class TestMoveMealPlanPreservesSource:
    """
    Regression: "把今天晚饭移到明天" must:
      1. Write the dishes to the destination date.
      2. Delete the source date slot (today's dinner) from the schedule.

    Before the fix: the pipeline had no "move" concept — it only persisted the
    destination, leaving the source date untouched.

    Fix (Step 6b): the LLM now sets source_date/source_meal_time; the pipeline
    detects these fields and deletes the origin slot after persisting the destination.
    """

    def setup_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def _pipeline(self):
        return PlanAheadPipeline(gemini_api_url="http://fake")

    def _state_today_dinner(self) -> dict:
        """DB state: 2026-04-06 dinner = [清蒸鲈鱼, 蒜蓉油麦菜, 杂粮饭]."""
        return {
            "meal_plan": {"2026-04-06": "清蒸鲈鱼, 蒜蓉油麦菜, 杂粮饭"},
            "meal_plan_slots": {
                "2026-04-06": {"dinner": ["清蒸鲈鱼", "蒜蓉油麦菜", "杂粮饭"]},
            },
            "dish_ingredients": {
                "清蒸鲈鱼": [{"name": "鲈鱼", "category": "protein"}],
                "蒜蓉油麦菜": [{"name": "油麦菜", "category": "vegetable"}],
                "杂粮饭": [{"name": "杂粮", "category": "grain"}],
            },
            "shopping_list": [],
            "schedule_id": 104,
            "is_draft": False,
            "last_pipeline_action": None,
        }

    def _llm_move_to_tomorrow_parsed(self) -> dict:
        """LLM response for move: source_date=2026-04-06, target_date=2026-04-07."""
        raw = json.dumps({
            "action": "modify",
            "target_date": "2026-04-07",
            "meal_time": "dinner",
            "source_date": "2026-04-06",
            "source_meal_time": "dinner",
            "user_message": "好的，已将今天晚餐移动到明天。",
            "meal_entries": [
                {
                    "date": "2026-04-07",
                    "meal_time": "dinner",
                    "dishes": [
                        {"name": "清蒸鲈鱼", "ingredients": [{"name": "鲈鱼", "category": "protein"}]},
                        {"name": "蒜蓉油麦菜", "ingredients": [{"name": "油麦菜", "category": "vegetable"}]},
                        {"name": "杂粮饭", "ingredients": [{"name": "杂粮", "category": "grain"}]},
                    ],
                }
            ],
        })
        return PlanAheadAgent(gemini_api_url="http://fake").parse_structured_response(raw)

    def _run_execute(self, old_state: dict, llm_parsed: dict,
                     user_input: str = "把今天晚饭移到明天") -> tuple:
        """Returns (pipeline_result, persist_meal_plan_calls, delete_orphaned_calls)."""
        pipeline = self._pipeline()
        sc = _make_storage_client_mock()
        persist_calls = []
        delete_calls = []

        async def _run():
            with patch.object(
                pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                AsyncMock(return_value=old_state),
            ), patch.object(
                pipeline, "_call_llm",
                AsyncMock(return_value=llm_parsed),
            ), patch.object(
                pipeline.plan_ahead_agent, "persist_meal_plan",
                AsyncMock(side_effect=lambda **kwargs: persist_calls.append(kwargs) or 104),
            ), patch.object(
                pipeline, "_delete_orphaned_schedules",
                AsyncMock(side_effect=lambda owner, dates, sc: delete_calls.append(dates)),
            ):
                result = await pipeline.execute(
                    owner_id=1,
                    user_input=user_input,
                    history=[],
                    user_timezone="Asia/Shanghai",
                    storage_client=sc,
                )
                return result, persist_calls, delete_calls

        return asyncio.run(_run())

    def _ad(self, result: dict) -> dict:
        return result["action_data"]

    def test_source_date_parsed_from_llm_response(self):
        """parse_structured_response must extract source_date and source_meal_time."""
        parsed = self._llm_move_to_tomorrow_parsed()
        assert parsed["source_date"] == "2026-04-06"
        assert parsed["source_meal_time"] == "dinner"

    def test_destination_date_written(self):
        """After a move, the destination date must appear in meal_plan_slots."""
        result, _, _ = self._run_execute(
            old_state=self._state_today_dinner(),
            llm_parsed=self._llm_move_to_tomorrow_parsed(),
        )
        slots = self._ad(result)["meal_plan_slots"]
        assert "2026-04-07" in slots
        dinner = slots["2026-04-07"].get("dinner", [])
        assert "清蒸鲈鱼" in dinner
        assert "蒜蓉油麦菜" in dinner
        assert "杂粮饭" in dinner

    def test_source_date_deleted_when_only_slot(self):
        """
        The regression: when the source date has only the moved slot,
        _delete_orphaned_schedules must be called for the source date.
        """
        _, _, delete_calls = self._run_execute(
            old_state=self._state_today_dinner(),
            llm_parsed=self._llm_move_to_tomorrow_parsed(),
        )
        deleted_dates = [d for dates in delete_calls for d in dates]
        assert "2026-04-06" in deleted_dates, (
            "Source date 2026-04-06 must be deleted after moving its only dinner slot"
        )

    def test_source_date_partially_removed_when_other_slots_exist(self):
        """
        When the source date still has other meal slots after the move,
        the source date should be updated (not fully deleted).
        """
        # Source has breakfast + dinner; move only dinner → breakfast must survive
        state = {
            "meal_plan": {"2026-04-06": "煮鸡蛋, 清蒸鲈鱼, 蒜蓉油麦菜, 杂粮饭"},
            "meal_plan_slots": {
                "2026-04-06": {
                    "breakfast": ["煮鸡蛋"],
                    "dinner": ["清蒸鲈鱼", "蒜蓉油麦菜", "杂粮饭"],
                },
            },
            "dish_ingredients": {
                "煮鸡蛋": [{"name": "鸡蛋", "category": "protein"}],
                "清蒸鲈鱼": [{"name": "鲈鱼", "category": "protein"}],
                "蒜蓉油麦菜": [{"name": "油麦菜", "category": "vegetable"}],
                "杂粮饭": [{"name": "杂粮", "category": "grain"}],
            },
            "shopping_list": [],
            "schedule_id": 104,
            "is_draft": False,
            "last_pipeline_action": None,
        }
        _, persist_calls, delete_calls = self._run_execute(
            old_state=state,
            llm_parsed=self._llm_move_to_tomorrow_parsed(),
        )
        # Source date should be updated (persist called with source date remaining)
        # NOT deleted
        deleted_dates = [d for dates in delete_calls for d in dates]
        assert "2026-04-06" not in deleted_dates, (
            "Source date with remaining breakfast must NOT be fully deleted"
        )
        # A persist call should have been made for the source date update
        source_persist = [
            c for c in persist_calls
            if "2026-04-06" in (c.get("meal_plan") or {})
        ]
        assert source_persist, "persist_meal_plan must be called to update source date"
        src_slots = source_persist[0].get("meal_plan_slots", {})
        assert "breakfast" in src_slots.get("2026-04-06", {}), (
            "Breakfast slot must survive on source date"
        )
        assert "dinner" not in src_slots.get("2026-04-06", {}), (
            "Dinner slot must be removed from source date"
        )

    def test_cooking_steps_carried_to_destination(self):
        """
        Regression: cooking steps already saved on the source schedule must be
        forwarded to persist_meal_plan via extra_existing_dish_data so that
        CookingStepsAgent does not waste tokens regenerating them.
        """
        source_steps = {
            "清蒸鲈鱼": {"steps": ["准备鲈鱼", "清蒸10分钟"], "ingredients": [{"name": "鲈鱼", "quantity": "500g"}]},
            "蒜蓉油麦菜": {"steps": ["热锅", "翻炒油麦菜"], "ingredients": [{"name": "油麦菜", "quantity": "300g"}]},
        }
        pipeline = self._pipeline()
        fake_schedule = {"id": 104, "metadata": {}}
        sc = _make_storage_client_mock()
        # Source schedule exists and contains cooking steps for the moved dishes.
        sc.get_user_schedules = AsyncMock(return_value=[fake_schedule])
        sc._extract_meal_plan_from_schedule = MagicMock(
            return_value=(
                {"2026-04-06": "清蒸鲈鱼, 蒜蓉油麦菜, 杂粮饭"},
                [],
                {},
                {"2026-04-06": {"dinner": ["清蒸鲈鱼", "蒜蓉油麦菜", "杂粮饭"]}},
            )
        )
        sc._extract_existing_dish_data = MagicMock(return_value=source_steps)

        persist_calls = []

        async def _run():
            with patch.object(
                pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                AsyncMock(return_value=self._state_today_dinner()),
            ), patch.object(
                pipeline, "_call_llm",
                AsyncMock(return_value=self._llm_move_to_tomorrow_parsed()),
            ), patch.object(
                pipeline.plan_ahead_agent, "persist_meal_plan",
                AsyncMock(side_effect=lambda **kwargs: persist_calls.append(kwargs) or 104),
            ), patch.object(
                pipeline, "_delete_orphaned_schedules",
                AsyncMock(return_value=None),
            ):
                await pipeline.execute(
                    owner_id=1,
                    user_input="把今天晚饭移到明天",
                    history=[],
                    user_timezone="Asia/Shanghai",
                    storage_client=sc,
                )

        asyncio.run(_run())

        # The first persist_meal_plan call is for the destination date.
        dest_call = next(
            (c for c in persist_calls if "2026-04-07" in (c.get("meal_plan") or {})),
            None,
        )
        assert dest_call is not None, "persist_meal_plan must be called for destination date"
        extra = dest_call.get("extra_existing_dish_data") or {}
        assert "清蒸鲈鱼" in extra, "Cooking steps for 清蒸鲈鱼 must be carried to destination"
        assert "蒜蓉油麦菜" in extra, "Cooking steps for 蒜蓉油麦菜 must be carried to destination"
        assert extra["清蒸鲈鱼"]["steps"] == source_steps["清蒸鲈鱼"]["steps"]

    def test_cooking_steps_not_carried_for_regular_modify(self):
        """
        For a regular modify (no source_date), extra_existing_dish_data must be
        None so that no stale steps bleed into unrelated dish updates.
        """
        raw = json.dumps({
            "action": "modify",
            "target_date": "2026-04-06",
            "meal_time": "dinner",
            # No source_date / source_meal_time → not a move
            "user_message": "好的，已更新。",
            "meal_entries": [
                {
                    "date": "2026-04-06",
                    "meal_time": "dinner",
                    "dishes": [
                        {"name": "清蒸鲈鱼", "ingredients": [{"name": "鲈鱼", "category": "protein"}]},
                    ],
                }
            ],
        })
        parsed = PlanAheadAgent(gemini_api_url="http://fake").parse_structured_response(raw)

        pipeline = self._pipeline()
        sc = _make_storage_client_mock()
        persist_calls = []

        async def _run():
            with patch.object(
                pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                AsyncMock(return_value=self._state_today_dinner()),
            ), patch.object(
                pipeline, "_call_llm",
                AsyncMock(return_value=parsed),
            ), patch.object(
                pipeline.plan_ahead_agent, "persist_meal_plan",
                AsyncMock(side_effect=lambda **kwargs: persist_calls.append(kwargs) or 104),
            ), patch.object(
                pipeline, "_delete_orphaned_schedules",
                AsyncMock(return_value=None),
            ):
                await pipeline.execute(
                    owner_id=1,
                    user_input="修改今天晚饭",
                    history=[],
                    user_timezone="Asia/Shanghai",
                    storage_client=sc,
                )

        asyncio.run(_run())

        # get_user_schedules must NOT be called for source extraction (no move)
        sc.get_user_schedules.assert_not_called()

        dest_call = next(
            (c for c in persist_calls if "2026-04-06" in (c.get("meal_plan") or {})),
            None,
        )
        assert dest_call is not None
        assert not dest_call.get("extra_existing_dish_data"), (
            "extra_existing_dish_data must be empty for non-move operations"
        )
