"""
Plan Ahead State Manager: Stores and retrieves meal planning state per user.
Uses in-memory storage keyed by owner_id. For production, consider Redis or DB.
"""
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# Sentinel value to distinguish "not provided" from "explicitly None (clear)"
_UNSET = object()

# In-memory storage: {owner_id: {meal_plan: {...}, shopping_list: [...], updated_at: datetime}}
_plan_states: Dict[int, Dict[str, Any]] = {}


def get_plan_state(owner_id: int) -> Dict[str, Any]:
    """
    Get current plan state for a user.

    Args:
        owner_id: User ID

    Returns:
        Dictionary with meal_plan, shopping_list, schedule_id, updated_at (or empty dict if none)
    """
    state = _plan_states.get(owner_id)
    if state:
        return {
            "meal_plan": state.get("meal_plan", {}),
            "shopping_list": state.get("shopping_list", []),
            "schedule_id": state.get("schedule_id"),
            "meal_plan_slots": state.get("meal_plan_slots", {}),
            "dish_ingredients": state.get("dish_ingredients", {}),
            "updated_at": state.get("updated_at"),
        }
    return {"meal_plan": {}, "shopping_list": [], "meal_plan_slots": {}, "dish_ingredients": {}}


def update_plan_state(
    owner_id: int,
    meal_plan: Optional[Dict[str, str]] = None,
    shopping_list: Optional[list] = None,
    schedule_id = _UNSET,  # Use sentinel to distinguish None from unset
    meal_plan_slots: Optional[Dict[str, Dict[str, str]]] = None,
    dish_ingredients: Optional[Dict[str, List[str]]] = None,
    merge: bool = True,
) -> Dict[str, Any]:
    """
    Update plan state for a user.
    meal_plan_slots: date -> { breakfast?, lunch?, dinner?, snack? } for storing lunch/breakfast/dinner.
    dish_ingredients: dish_name -> list of ingredient names for per-dish display (can be updated via chat).
    """
    current = _plan_states.get(owner_id, {})

    if merge:
        if meal_plan is not None:
            current_meal_plan = current.get("meal_plan", {})
            current_meal_plan.update(meal_plan)
            current["meal_plan"] = current_meal_plan
        elif "meal_plan" not in current:
            current["meal_plan"] = {}

        if shopping_list is not None:
            current_shopping_list = current.get("shopping_list", [])
            combined = list(set(current_shopping_list + shopping_list))
            current["shopping_list"] = combined
        elif "shopping_list" not in current:
            current["shopping_list"] = []

        if meal_plan_slots is not None:
            cur_slots = current.get("meal_plan_slots", {})
            for d, slot_dict in meal_plan_slots.items():
                cur_slots[d] = {**cur_slots.get(d, {}), **slot_dict}
            current["meal_plan_slots"] = cur_slots
        elif "meal_plan_slots" not in current:
            current["meal_plan_slots"] = {}

        if dish_ingredients is not None:
            cur_di = current.get("dish_ingredients", {})
            for dish_name, ing_list in dish_ingredients.items():
                cur_di[dish_name] = list(set(cur_di.get(dish_name, []) + (ing_list or [])))
            current["dish_ingredients"] = cur_di
        elif "dish_ingredients" not in current:
            current["dish_ingredients"] = {}
    else:
        if meal_plan is not None:
            current["meal_plan"] = meal_plan
        elif "meal_plan" not in current:
            current["meal_plan"] = {}

        if shopping_list is not None:
            current["shopping_list"] = shopping_list
        elif "shopping_list" not in current:
            current["shopping_list"] = []

        if meal_plan_slots is not None:
            current["meal_plan_slots"] = meal_plan_slots
        elif "meal_plan_slots" not in current:
            current["meal_plan_slots"] = {}

        if dish_ingredients is not None:
            current["dish_ingredients"] = dish_ingredients
        elif "dish_ingredients" not in current:
            current["dish_ingredients"] = {}

    # Handle schedule_id: support explicit None to clear stale ID
    if schedule_id is not _UNSET:
        if schedule_id is None:
            # Explicitly clear schedule_id from state
            current.pop("schedule_id", None)
            logger.info(f"Cleared schedule_id from plan state for user {owner_id}")
        else:
            current["schedule_id"] = schedule_id

    current["updated_at"] = datetime.now(timezone.utc)
    _plan_states[owner_id] = current
    
    logger.info(f"Updated plan state for user {owner_id}: {len(current.get('meal_plan', {}))} meals, {len(current.get('shopping_list', []))} items")
    return current


def clear_plan_state(owner_id: int) -> bool:
    """
    Clear plan state for a user (e.g. after saving to schedule).
    
    Args:
        owner_id: User ID
        
    Returns:
        True if state existed and was cleared, False otherwise
    """
    if owner_id in _plan_states:
        del _plan_states[owner_id]
        logger.info(f"Cleared plan state for user {owner_id}")
        return True
    return False
