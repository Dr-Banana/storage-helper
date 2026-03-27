"""
Plan Ahead State Manager: Stores and retrieves meal planning state per user.
Uses in-memory storage keyed by owner_id. For production, consider Redis or DB.
"""
from enum import Enum
from typing import Dict, Any, Optional, List, Set, Union
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


class PipelinePhase(str, Enum):
    """
    Explicit conversational phase of the PLAN_AHEAD pipeline.

    Replaces the scattered boolean/string flags that previously drove
    branching logic (is_draft, last_pipeline_action, pending_planning_queue,
    meal_planning_queue, etc.).  Each phase maps to a single handler method
    in PlanAheadPipeline, making execute() a clean dispatcher.

    Transitions:
        IDLE ──────────────────────────────────► AWAITING_DATE_CONFIRM
              (multi-day planning request)
        AWAITING_DATE_CONFIRM ─(confirmed)────► IN_QUEUE
                              ─(corrected)───► AWAITING_DATE_CONFIRM
                              ─(unclear)─────► AWAITING_DATE_CONFIRM

        IDLE / DRAFT_ACTIVE ──(ask guard)─────► AWAITING_CLARIFICATION
        AWAITING_CLARIFICATION ─(answered)────► IDLE (or IN_QUEUE)

        IN_QUEUE ─(slot planned & accepted)──► IN_QUEUE (next slot)
                 ─(queue exhausted)──────────► IDLE
    """

    # No active session; normal add/modify/recommend intent handling.
    IDLE = "idle"

    # _init_planning_queue detected a multi-day intent and emitted a
    # date-range confirmation question.  Waiting for user's yes/no/correct.
    AWAITING_DATE_CONFIRM = "awaiting_date_confirm"

    # User confirmed (or we bypassed confirmation for single-slot requests).
    # meal_planning_queue is populated; pipeline pops one slot per turn.
    IN_QUEUE = "in_queue"

    # is_draft=True; a plan has been recommended but not yet confirmed/saved.
    DRAFT_ACTIVE = "draft_active"

    # Pipeline asked the user a clarifying question (action=ask).
    # Waiting for their answer before proceeding.
    AWAITING_CLARIFICATION = "awaiting_clarification"


