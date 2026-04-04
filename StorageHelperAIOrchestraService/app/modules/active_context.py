"""
Active Context: cross-turn working memory for PlanAheadPipeline.

Stores facts extracted from the current conversation window so that follow-up
questions ("葱花的话能做啥") automatically inherit context ("user also has 牛棒骨").

Storage is in-process (dict keyed by owner_id).  An optional TTL auto-expires
stale context after a period of inactivity so it does not bleed across sessions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

# Default TTL: context expires after 30 minutes of no activity
_DEFAULT_TTL_MINUTES = 30

# In-memory store: { owner_id: { ...fields..., expires_at: datetime } }
_active_contexts: Dict[int, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_active_context(owner_id: int) -> Dict[str, Any]:
    """
    Return the current active context for *owner_id*.

    Returns an empty context dict if nothing is stored or the TTL has expired.
    """
    raw = _active_contexts.get(owner_id)
    if not raw:
        return _empty()

    # TTL check
    expires_at: Optional[datetime] = raw.get("expires_at")
    if expires_at and datetime.now(timezone.utc) > expires_at:
        logger.debug("[ActiveContext] Context expired for user %d — clearing.", owner_id)
        del _active_contexts[owner_id]
        return _empty()

    return {
        "active_ingredients": list(raw.get("active_ingredients", [])),
        "avoid_dishes": list(raw.get("avoid_dishes", [])),
        "avoid_ingredients": list(raw.get("avoid_ingredients", [])),
        "avoid_cuisines": list(raw.get("avoid_cuisines", [])),
        "target_date": raw.get("target_date"),
        "target_meal_type": raw.get("target_meal_type"),
        "updated_at": raw.get("updated_at"),
    }


def update_active_context(
    owner_id: int,
    *,
    add_ingredients: Optional[List[str]] = None,
    add_avoid_dishes: Optional[List[str]] = None,
    add_avoid_ingredients: Optional[List[str]] = None,
    add_avoid_cuisines: Optional[List[str]] = None,
    target_date: Optional[str] = None,
    target_meal_type: Optional[str] = None,
    ttl_minutes: int = _DEFAULT_TTL_MINUTES,
) -> Dict[str, Any]:
    """
    Merge new facts into the active context for *owner_id*.

    ``add_ingredients`` is **union-merged** — existing ingredients are never
    removed so follow-up turns accumulate the full ingredient list.
    ``target_date`` / ``target_meal_type`` are overwritten when provided.
    """
    raw = _active_contexts.get(owner_id, {})

    # Union-merge list fields (preserve order, deduplicate case-insensitively)
    def _merge_list(existing: List[str], to_add: Optional[List[str]]) -> List[str]:
        merged = list(existing or [])
        if not to_add:
            return merged
        seen_lower = {i.lower() for i in merged}
        for item in to_add:
            if item and item.lower() not in seen_lower:
                merged.append(item)
                seen_lower.add(item.lower())
        return merged

    existing_ingredients: List[str] = _merge_list(raw.get("active_ingredients", []), add_ingredients)
    existing_avoid_dishes: List[str] = _merge_list(raw.get("avoid_dishes", []), add_avoid_dishes)
    existing_avoid_ingredients: List[str] = _merge_list(
        raw.get("avoid_ingredients", []), add_avoid_ingredients
    )
    existing_avoid_cuisines: List[str] = _merge_list(raw.get("avoid_cuisines", []), add_avoid_cuisines)

    now = datetime.now(timezone.utc)
    updated: Dict[str, Any] = {
        "active_ingredients": existing_ingredients,
        "avoid_dishes": existing_avoid_dishes,
        "avoid_ingredients": existing_avoid_ingredients,
        "avoid_cuisines": existing_avoid_cuisines,
        "target_date": target_date if target_date is not None else raw.get("target_date"),
        "target_meal_type": (
            target_meal_type if target_meal_type is not None else raw.get("target_meal_type")
        ),
        "updated_at": now,
        "expires_at": now + timedelta(minutes=ttl_minutes),
    }
    _active_contexts[owner_id] = updated
    logger.info(
        "[ActiveContext] Updated for user %d: ingredients=%s, avoid_dishes=%s, avoid_ingredients=%s, avoid_cuisines=%s, target=%s %s",
        owner_id,
        updated["active_ingredients"],
        updated["avoid_dishes"],
        updated["avoid_ingredients"],
        updated["avoid_cuisines"],
        updated.get("target_date"),
        updated.get("target_meal_type") or "",
    )
    return {
        "active_ingredients": list(updated["active_ingredients"]),
        "avoid_dishes": list(updated["avoid_dishes"]),
        "avoid_ingredients": list(updated["avoid_ingredients"]),
        "avoid_cuisines": list(updated["avoid_cuisines"]),
        "target_date": updated["target_date"],
        "target_meal_type": updated["target_meal_type"],
        "updated_at": updated["updated_at"],
    }


def clear_active_context(owner_id: int) -> bool:
    """
    Remove the active context for *owner_id*.

    Called when a planning session fully completes (e.g. dishes added to
    calendar) so the next conversation starts fresh.
    """
    if owner_id in _active_contexts:
        del _active_contexts[owner_id]
        logger.info("[ActiveContext] Cleared for user %d.", owner_id)
        return True
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _empty() -> Dict[str, Any]:
    return {
        "active_ingredients": [],
        "avoid_dishes": [],
        "avoid_ingredients": [],
        "avoid_cuisines": [],
        "target_date": None,
        "target_meal_type": None,
        "updated_at": None,
    }
