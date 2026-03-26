"""
DiversityEngine — Phase 2: Recency-Penalty Variety Algorithm
=============================================================

Prevents the LLM from recommending the same dish within a rolling
14-day window by injecting a diversity directive into the system prompt.

Algorithm (Phase 2 spec):

  P(dish) ∝  W_cuisine / (1 + Penalty(t))

  where:
    t           = days since the dish was last eaten
    Penalty(t)  = max(0, (WINDOW_DAYS - t) / WINDOW_DAYS)
    Penalty(0)  = 1.0  (ate today — hard ban)
    Penalty(7)  = 0.5  (mild discourage)
    Penalty(14) = 0.0  (fully cleared — no penalty)

Because the LLM produces free-form dish names (not picks from a fixed DB),
the probability score is translated into a plain-text directive:

  • HARD BAN  (eaten ≤ HARD_BAN_DAYS ago)   → "DO NOT recommend"
  • SOFT AVOID (eaten ≤ SOFT_AVOID_DAYS ago) → "prefer alternatives"
  • INGREDIENT SOFT AVOID                    → key ingredients stored at commit time
  • WEEKLY VARIETY TARGET                    → from cuisine_weights

Ingredient-level diversity
--------------------------
Each recent_dishes entry may carry an optional "ingredients" list.  This list
is populated at plan-commit time from the already-resolved dish_ingredients
mapping — so it is language-agnostic and requires no keyword scanning.

When hard-banned dishes carry ingredient lists, the engine emits an
INGREDIENT SOFT AVOID line so the LLM avoids re-using the same dominant
ingredients in new recommendations (e.g. if 番茄炖排骨 is hard-banned and
its ingredients include "番茄", a new 番茄牛腩汤 would be soft-discouraged).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Rolling-window constants ─────────────────────────────────────────────────
WINDOW_DAYS: int = 14      # Full penalty-clear horizon (days)
HARD_BAN_DAYS: int = 3     # Dishes eaten ≤ this many days ago → forbidden
SOFT_AVOID_DAYS: int = 7   # Dishes eaten ≤ this many days ago → discouraged


# ── Internal helpers ─────────────────────────────────────────────────────────

def _parse_date(date_str: str) -> Optional[date]:
    """Parse a YYYY-MM-DD string. Returns None on failure."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _days_since(date_str: str, today: date) -> int:
    """Days between date_str and today, clamped to [0, ∞)."""
    d = _parse_date(date_str)
    if d is None:
        return WINDOW_DAYS + 1   # Unparseable → treat as fully cleared
    return max(0, (today - d).days)