def get_phase(state: Dict[str, Any]) -> PipelinePhase:
    """
    Derive the current PipelinePhase from an existing state dict.

    This is the single authoritative place that maps legacy boolean/string
    flags to the new phase enum, so all call sites read the same logic.
    """
    if state.get("meal_planning_queue"):
        return PipelinePhase.IN_QUEUE
    if state.get("pending_planning_queue") and state.get("last_pipeline_action") == "ask_confirm_dates":
        return PipelinePhase.AWAITING_DATE_CONFIRM
    if state.get("last_pipeline_action") == "ask":
        return PipelinePhase.AWAITING_CLARIFICATION
    if state.get("is_draft"):
        return PipelinePhase.DRAFT_ACTIVE
    return PipelinePhase.IDLE

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
            "is_draft": state.get("is_draft", False),
            # Date strings that were already in DB when the recommend draft was created.
            # Used at confirm time to detect dates the user deleted via the Web UI.
            "draft_base_db_dates": state.get("draft_base_db_dates", set()),
            # Last action returned by the pipeline — used to enforce ask-before-recommend flow.
            "last_pipeline_action": state.get("last_pipeline_action"),
            # Active cooking context — tracks the dish/steps currently being discussed.
            # Enables follow-up questions ("酱料比例?") to be answered conversationally
            # instead of re-triggering a full COOKING_STEPS generation.
            "cooking_context": state.get("cooking_context"),
            # Deferred modification request (pending user confirmation).
            # Set when MODIFY_RECIPE intent confidence < 0.8 so the next turn can
            # execute the modification if the user confirms with "yes".
            "pending_modify_action": state.get("pending_modify_action"),
            # Pending recipe overwrite (ASK_OVERWRITE flow).
            # Holds the proposed new recipe when the dish already has a saved recipe,
            # waiting for the user to click "Save this version" in the RecipeDiffCard.
            "pending_overwrite": state.get("pending_overwrite"),
            # Pending meal plan options presented to the user (suggest_options action).
            # Each option is a dict with {option_id, label, meal_plan_slots, dish_ingredients}.
            # Cleared when user selects an option (recommend action follows).
            "pending_options": state.get("pending_options"),
            # Dishes explicitly replaced/rejected by the user in the current draft session.
            # Used to avoid re-suggesting the same dishes and to guide the seed library fallback.
            "draft_rejected_dishes": state.get("draft_rejected_dishes", set()),
            # Proposed date range waiting for user confirmation before planning starts.
            # Set when a week-planning intent is detected; cleared on confirm or correction.
            "pending_planning_queue": state.get("pending_planning_queue", []),
            # Dates captured by the Fresh-plan guard when it forced action=ask (e.g. ["2026-03-27","2026-03-28"]).
            # Used on the next turn to combine the user's meal-type choice with the known dates,
            # bypassing a second _init_planning_queue call that would lose the date context.
            # Cleared after being consumed or when a new planning cycle begins.
            "pending_ask_dates": state.get("pending_ask_dates", []),
            # Active per-meal planning queue: slots remaining after date confirmation.
            # Populated when user confirms a multi-day range; each slot is popped (popleft)
            # only after the user accepts that meal's recommend draft.
            "meal_planning_queue": state.get("meal_planning_queue", []),
            # Total number of slots originally in meal_planning_queue (for progress display).
            "meal_planning_total": state.get("meal_planning_total", 0),
            # How many times the user corrected the pending_planning_queue without changing it.
            # Resets to 0 when the queue content changes or is confirmed.
            # Used to detect dead-loop and trigger guided recovery.
            "confirmation_retry_count": state.get("confirmation_retry_count", 0),
            # How many times the user requested a full option refresh ("再换一批") in the
            # current planning session.  Used to drive dynamic temperature escalation:
            # 0-1 → 0.2 (default), 2 → 0.45, ≥3 → 0.7 (maximum creativity).
            "refresh_count": state.get("refresh_count", 0),
            "updated_at": state.get("updated_at"),
        }
    return {
        "meal_plan": {}, "shopping_list": [], "meal_plan_slots": {}, "dish_ingredients": {},
        "is_draft": False, "draft_base_db_dates": set(), "last_pipeline_action": None,
        "cooking_context": None, "pending_modify_action": None, "pending_overwrite": None,
        "pending_options": None, "draft_rejected_dishes": set(),
        "pending_planning_queue": [],
        "meal_planning_queue": [],
        "meal_planning_total": 0,
        "confirmation_retry_count": 0,
        "pending_ask_dates": [],
        "refresh_count": 0,
    }


