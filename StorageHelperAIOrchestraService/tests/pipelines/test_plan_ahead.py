"""
Tests for PLAN_AHEAD pipeline — new schema-based architecture.

Covers:
- compute_shopping_list: deterministic ingredient aggregation
- parse_structured_response: structured LLM JSON → internal state
- ingredient format compatibility: string vs {"name": ...} dict
- apply_plan_modification: split-safety for dish names containing " and "
- inventory sorting: expiring items surface before condiments
"""
import json
import pytest
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
