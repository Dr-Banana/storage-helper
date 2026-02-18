"""
PlanIngredientsAgent: Dedicated agent for managing ingredients (dish_ingredients and shopping_list).
Handles: remove ingredients for a dish, set/update ingredients for a dish.
"""
from typing import Dict, Any, List, Tuple
import logging

from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


def _match_dish(dish_key: str, target: str) -> bool:
    """True if target matches dish_key (exact or target in dish_key)."""
    if not dish_key or not target:
        return False
    d = dish_key.strip().lower()
    t = target.strip().lower()
    return d == t or t in d


class PlanIngredientsAgent(BaseAgent):
    """Agent for adding/removing/updating ingredients for a dish. Only manages dish_ingredients and shopping_list."""

    def __init__(self):
        super().__init__("PLAN_INGREDIENTS")

    async def execute(self, user_input: str, owner_id: int, **kwargs) -> Dict[str, Any]:
        """Sub-agent: invoked via apply_*; stub for ABC compliance."""
        return self.format_response("PLAN_INGREDIENTS", "Use apply_remove_ingredients or apply_set_ingredients.", {})

    def apply_remove_ingredients(
        self,
        dish_name: str,
        dish_ingredients: Dict[str, List[str]],
        shopping_list: List[str],
    ) -> Tuple[Dict[str, List[str]], List[str]]:
        """Remove all ingredients for the given dish. Returns (new_dish_ingredients, new_shopping_list)."""
        dish_ingredients = dict(dish_ingredients or {})
        # Find key that matches dish_name (exact or dish_name in key, e.g. "日餐")
        to_remove = None
        for key in list(dish_ingredients.keys()):
            if _match_dish(key, dish_name):
                to_remove = key
                break
        if to_remove is None:
            self.logger.info(f"[PLAN_INGREDIENTS] No dish_ingredients entry for '{dish_name}', nothing to remove")
            return dish_ingredients, list(shopping_list or [])

        removed_ings = set(
            y.strip() for y in (dish_ingredients.pop(to_remove, []) or []) if y and str(y).strip()
        )
        new_sl = [x for x in (shopping_list or []) if x.strip() not in removed_ings]
        self.logger.info(f"[PLAN_INGREDIENTS] Removed ingredients for dish '{to_remove}': {removed_ings}, shopping_list len {len(shopping_list or [])} -> {len(new_sl)}")
        return dish_ingredients, new_sl

    def apply_set_ingredients(
        self,
        dish_name: str,
        ingredients_list: List[str],
        dish_ingredients: Dict[str, List[str]],
        shopping_list: List[str],
    ) -> Tuple[Dict[str, List[str]], List[str]]:
        """Set (replace) ingredients for the given dish. Returns (new_dish_ingredients, new_shopping_list)."""
        dish_ingredients = dict(dish_ingredients or {})
        # Remove old entry for this dish (match by name)
        for key in list(dish_ingredients.keys()):
            if _match_dish(key, dish_name):
                old_ings = set(dish_ingredients.pop(key, []))
                break
        else:
            old_ings = set()

        ingredients_list = [x.strip() for x in (ingredients_list or []) if x.strip()]
        dish_ingredients[dish_name] = ingredients_list

        # Update shopping_list: remove old ingredients for this dish, add new
        sl_set = set(x.strip() for x in (shopping_list or []) if x.strip())
        sl_set -= old_ings
        sl_set |= set(ingredients_list)
        new_sl = list(sl_set)
        self.logger.info(f"[PLAN_INGREDIENTS] Set ingredients for '{dish_name}': {ingredients_list}")
        return dish_ingredients, new_sl
