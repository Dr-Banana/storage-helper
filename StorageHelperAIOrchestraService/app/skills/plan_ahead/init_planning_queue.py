# -*- coding: utf-8 -*-
"""
InitPlanningQueueSkill
=======================
Parses multi-slot meal-planning intent from the user's message and returns
a list of "YYYY-MM-DD|meal_type" slot strings.

Returns [] when no planning intent is detected (e.g. user is adding a specific
dish, querying, or asking something unrelated to planning).

Output schema (internal)
------------------------
{
  "has_planning_intent": bool,
  "start":      "YYYY-MM-DD" | None,
  "end":        "YYYY-MM-DD" | None,
  "meal_times": List[str] | None,   # null = all three meals
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
    MAX_OUTPUT_TOKENS = 256
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "has_planning_intent": {"type": "boolean"},
            "start":      {"type": "string", "nullable": True},
            "end":        {"type": "string", "nullable": True},
            "meal_times": {"type": "array", "items": {"type": "string"}, "nullable": True},
        },
        "required": ["has_planning_intent"],
    }

    # ── SKILL PROMPT ─────────────────────────────────────────────────────────
    # today_str is injected at call-time.
    SKILL_PROMPT_TEMPLATE = """\
Today is {today_str}. You are a date-range parser for a meal-planning app.
Decide if the user wants to PLAN meals (not view/delete/modify existing plans).
If yes, extract the date range and specific meal times (if mentioned).

Respond with JSON only — no markdown, no explanation.
Format:
  {{"has_planning_intent": false}}  — not a planning request
  {{"has_planning_intent": true, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "meal_times": null}}  — plan all meals
  {{"has_planning_intent": true, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "meal_times": ["dinner"]}}  — specific meals

meal_times values: breakfast, lunch, dinner

Rules:
- view/delete/modify requests → has_planning_intent: false
- CRITICAL RULE — 'ADD/MODIFY ACTION = NOT PLANNING': If the user's message
  contains an explicit add/remove/modify verb targeting a specific meal slot or dish
  (e.g. 再加个, 加一个, 帮我加, 不要XX了, 把XX换成, 删掉, 去掉), you MUST return
  has_planning_intent: false. These are single-slot operations, not fresh meal planning.
  Examples: "晚饭再加个清炒蔬菜" → false; "把米饭换成馒头" → false; "不要蔬菜了" → false.
- CRITICAL RULE — 'HAS DISH = NOT PLANNING': If the user's message contains a
  SPECIFIC DISH NAME (a concrete food item, e.g. 芋艿猪排骨, 红烧肉, 火锅, 水煮鱼,
  小笼包, 饺子, 清炒蔬菜, 清炒XX, 红烧XX), you MUST return has_planning_intent: false.
  Examples: "今天晚上吃芋艿猪排骨" → false; "做个红烧肉" → false; "吃个小笼包" → false;
  "晚饭再加个清炒蔬菜" → false.
  Contrast: "今天晚上吃什么" → true; "帮我规划今天三餐" → true.
- Food categories (海鲜, 肉, 蔬菜, 辣的, 清淡, 日式, 家常菜) are NOT specific dishes.
  A preference like "我想吃个海鲜" IS a planning request → has_planning_intent: true.
  However "再加个清炒蔬菜" is a SPECIFIC DISH (even though 蔬菜 is a category, 清炒蔬菜
  is a concrete dish name) combined with an add verb → has_planning_intent: false.
- If user asks AI to SUGGEST or PLAN meals without naming a specific dish →
  has_planning_intent: true, meal_times must reflect mentioned meal time
  (晚上/晚餐/dinner → ['dinner']; 早上/早餐 → ['breakfast']; 中午/午餐 → ['lunch']);
  if no meal time mentioned, use null.
- "今天的午餐" → start=today, end=today, meal_times: ['lunch']
- "今天晚上/今晚/晚上" → start=today, end=today, meal_times: ['dinner']
- "今天早上/今早" → start=today, end=today, meal_times: ['breakfast']
- "今天" / "今天的计划" / "今天三餐" → start=today, end=today, meal_times: null
- "明天" → start=tomorrow, end=tomorrow, meal_times: null
- "下周" / "next week" → Mon–Sun of next calendar week, meal_times: null
- "这周" / "this week" → today through this Sunday, meal_times: null
- CRITICAL — MULTI-DAY: If the user mentions TWO OR MORE specific days, set 'end'
  to the LAST day mentioned. NEVER collapse multiple days into a single date.
  Example: "下周五周六" → start=next_Friday, end=next_Saturday
- CRITICAL: If user mentions 晚上/今晚/晚餐/晚饭, meal_times MUST be ['dinner'].
  If 早上/早餐/早饭, must be ['breakfast']. If 中午/午餐/午饭, must be ['lunch'].
  NEVER return meal_times: null when a specific meal time was mentioned.
- end date must be >= start date
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
            logger.warning("[%s] JSON parse failed for response: %r", self.SKILL_NAME, raw[:100])
            return {"has_planning_intent": False, "slots": [], "raw": {}}

        logger.info("[%s] LLM result: %s", self.SKILL_NAME, parsed)

        if not parsed.get("has_planning_intent"):
            return {"has_planning_intent": False, "slots": [], "raw": parsed}

        # Resolve reference date
        ref_today = today_date or date.today()

        start_str = (parsed.get("start") or "")[:10]
        end_str = (parsed.get("end") or "")[:10]
        meal_times_raw = parsed.get("meal_times") or None

        try:
            start_date = date.fromisoformat(start_str) if start_str else ref_today
            end_date = date.fromisoformat(end_str) if end_str else start_date
        except ValueError:
            start_date = end_date = ref_today

        if end_date < start_date:
            end_date = start_date

        meal_times: List[str] = (
            [m for m in meal_times_raw if m in _ALL_MEAL_TIMES]
            if isinstance(meal_times_raw, list) and meal_times_raw
            else _ALL_MEAL_TIMES
        )

        slots: List[str] = []
        cur = start_date
        while cur <= end_date:
            for mt in meal_times:
                slots.append(f"{cur.strftime('%Y-%m-%d')}|{mt}")
            cur += timedelta(days=1)

        logger.info("[%s] → %d slots: %s", self.SKILL_NAME, len(slots), slots[:6])
        return {"has_planning_intent": True, "slots": slots, "raw": parsed}
