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
        "target_date": raw.get("target_date"),
        "target_meal_type": raw.get("target_meal_type"),
        "updated_at": raw.get("updated_at"),
    }


def update_active_context(
    owner_id: int,
    *,
    add_ingredients: Optional[List[str]] = None,
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

    # Union-merge ingredients (preserve order, deduplicate case-insensitively)
    existing: List[str] = raw.get("active_ingredients", [])
    if add_ingredients:
        seen_lower = {i.lower() for i in existing}
        for ing in add_ingredients:
            if ing and ing.lower() not in seen_lower:
                existing.append(ing)
                seen_lower.add(ing.lower())

    now = datetime.now(timezone.utc)
    updated: Dict[str, Any] = {
        "active_ingredients": existing,
        "target_date": target_date if target_date is not None else raw.get("target_date"),
        "target_meal_type": (
            target_meal_type if target_meal_type is not None else raw.get("target_meal_type")
        ),
        "updated_at": now,
        "expires_at": now + timedelta(minutes=ttl_minutes),
    }
    _active_contexts[owner_id] = updated
    logger.info(
        "[ActiveContext] Updated for user %d: ingredients=%s, target=%s %s",
        owner_id,
        updated["active_ingredients"],
        updated.get("target_date"),
        updated.get("target_meal_type") or "",
    )
    return {
        "active_ingredients": list(updated["active_ingredients"]),
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
        "target_date": None,
        "target_meal_type": None,
        "updated_at": None,
    }
