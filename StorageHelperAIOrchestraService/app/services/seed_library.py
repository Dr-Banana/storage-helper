"""
Seed Library — Phase 3

Loads the curated 50-dish seed library and provides candidate selection +
LLM context building.  Selection logic uses the same P(dish) score from the
Diversity Engine so that the two systems stay consistent:

    score(dish) = W_cuisine / (1 + recency_penalty(t))

where recency_penalty mirrors HARD_BAN / SOFT_AVOID constants from
diversity_engine.py.  Dishes that would be hard-banned are excluded entirely;
soft-avoided dishes receive a lower score and are therefore deprioritised.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent.parent / "data" / "seed_dishes.json"

# ─── constants (mirror diversity_engine.py) ──────────────────────────────────
HARD_BAN_DAYS = 3
SOFT_AVOID_DAYS = 7

# How many candidate dishes to surface to the LLM
DEFAULT_CANDIDATE_COUNT = 12

# ─── internal dish cache ─────────────────────────────────────────────────────
_DISHES: Optional[List[Dict[str, Any]]] = None


def _load() -> List[Dict[str, Any]]:
    global _DISHES
    if _DISHES is None:
        with open(_DATA_PATH, "r", encoding="utf-8") as fh:
            _DISHES = json.load(fh)
        logger.info("[SEED_LIBRARY] Loaded %d seed dishes from %s", len(_DISHES), _DATA_PATH)
    return _DISHES


# ─── helpers ─────────────────────────────────────────────────────────────────

def _contains_disliked(dish: Dict[str, Any], disliked: List[str]) -> bool:
    """Return True if any main_ingredient overlaps with the user's disliked list."""
    if not disliked:
        return False
    dish_ingredients_lower = {i.lower() for i in dish.get("main_ingredients", [])}
    for term in disliked:
        if term.lower() in dish_ingredients_lower:
            return True
        # substring match: e.g. disliked=["香菜"] vs ingredient="fresh cilantro/香菜"
        for ing in dish_ingredients_lower:
            if term.lower() in ing:
                return True
    return False


def _recency_penalty(dish_name_zh: str, dish_name_en: str, recent_dishes: List[Dict[str, Any]]) -> float:
    """Returns the recency penalty score (0 = not seen recently, higher = seen recently)."""
    from datetime import date as _date
    from app.services import diversity_engine  # lazy import to avoid circular

    today = _date.today()
    for entry in recent_dishes:
        entry_name = entry.get("dish", "")
        if entry_name in (dish_name_zh, dish_name_en):
            days = diversity_engine._days_since(entry.get("date", ""), today=today)
            return diversity_engine.recency_penalty(days)
    return 0.0


def _is_hard_banned(dish_name_zh: str, dish_name_en: str, recent_dishes: List[Dict[str, Any]]) -> bool:
    """Return True if the dish was eaten within HARD_BAN_DAYS."""
    from datetime import date as _date
    from app.services import diversity_engine

    today = _date.today()
    for entry in recent_dishes:
        entry_name = entry.get("dish", "")
        if entry_name in (dish_name_zh, dish_name_en):
            days = diversity_engine._days_since(entry.get("date", ""), today=today)
            if days <= HARD_BAN_DAYS:
                return True
    return False


# ─── public API ──────────────────────────────────────────────────────────────

def select_candidates(
    cuisine_weights: Optional[Dict[str, int]] = None,
    disliked_ingredients: Optional[List[str]] = None,
    recent_dishes: Optional[List[Dict[str, Any]]] = None,
    n: int = DEFAULT_CANDIDATE_COUNT,
) -> List[Dict[str, Any]]:
    """
    Select up to *n* candidate dishes from the seed library, weighted by
    cuisine preference and filtered by disliked ingredients / recency bans.

    Algorithm:
    1. Filter out hard-banned dishes and dishes containing disliked ingredients.
    2. Score remaining dishes:  score = W_cuisine / (1 + recency_penalty)
    3. Distribute n slots proportionally across cuisine_l1 buckets, then pick
       top-scored (with a small random jitter for freshness).
    4. Guarantee at least one Soup entry if the pool contains any.
    """
    all_dishes = _load()
    weights = cuisine_weights or {"Chinese": 50, "Western": 20, "Japanese": 15, "Korean": 10, "Other": 5}
    disliked = disliked_ingredients or []
    recent = recent_dishes or []

    # Step 1 — filter
    pool: List[Dict[str, Any]] = []
    for dish in all_dishes:
        if _contains_disliked(dish, disliked):
            continue
        if _is_hard_banned(dish["name_zh"], dish["name_en"], recent):
            continue
        pool.append(dish)

    if not pool:
        logger.warning("[SEED_LIBRARY] Pool is empty after filtering — returning empty list")
        return []

    # Step 2 — score each dish
    total_weight = sum(weights.values()) or 100
    scored: List[tuple[float, Dict[str, Any]]] = []
    for dish in pool:
        w = weights.get(dish["cuisine_l1"], weights.get("Other", 5))
        normalized_w = w / total_weight
        penalty = _recency_penalty(dish["name_zh"], dish["name_en"], recent)
        score = normalized_w / (1.0 + penalty)
        # small random jitter so repeated calls yield variety
        jitter = random.uniform(0.85, 1.15)
        scored.append((score * jitter, dish))

    scored.sort(key=lambda t: t[0], reverse=True)

    # Step 3 — pick top n
    candidates = [d for _, d in scored[:n]]

    # Step 4 — ensure at least one Soup if requested
    has_soup = any(d["category"] == "Soup" for d in candidates)
    if not has_soup:
        soup_pool = [d for _, d in scored if d["category"] == "Soup"]
        if soup_pool:
            candidates[-1] = soup_pool[0]  # replace lowest-ranked with a soup

    logger.info(
        "[SEED_LIBRARY] Selected %d candidates (pool=%d, disliked_filter=%d, hard_ban_filter=%d)",
        len(candidates),
        len(pool),
        len(all_dishes) - len(pool),
        sum(1 for d in all_dishes if _is_hard_banned(d["name_zh"], d["name_en"], recent)),
    )
    return candidates


def build_seed_context(candidates: List[Dict[str, Any]]) -> str:
    """
    Format the candidate list into a compact LLM-readable reference block.

    Example output line:
      [Chinese·Sichuan] 宫保鸡丁 / Kung Pao Chicken — Spicy, Umami — Meat
    """
    if not candidates:
        return ""

    lines = ["=== SEED DISH REFERENCE (pre-validated — prefer these candidates) ==="]
    for d in candidates:
        flavors = ", ".join(d.get("flavor_profile", []))
        line = (
            f"  [{d['cuisine_l1']}·{d['cuisine_l2']}] "
            f"{d['name_zh']} / {d['name_en']} "
            f"— {flavors} — {d['category']}"
        )
        lines.append(line)
    lines.append(
        "You MAY suggest dishes not on this list, but they MUST fit the cuisine style framework above."
    )
    return "\n".join(lines)


def lookup_dish(name: str) -> Optional[Dict[str, Any]]:
    """Return the seed entry for a dish name (Chinese or English), or None."""
    for dish in _load():
        if name in (dish["name_zh"], dish["name_en"]):
            return dish
    return None
