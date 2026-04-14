"""
Seed Library — Phase 3

Loads the curated seed library and provides candidate selection +
LLM context building.

Selection algorithm (v2 — cuisine-fair rotation):
  1. Filter out hard-banned dishes (eaten ≤ 3 days ago) and dishes containing
     disliked ingredients.
  2. Score each dish: score = 1 / (1 + recency_penalty(t))  [pure recency, no weights]
  3. Distribute n slots using FAIR ROTATION:
       • Each cuisine_l1 bucket that has eligible dishes gets a guaranteed
         MIN_PER_CUISINE = 2 slots.
       • Remaining slots (n - guaranteed) are filled by top-scored dishes
         across all cuisines.
       • This ensures Chinese, Japanese, Thai, Western all appear in every
         candidate set, giving the LLM diverse inspiration even without weights.
  4. Guarantee at least one Soup entry if the pool contains any.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── constants (mirror diversity_engine.py) ──────────────────────────────────
HARD_BAN_DAYS = 3
SOFT_AVOID_DAYS = 7

# How many candidate dishes to surface to the LLM
DEFAULT_CANDIDATE_COUNT = 12

# ─── dish catalog (populated from HowToCookService at startup) ───────────────
_DISHES: Optional[List[Dict[str, Any]]] = None


def _load() -> List[Dict[str, Any]]:
    global _DISHES
    if _DISHES is None:
        try:
            from app.services.howtocook_service import get_howtocook_service
            catalog = get_howtocook_service().get_dish_catalog()
            if catalog:
                _DISHES = catalog
                logger.info("[SEED_LIBRARY] Loaded %d dishes from HowToCookService", len(_DISHES))
                return _DISHES
            else:
                from app.services.howtocook_service import FOODIE_ERR_004
                logger.warning(
                    "[%s] HowToCookService returned an empty catalog — "
                    "falling back to seed_dishes.json",
                    FOODIE_ERR_004,
                )
        except Exception as exc:
            from app.services.howtocook_service import FOODIE_ERR_004
            logger.warning(
                "[%s] HowToCookService unavailable — meal recommendations falling back to seed_dishes.json. "
                "Cause: %s",
                FOODIE_ERR_004, exc,
            )

        # Fallback: bundled seed_dishes.json (used when MCP service is not yet available)
        _DATA_PATH = Path(__file__).parent.parent / "data" / "seed_dishes.json"
        try:
            with open(_DATA_PATH, "r", encoding="utf-8") as fh:
                _DISHES = json.load(fh)
            logger.info("[SEED_LIBRARY] Loaded %d seed dishes from %s (fallback)", len(_DISHES), _DATA_PATH)
        except FileNotFoundError:
            logger.warning("[SEED_LIBRARY] seed_dishes.json not found and HowToCook unavailable — empty catalog")
            _DISHES = []
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

# Minimum slots reserved per cuisine_l1 bucket in the fair-rotation algorithm.
# This prevents the Chinese-heavy seed library from crowding out all other cuisines.
MIN_PER_CUISINE = 2


# Number of candidate slots reserved for anchor-ingredient matching.
# These are filled BEFORE the fair-rotation phase so the LLM always
# has "同食材 × 不同做法" options when the user asks for a similar dish.
ANCHOR_SLOTS = 4


def _matches_anchor(dish: Dict[str, Any], anchor_ingredients: List[str]) -> bool:
    """True if any anchor ingredient appears in the dish's main_ingredients."""
    if not anchor_ingredients:
        return False
    dish_ings_lower = {i.lower() for i in dish.get("main_ingredients", [])}
    for term in anchor_ingredients:
        t = term.lower()
        if t in dish_ings_lower:
            return True
        for ing in dish_ings_lower:
            if t in ing or ing in t:   # substring match (e.g. "虾" ⊆ "虾仁")
                return True
    return False


