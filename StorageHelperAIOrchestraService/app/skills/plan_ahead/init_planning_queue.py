# -*- coding: utf-8 -*-
"""
InitPlanningQueueSkill
=======================
Parses multi-slot meal-planning intent from the user's message and returns
a list of "YYYY-MM-DD|meal_type" slot strings.

Returns [] when no planning intent is detected (e.g. user is adding a specific
dish, querying, or asking something unrelated to planning).

Design principle
----------------
The LLM extracts *semantic meaning* (duration, offset, meal times) — it does NOT
need to compute absolute dates.  Python handles all date arithmetic.  This makes
the skill language-agnostic: it works equally well for Chinese, English, Japanese,
or any colloquial expression, because the LLM understands semantics, not patterns.

Output schema (internal)
------------------------
{
  "has_planning_intent": bool,

  // Option A — explicit named dates (user said "next Monday" / "April 5–7" etc.)
  "start":         "YYYY-MM-DD" | null,
  "end":           "YYYY-MM-DD" | null,

  // Option B — duration-based (user said "the next 3 days" / "后面三天" / etc.)
  "duration_days":      int | null,   // total number of days in the planning period
  "start_offset_days":  int,          // 0=starts today, 1=starts tomorrow (default for
                                      // "next/upcoming/future N days" expressions)

  "meal_times": List[str] | null,     // null = all three meals
}
→ converted to List["YYYY-MM-DD|meal_type"] by the skill
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .base import LLMSkill

logger = logging.getLogger(__name__)

_ALL_MEAL_TIMES = ["breakfast", "lunch", "dinner"]


class InitPlanningQueueSkill(LLMSkill):
    """
    Detect multi-slot meal-planning intent and expand it into a queue of slots.

    Used by Phase 1a of the pipeline to decide whether to start a planning queue
    (one LLM call per slot) or fall through to the main single-shot LLM call.
    """

    SKILL_NAME = "InitPlanningQueue"
    MAX_OUTPUT_TOKENS = 300
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "has_planning_intent":  {"type": "boolean"},
            "start":                {"type": "string",  "nullable": True},
            "end":                  {"type": "string",  "nullable": True},
            "duration_days":        {"type": "integer", "nullable": True},
            "start_offset_days":    {"type": "integer", "nullable": True},
            "meal_times":           {
                "type": "array",
                "items": {"type": "string"},
                "nullable": True,
            },
        },
        "required": ["has_planning_intent"],
    }

    # ── SKILL PROMPT ─────────────────────────────────────────────────────────
    SKILL_PROMPT_TEMPLATE = """\
Today is {today_str}.  You are a meal-planning intent parser.

Decide if the user wants to PLAN meals (generate new meal suggestions).
Then extract the date range and meal times using the semantic fields below.

──────────────────────────────────────────────────────
RESPOND WITH JSON ONLY.  No markdown, no explanation.
──────────────────────────────────────────────────────

Schema:
{{
  "has_planning_intent": true | false,

  // --- Date range (choose ONE of the two options below) ---

  // Option A: user named explicit dates or relative single-day anchors
  //   "start": "YYYY-MM-DD",   // first day of the planning period
  //   "end":   "YYYY-MM-DD",   // last day  (must be >= start)

  // Option B: user expressed a duration ("N days", "a week", "三天", etc.)
  //   "duration_days": <integer>,       // total number of days (e.g. 3)
  //   "start_offset_days": 0 or 1,     // 0 = period starts TODAY,
  //                                    // 1 = period starts TOMORROW
  //   (leave start/end null when using Option B)

  "meal_times": ["breakfast","lunch","dinner"] | null
  // null means all three meals; list specific ones if the user mentioned them
}}

─── WHEN TO USE OPTION B ────────────────────────────────────────────────────
Use duration_days + start_offset_days whenever the user describes a SPAN of
time by number rather than naming specific dates:
  • "the next 3 days" / "后面三天" / "接下来三天" / "次の3日" / "les 3 prochains jours"
    → duration_days=3, start_offset_days=1   (starts TOMORROW — "next" means future)
  • "next week" / "下周" / "来週"
    → duration_days=7, start_offset_days=1
  • "today's meals" / "今天三餐" / "今日の食事"
    → duration_days=1, start_offset_days=0   (starts TODAY)
  • "this weekend" / "这个周末"
    → duration_days=2, start_offset_days=<days until Saturday>

The key semantic distinction:
  start_offset_days=0  →  period INCLUDES today  ("today", "now", "这周")
  start_offset_days=1  →  period starts TOMORROW  ("next N days", "后面", "接下来",
                           "upcoming", "going forward", "from tomorrow", "以后")

