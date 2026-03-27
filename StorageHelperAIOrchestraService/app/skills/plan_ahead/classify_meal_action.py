# -*- coding: utf-8 -*-
"""
ClassifyMealActionSkill  ← THE CORE FIX
========================================
Decides the correct pipeline action BEFORE the main meal-generation LLM call.

This skill solves the root cause of the "海鲜 → direct save" bug:
  Previously: the main LLM received a giant prompt and had to choose the action
              (ask / suggest_options / add / recommend) from text hints.
              It often chose "add" or "recommend" directly, skipping the multi-option flow.

  Now: this lightweight classifier runs first and returns the action.
       The pipeline then routes accordingly — the main LLM only generates content
       for the action that was already decided.

Decision rules (in priority order)
-----------------------------------
1. is_explicit_dish=True             → "add"     (user named a specific dish)
2. pipeline_phase="in_queue"         → "recommend" (queue mode: plan one slot)
3. pipeline_phase="awaiting_suggest" → "recommend" (user just selected an option)
4. has_food_preference=True          → "suggest_options" (preference given, show options)
5. (default)                         → "ask"     (gather preferences first)

Output schema
-------------
{
  "action":  "ask" | "suggest_options" | "add" | "recommend",
  "reason":  str,    # one-line explanation (logged, not shown to user)
}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import LLMSkill

logger = logging.getLogger(__name__)


class ClassifyMealActionSkill(LLMSkill):
    """
    Pre-route the pipeline action before calling the main meal-generation LLM.

    Uses a small, focused LLM call so the decision is semantic (not keyword-based),
    but fast and cheap (< 200 tokens output).
    """

    SKILL_NAME = "ClassifyMealAction"
    MAX_OUTPUT_TOKENS = 150

    # ── SKILL PROMPT ─────────────────────────────────────────────────────────
    SKILL_PROMPT = """\
You are a meal-planning action router for a cooking assistant chatbot.
Given the user's latest message and context, decide which action to take next.

Actions (choose exactly one):
  "ask"            — User wants meal suggestions but has given NO preference AND no ingredients at all.
                     Ask one focused question to learn their preference.
  "suggest_options" — User provided a food preference, cuisine, OR mentioned a specific ingredient
                     they have. Present 2 alternative meal plan styles so the user can choose.
  "add"            — User explicitly named a SPECIFIC dish they want to add.
  "recommend"      — User already selected from options presented in the previous turn.

CRITICAL decision rules:
1. If the user names a specific dish (e.g. 红烧肉, 小笼包, pizza) → "add"
2. If the user provides ANY food preference/category/cuisine OR mentions a specific ingredient
   they have on hand (e.g. 海鲜, 日式, 清淡, 辣的, 牛棒骨, 葱花, 土豆, 鸡蛋, beef, onion) → "suggest_options"
   The user is telling you what ingredients/preferences they have — help them use those.
3. If the user says "选方案X" / "I choose option X" / "第一个" / "就第一个" → "recommend"
4. Only use "ask" when there is truly NO preference AND no ingredient mentioned at all
   (e.g. "今天吃什么" with absolutely no other context)

Examples:
  "我今天晚上想吃个海鲜"              → suggest_options  // 海鲜 is a food category
  "帮我做个清淡的晚餐"                → suggest_options  // 清淡 is a preference
  "今晚来点日式的"                    → suggest_options  // 日式 is cuisine preference
  "明天晚饭有个牛棒骨能做什么"        → suggest_options  // 牛棒骨 is an ingredient on hand
  "葱花的话能做啥"                    → suggest_options  // 葱花 is an ingredient on hand
  "我有很多葱花能做什么口味的"        → suggest_options  // ingredient + asking for ideas
  "有鸡蛋和番茄，做什么好"           → suggest_options  // ingredients on hand
  "加个红烧肉"                        → add              // specific dish
  "换成番茄炒蛋"                      → add              // specific dish
  "选方案2"                          → recommend        // user selected an option
  "今晚吃什么好呢"                    → ask              // no preference, no ingredient
  "帮我想想今天吃什么"                → ask              // no preference, no ingredient

