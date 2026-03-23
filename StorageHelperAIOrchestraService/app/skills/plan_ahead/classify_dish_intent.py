# -*- coding: utf-8 -*-
"""
ClassifyDishIntentSkill
=======================
Decides whether the user named a **specific dish** (EXPLICIT) or expressed a
**preference / category** that should trigger recommendations (RECOMMEND).

Prompt lives HERE — not buried inside plan_ahead_pipeline.py.
To tune the classifier, edit SKILL_PROMPT below.

Output schema
-------------
{
  "intent":      "EXPLICIT" | "RECOMMEND" | "UNKNOWN",
  "is_explicit": bool,          # True only when intent=EXPLICIT AND dishes is non-empty
  "dishes":      List[str],     # extracted dish names ([] for RECOMMEND)
}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import LLMSkill

logger = logging.getLogger(__name__)

# ── Keyword-based fallback (used when the LLM call fails) ────────────────────
_EXPLICIT_KEYWORDS = (
    "加", "加个", "加上", "加一个",
    "吃个", "吃一个",
    "做", "做个", "做一个",
    "换成", "换个", "改成",
    "add", "want", "have",
)

# Food categories that are PREFERENCES, not dish names.
# If the LLM incorrectly returns one of these as a dish, we downgrade to RECOMMEND.
_FOOD_CATEGORY_WORDS = frozenset([
    "海鲜", "肉", "肉类", "蔬菜", "素食", "辣的", "辣", "清淡", "甜的",
    "日式", "川菜", "粤菜", "西餐", "韩餐", "东南亚", "家常菜", "快手菜",
    "seafood", "meat", "vegan", "vegetarian", "spicy", "light",
])


class ClassifyDishIntentSkill(LLMSkill):
    """Classify user query: is the user naming a dish or requesting suggestions?"""

    SKILL_NAME = "ClassifyDishIntent"
    MAX_OUTPUT_TOKENS = 200

    # ── SKILL PROMPT ─────────────────────────────────────────────────────────
    SKILL_PROMPT = """\
You are a meal-planning intent classifier.
Analyze the user's latest message and return a JSON object with exactly two keys:
  "intent": "EXPLICIT" if the user names a SPECIFIC dish they want to ADD or EAT,
            or "RECOMMEND" if they ask the AI to suggest/plan/replace a dish.
  "dishes": an array of the explicitly named dish names (empty array [] for RECOMMEND).

CRITICAL RULE 1 — "Replace X with something similar" → RECOMMEND:
  When the user names a dish in order to REPLACE / SWAP / CHANGE it to something else,
  that dish is the SOURCE (being removed), NOT the TARGET.
  Return RECOMMEND with an empty dishes list.

  Pattern triggers (any of these mean the named dish is the one being removed):
    "把X换成另一道/其他的/类似的/别的"
    "把X改成其他菜"
    "换掉X"
    "X换一道"
    "replace X with something"
    "swap X for another dish"
    "change X to something similar/else"

  Examples of this pattern:
    "把'萝卜牛骨汤'换成另一道类似的菜，其他菜品保持不变"
                                    → {"intent":"RECOMMEND","dishes":[]}
    "请把'牛大棒骨汤'换成另一道类似的菜" → {"intent":"RECOMMEND","dishes":[]}
    "把宫保鸡丁换掉，换个其他的"        → {"intent":"RECOMMEND","dishes":[]}

CRITICAL RULE 2 — Food categories/preferences → RECOMMEND:
  海鲜, 肉, 蔬菜, 辣的, 清淡, 减脂, 日式, 川菜, 西餐, 家常菜, seafood, meat, spicy, light
  → These describe a style or category, not a dish name.

Standard EXPLICIT examples (user names the dish they WANT TO ADD):
  "加个萝卜炖牛腩"          → {"intent":"EXPLICIT","dishes":["萝卜炖牛腩"]}
  "换成照烧鸡腿"            → {"intent":"EXPLICIT","dishes":["照烧鸡腿"]}
  "想吃个小笼包"            → {"intent":"EXPLICIT","dishes":["小笼包"]}
  "今晚吃宫保鸡丁"          → {"intent":"EXPLICIT","dishes":["宫保鸡丁"]}

Standard RECOMMEND examples:
  "帮我计划今晚吃什么"      → {"intent":"RECOMMEND","dishes":[]}
  "给我推荐几个菜"          → {"intent":"RECOMMEND","dishes":[]}
  "我今天晚上想吃个海鲜"    → {"intent":"RECOMMEND","dishes":[]}
  "想吃辣的东西"            → {"intent":"RECOMMEND","dishes":[]}
  "今晚整个日式的"          → {"intent":"RECOMMEND","dishes":[]}

Return ONLY the JSON object — no markdown, no extra text.
"""

    # ─────────────────────────────────────────────────────────────────────────

    async def execute(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Classify the dish intent for *query*.

        Returns
        -------
        {
          "intent":      "EXPLICIT" | "RECOMMEND" | "UNKNOWN",
          "is_explicit": bool,
          "dishes":      List[str],
        }
        """
        fallback = {"is_explicit": False, "dishes": [], "intent": "UNKNOWN"}

        raw = await self._call(query, history)
        if raw:
            parsed = self._extract_json(raw)
            if parsed:
                intent = str(parsed.get("intent", "UNKNOWN")).upper()
                dishes = [str(d) for d in (parsed.get("dishes") or []) if d]

                # Guard: if LLM returned a food-category word as a "dish", downgrade
                dishes = [d for d in dishes if d not in _FOOD_CATEGORY_WORDS]
                if not dishes and intent == "EXPLICIT":
                    intent = "RECOMMEND"

                is_explicit = intent == "EXPLICIT" and bool(dishes)
                result = {"is_explicit": is_explicit, "dishes": dishes, "intent": intent}
                logger.info(
                    "[%s] query=%r → intent=%s dishes=%s",
                    self.SKILL_NAME, query[:60], intent, dishes,
                )
                return result

        logger.warning("[%s] LLM failed, using keyword fallback for %r", self.SKILL_NAME, query[:60])
        return self._keyword_fallback(query)

    @staticmethod
    def _keyword_fallback(query: str) -> Dict[str, Any]:
        """Last-resort keyword-based classification (no dish extraction)."""
        q_lower = query.lower()
        for kw in _EXPLICIT_KEYWORDS:
            if kw in q_lower:
                return {"is_explicit": False, "dishes": [], "intent": "EXPLICIT_HINT"}
        return {"is_explicit": False, "dishes": [], "intent": "UNKNOWN"}