─── WHEN TO USE OPTION A ────────────────────────────────────────────────────
Use start/end when the user names SPECIFIC dates or day-of-week anchors:
  • "next Monday" / "下周一" → start=next_monday, end=next_monday
  • "April 5 to 7" / "4月5日から7日" → start=2026-04-05, end=2026-04-07
  • "tomorrow" / "明天" → start=tomorrow, end=tomorrow
  • "today" / "今天" → start=today, end=today
  • "this week Mon–Fri" → start=this_monday, end=this_friday

─── NOT A PLANNING REQUEST ──────────────────────────────────────────────────
Return has_planning_intent: false when:
• User is VIEWING, DELETING, or MODIFYING an existing plan (not creating new meals).
• User names a SPECIFIC DISH (e.g. "红烧肉", "fish tacos", "pasta carbonara").
  — Naming a food category ("seafood", "海鲜", "something spicy") is NOT a specific
    dish; treat it as a planning request with a preference.
• User says "add X to …" / "再加个X" / "replace X with Y" — single-slot ops.

─── MEAL TIMES ──────────────────────────────────────────────────────────────
Values: breakfast, lunch, dinner.
• If the user mentions a specific meal (breakfast / 早餐 / 早饭 / 朝ごはん / petit-déjeuner,
  lunch / 午餐 / 昼食, dinner / 晚餐 / 夕食), set meal_times accordingly.
• If no meal time is mentioned, set meal_times: null  (means all three meals).
"""

    # ─────────────────────────────────────────────────────────────────────────

    async def execute(
        self,
        user_input: str,
        today_str: str,  # "YYYY-MM-DD (Weekday)"
        *,
        today_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Parse planning intent and return slot list + raw LLM result.

        Returns
        -------
        {
          "has_planning_intent": bool,
          "slots": List[str],   # ["YYYY-MM-DD|meal_type", ...]
          "raw":   dict,        # raw LLM parsed object (for debugging)
        }
        """
        prompt = self.SKILL_PROMPT_TEMPLATE.format(today_str=today_str)
        raw = await self._call(user_input, system_prompt_override=prompt, timeout=10.0)

        if not raw:
            logger.warning("[%s] LLM failed, returning no-intent", self.SKILL_NAME)
            return {"has_planning_intent": False, "slots": [], "raw": {}}

        parsed = self._extract_json(raw)
        if not parsed:
            logger.warning(
                "[%s] JSON parse failed for response: %r", self.SKILL_NAME, raw[:100]
            )
            return {"has_planning_intent": False, "slots": [], "raw": {}}

        logger.info("[%s] LLM result: %s", self.SKILL_NAME, parsed)

        if not parsed.get("has_planning_intent"):
            return {"has_planning_intent": False, "slots": [], "raw": parsed}

        # ── Date range resolution ─────────────────────────────────────────────
        ref_today = today_date or date.today()
        start_date, end_date = self._resolve_date_range(parsed, ref_today)

        # ── Meal times ────────────────────────────────────────────────────────
        meal_times_raw = parsed.get("meal_times") or None
        meal_times: List[str] = (
            [m for m in meal_times_raw if m in _ALL_MEAL_TIMES]
            if isinstance(meal_times_raw, list) and meal_times_raw
            else _ALL_MEAL_TIMES
        )

        # ── Build slot list ───────────────────────────────────────────────────
        slots: List[str] = []
        cur = start_date
        while cur <= end_date:
            for mt in meal_times:
                slots.append(f"{cur.strftime('%Y-%m-%d')}|{mt}")
            cur += timedelta(days=1)

        logger.info(
            "[%s] → %d slots: %s",
            self.SKILL_NAME,
            len(slots),
            slots[:9],
        )
        return {"has_planning_intent": True, "slots": slots, "raw": parsed}

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_date_range(
        parsed: Dict[str, Any],
        ref_today: date,
    ) -> tuple[date, date]:
        """
        Convert LLM output into (start_date, end_date).

        Priority:
          1. Option B — duration_days + start_offset_days  (language-agnostic)
          2. Option A — explicit start / end strings
          3. Fallback  — today only
        """
        duration_days: Optional[int] = parsed.get("duration_days")
        start_offset: int = parsed.get("start_offset_days") or 0

        # Option B: duration-based (handles any language's "next N days" expression)
        if duration_days and duration_days > 0:
            start_date = ref_today + timedelta(days=start_offset)
            end_date = start_date + timedelta(days=duration_days - 1)
            return start_date, end_date

        # Option A: explicit date strings
        start_str = (parsed.get("start") or "")[:10]
        end_str = (parsed.get("end") or "")[:10]
        try:
            start_date = date.fromisoformat(start_str) if start_str else ref_today
            end_date = date.fromisoformat(end_str) if end_str else start_date
        except ValueError:
            start_date = end_date = ref_today

        if end_date < start_date:
            end_date = start_date

        return start_date, end_date
