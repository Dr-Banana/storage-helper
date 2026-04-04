# -*- coding: utf-8 -*-
"""
GenerateMealPlanSkill
=====================
Encapsulates ALL meal-plan generation logic:

  1. _build_system_context  — structured prompt with user profile, inventory,
                               diversity constraints, seed candidates, etc.
  2. _inject_pending_options — append pending options block to context
  3. _generate_with_retry    — structured LLM call with up to N retries

The PlanAheadPipeline delegates the context-build + LLM-call step to this
Skill, keeping the pipeline itself as a thin state-machine orchestrator.

Benefits
--------
• All prompt engineering lives in ONE file (easy to A/B-test or iterate).
• The pipeline contains zero prompt strings — it only wires Skills together.
• Skill is independently unit-testable: mock `_generate_with_retry`.
• RAG / seed-library injection happens here, not scattered across the pipeline.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from app.agents.scheduling_agent import PLAN_AHEAD_RESPONSE_SCHEMA, PlanAheadAgent

from .base import LLMSkill

logger = logging.getLogger(__name__)


class GenerateMealPlanSkill(LLMSkill):
    """
    Single-purpose skill: build the full generation context and call the LLM
    to produce a structured meal plan JSON (PLAN_AHEAD_RESPONSE_SCHEMA).

    Replaces the inline ``_build_context`` + ``_call_llm`` code that
    previously lived in ``PlanAheadPipeline``.
    """

    SKILL_NAME = "GenerateMealPlan"
    # System prompt is built dynamically per-call in _build_system_context.
    SKILL_PROMPT = ""
    TEMPERATURE = 0.2
    MAX_OUTPUT_TOKENS = 8192
    RESPONSE_MIME_TYPE = "application/json"
    RESPONSE_SCHEMA = PLAN_AHEAD_RESPONSE_SCHEMA

    def __init__(self, gemini_api_url: str) -> None:
        super().__init__(gemini_api_url)
        # PlanAheadAgent is used purely for its parse_structured_response helper.
        self._parser = PlanAheadAgent(gemini_api_url=gemini_api_url)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def execute(  # type: ignore[override]
        self,
        user_input: str,
        history: List[Dict[str, Any]],
        state: Dict[str, Any],
        user_timezone: Optional[str] = None,
        *,
        inventory_items: Optional[List[Dict[str, Any]]] = None,
        is_draft: bool = False,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
        scheduling_context: Optional[str] = None,
        user_profile: Optional[Dict[str, Any]] = None,
        queue_target_date: Optional[str] = None,
        precomputed_action: Optional[str] = None,
        active_context: Optional[Dict[str, Any]] = None,
        is_escalation: bool = False,
        max_attempts: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """
        Build the full generation context and call the LLM.

        Returns the parsed structured response dict (same shape as
        ``PlanAheadAgent.parse_structured_response``), or ``None`` on failure.
        """
        refresh_count: int = state.get("refresh_count", 0)
        system_context = self._build_system_context(
            state=state,
            user_timezone=user_timezone,
            inventory_items=inventory_items,
            is_draft=is_draft,
            cooking_level=cooking_level,
            language=language,
            scheduling_context=scheduling_context,
            user_profile=user_profile,
            queue_target_date=queue_target_date,
            precomputed_action=precomputed_action,
            active_context=active_context,
            is_escalation=is_escalation,
            query=user_input,
        )
        system_context = self._inject_pending_options(system_context, state)

        return await self._generate_with_retry(
            system_context=system_context,
            history=history,
            user_input=user_input,
            max_attempts=max_attempts,
            refresh_count=refresh_count,
        )

    # ------------------------------------------------------------------
    # Context builder (moved from PlanAheadPipeline._build_context)
    # ------------------------------------------------------------------

    @staticmethod
    def _now_in_timezone(user_timezone: Optional[str] = None) -> datetime:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            tz = None
        return datetime.now(tz) if tz else datetime.utcnow()

    def _build_system_context(
        self,
        state: Dict[str, Any],
        user_timezone: Optional[str],
        inventory_items: Optional[List[Dict[str, Any]]] = None,
        is_draft: bool = False,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
        scheduling_context: Optional[str] = None,
        user_profile: Optional[Dict[str, Any]] = None,
        queue_target_date: Optional[str] = None,
        precomputed_action: Optional[str] = None,
        active_context: Optional[Dict[str, Any]] = None,
        is_escalation: bool = False,
        query: Optional[str] = None,
    ) -> str:
        """Build system context string for the single-shot structured LLM call."""
        now = self._now_in_timezone(user_timezone)
        today = now.date()
        days_ahead = (7 - today.weekday()) % 7 or 7
        next_monday = today + timedelta(days=days_ahead)
        next_sunday = next_monday + timedelta(days=6)

        meal_plan_slots: Dict[str, Any] = state.get("meal_plan_slots") or {}
        dish_ingredients: Dict[str, List[str]] = state.get("dish_ingredients") or {}

        ctx = f"TODAY: {today.strftime('%Y-%m-%d (%A)')}"
        ctx += f"\nNEXT WEEK: Monday {next_monday.strftime('%Y-%m-%d')} to Sunday {next_sunday.strftime('%Y-%m-%d')}"
        if is_draft:
            ctx += "\n\n=== DRAFT MEAL PLAN (proposed — NOT saved to calendar yet) ==="
            ctx += "\n(This is a proposal shown to the user. They may still request changes or confirm to save.)"
        else:
            ctx += "\n\n=== CURRENT MEAL PLAN (saved in calendar — authoritative) ==="
        if meal_plan_slots:
            for date_str in sorted(meal_plan_slots.keys()):
                slots = meal_plan_slots[date_str] or {}
                for mt in ("breakfast", "lunch", "dinner"):
                    slot_val = slots.get(mt)
                    if not slot_val:
                        continue
                    dishes: List[str] = slot_val if isinstance(slot_val, list) else [slot_val]
                    dish_strs = []
                    for d in dishes:
                        ings = dish_ingredients.get(d)
                        if ings:
                            ing_names = [
                                i["name"] if isinstance(i, dict) else i
                                for i in ings if i
                            ]
                            dish_strs.append(f"{d} [ingredients: {', '.join(ing_names)}]")
                        else:
                            dish_strs.append(d)
                    ctx += f"\n{date_str} {mt}: {', '.join(dish_strs)}"
        else:
            ctx += "\n(no plan yet)"

        # --- Inventory section ---
        ctx += "\n\n=== USER'S CURRENT FOOD INVENTORY ==="
        if inventory_items:
            ctx += "\nThese ingredients are ALREADY in the kitchen. Prioritize using them to reduce waste."
            ctx += "\nMark items with shelf life ≤3 days as urgent (should be consumed soon).\n"
            _INF = 9999
            sorted_items = sorted(
                inventory_items,
                key=lambda x: (x.get("estimated_shelf_life_days") or _INF),
            )
            _LIMIT = 50
            for item in sorted_items[:_LIMIT]:
                name = item.get("product_name", "Unknown")
                qty = item.get("quantity", "")
                shelf = item.get("estimated_shelf_life_days") or 0
                line = f"  - {name}"
                if qty:
                    line += f" (qty: {qty})"
                if shelf > 0:
                    urgency = " [USE SOON]" if shelf <= 3 else f" [~{shelf}d shelf life]"
                    line += urgency
                ctx += line + "\n"
            if len(inventory_items) > _LIMIT:
                ctx += f"  (... and {len(inventory_items) - _LIMIT} more items)\n"
        else:
            ctx += "\n(No food items found — recommend based on common ingredients)\n"

        # --- Active Conversation Context (Memory Layer) ---
        _active_ingredients: List[str] = (active_context or {}).get("active_ingredients", [])
        _avoid_dishes: List[str] = (active_context or {}).get("avoid_dishes", [])
        _avoid_ingredients: List[str] = (active_context or {}).get("avoid_ingredients", [])
        _avoid_cuisines: List[str] = (active_context or {}).get("avoid_cuisines", [])
        _active_target_date: Optional[str] = (active_context or {}).get("target_date")
        _active_meal_type: Optional[str] = (active_context or {}).get("target_meal_type")
        if _active_ingredients:
            ctx += "\n\n=== ACTIVE CONVERSATION CONTEXT (remembered from this session) ==="
            ctx += (
                "\nThe user mentioned having these ingredients on hand during this conversation."
                "\nIncorporate them into your suggestions where appropriate:"
            )
            ctx += f"\n  Ingredients available: {', '.join(_active_ingredients)}"
            if _active_target_date or _active_meal_type:
                _slot_hint = " | ".join(
                    filter(None, [_active_target_date, _active_meal_type])
                )
                ctx += f"\n  Planning context: {_slot_hint}"
            ctx += (
                "\nCRITICAL: When the user says things like '葱花的话能做啥' or 'what else can I make',"
                "\n  assume they are ADDING to the previously mentioned ingredients, not starting fresh."
                "\n  Combine all on-hand ingredients into a coherent meal suggestion."
            )
            logger.info(
                "[%s] ACTIVE CONTEXT injected: ingredients=%s",
                self.SKILL_NAME, _active_ingredients,
            )
        if _avoid_dishes or _avoid_ingredients or _avoid_cuisines:
            ctx += "\n\n=== SESSION AVOIDANCE PREFERENCES (HIGHEST PRIORITY) ==="
            ctx += (
                "\nThe user explicitly said they do NOT want these items recently in this conversation."
                "\nTreat these as temporary hard constraints for this planning session."
            )
            if _avoid_dishes:
                ctx += f"\n- Avoid dishes: {', '.join(_avoid_dishes)}"
            if _avoid_ingredients:
                ctx += f"\n- Avoid ingredients: {', '.join(_avoid_ingredients)}"
            if _avoid_cuisines:
                ctx += f"\n- Avoid cuisines/styles: {', '.join(_avoid_cuisines)}"
            ctx += (
                "\nDo NOT suggest these in options/recommendations unless the user explicitly asks for them again"
                " in the current turn."
            )
            logger.info(
                "[%s] SESSION AVOIDANCE injected: dishes=%s ingredients=%s cuisines=%s",
                self.SKILL_NAME, _avoid_dishes or "none", _avoid_ingredients or "none", _avoid_cuisines or "none",
            )

        # --- Cooking level section ---
        _level_map = {
            "beginner": "Complete Beginner (no cooking experience)",
            "intermediate": "Some Experience (knows basic techniques)",
            "expert": "Experienced Cook (can handle complex recipes)",
        }
        _level_label = _level_map.get(cooking_level or "beginner", _level_map["beginner"])
        ctx += f"\n\n=== USER COOKING LEVEL: {_level_label} ==="
        if cooking_level == "beginner":
            ctx += (
                "\nThis user is a complete beginner. When recommending meals:"
                "\n- Suggest beginner-friendly dishes, but keep them varied and interesting (avoid repetitive 'safe' defaults)."
                "\n- Avoid dishes requiring advanced knife skills, precise temperatures, or complex techniques."
                "\n- Keep prep approachable, while still introducing diverse cuisines/flavors and different protein sources."
                "\n- Do NOT repeatedly default to cliche starter dishes (e.g. tomato-egg, generic stir-fried pork slices) unless user explicitly asks."
            )
        elif cooking_level == "intermediate":
            ctx += (
                "\nThis user has some cooking experience. When recommending meals:"
                "\n- Suggest moderately complex dishes (e.g. braised meats, dumplings, multi-component stir-fries)."
                "\n- Include dishes that require basic techniques like marinating, blanching, or sauce-making."
                "\n- Balance simplicity with variety."
            )
        elif cooking_level == "expert":
            ctx += (
                "\nThis user is an experienced cook. When recommending meals:"
                "\n- Feel free to suggest complex, multi-step dishes (e.g. Peking duck, hand-made noodles, elaborate stews)."
                "\n- Include dishes requiring advanced techniques like deep-frying, precise timing, or complex flavor layering."
                "\n- Prioritize variety, creativity, and culinary challenge."
            )

        # --- Language section ---
        _lang_name_map = {
            "zh": "Simplified Chinese (简体中文)",
            "en": "English",
            "ja": "Japanese (日本語)",
            "ko": "Korean (한국어)",
        }
        _lang_name = _lang_name_map.get(language or "zh", _lang_name_map["zh"])
        ctx += f"\n\n=== LANGUAGE REQUIREMENT (CRITICAL) ==="
        ctx += (
            f"\nYou MUST use {_lang_name} for ALL text content in your response — this includes:"
            f"\n- 'user_message': must be in {_lang_name}."
            f"\n- ALL ingredient 'name' fields inside meal_entries → dishes → ingredients: must be in {_lang_name}."
            f"\n- ALL dish names inside meal_entries: must be in {_lang_name}."
            f"\n- 'recommendation_reason' if present: must be in {_lang_name}."
            f"\nDo NOT use English (or any other language) for ingredient names or dish names unless {_lang_name} is English."
            f"\nThis rule overrides everything else, regardless of what language the user writes in."
        )

        # --- User meal blueprint (soft defaults from DB, overridable by user in conversation) ---
        if user_profile:
            ctx += "\n\n=== USER MEAL BLUEPRINT (DEFAULT PREFERENCES — can be overridden by user's explicit requests) ==="
            ctx += (
                "\n⚠️  OVERRIDE RULE: If the user explicitly says they DON'T want something"
                " (e.g. '不要蔬菜', '不要汤', '去掉这道菜', 'no soup today', 'remove the vegetable dish'),"
                " ALWAYS honor that request immediately — modify or remove the dish and confirm the change."
                " These defaults are guides for AI-generated plans, NOT mandatory rules that override user intent."
            )
            _servings = user_profile.get("default_servings", 1)
            ctx += f"\n- Default servings per dish: {_servings} person(s). Scale all ingredient quantities accordingly."
            _ratio = user_profile.get("meat_veg_ratio", "1:1:1")
            _ratio_parts = _ratio.split(":")
            if len(_ratio_parts) == 3:
                ctx += (
                    f"\n- Preferred dish composition per meal: {_ratio_parts[0]} meat dish(es),"
                    f" {_ratio_parts[1]} vegetable dish(es), {_ratio_parts[2]} staple(s)."
                    " (User can override this for any specific meal.)"
                )
            _soup = user_profile.get("include_soup", True)
            if _soup:
                ctx += "\n- Prefers at least one soup dish per meal — suggest one when generating plans, but skip if the user says they don't want soup."
            _cal = user_profile.get("calorie_target")
            if _cal:
                ctx += f"\n- Per-meal calorie target: ~{_cal} kcal. Prefer lighter dishes if near the limit."
            _disliked = user_profile.get("disliked_ingredients") or []
            if _disliked:
                ctx += f"\n- FORBIDDEN ingredients (user dislikes): {', '.join(_disliked)}. Do NOT include any of these in any dish."

            _blueprint_summary = (
                f"servings={_servings}, soup={'yes' if _soup else 'no'}, "
                f"cal={_cal or 'none'}, "
                f"disliked={_disliked if _disliked else '(none)'}"
            )
            logger.info("[%s] USER MEAL BLUEPRINT injected: %s", self.SKILL_NAME, _blueprint_summary)
        else:
            logger.info("[%s] USER MEAL BLUEPRINT skipped (no profile)", self.SKILL_NAME)

        # --- Phase 2: Diversity Engine directive ---
        if user_profile:
            try:
                from app.services.diversity_engine import compute_diversity_directive
                _recent = user_profile.get("recent_dishes") or []
                _diversity_directive = compute_diversity_directive(_recent)
                ctx += "\n\n=== DIVERSITY ENGINE: VARIETY DIRECTIVE (applies to AI-generated suggestions only) ==="
                ctx += "\nTo ensure meal variety when YOU are recommending dishes, prefer avoiding repetition:"
                ctx += f"\n{_diversity_directive}"
                ctx += (
                    "\n\n⚠️  OVERRIDE RULE: These diversity constraints apply ONLY when you are autonomously"
                    " choosing dishes. If the user explicitly names a specific dish they want"
                    " (e.g. '我想吃萝卜炖牛腩', '加个红烧肉'), you MUST honor their request"
                    " and add that exact dish — even if it appears in a SOFT AVOID or HARD BAN list above."
                    " User's explicit choice always takes priority over diversity rules."
                )
                _hard_bans = [l for l in _diversity_directive.splitlines() if "HARD BAN" in l]
                _soft_avoids = [l for l in _diversity_directive.splitlines() if "SOFT AVOID" in l]
                logger.info(
                    "[%s] DIVERSITY ENGINE injected: recent_count=%d, hard_bans=%s, soft_avoids=%s",
                    self.SKILL_NAME,
                    len(_recent),
                    "yes" if _hard_bans else "none",
                    "yes" if _soft_avoids else "none",
                )
            except Exception as _de_err:
                logger.warning("[%s] DiversityEngine failed (non-fatal): %s", self.SKILL_NAME, _de_err)
        else:
            logger.info("[%s] DIVERSITY ENGINE skipped (no profile)", self.SKILL_NAME)

        # --- Phase 3: Seed Library candidate reference (RAG) ---
        if user_profile:
            try:
                from app.services import seed_library as _seed_lib
                _sl_recent = user_profile.get("recent_dishes") or []
                _sl_disliked = user_profile.get("disliked_ingredients") or []

                # ── Anchor ingredient extraction ──────────────────────────────
                # When the user asks to replace a specific dish ("把蒜蓉虾仁换成
                # 类似的菜"), look up the rejected dish(es) in the seed library and
                # extract their main ingredients so we can anchor the first 4 RAG
                # slots to "same ingredient × different cooking method" options.
                _anchor_ingredients: List[str] = []
                _rejected_this_turn: set = state.get("draft_rejected_dishes") or set()
                for _rname in _rejected_this_turn:
                    _rdish = _seed_lib.lookup_dish(_rname)
                    if _rdish:
                        _anchor_ingredients.extend(_rdish.get("main_ingredients", []))
                # Also include ingredients already active in the conversation
                # (e.g. user said "虾仁" earlier in the session).
                _active_ings_from_ctx: List[str] = (active_context or {}).get("active_ingredients", [])
                _anchor_ingredients.extend(_active_ings_from_ctx)
                # Deduplicate while preserving order
                _seen: set = set()
                _anchor_deduped: List[str] = []
                for _ai in _anchor_ingredients:
                    if _ai not in _seen:
                        _seen.add(_ai)
                        _anchor_deduped.append(_ai)
                _anchor_ingredients = _anchor_deduped

                _candidates = _seed_lib.select_candidates(
                    disliked_ingredients=_sl_disliked,
                    recent_dishes=_sl_recent,
                    anchor_ingredients=_anchor_ingredients if _anchor_ingredients else None,
                )
                if _candidates:
                    ctx += f"\n\n{_seed_lib.build_seed_context(_candidates)}"
                    logger.info(
                        "[%s] SEED LIBRARY injected: %d candidates, anchor=%s",
                        self.SKILL_NAME, len(_candidates), _anchor_ingredients or "none",
                    )
            except Exception as _sl_err:
                logger.warning("[%s] SeedLibrary failed (non-fatal): %s", self.SKILL_NAME, _sl_err)
        else:
            logger.debug("[%s] SEED LIBRARY skipped (no profile)", self.SKILL_NAME)

        # --- Phase 3b: Rejected / already-tried dishes (current draft session) ---
        _rejected: set = state.get("draft_rejected_dishes") or set()
        if _rejected:
            ctx += "\n\n=== DISHES ALREADY TRIED / REJECTED THIS SESSION ==="
            ctx += (
                "\nThe user has already replaced or rejected the following dishes during this planning session. "
                "Do NOT suggest any of them again:"
            )
            for _rd in sorted(_rejected):
                ctx += f"\n- {_rd}"
            ctx += (
                "\n\nWhen suggesting a replacement dish, FIRST pick from the SEED LIBRARY CANDIDATES above "
                "that have not been used in the current plan and are not in the rejected list. "
                "Only freely invent a new dish if no suitable unused seed candidate exists."
            )
            logger.info(
                "[%s] Rejected dishes injected: %d — %s",
                self.SKILL_NAME, len(_rejected), sorted(_rejected),
            )

        # --- Live schedule context from SchedulingAgent ---
        if scheduling_context:
            ctx += "\n\n=== LIVE CALENDAR CONTEXT (from SchedulingAgent — most up-to-date) ==="
            ctx += f"\n{scheduling_context}"
            ctx += (
                "\n\nCRITICAL ANTI-DUPLICATION RULES:"
                "\n- Compare ANY dish you are about to add against BOTH the CURRENT MEAL PLAN above AND the LIVE CALENDAR CONTEXT."
                "\n- If the same dish already exists in the same meal slot (date + meal_time), do NOT add it again."
                "\n- If the user explicitly asks to add a dish that is already there, acknowledge the duplicate and ask for confirmation."
            )
        else:
            ctx += (
                "\n\nCRITICAL ANTI-DUPLICATION RULE:"
                "\n- Do NOT add a dish to a meal slot if that slot already contains the same dish in the CURRENT MEAL PLAN above."
            )

        # --- Session flow state ---
        _last_pa_action = state.get("last_pipeline_action")
        if _last_pa_action == "ask":
            ctx += (
                "\n\n=== SESSION FLOW STATE ==="
                "\nPREVIOUS TURN: You asked the user for their meal preferences (action=ask)."
                "\nCURRENT TURN: The user has now provided their preferences."
                "\nNEXT STEP REQUIRED: Use action='suggest_options' to present 2-3 alternative meal plan options."
                "\nDO NOT use 'recommend' directly — present options first so the user can choose."
                "\nDO NOT use 'ask' again — the user already provided their preferences."
            )
        elif _last_pa_action == "suggest_options":
            ctx += (
                "\n\n=== SESSION FLOW STATE ==="
                "\nPREVIOUS TURN: You presented meal plan options (action=suggest_options)."
                "\nCURRENT TURN: The user is selecting/modifying an option."
                "\nNEXT STEP REQUIRED: Use action='recommend' with the selected option's meal_entries as the draft."
            )

        # --- Queue planning mode directive ---
        if queue_target_date:
            _day_labels = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            from datetime import date as _qdate_cls
            _meal_zh_map = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}

            if "|" in queue_target_date:
                _slot_date_str, _slot_meal = queue_target_date.split("|", 1)
                _qd = _qdate_cls.fromisoformat(_slot_date_str)
                _qday_name = _day_labels[_qd.weekday()]
                _meal_zh = _meal_zh_map.get(_slot_meal, _slot_meal)
                ctx += (
                    f"\n\n=== QUEUE PLANNING MODE — SINGLE MEAL ==="
                    f"\nYou are planning one meal at a time to keep each response short."
                    f"\nCURRENT TASK: Plan {_slot_meal} ({_meal_zh}) for {_slot_date_str} ({_qday_name}) ONLY."
                    f"\n• Use action='suggest_options' with EXACTLY 2 compact options for {_slot_meal}."
                    f"\n• Each option: 2-3 dishes suitable for {_slot_meal} (no more)."
                    f"\n• meal_entries MUST only contain date={_slot_date_str}, meal_time={_slot_meal}."
                    f"\n• Do NOT plan any other meal_time or date."
                    f"\n• Do NOT use action='confirm' or action='recommend' directly."
                    f"\n• Be concise in user_message — one short line per option."
                )
            else:
                _qd = _qdate_cls.fromisoformat(queue_target_date)
                _qday_name = _day_labels[_qd.weekday()]
                ctx += (
                    f"\n\n=== QUEUE PLANNING MODE — ACTIVE ==="
                    f"\nCURRENT TASK: Generate a meal plan for {queue_target_date} ({_qday_name}) ONLY."
                    f"\n• Use action='suggest_options' with 2 alternatives for {queue_target_date}."
                    f"\n• Do NOT include meal entries for any other date."
                    f"\n• Do NOT use action='confirm' — the queue is still in progress."
                )

        # --- Skill-pre-routed action directive ---
        if precomputed_action == "suggest_options" and not queue_target_date:
            _suggest_directive = (
                "\n\n=== ACTION DIRECTIVE (mandatory — do not override) ==="
                "\nThe system pre-analyzed the user's request and determined:"
                "\n  The user expressed a FOOD PREFERENCE or CATEGORY (not a specific dish)."
                "\n  They want to CHOOSE from multiple options — not receive a direct plan."
                "\nMandatory action: use 'suggest_options' with EXACTLY 2 compact alternative plans."
                "\n• Each option: 2-3 dish names only, ingredients=[] (filled when user selects)."
                "\n• Do NOT use action='add', 'recommend', or 'ask'."
                "\n• Do NOT save anything yet — present options for the user to choose from."
                "\n• CRITICAL — REJECTED DISHES: Do NOT include ANY dish from the"
                " 'DISHES ALREADY TRIED / REJECTED THIS SESSION' list above in any option."
                " If 馒头, 米饭, or any other dish is in the rejected list, do NOT suggest it."
            )
            if is_draft:
                _draft_slots = state.get("meal_plan_slots") or {}
                _draft_staples: List[str] = []
                for _ds_date, _ds_meals in _draft_slots.items():
                    for _ds_mt, _ds_dishes in _ds_meals.items():
                        for _ds_dish in (_ds_dishes or []):
                            if any(kw in _ds_dish for kw in ("饭", "面", "粥", "馒", "包", "饼", "米", "noodle", "rice", "bread")):
                                _draft_staples.append(_ds_dish)
                if _draft_staples:
                    _suggest_directive += (
                        f"\n• CARRY FORWARD: The user's current draft already has staple: {', '.join(_draft_staples)}."
                        " Keep the same staple in BOTH options unless the user explicitly asks to change it."
                    )
            ctx += _suggest_directive
        elif precomputed_action == "ask" and not queue_target_date:
            ctx += (
                "\n\n=== ACTION DIRECTIVE (mandatory — do not override) ==="
                "\nThe user provided NO food preference at all."
                "\nMandatory action: use 'ask' with ONE focused question to gather a preference."
                "\n• Ask about cuisine style, ingredients, or dietary needs."
                "\n• Do NOT generate a plan or options yet."
            )
        elif precomputed_action == "add" and not queue_target_date:
            ctx += (
                "\n\n=== ACTION DIRECTIVE (mandatory — do not override) ==="
                "\nThe user explicitly named a specific dish to add."
                "\nMandatory action: use 'add' to add that exact dish."
            )

        ctx += "\n\n=== INSTRUCTIONS ==="
        ctx += (
            "\n1. Understand the user's intent and set 'action' to one of:"
            " add, modify, remove, update_ingredients, remove_ingredients, view, ask, recommend, suggest_options, confirm."
        )
        ctx += (
            "\n2. Set 'target_date' (YYYY-MM-DD) for the affected date,"
            " and 'meal_time' (breakfast/lunch/dinner)."
        )
        ctx += "\n3. Apply the change and output the relevant dates in 'meal_entries':"
        ctx += "\n   - For 'add': include ONLY the new date(s) the user mentioned. Do NOT echo back dates that already exist in the current plan."
        ctx += "\n   - For 'modify'/'remove'/'update_ingredients'/'remove_ingredients': include ONLY the date(s) being changed."
        ctx += "\n   - For 'view'/'confirm': mirror the current plan exactly (all dates)."
        ctx += "\n   - For unchanged dates in a modify/remove operation, you may omit them — they are preserved automatically."
        ctx += (
            f"\n   - Each dish MUST include ingredients as an array of objects."
            f" Each ingredient object MUST have 'name' (write in {_lang_name}), 'category' (from the list below, keep in English), and an optional 'quantity' (e.g. '200g', '2 pieces', '1 tbsp')."
            "\n     category values (keep these exact English codes): vegetable, protein, dairy, grain, spice, other."
            "\n   - Each dish SHOULD include a 'slot' field (keep in English) to indicate its role in the meal:"
            "\n     slot values: 'main' (主菜, e.g. meat/fish/tofu dish), 'side' (配菜, e.g. stir-fried vegetables),"
            "\n     'soup' (汤品, any soup/broth/congee), 'staple' (主食, e.g. rice/noodles/bread), 'other'."
        )
        ctx += (
            "\n   - If user removes an ENTIRE date (e.g. '去掉今天的计划'), omit that date from meal_entries entirely."
            "\n   - If user removes only a SPECIFIC meal_time (e.g. '去掉今天晚上', 'remove tonight's dinner'):"
            "\n     set meal_time to the removed slot (breakfast/lunch/dinner), and include the OTHER"
            "\n     remaining meal slots for that date in meal_entries. Leave meal_entries empty ONLY"
            "\n     if that meal_time was the only one for that date."
        )
        ctx += (
            "\n4. Write a brief, friendly message in 'user_message' (match user's language)."
            "\n   CRITICAL: NEVER mention 'JSON', 'data format', 'structured response', or any technical"
            " implementation detail in user_message. The message is shown directly to the user."
            "\n   If you need to mention cooking steps, write them as plain text — do NOT say"
            " 'I cannot provide this in JSON'."
        )
        ctx += "\n\nRULES:"
        ctx += "\n- NEVER invent meals for dates not mentioned unless user explicitly asks."
        ctx += (
            "\n- ANTI-TEMPLATE RULE: Do NOT repeatedly start recommendations with the same high-frequency home dishes."
            "\n  Across options, enforce novelty in at least TWO dimensions: protein source, cuisine style, or cooking method."
            "\n  If a common fallback dish appeared recently in this session, avoid repeating it unless user explicitly requests it."
        )
        ctx += (
            "\n- EXPLICIT DISH RULE: If the user explicitly names specific dish(es) they want"
            " (e.g. '吃个萝卜炖牛腩', '加个红烧肉', 'I want beef stew'):"
            "\n  1. Use action='add' and include ONLY those exact dishes — do NOT pad the meal with extra dishes."
            "\n  2. IGNORE diversity soft-avoids and hard-bans for those dishes — user's explicit choice overrides variety rules."
            "\n  3. NEVER refuse or redirect with 'you ate this recently' — honor the request directly."
        )
        ctx += (
            "\n- INGREDIENT PERSISTENCE RULE (applies when user asks to swap/replace a dish"
            " WITHOUT specifying a new dish):"
            "\n  Step 1 — Identify the MAIN INGREDIENT of the dish being replaced"
            " (e.g. 清炒虾仁 → 虾; 红烧肉 → 猪肉; 番茄炒蛋 → 番茄+鸡蛋)."
            "\n  Step 2 — Try a DIFFERENT COOKING METHOD or CUISINE STYLE for the same ingredient:"
            "\n    虾 → 泰式咖喱虾、蒜蓉粉丝蒸虾、宫保虾仁、黄油烤虾"
            "\n    猪肉 → 东坡肉、糖醋里脊、梅菜扣肉、猪肉白菜炖粉条"
            "\n    鸡肉 → 口水鸡、柠檬鸡扒、咖喱鸡、照烧鸡腿"
            "\n  Step 3 — Only switch to a COMPLETELY DIFFERENT ingredient if the user"
            " explicitly said they dislike/don't want that ingredient"
            " (e.g. '不想吃虾了', 'I don't want shrimp', '换个不含肉的')."
            "\n  CRITICAL: Do NOT default to 番茄炒蛋 / 清炒时蔬 as the 'safe' swap —"
            " these are last-resort fallbacks, not first choices."
            " Always consult the SEED DISH REFERENCE above for diverse alternatives."
        )
        ctx += (
            "\n- SIMILARITY RULE (applies when user says '类似的', 'similar', '差不多的',"
            " 'something like X', or asks for a dish 'like' the one they are replacing):"
            "\n  'Similar' means SAME PRIMARY INGREDIENT + DIFFERENT cooking method or flavor profile."
            "\n  It does NOT mean 'same category' (e.g. seafood → salmon is a WRONG interpretation of"
            " 'similar to shrimp'; 虾 → 泰式咖喱虾 is CORRECT)."
            "\n  Priority order when selecting a 'similar' replacement:"
            "\n    1st — SEED LIBRARY CANDIDATES above that share the same main ingredient"
            "  (these are pre-selected specifically for this request; use them first)."
            "\n    2nd — Any dish in your knowledge base with the same primary ingredient"
            "  but a distinct cooking technique or cuisine style."
            "\n    3rd — Only if no same-ingredient option is appropriate, choose a dish"
            "  with a similar FLAVOR PROFILE (e.g. light and savory → 清蒸鱼)."
            "\n  NEVER pick a dish purely because it is in the same broad category"
            " (海鲜, 肉, 蔬菜) — that is too loose and produces 'logic jump' results like"
            " 'shrimp → salmon' which feels random to the user."
        )
        ctx += "\n- For 'view', meal_entries should mirror the current plan exactly."
        ctx += "\n- For 'update_ingredients'/'remove_ingredients', keep the same meals but update dish ingredients."
        ctx += (
            "\n- For 'remove' of an ENTIRE date: that date MUST NOT appear in meal_entries."
            "\n- For 'remove' of a specific meal_time only: set meal_time= the removed slot,"
            "\n  include the remaining slots for that date in meal_entries (if any)."
        )
        if is_draft:
            ctx += (
                "\n- In DRAFT MODE: prefer acting on the user's intent over asking clarifying questions."
                " A loose dish reference (wrong name, wrong date) is NOT a reason to use 'ask' —"
                " make a sensible substitution and tell the user what you changed."
                "\n- REMOVE DISH IN DRAFT: If the user says they don't want a dish"
                " (e.g. '不要蔬菜', '去掉这道菜', '我不想要汤了', '删掉青菜', 'remove the soup', 'I don't want the vegetable dish'):"
                "\n  * Use action='modify' and remove that dish from the plan (keep all other dishes as-is)."
                "\n  * Do NOT replace it with another dish unless the user asks for a replacement."
                "\n  * Do NOT refuse because of meal blueprint defaults — user's explicit removal always wins."
                "\n  * Confirm in user_message what was removed."
            )
        ctx += "\n\n--- MULTI-TURN RECOMMENDATION FLOW (read carefully) ---"
        ctx += (
            "\n- Use 'ask' SPARINGLY. Prefer 'suggest_options' by default, even when user preferences are minimal."
            "\n  * Ask only when there is truly insufficient planning scope (no usable date/meal hint and no inferable context)."
            "\n  * If you ask, ask ONE concise question and avoid repeated back-and-forth."
            "\n  * The first recommendation turn should generally provide concrete options, not only clarifying questions."
        )
        ctx += (
            "\n- Use 'suggest_options' whenever the user asks for meal suggestions AND has any preference"
            " context available — either in the CURRENT message or prior turns. Preference context includes:"
            "\n  * A food category or ingredient type (海鲜, 肉类, 蔬菜, etc.)"
            "\n  * A cuisine style (日式, 川菜, 西餐, etc.)"
            "\n  * A dietary constraint or occasion (清淡, 辛辣, 减脂, 家常, etc.)"
            "\n  * Any answer to a previous 'ask' turn."
            "\n  This presents 2 alternative meal plan styles so the user can choose — NO plan is saved yet."
            "\n  * Set meal_entries=[] (the primary plan slot is empty for suggest_options)."
            "\n  * In 'dish_options', provide EXACTLY 2 alternative plans (option_id '1' and '2')."
            "\n    Each option MUST have: option_id, label (e.g. '方案一：家常风味'), and meal_entries."
            "\n    CRITICAL — keep options COMPACT to avoid token overflow:"
            "\n      - Each option's meal_entries must list dish names only."
            "\n      - Set ingredients=[] for every dish in dish_options (ingredients are filled when user selects)."
            "\n      - Do NOT write out shopping lists or per-dish ingredient details in options."
            "\n  * In user_message, present the options clearly:"
            "\n    - List each option with its label and a one-line summary of the dishes."
            "\n    - Tell the user to reply with '选方案X' or 'I choose option X' to select."
            "\n    - Invite the user to request modifications (e.g. 'add more vegetables', 'make it simpler')."
            "\n  * PRIORITIZE dishes that use ingredients from the FOOD INVENTORY for ALL options."
            "\n  * Ensure variety across options (different cuisines/flavors/complexity levels)."
        )
        ctx += (
            "\n- Use 'add' (NOT 'recommend') when the user explicitly names a specific dish they want to add"
            " (e.g. '明天晚上加个萝卜炖牛腩', '帮我加上红烧肉', 'add beef stew for dinner')."
            " Just add that exact dish — do NOT pad the meal with extra dishes unless the user asks for a full plan."
        )
        ctx += (
            "\n- Use 'recommend' when:"
            "\n  (a) The user has SELECTED one of the suggested options (e.g. '选方案2', 'I choose option 1')."
            "    Use the EXACT meal_entries from that option as the draft."
            "\n  (b) The user has selected AND requested modifications: apply the modifications to the option's plan."
            "\n  NEVER use 'recommend' to skip the options step. The user must always see 'suggest_options' first"
            " so they can choose before a plan is drafted — even if they provided preferences in the first message."
            "\n  This generates a DRAFT — it is NOT saved to calendar yet."
            "\n  * Fill requested slots with suitable dishes based on stated preferences."
            "\n  * PRIORITIZE dishes that use ingredients from the FOOD INVENTORY above."
            "\n  * Prefer [USE SOON] items — help the user avoid food waste."
            "\n  * Set 'recommendation_reason' to briefly explain the choice."
            "\n  * Include realistic ingredients in every dish entry."
            "\n  * CRITICAL — user_message MUST include a formatted meal-by-meal summary of the entire proposed plan."
            "\n    Format each entry as one line: '📅 <date> <meal_time>: <dish1>, <dish2>, ...'"
            "\n    List ALL dates in chronological order, then add an invitation to refine."
            "\n    Example structure:"
            "\n      Here is your proposed plan:"
            "\n      📅 2026-02-24 dinner: Kung Pao Chicken, Stir-fried Broccoli"
            "\n      📅 2026-02-25 dinner: Braised Pork Ribs, Tomato Egg Drop Soup"
            "\n      ..."
            "\n      How does this look? Feel free to ask for any changes, or say 'confirm' to save."
        )
        ctx += (
            "\n  * CRITICAL — Name alignment: when a dish uses an ingredient from the FOOD INVENTORY,"
            " copy its name EXACTLY as listed above"
            " (e.g. if inventory lists 'Tomato', write 'Tomato' not 'tomatoes'; if inventory lists 'Egg', write 'Egg' not 'Eggs')."
        )
        if is_draft:
            ctx += (
                "\n- DRAFT MODE IS ACTIVE. ALL modification requests from the user target the DRAFT above."
                "\n  NEVER ask the user whether they mean the draft or previously-saved data — always assume the DRAFT."
                "\n- When the user references a dish or ingredient that does not exactly appear in the DRAFT on that date,"
                " interpret their intent flexibly:"
                "\n  * 'change the tofu on Feb 26 to a meat dish' — if Feb 26 has no tofu, find the most vegetarian-heavy"
                " dish on that date and replace it with the requested type of dish."
                "\n  * 'remove the spicy dish on Wednesday' — if there are multiple dishes, pick the most likely spicy one."
                "\n  * Use 'modify' and make a reasonable substitution; explain your interpretation in user_message."
                "\n  * Only use 'ask' if the user's intent is truly ambiguous (e.g. they name a date not in the draft at all)."
                "\n- Use 'confirm' when the user approves the draft — this includes EXPLICIT and IMPLICIT signals:"
                "\n  Explicit: 'ok', 'confirm', 'save it', 'yes', 'looks good', 'that works', '可以', '确认', '保存', '好的', '行', '没问题', 'sure', 'perfect', '就这个吧', '就这样', '这个可以'."
                "\n  Implicit: 'sounds good', 'not bad', '听起来不错', '挺好的', '就按这个来', '行吧', 'go ahead', 'let's do it', '就这么定了', '按这个做'."
                "\n  Compound: if the user confirms AND adds a new request (e.g. '可以，周三的牛排怎么做？'),"
                " STILL use 'confirm' — the system will handle the secondary request separately."
                "\n  * For 'confirm': set meal_entries=[] (the system uses the current draft plan)."
                "\n  * In user_message, tell the user their plan has been saved and briefly summarize."
            )
        else:
            ctx += (
                "\n- Use 'confirm' only when there is an active draft to save."
                " (Currently no draft — direct mutations like add/modify save immediately.)"
            )
        ctx += f"\n- Date references: today={today.strftime('%Y-%m-%d')}, "
        days_labels = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        refs = ", ".join(f"{d}={(next_monday + timedelta(days=i)).strftime('%Y-%m-%d')}" for i, d in enumerate(days_labels))
        ctx += refs

        # --- Escalation Prompt: force creative leaps after ≥2 consecutive refresh requests ---
        if is_escalation:
            ctx += (
                "\n\n=== ⚡ ESCALATION MODE: FOUR FORCED CREATIVE LEAPS (MANDATORY) ==="
                "\nThe user has already seen multiple sets of options and is asking for something"
                " GENUINELY DIFFERENT.  You MUST apply ALL four rules below simultaneously:"
                "\n"
                "\n  RULE 1 — PROTEIN HAND-OFF (蛋白质换手):"
                "\n  Look at the last 3 recommended protein sources. If they included chicken or pork,"
                " you MUST switch to a completely different protein: fish, shrimp/prawns, tofu, beef,"
                " eggs, or legumes. Do NOT repeat the same protein type used in recent suggestions."
                "\n"
                "\n  RULE 2 — CUISINE CONTRAST (菜系对冲):"
                "\n  If the previous options were predominantly Chinese (川菜, 粤菜, 家常菜),"
                " at least ONE option MUST feature a non-Chinese cuisine:"
                " Japanese (日式), Korean (韩式), Thai (泰式), Western (西餐), or Southeast Asian (东南亚)."
                " The cuisines across the two options MUST differ from each other."
                "\n"
                "\n  RULE 3 — COOKING METHOD JUMP (烹饪方式跳跃):"
                " If the previous suggestions heavily featured stir-frying (炒) or braising (炖),"
                " now PRIORITIZE completely different techniques:"
                " steaming (蒸), cold dressing/salad (凉拌), roasting/baking (烤), poaching (汆/煮),"
                " or one-pot hotpot (锅). Each option must use a distinct primary cooking method."
                "\n"
                "\n  RULE 4 — FLAVOR POLARITY FLIP (口味极性翻转):"
                " If previous suggestions leaned spicy/rich/heavy (辣、红烧、重口),"
                " now produce light/refreshing/clean options (清淡、凉、酸、清蒸)."
                " If they were light, go bold. Polarity must flip from the last round."
                "\n"
                "\nCRITICAL: These four rules override all soft-avoid diversity constraints."
                " The goal is RADICAL novelty — surprise the user with options they haven't considered."
                " Do NOT repeat any dish from the 'DISHES ALREADY TRIED / REJECTED THIS SESSION' list."
            )
            logger.info("[%s] ESCALATION MODE active — four creative-leap rules injected", self.SKILL_NAME)

        return ctx

    # ------------------------------------------------------------------
    # Pending options injector (moved from PlanAheadPipeline._inject_pending_options)
    # ------------------------------------------------------------------

    def _inject_pending_options(self, ctx: str, state: Dict[str, Any]) -> str:
        """Append pending_options section to the context string when options exist."""
        pending_options = state.get("pending_options")
        if not pending_options:
            return ctx

        ctx += "\n\n=== PENDING MEAL PLAN OPTIONS (user has NOT selected yet) ==="
        ctx += "\nThe following options were presented to the user. They are now selecting or modifying:"
        for opt in pending_options:
            oid = opt.get("option_id", "?")
            label = opt.get("label", f"方案{oid}")
            slots = opt.get("meal_plan_slots") or {}
            ctx += f"\n\n[方案{oid}] {label}:"
            for date_str in sorted(slots.keys()):
                for mt in ("breakfast", "lunch", "dinner"):
                    dishes = (slots.get(date_str) or {}).get(mt)
                    if dishes:
                        dish_list = dishes if isinstance(dishes, list) else [dishes]
                        ctx += f"\n  {date_str} {mt}: {', '.join(dish_list)}"
        ctx += (
            "\n\nWhen the user selects an option (e.g. '选方案2', 'option 1', '我要第三个'):"
            "\n  - Use 'recommend' action."
            "\n  - Copy the selected option's dishes into meal_entries with ALL fields filled:"
            "\n    each dish MUST include 'name', 'slot', and a complete 'ingredients' array"
            "\n    (the original options may not have ingredients — you MUST generate them now)."
            "\n  - If the user also requests a modification, apply it to the selected option's plan."
            "\n  - Clear pending options (they are resolved once the user selects)."
        )
        return ctx

    # ------------------------------------------------------------------
    # LLM call with retry (moved from PlanAheadPipeline._call_llm)
    # ------------------------------------------------------------------

    async def _generate_with_retry(
        self,
        system_context: str,
        history: List[Dict[str, Any]],
        user_input: str,
        max_attempts: int = 3,
        max_history_messages: int = 20,
        refresh_count: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Single structured LLM call with up to ``max_attempts`` retries on
        transient failures.

        ``max_history_messages`` caps history included in the request to
        prevent unbounded token growth in long sessions.  The slice always
        starts on a user-role message so the contents array begins with the
        correct role for the Gemini API.

        Dynamic temperature schedule driven by ``refresh_count``
        (number of consecutive "再换一批" requests in the current session):
          0–1 → 0.2  (default, focused & deterministic)
          2   → 0.45 (noticeably more varied)
          ≥3  → 0.70 (maximum creative diversity, capped here)
        """
        # Compute effective temperature — escalates with consecutive refresh requests.
        _effective_temp: float
        if refresh_count <= 1:
            _effective_temp = 0.2
        elif refresh_count == 2:
            _effective_temp = 0.45
        else:
            _effective_temp = 0.7
        if _effective_temp != self.TEMPERATURE:
            logger.info(
                "[%s] dynamic temperature: %.2f (refresh_count=%d)",
                self.SKILL_NAME, _effective_temp, refresh_count,
            )

        _history = (history or [])[-max_history_messages:]
        if _history and _history[0].get("role") != "user":
            _history = _history[1:]
        if len(history or []) > max_history_messages:
            logger.debug(
                "[%s] history truncated %d → %d messages (max=%d)",
                self.SKILL_NAME, len(history), len(_history), max_history_messages,
            )

        contents = []
        for msg in _history:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": user_input}]})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_context}]},
            "generationConfig": {
                "temperature": _effective_temp,
                "maxOutputTokens": self.MAX_OUTPUT_TOKENS,
                "responseMimeType": self.RESPONSE_MIME_TYPE,
                "responseSchema": self.RESPONSE_SCHEMA,
            },
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=90.0) as client:
                    resp = await client.post(
                        self.gemini_api_url,
                        headers={"Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    result = resp.json()

                if "error" in result:
                    logger.error("[%s] Gemini API error: %s", self.SKILL_NAME, result["error"])
                    return None

                candidates = result.get("candidates") or []
                if not candidates:
                    logger.warning("[%s] attempt %d: no candidates — retrying", self.SKILL_NAME, attempt)
                    last_error = ValueError("no candidates")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                candidate = candidates[0]
                finish_reason = candidate.get("finishReason", "")
                if finish_reason not in ("STOP", ""):
                    logger.warning(
                        "[%s] unexpected finishReason=%r", self.SKILL_NAME, finish_reason
                    )

                text = (
                    candidate.get("content", {}).get("parts", [{}])[0].get("text", "") or ""
                ).strip()
                if not text:
                    logger.warning("[%s] attempt %d: empty response — retrying", self.SKILL_NAME, attempt)
                    last_error = ValueError("empty text")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                parsed = self._parser.parse_structured_response(text)
                if parsed is None:
                    logger.warning("[%s] attempt %d: JSON parse failed — retrying", self.SKILL_NAME, attempt)
                    last_error = ValueError("JSON parse failed")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                if attempt > 1:
                    logger.info("[%s] succeeded on attempt %d", self.SKILL_NAME, attempt)
                return parsed

            except httpx.TimeoutException as e:
                logger.warning(
                    "[%s] attempt %d timed out: %s",
                    self.SKILL_NAME, attempt, e,
                    exc_info=(attempt == max_attempts),
                )
                last_error = e
                if attempt < max_attempts:
                    await asyncio.sleep(3.0 * attempt)
            except Exception as e:
                logger.warning(
                    "[%s] attempt %d failed: %s",
                    self.SKILL_NAME, attempt, e,
                    exc_info=(attempt == max_attempts),
                )
                last_error = e
                if attempt < max_attempts:
                    await asyncio.sleep(1.0 * attempt)

        logger.error(
            "[%s] failed after %d attempts. Last error: %s",
            self.SKILL_NAME, max_attempts, last_error,
        )
        return None
