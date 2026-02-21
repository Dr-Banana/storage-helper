"""
Tests for PLAN_AHEAD pipeline — new schema-based architecture.

Covers:
- compute_shopping_list: deterministic ingredient aggregation
- parse_structured_response: structured LLM JSON → internal state
"""
import json
import pytest
from app.agents.scheduling_agent import compute_shopping_list, PlanAheadAgent


class TestComputeShoppingList:
    """Tests for the deterministic compute_shopping_list function."""

    def test_basic_aggregation(self):
        """Ingredients from multiple dishes are merged into a sorted unique list."""
        dish_ingredients = {
            "Pasta": ["pasta", "tomato", "garlic"],
            "Pizza": ["flour", "tomato", "cheese"],
        }
        result = compute_shopping_list(dish_ingredients)
        assert result == sorted({"pasta", "tomato", "garlic", "flour", "cheese"})

    def test_deduplication(self):
        """Duplicate ingredients across dishes appear only once."""
        dish_ingredients = {
            "DishA": ["onion", "garlic"],
            "DishB": ["garlic", "ginger"],
        }
        result = compute_shopping_list(dish_ingredients)
        assert result.count("garlic") == 1
        assert set(result) == {"onion", "garlic", "ginger"}

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
        assert result == ["lettuce", "tomato"]

    def test_output_is_sorted(self):
        """Output is always alphabetically sorted."""
        dish_ingredients = {
            "Dish": ["zucchini", "apple", "mango"],
        }
        result = compute_shopping_list(dish_ingredients)
        assert result == ["apple", "mango", "zucchini"]

    def test_single_dish(self):
        """Single dish returns its own ingredient list sorted."""
        dish_ingredients = {"回锅肉": ["猪肉", "豆瓣酱", "大蒜", "青椒"]}
        result = compute_shopping_list(dish_ingredients)
        assert set(result) == {"猪肉", "豆瓣酱", "大蒜", "青椒"}
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
        assert set(result["dish_ingredients"]["回锅肉"]) == {"猪肉", "豆瓣酱"}

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
        assert set(result["shopping_list"]) == {"pasta", "tomato"}

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
        assert set(result["shopping_list"]) == {"ing1", "ing2"}

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
