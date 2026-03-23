"""
ExtractIngredientsSkill: extract "ingredients user has on hand" from a single
user message, along with an optional target date / meal-type hint.

Result feeds the Memory Layer (ActiveContext) so follow-up queries like
"葱花的话能做啥" can automatically reference previously mentioned ingredients
such as "牛棒骨".
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.skills.plan_ahead.base import LLMSkill

logger = logging.getLogger(__name__)


class ExtractIngredientsSkill(LLMSkill):
    SKILL_NAME = "ExtractIngredients"
    MAX_OUTPUT_TOKENS = 256
    TEMPERATURE = 0.0

    SKILL_PROMPT = """\
You are a kitchen ingredient extractor for a meal-planning assistant.

Read the user's message and extract:
1. "ingredients" — specific food ingredients or proteins they explicitly say they HAVE
   (e.g. "有个牛棒骨", "我有葱花", "剩了点鸡蛋", "有一块豆腐").
   • Only include items the user says they currently HAVE / possess.
   • Do NOT include items they merely want to eat or ask about (e.g. "我想吃海鲜" → not an ingredient).
   • Include the ingredient as-is (Chinese or English), trimmed.
2. "target_date" — the date they're planning for, as YYYY-MM-DD.
   Leave null if not mentioned or cannot be determined from context.
3. "target_meal_type" — "breakfast" | "lunch" | "dinner" | null.

IMPORTANT: if the user is NOT talking about ingredients they have on hand,
return an empty ingredients list — do not hallucinate.

Examples:
  "明天晚饭有个牛棒骨能做什么"   → {"ingredients":["牛棒骨"],"target_date":null,"target_meal_type":"dinner"}
  "葱花的话能做啥"               → {"ingredients":["葱花"],"target_date":null,"target_meal_type":null}
  "我今天午饭想吃海鲜"           → {"ingredients":[],"target_date":null,"target_meal_type":"lunch"}
  "帮我计划下今天吃什么"         → {"ingredients":[],"target_date":null,"target_meal_type":null}
  "我有很多葱花能做什么口味的"   → {"ingredients":["葱花"],"target_date":null,"target_meal_type":null}
  "有鸡蛋和番茄做什么好"         → {"ingredients":["鸡蛋","番茄"],"target_date":null,"target_meal_type":null}

Return ONLY valid JSON matching this schema:
{"ingredients": [...], "target_date": "YYYY-MM-DD or null", "target_meal_type": "...or null"}
"""

    async def execute(  # type: ignore[override]
        self,
        query: str,
        today_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extract ingredients the user mentions having on hand.

        Parameters
        ----------
        query       : Latest user message.
        today_date  : ISO date string for the current day (used for relative date resolution).

        Returns
        -------
        {
            "ingredients":     List[str],
            "target_date":     Optional[str],   # "YYYY-MM-DD" or None
            "target_meal_type": Optional[str],  # "breakfast"/"lunch"/"dinner"/None
        }
        """
        prefix = f"Today: {today_date}\n" if today_date else ""
        raw = await self._call(f'{prefix}User message: "{query}"')
        if raw:
            parsed = self._extract_json(raw)
            if parsed and isinstance(parsed.get("ingredients"), list):
                result = {
                    "ingredients": [str(i) for i in parsed["ingredients"] if i],
                    "target_date": parsed.get("target_date") or None,
                    "target_meal_type": parsed.get("target_meal_type") or None,
                }
                logger.info(
                    "[%s] query=%r → ingredients=%s target=%s %s",
                    self.SKILL_NAME, query,
                    result["ingredients"],
                    result.get("target_date"),
                    result.get("target_meal_type") or "",
                )
                return result

        # ── Keyword fallback ────────────────────────────────────────────────
        return self._keyword_fallback(query)

    @staticmethod
    def _keyword_fallback(query: str) -> Dict[str, Any]:
        """
        Lightweight heuristic: detect "I have <ingredient>" patterns.

        Handles the most common Chinese patterns without LLM:
          有个X / 有点X / 有一些X / 有很多X / 有剩的X / X的话能做啥
        """
        ingredients: List[str] = []

        # Pattern: 有(个|点|一些|很多|剩|块|条|颗|袋|盒|瓶)? + 2-8 char ingredient
        have_pat = re.findall(
            r"有(?:个|点|一些|很多|剩|块|条|颗|袋|盒|瓶)?([^\s，,。？?！!的话能可以，]{2,8})",
            query,
        )
        ingredients.extend(have_pat)

        # Pattern: "X的话" → X is the ingredient (stop before 的话)
        topic_pat1 = re.findall(r"([^\s，,。？?！!]{1,6})的话", query)
        ingredients.extend(topic_pat1)

        # Pattern: "X能做/可以做/怎么做" → X is the ingredient
        topic_pat2 = re.findall(
            r"([^\s，,。？?！!]{1,6})(?:能做|可以做|怎么做|用来做)", query
        )
        ingredients.extend(topic_pat2)

        # Deduplicate, strip whitespace
        seen: set = set()
        unique: List[str] = []
        for ing in ingredients:
            ing = ing.strip()
            if ing and ing not in seen:
                seen.add(ing)
                unique.append(ing)

        meal_type: Optional[str] = None
        q = query.lower()
        if any(k in q for k in ("早饭", "早餐", "早上", "breakfast")):
            meal_type = "breakfast"
        elif any(k in q for k in ("午饭", "午餐", "中午", "lunch")):
            meal_type = "lunch"
        elif any(k in q for k in ("晚饭", "晚餐", "晚上", "dinner")):
            meal_type = "dinner"

        if unique:
            logger.info(
                "[ExtractIngredients] keyword fallback: query=%r → %s", query, unique
            )
        return {
            "ingredients": unique,
            "target_date": None,
            "target_meal_type": meal_type,
        }
