# -*- coding: utf-8 -*-
"""
ClassifyDateConfirmationSkill
==============================
Classifies the user's reply to a date-range confirmation prompt.

Output schema
-------------
{
  "intent":        "confirmed" | "corrected" | "unclear",
  "new_dates":     List[str],       # YYYY-MM-DD list (for corrected + date change)
  "new_meal_times": List[str] | None,  # for corrected + meal-scope change
}

Key rule (extracted from prompt for easy tuning):
  "corrected" requires the user to be ACTIVELY changing the proposed plan dates.
  A reply that merely MENTIONS a date (e.g. "我明天要去旅游") is NOT a correction → unclear.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .base import LLMSkill

logger = logging.getLogger(__name__)

# ── Layer-2 fast-path patterns (no LLM needed for trivial cases) ──────────────
_AFFIRM_RE = re.compile(
    r"^(好|行|是|对|没问题|可以|当然|开始|嗯|ok|yes|sure|right|好的|好啊|好呀|确定|确认|"
    r"就这样|就这个|没错|嗯嗯|嗯哦|嗯啊|嗯对|嗯行|嗯好|嗯是)[！!。.，,\s]*$",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"^(不|不行|不对|不好|不是|不要|算了|取消|cancel|no)[！!。.，,\s]*$",
    re.IGNORECASE,
)


class ClassifyDateConfirmationSkill(LLMSkill):
    """Classify user reply to a meal-plan date confirmation prompt."""

    SKILL_NAME = "ClassifyDateConfirmation"
    MAX_OUTPUT_TOKENS = 350
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "new_start": {"type": "string", "nullable": True},
            "new_end": {"type": "string", "nullable": True},
            "new_meal_times": {
                "type": "array",
                "items": {"type": "string"},
                "nullable": True,
            },
        },
        "required": ["intent"],
    }

    # ── SKILL PROMPT ─────────────────────────────────────────────────────────
    # Note: today_str and pending info are injected at call-time via user_message.
    SKILL_PROMPT = """\
You are a classifier for a meal-planning chatbot.
The chatbot proposed a meal plan (dates + meals) and asked the user to confirm.

Classify the user's reply as one of:
  "confirmed" — user accepts the proposed plan as-is
  "corrected" — user wants different dates OR different meal types
  "unclear"   — cannot determine intent

Output JSON with these fields:
  intent: string  (required)
  new_start: string | null  — YYYY-MM-DD, set when user specifies a new start date
  new_end:   string | null  — YYYY-MM-DD, set when user specifies a new end date
  new_meal_times: [string] | null  — subset of ['breakfast','lunch','dinner'],
                  null means 'all three meals', set when user changes meal scope

Rules:
- Affirmative (好/行/是/对/没问题/可以/当然/开始/嗯/ok/yes/sure/right) → confirmed
- User EXPLICITLY corrects the dates (不对是明天/改成下周/换成周六/etc.) → corrected,
  fill new_start+new_end
- User changes meal scope (一整天/三餐/一日三餐/全天/只要午餐/etc.) → corrected,
  fill new_meal_times (null = all meals; ['dinner'] = dinner only; etc.)
- Both date AND meal change → corrected, fill all applicable fields
- IMPORTANT: A reply that merely MENTIONS a date is NOT a date correction.
  "我明天要出差", "这几天不在", "I'm traveling tomorrow" → classify as unclear.
  'corrected' requires the user to be ACTIVELY changing the proposed plan dates.
- Ambiguous/off-topic/irrelevant context → unclear

Examples:
  Proposed dinner only → user says "一整天"              → {"intent":"corrected","new_meal_times":null}
  Proposed dinner only → user says "三餐都要"            → {"intent":"corrected","new_meal_times":null}
  Proposed today       → user says "不对，是明天"        → {"intent":"corrected","new_start":"YYYY-MM-DD","new_end":"YYYY-MM-DD"}
  Proposed 7 days      → user says "没问题"              → {"intent":"confirmed"}
  Proposed 7 days      → user says "开始吧"              → {"intent":"confirmed"}
  Proposed Mon-Tue     → user says "我明天要去旅游"      → {"intent":"unclear"}
  Proposed Mon-Tue     → user says "这几天我不在家"      → {"intent":"unclear"}