def select_candidates(
    cuisine_weights: Optional[Dict[str, int]] = None,  # kept for API compat; no longer used
    disliked_ingredients: Optional[List[str]] = None,
    recent_dishes: Optional[List[Dict[str, Any]]] = None,
    anchor_ingredients: Optional[List[str]] = None,
    n: int = DEFAULT_CANDIDATE_COUNT,
) -> List[Dict[str, Any]]:
    """
    Select up to *n* candidate dishes with two-phase retrieval:

    Phase 1 — Anchored Retrieval (when anchor_ingredients is provided):
      Reserve up to ANCHOR_SLOTS (4) slots for dishes that share a main
      ingredient with the rejected dish.  This ensures the LLM sees
      "同食材 × 不同做法" options (e.g. 泰式咖喱虾, 蒜蓉粉丝蒸虾, 宫保虾仁)
      rather than random safe fallbacks (西红柿炒蛋).

    Phase 2 — Cuisine-fair rotation for remaining slots:
      Give every cuisine_l1 bucket MIN_PER_CUISINE (2) guaranteed slots,
      then fill the rest with globally top-scored un-picked dishes.

    Algorithm:
    1. Filter hard-banned (eaten ≤ 3 days) and disliked-ingredient dishes.
    2. Score purely by recency: score = 1 / (1 + recency_penalty)  + jitter.
    3. (If anchor_ingredients) fill up to ANCHOR_SLOTS with anchor-matching dishes.
    4. Fair rotation for remaining (n - anchor_count) slots.
    5. Guarantee at least one Soup entry if the pool contains any.
    """
    all_dishes = _load()
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

    # Step 2 — score each dish by recency only (no cuisine weight bias)
    scored: List[tuple[float, Dict[str, Any]]] = []
    for dish in pool:
        penalty = _recency_penalty(dish["name_zh"], dish["name_en"], recent)
        score = 1.0 / (1.0 + penalty)
        jitter = random.uniform(0.85, 1.15)
        scored.append((score * jitter, dish))

    scored.sort(key=lambda t: t[0], reverse=True)

    selected: List[Dict[str, Any]] = []
    selected_ids: set = set()

    # Step 3 (NEW) — Anchor phase: reserve up to ANCHOR_SLOTS for dishes that
    # share a main ingredient with the rejected dish so the LLM always has
    # "same ingredient × different cooking method" choices in view.
    _anchor = anchor_ingredients or []
    if _anchor:
        for _, dish in scored:
            if len(selected) >= ANCHOR_SLOTS:
                break
            if dish["id"] not in selected_ids and _matches_anchor(dish, _anchor):
                selected.append(dish)
                selected_ids.add(dish["id"])
        logger.info(
            "[SEED_LIBRARY] Anchor phase: %d/%d slots filled for ingredients=%s",
            len(selected), ANCHOR_SLOTS, _anchor,
        )

    # Remaining slots available for fair rotation
    remaining_slots = n - len(selected)

    # Step 4 — fair rotation: guarantee MIN_PER_CUISINE slots per cuisine_l1
    # a. Build per-cuisine sorted sublists
    from collections import defaultdict
    cuisine_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for _, dish in scored:
        if dish["id"] not in selected_ids:
            cuisine_buckets[dish["cuisine_l1"]].append(dish)

    # b. Guaranteed picks — take up to MIN_PER_CUISINE from each bucket
    for cuisine, bucket in cuisine_buckets.items():
        if len(selected) >= n:
            break
        count = 0
        for dish in bucket:
            if count >= MIN_PER_CUISINE or len(selected) >= n:
                break
            if dish["id"] not in selected_ids:
                selected.append(dish)
                selected_ids.add(dish["id"])
                count += 1

    # c. Fill remaining slots with globally top-scored un-picked dishes
    for _, dish in scored:
        if len(selected) >= n:
            break
        if dish["id"] not in selected_ids:
            selected.append(dish)
            selected_ids.add(dish["id"])

    candidates = selected[:n]

    # Step 4 — ensure at least one Soup if pool contains any
    has_soup = any(d["category"] == "Soup" for d in candidates)
    if not has_soup:
        soup_pool = [d for _, d in scored if d["category"] == "Soup" and d["id"] not in selected_ids]
        if soup_pool:
            candidates[-1] = soup_pool[0]

    cuisine_dist = {}
    for d in candidates:
        cuisine_dist[d["cuisine_l1"]] = cuisine_dist.get(d["cuisine_l1"], 0) + 1
    logger.info(
        "[SEED_LIBRARY] Selected %d candidates (pool=%d, cuisine_dist=%s)",
        len(candidates), len(pool), cuisine_dist,
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


def invalidate_cache() -> None:
    """Force _load() to re-fetch from HowToCookService on next call.

    Called by HowToCookService after a successful catalog refresh so that
    the seed library always reflects the latest data.
    """
    global _DISHES
    _DISHES = None


def lookup_dish(name: str) -> Optional[Dict[str, Any]]:
    """Return the catalog entry for a dish name, or None.

    Tries HowToCookService first (supports fuzzy match), then falls back
    to linear scan of the loaded catalog.
    """
    try:
        from app.services.howtocook_service import get_howtocook_service
        entry = get_howtocook_service().lookup_dish(name)
        if entry:
            return entry
    except Exception:
        pass
    for dish in _load():
        if name in (dish["name_zh"], dish["name_en"]):
            return dish
    return None