Return ONLY valid JSON: {"action": "...", "reason": "one line explanation"}
"""

    # ─────────────────────────────────────────────────────────────────────────

    async def execute(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
        *,
        is_explicit_dish: bool = False,
        pipeline_phase: str = "idle",
        is_draft: bool = False,
    ) -> Dict[str, Any]:
        """
        Classify the action for the current turn.

        Parameters
        ----------
        query           : User's latest message.
        history         : Recent conversation turns (role/content dicts).
        is_explicit_dish: True if ClassifyDishIntentSkill already detected a specific dish.
        pipeline_phase  : Current FSM phase from get_phase() as string value
                          ("idle", "in_queue", "awaiting_date_confirm", etc.)
        is_draft        : True when a draft plan is currently active.  Used to detect
                          modification signals so they don't get misrouted as "add".

        Returns
        -------
        {"action": str, "reason": str}
        """
        # ── Fast-path overrides (no LLM needed) ──────────────────────────────
        if is_explicit_dish:
            return {"action": "add", "reason": "explicit dish detected by ClassifyDishIntentSkill"}

        if pipeline_phase == "in_queue":
            return {"action": "recommend", "reason": "queue mode: planning one slot"}

        if pipeline_phase == "awaiting_clarification":
            # Previous turn was ask → user just answered → suggest_options
            return {"action": "suggest_options", "reason": "user answered preference question"}

        # ── Draft-mode modification fast-path ─────────────────────────────────
        # When a draft is active and the user uses swap/replace language WITHOUT
        # naming a specific dish, we pre-route to "modify" so the main LLM is
        # not given the "add a specific dish" directive that causes it to add a
        # random safe dish (e.g. 西红柿炒蛋) instead of intelligently swapping.
        if is_draft and not is_explicit_dish:
            _swap_patterns = (
                "换一个", "换一下", "换掉", "换掉这道", "换这道",
                "不要这道", "不喜欢这个", "这道换", "这个换",
                "replace", "swap", "change this", "不想要这道",
            )
            if any(p in query for p in _swap_patterns):
                return {
                    "action": "modify",
                    "reason": "draft-mode fast-path: swap signal without explicit dish → let main LLM choose replacement",
                }

        # ── LLM classification ───────────────────────────────────────────────
        user_msg = f'User message: "{query}"'
        raw = await self._call(user_msg, history)
        if raw:
            parsed = self._extract_json(raw)
            if parsed and parsed.get("action") in ("ask", "suggest_options", "add", "recommend", "modify"):
                result = {
                    "action": parsed["action"],
                    "reason": str(parsed.get("reason", "")),
                }
                logger.info(
                    "[%s] query=%r → action=%s | reason=%s",
                    self.SKILL_NAME, query[:60], result["action"], result["reason"],
                )
                return result

        # ── Keyword fallback ──────────────────────────────────────────────────
        logger.warning("[%s] LLM failed, using keyword fallback for %r", self.SKILL_NAME, query[:60])
        return self._keyword_fallback(query, is_draft=is_draft)

    @staticmethod
    def _keyword_fallback(query: str, is_draft: bool = False) -> Dict[str, Any]:
        """Heuristic fallback when the LLM call fails."""
        q = query.lower()

        # ── Selection signals → recommend ─────────────────────────────────────
        # Covers both "选方案2" and compound "我想要方案2，但把X换成Y" patterns.
        # Extended to capture "方案1/2/3", "我要方案", "想要方案X" without "选".
        _select_kw = (
            "选方案", "选第", "第一个", "第二个", "选1", "选2",
            "choose option", "option 1", "option 2",
            "我要方案", "我选方案", "想要方案", "选择方案",
            "方案一", "方案二", "方案三", "方案1", "方案2", "方案3",
        )
        _has_selection = any(kw in q for kw in _select_kw)

        # ── Modification/swap signals ─────────────────────────────────────────
        # "换成" is deliberately included here so "把X换成另一道类似的" doesn't fall
        # into the add-kw path. When is_explicit_dish=True the fast-path in
        # execute() already routes to "add" before we reach the fallback.
        _modify_kw = (
            "换一个", "换一下", "换掉", "替换", "换这道", "换成",
            "change to", "replace with", "instead of",
            "不要这道", "这道换", "再换", "换个别的",
            "类似的", "similar", "差不多的", "相似的",
        )
        _has_modification = any(kw in q for kw in _modify_kw)

        # ── Compound: selection + modification → recommend (main LLM handles both) ──
        # e.g. "我想要方案2，但把蒜蓉虾仁换成另一道类似的菜"
        # Route to "recommend" so the selected plan is loaded; the main LLM will
        # apply the modification on top of the chosen option.
        if _has_selection and _has_modification:
            return {"action": "recommend", "reason": "fallback: select+modify compound — recommend with in-line swap"}

        # Selection only → recommend
        if _has_selection:
            return {"action": "recommend", "reason": "fallback: selection keyword detected"}

        # Modification only in draft mode → modify
        if is_draft and _has_modification:
            return {"action": "modify", "reason": "fallback: modification signal in draft mode"}

        # ── Preference / category / cuisine signals → suggest_options ─────────
        _preference_kw = (
            "海鲜", "肉", "蔬菜", "辣", "清淡", "日式", "川菜", "粤菜", "西餐",
            "家常", "简单", "快手", "减脂", "素", "甜", "酸",
            "seafood", "spicy", "light", "simple", "japanese", "chinese",
        )
        if any(kw in q for kw in _preference_kw):
            return {"action": "suggest_options", "reason": "fallback: preference keyword detected"}

        # ── Ingredient "I have X / what can I make with X" → suggest_options ──
        _have_ingredient_kw = (
            "的话", "能做", "可以做", "怎么做", "做什么", "能做啥",
            "有个", "有点", "有一些", "有很多", "有剩",
            "i have", "i've got", "using", "with some",
        )
        if any(kw in q for kw in _have_ingredient_kw):
            return {"action": "suggest_options", "reason": "fallback: ingredient-on-hand signal detected"}

        # ── Explicit add signals (no modification context) ────────────────────
        _add_kw = ("加个", "加上", "吃个", "做个", "来个", "add ", "want ")
        if any(kw in q for kw in _add_kw):
            return {"action": "add", "reason": "fallback: add keyword detected"}

        # Default: ask for preferences
        return {"action": "ask", "reason": "fallback: no clear preference signal"}