Return JSON only — no markdown, no extra text.
"""

    # ─────────────────────────────────────────────────────────────────────────

    async def execute(
        self,
        user_input: str,
        pending_queue: List[str],
        today_str: str,  # "YYYY-MM-DD"
    ) -> Dict[str, Any]:
        """
        Classify the user's reply to a date-confirmation prompt.

        Parameters
        ----------
        user_input    : User's reply.
        pending_queue : List of "YYYY-MM-DD|meal_type" slot strings.
        today_str     : Current date as "YYYY-MM-DD" (for relative date resolution).
        """
        if not user_input:
            return {"intent": "unclear", "new_dates": [], "new_meal_times": None}

        # ── Layer-2 fast-path (trivially clear cases, no LLM needed) ────────
        stripped = user_input.strip()
        if _AFFIRM_RE.match(stripped):
            logger.debug("[%s] L2 fast-path: confirmed for %r", self.SKILL_NAME, stripped[:40])
            return {"intent": "confirmed", "new_dates": [], "new_meal_times": None}
        if _NEGATIVE_RE.match(stripped):
            logger.debug("[%s] L2 fast-path: unclear (negative) for %r", self.SKILL_NAME, stripped[:40])
            return {"intent": "unclear", "new_dates": [], "new_meal_times": None}

        # ── Build user message with context ──────────────────────────────────
        pending_start = (pending_queue[0].split("|")[0] if pending_queue else "")
        pending_end = (pending_queue[-1].split("|")[0] if pending_queue else "")
        pending_meals = list(dict.fromkeys(
            s.split("|")[1] for s in pending_queue if "|" in s
        ))
        n_slots = len(pending_queue)
        system_override = f"Today is {today_str}.\n" + self.SKILL_PROMPT

        user_msg = (
            f"Proposed: {pending_start} to {pending_end} "
            f"(meals: {', '.join(pending_meals) or 'all'}, {n_slots} slot(s)).\n"
            f'User reply: "{user_input}"'
        )

        raw = await self._call(
            user_msg,
            system_prompt_override=system_override,
            timeout=10.0,
        )
        if not raw:
            return {"intent": "unclear", "new_dates": [], "new_meal_times": None}

        parsed = self._extract_json(raw)
        if not parsed:
            return {"intent": "unclear", "new_dates": [], "new_meal_times": None}

        intent = str(parsed.get("intent", "unclear")).lower().strip()
        if intent not in ("confirmed", "corrected", "unclear"):
            intent = "unclear"

        if intent == "corrected":
            new_dates = self._parse_new_dates(
                parsed.get("new_start"), parsed.get("new_end"), today_str
            )
            raw_mt = parsed.get("new_meal_times")
            new_meal_times: Optional[List[str]] = None
            if isinstance(raw_mt, list) and raw_mt:
                new_meal_times = raw_mt
            elif raw_mt == [] or raw_mt is None:
                new_meal_times = None

            logger.info(
                "[%s] intent=corrected new_dates=%r new_meal_times=%r for %r",
                self.SKILL_NAME, new_dates, new_meal_times, user_input[:60],
            )
            return {"intent": "corrected", "new_dates": new_dates, "new_meal_times": new_meal_times}

        logger.info("[%s] intent=%s for %r", self.SKILL_NAME, intent, user_input[:60])
        return {"intent": intent, "new_dates": [], "new_meal_times": None}

    @staticmethod
    def _parse_new_dates(
        new_start: Optional[str],
        new_end: Optional[str],
        today_str: str,
    ) -> List[str]:
        """Expand new_start → new_end into a list of YYYY-MM-DD strings."""
        if not new_start or not new_end:
            return []
        try:
            s = date.fromisoformat(new_start[:10])
            e = date.fromisoformat(new_end[:10])
            if e < s:
                return []
            return [
                (s + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range((e - s).days + 1)
            ]
        except ValueError:
            return []
