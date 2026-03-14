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
  • WEEKLY VARIETY TARGET                    → from cuisine_weights
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
        recent_dishes:   List of {"dish": str, "date": "YYYY-MM-DD"} records
                         from the user's recent_dishes DB field.
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
    today: Optional[date] = None,
) -> List[Dict[str, str]]:
    """
    Extract dish entries from a committed meal_plan_slots for recent_dishes.

    Only dates within the WINDOW_DAYS rolling window are included (future
    planned dates are included too — they count once reached).

    Args:
        meal_plan_slots: {date_str: {meal_time: dish_list | str}}.
        today:           Override for deterministic testing.

    Returns:
        List of {"dish": str, "date": "YYYY-MM-DD"} records.
    """
    if today is None:
        today = date.today()
    cutoff = today - timedelta(days=WINDOW_DAYS)

    entries: List[Dict[str, str]] = []
    for date_str, slots in (meal_plan_slots or {}).items():
        d = _parse_date(date_str)
        if d is None or d < cutoff:
            continue
        for _meal_time, dishes in (slots or {}).items():
            if isinstance(dishes, list):
                for name in dishes:
                    if name and isinstance(name, str) and name.strip():
                        entries.append({"dish": name.strip(), "date": date_str})
            elif isinstance(dishes, str) and dishes.strip():
                entries.append({"dish": dishes.strip(), "date": date_str})
    return entries


def merge_and_prune_recent_dishes(
    existing: Optional[List[Dict[str, str]]],
    new_entries: List[Dict[str, str]],
    today: Optional[date] = None,
) -> List[Dict[str, str]]:
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

    merged: List[Dict[str, str]] = list(existing or [])
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