def update_plan_state(
    owner_id: int,
    meal_plan: Optional[Dict[str, str]] = None,
    shopping_list: Optional[list] = None,
    schedule_id = _UNSET,  # Use sentinel to distinguish None from unset
    meal_plan_slots: Optional[Dict[str, Dict[str, Any]]] = None,
    dish_ingredients: Optional[Dict[str, List[Any]]] = None,
    is_draft = _UNSET,  # True = draft (not saved to DB); False = confirmed
    draft_base_db_dates: Optional[Set[str]] = None,  # snapshot of DB dates at draft creation
    last_pipeline_action: Optional[str] = _UNSET,  # last action returned by pipeline
    cooking_context = _UNSET,  # Dict with dish_name + steps, or None to clear
    pending_modify_action = _UNSET,  # Deferred MODIFY_RECIPE request awaiting user confirmation
    pending_overwrite = _UNSET,  # Proposed recipe overwrite awaiting user confirmation
    pending_options = _UNSET,  # List of meal plan options for suggest_options flow
    draft_rejected_dishes = _UNSET,  # Set[str] of dishes rejected/replaced in this draft session
    pending_planning_queue = _UNSET,  # List[str] proposed date range awaiting user confirmation
    pending_ask_dates = _UNSET,  # List[str] dates saved by Fresh-plan guard for next-turn resolution
    meal_planning_queue = _UNSET,  # List[str] remaining dates to plan in sequential queue mode
    meal_planning_total = _UNSET,  # int total dates in the original queue (for progress display)
    confirmation_retry_count = _UNSET,  # int: consecutive no-op corrections, for dead-loop guard
    refresh_count = _UNSET,  # int: consecutive "再换一批" requests, drives temperature escalation
    merge: bool = True,
) -> Dict[str, Any]:
    """
    Update plan state for a user.
    meal_plan_slots: date -> { breakfast?, lunch?, dinner? } where values are List[str] (Phase 2 format).
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
            # shopping_list items are dicts (not hashable), deduplicate by 'name' key.
            seen: Dict[str, Any] = {}
            for item in current_shopping_list + shopping_list:
                key = item.get("name") if isinstance(item, dict) else str(item)
                if key:
                    seen[key] = item
            current["shopping_list"] = list(seen.values())
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
                # Merge by ingredient name — supports both new dict and legacy str formats.
                existing: Dict[str, Any] = {}
                for i in cur_di.get(dish_name, []):
                    key = i.get("name") if isinstance(i, dict) else i
                    if key:
                        existing[key] = i
                for item in (ing_list or []):
                    key = item.get("name") if isinstance(item, dict) else item
                    if key:
                        existing[key] = item
                cur_di[dish_name] = list(existing.values())
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

    if is_draft is not _UNSET:
        current["is_draft"] = bool(is_draft)

    if draft_base_db_dates is not None:
        current["draft_base_db_dates"] = set(draft_base_db_dates)

    if last_pipeline_action is not _UNSET:
        if last_pipeline_action is None:
            current.pop("last_pipeline_action", None)
        else:
            current["last_pipeline_action"] = last_pipeline_action

    if cooking_context is not _UNSET:
        if cooking_context is None:
            current.pop("cooking_context", None)
        else:
            current["cooking_context"] = cooking_context

    if pending_modify_action is not _UNSET:
        if pending_modify_action is None:
            current.pop("pending_modify_action", None)
        else:
            current["pending_modify_action"] = pending_modify_action

    if pending_overwrite is not _UNSET:
        if pending_overwrite is None:
            current.pop("pending_overwrite", None)
        else:
            current["pending_overwrite"] = pending_overwrite

    if pending_options is not _UNSET:
        if pending_options is None:
            current.pop("pending_options", None)
        else:
            current["pending_options"] = pending_options

    if draft_rejected_dishes is not _UNSET:
        if draft_rejected_dishes is None:
            current.pop("draft_rejected_dishes", None)
        else:
            # Always union-merge so we accumulate rejections across turns
            existing_rejected: set = current.get("draft_rejected_dishes", set())
            current["draft_rejected_dishes"] = existing_rejected | set(draft_rejected_dishes)

    if pending_planning_queue is not _UNSET:
        if pending_planning_queue is None:
            current.pop("pending_planning_queue", None)
        else:
            current["pending_planning_queue"] = list(pending_planning_queue)

    if pending_ask_dates is not _UNSET:
        if pending_ask_dates is None:
            current.pop("pending_ask_dates", None)
        else:
            current["pending_ask_dates"] = list(pending_ask_dates)

    if meal_planning_queue is not _UNSET:
        if meal_planning_queue is None:
            current.pop("meal_planning_queue", None)
        else:
            current["meal_planning_queue"] = list(meal_planning_queue)

    if meal_planning_total is not _UNSET:
        if meal_planning_total is None:
            current.pop("meal_planning_total", None)
        else:
            current["meal_planning_total"] = int(meal_planning_total)

    if confirmation_retry_count is not _UNSET:
        if confirmation_retry_count is None:
            current.pop("confirmation_retry_count", None)
        else:
            current["confirmation_retry_count"] = int(confirmation_retry_count)

    if refresh_count is not _UNSET:
        if refresh_count is None:
            current.pop("refresh_count", None)
        else:
            current["refresh_count"] = int(refresh_count)

    current["updated_at"] = datetime.now(timezone.utc)
    _plan_states[owner_id] = current

    draft_flag = current.get("is_draft", False)
    logger.info(
        f"Updated plan state for user {owner_id}: {len(current.get('meal_plan', {}))} meals, "
        f"{len(current.get('shopping_list', []))} items, is_draft={draft_flag}"
    )
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