def _collect_banned_ingredients(
    recent_dishes: List[Dict[str, Any]],
    hard_banned_names: List[str],
) -> List[str]:
    """
    Collect ingredient names from entries whose dish appears in hard_banned_names.

    Returns a de-duplicated list preserving first-seen order.
    Each entry may have an "ingredients" field: List[str | dict].
    dict format: {"name": str, ...} (same shape as dish_ingredients values).
    """
    banned_set = set(hard_banned_names)
    seen: set = set()
    result: List[str] = []

    for entry in recent_dishes:
        if entry.get("dish") not in banned_set:
            continue
        for ing in (entry.get("ingredients") or []):
            name: str = ""
            if isinstance(ing, str):
                name = ing.strip()
            elif isinstance(ing, dict):
                name = (ing.get("name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def recency_penalty(days: int) -> float:
    """
    Penalty score in [0.0, 1.0].
      days=0  → 1.0 (maximum)
      days=7  → 0.5
      days=14 → 0.0 (cleared)
    """
    return max(0.0, (WINDOW_DAYS - days) / WINDOW_DAYS)


def compute_diversity_directive(
    recent_dishes: Optional[List[Dict[str, Any]]],
    cuisine_weights: Optional[Dict[str, int]] = None,
    today: Optional[date] = None,
) -> str:
    """
    Produce a natural-language diversity directive for the LLM system prompt.

    Args:
        recent_dishes:   List of {"dish": str, "date": "YYYY-MM-DD",
                         "ingredients": List[str|dict] (optional)} records.
        cuisine_weights: Optional {cuisine_name: weight_pct} for variety hints.
        today:           Today's date (injectable for deterministic testing).

    Returns:
        Multi-line string ready to embed inside === DIVERSITY ENGINE === block.
    """
    if today is None:
        today = date.today()

    hard_banned: List[str] = []
    soft_discouraged: List[Tuple[str, int]] = []  # (dish_name, days_ago)
    seen: set = set()

    for entry in (recent_dishes or []):
        dish = (entry.get("dish") or "").strip()
        date_str = (entry.get("date") or "").strip()
        if not dish or not date_str or dish in seen:
            continue
        seen.add(dish)
        days = _days_since(date_str, today)
        if days <= HARD_BAN_DAYS:
            hard_banned.append(dish)
        elif days <= SOFT_AVOID_DAYS:
            soft_discouraged.append((dish, days))

    lines: List[str] = []

    if hard_banned:
        lines.append(
            f"- HARD BAN (eaten within the last {HARD_BAN_DAYS} days — "
            f"DO NOT recommend): {', '.join(hard_banned)}."
        )

    if soft_discouraged:
        avoid_str = ", ".join(f"{d} ({n}d ago)" for d, n in soft_discouraged)
        lines.append(
            f"- SOFT AVOID (eaten {HARD_BAN_DAYS + 1}–{SOFT_AVOID_DAYS} days ago — "
            f"choose alternatives when possible): {avoid_str}."
        )

    # Ingredient-level soft avoid: use stored ingredients from hard-banned entries.
    # Ingredients are recorded at plan-commit time from dish_ingredients, so they
    # are language-agnostic and require no keyword scanning.
    if hard_banned:
        banned_ingredients = _collect_banned_ingredients(recent_dishes or [], hard_banned)
        if banned_ingredients:
            lines.append(
                f"- INGREDIENT SOFT AVOID (key ingredients that dominated recently "
                f"eaten dishes — avoid making these the star of a new dish when "
                f"alternatives exist): {', '.join(banned_ingredients)}."
            )

    if not hard_banned and not soft_discouraged:
        lines.append("- No recent dish history — feel free to recommend any dishes.")

    # Cuisine weekly variety targets derived from weights
    if cuisine_weights:
        total = sum(
            v for v in cuisine_weights.values()
            if isinstance(v, (int, float)) and v > 0
        )
        if total > 0:
            sorted_cw = sorted(
                [
                    (k, v)
                    for k, v in cuisine_weights.items()
                    if isinstance(v, (int, float)) and v > 0
                ],
                key=lambda x: -x[1],
            )
            variety_hints: List[str] = []
            for cuisine, pct in sorted_cw:
                expected = round(pct / 100 * 7)
                if expected >= 1:
                    variety_hints.append(
                        f"{cuisine} (~{expected} dishes/week, {pct}% weight)"
                    )
            if variety_hints:
                lines.append(
                    "- WEEKLY VARIETY TARGET (spread dishes to match cuisine weights): "
                    + ", ".join(variety_hints) + "."
                )

    directive = "\n".join(lines)
    logger.debug("[DIVERSITY_ENGINE] directive:\n%s", directive)
    return directive


def extract_dishes_for_history(
    meal_plan_slots: Optional[Dict[str, Any]],
    dish_ingredients: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    Extract dish entries from a committed meal_plan_slots for recent_dishes.

    Only dates within the WINDOW_DAYS rolling window are included (future
    planned dates are included too — they count once reached).

    Args:
        meal_plan_slots:  {date_str: {meal_time: dish_list | str}}.
        dish_ingredients: Optional {dish_name: List[str | dict]} mapping from
                          the plan state.  When provided, each entry gains an
                          "ingredients" field so the diversity engine can emit
                          ingredient-level soft-avoids on future turns.
        today:            Override for deterministic testing.

    Returns:
        List of {"dish": str, "date": "YYYY-MM-DD",
                 "ingredients": List[str]} records.
        The "ingredients" key is omitted when dish_ingredients is not provided
        or the dish has no recorded ingredients.
    """
    if today is None:
        today = date.today()
    cutoff = today - timedelta(days=WINDOW_DAYS)

    entries: List[Dict[str, Any]] = []
    for date_str, slots in (meal_plan_slots or {}).items():
        d = _parse_date(date_str)
        if d is None or d < cutoff:
            continue
        for _meal_time, dishes in (slots or {}).items():
            dish_names: List[str] = []
            if isinstance(dishes, list):
                dish_names = [n.strip() for n in dishes if n and isinstance(n, str) and n.strip()]
            elif isinstance(dishes, str) and dishes.strip():
                dish_names = [dishes.strip()]

            for name in dish_names:
                entry: Dict[str, Any] = {"dish": name, "date": date_str}
                if dish_ingredients:
                    raw = dish_ingredients.get(name) or []
                    # Normalise to plain strings for compact storage.
                    ing_names: List[str] = []
                    for ing in raw:
                        if isinstance(ing, str) and ing.strip():
                            ing_names.append(ing.strip())
                        elif isinstance(ing, dict):
                            n = (ing.get("name") or "").strip()
                            if n:
                                ing_names.append(n)
                    if ing_names:
                        entry["ingredients"] = ing_names
                entries.append(entry)
    return entries


def merge_and_prune_recent_dishes(
    existing: Optional[List[Dict[str, Any]]],
    new_entries: List[Dict[str, Any]],
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    Merge new entries into existing recent_dishes and prune >WINDOW_DAYS old records.

    Args:
        existing:    Current recent_dishes from DB (may be None or []).
        new_entries: Newly committed dishes to append.
        today:       Override for deterministic testing.

    Returns:
        Merged + pruned list, safe to write back to DB.
    """
    if today is None:
        today = date.today()
    cutoff = today - timedelta(days=WINDOW_DAYS)

    merged: List[Dict[str, Any]] = list(existing or [])
    seen_keys: set = {(e.get("dish", ""), e.get("date", "")) for e in merged}

    for entry in new_entries:
        key = (entry.get("dish", ""), entry.get("date", ""))
        if key not in seen_keys:
            merged.append(entry)
            seen_keys.add(key)

    return [
        e for e in merged
        if _parse_date(e.get("date", "")) is not None
        and _parse_date(e.get("date", "")) >= cutoff
    ]
