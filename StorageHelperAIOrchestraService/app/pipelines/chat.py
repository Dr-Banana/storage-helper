import logging
import httpx
import json
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from app.modules.intent_classifier import intent_classifier, Intent
from app.modules.plan_ahead_state import get_plan_state, update_plan_state, clear_plan_state
from app.pipelines.intent_router import route_by_intent
from app.core.config import settings

logger = logging.getLogger(__name__)

# Import scheduling agents and plan operation routing
try:
    from app.agents.scheduling_agent import (
        ScheduleSessionContext,
        SchedulingResponseGenerator,
        PlanAheadAgent,
    )
    from app.agents.plan_operation_agent import get_operation_type, PlanOperationType
    SCHEDULING_AGENTS_AVAILABLE = True
    logger.info("Scheduling agents imported successfully")
except ImportError as e:
    logger.warning(f"Scheduling agents not available: {e}")
    SCHEDULING_AGENTS_AVAILABLE = False
    get_operation_type = None
    PlanOperationType = None

# Global flag: disable ALL schedule fetching in chat pipeline for now.
# Fetch logic will be redesigned and moved fully into scheduling agents.
ENABLE_SCHEDULE_FETCH_IN_CHAT = True

class ChatPipeline:
    """
    Pipeline for handling user chat interactions.
    """

    SYSTEM_PROMPT = """
You are a helpful and proactive Home AI Agent named "Storage Helper". 
You assist users with managing their home life, specifically focused on kitchen inventory, meal planning, and document organization.

Current Intent: {intent}
Reasoning: {reasoning}

If the intent is SEARCH: Acknowledge that you are looking for their items or documents.
If the intent is UPDATE: The system has searched for candidates to update. Present these candidates to the user and ASK FOR CONFIRMATION on which one to update and what values to change. DO NOT update until confirmed.
If the intent is CORRECTION_MODE (user is viewing a list): You can help them FIX existing items or ADD missing items to the list.
If the intent is PLAN_EAT_OUT: Suggest you can help find restaurants or make reservations.
If the intent is PLAN_AHEAD: Help the user plan meals for a future period or a specific date (e.g. next Monday), then generate a shopping list of ingredients to buy. Offer to save the list to their schedule when ready. Plan Cook Home is a sub-flow: when the user is cooking at home, use the provided USER'S ACTUAL INVENTORY (if any) to suggest recipes — only suggest recipes using ingredients that are ACTUALLY in the inventory; never invent ingredients not in the list. If inventory is empty or limited, say so and suggest what to buy.
If the intent is GENERAL: Be friendly and helpful.

LANGUAGE REQUIREMENT: {language_instruction} This applies to ALL text you generate — including dish names, ingredient names, step descriptions, and any other human-readable content.
"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"


    async def run(
        self,
        user_input: str,
        owner_id: int,
        history: List[Dict[str, str]] = None,
        context: Optional[Dict[str, Any]] = None,
        user_timezone: Optional[str] = None,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
    ) -> Dict[str, Any]:
        """
        Runs the chat pipeline: Classify intent -> Route/Mock Action -> Generate response.
        """
        # Build language instruction injected into every LLM system prompt
        _lang_map = {
            "zh": "Always respond in Simplified Chinese (简体中文), regardless of what language the user writes in.",
            "en": "Always respond in English, regardless of what language the user writes in.",
            "ja": "Always respond in Japanese (日本語), regardless of what language the user writes in.",
            "ko": "Always respond in Korean (한국어), regardless of what language the user writes in.",
        }
        _language_instruction = _lang_map.get(language or "zh", _lang_map["zh"])

        # Initialise cooking-steps data containers (populated later if intent == COOKING_STEPS)
        _cs_injected_context: str = ""
        _cs_action_data: Dict[str, Any] = {}
        active_cooking_ctx: Optional[Dict[str, Any]] = None

        # 0. Check for context-based intent (e.g. Correction)
        # If we have explicit correction context, we bypass standard intent classification
        is_correction = False
        if context and context.get("type") == "correction" and context.get("data"):
            intent_result = type('obj', (object,), {'intent': Intent.UPDATE, 'reasoning': 'User is correcting items based on provided context', 'confidence': 1.0})
            intent_action = {'action': 'CORRECTION_MODE', 'data': {}, 'message': 'Processing correction...'}
            is_correction = True
        else:
            # Detect whether user is currently in a meal-planning session
            plan_state = get_plan_state(owner_id)
            in_plan_ahead_flow = (
                (context and context.get("type") == "plan_ahead")
                or bool(plan_state.get("meal_plan") or plan_state.get("shopping_list"))
                # Active suggest_options flow: user is selecting/refining presented options
                or bool(plan_state.get("pending_options"))
                or (
                    history
                    and any(
                        kw in " ".join(m.get("content", "").lower() for m in history[-6:])
                        for kw in (
                            "meal plan", "next week", "monday", "tuesday", "planning", "shopping list",
                            "cook at home", "don't know what to cook", "decide what to cook", "what to cook",
                            "不知道做什么", "在家做饭", "做什么菜",
                            "同一天", "same day", "那天", "that day", "再加一个", "add another", "也加",
                            # suggest_options flow keywords
                            "方案一", "方案二", "方案", "选方案", "选择方案",
                        )
                    )
                )
            )

            # ── Pending-action boomerang check ───────────────────────────────────
            # If the previous turn stored a deferred MODIFY_RECIPE action (due to
            # low confidence) and the user now confirms with a short affirmative,
            # restore the original intent so the modification executes this turn.
            _pending_modify = plan_state.get("pending_modify_action")
            _confirm_words = {
                "是", "好", "可以", "行", "确认", "保存", "是的", "当然", "好的", "没问题",
                "yes", "ok", "okay", "sure", "confirm", "save", "apply", "update", "do it",
                "就这样", "就按这个",
            }
            _user_tokens = set(user_input.strip().lower().split())
            _is_short_affirmative = (
                len(user_input.strip()) <= 20
                and bool(_user_tokens & _confirm_words)
            )
            if _pending_modify and _is_short_affirmative:
                logger.info(
                    f"[chat] Pending MODIFY_RECIPE confirmed by user '{user_input.strip()}' — "
                    f"restoring action for dish '{_pending_modify.get('dish_name')}'"
                )
                # Clear the pending action immediately to avoid infinite loops
                update_plan_state(owner_id=owner_id, pending_modify_action=None)
                # Synthesise a fake intent_result/action so the MODIFY_RECIPE block runs
                intent_result = type("IR", (), {
                    "intent": Intent.MODIFY_RECIPE,
                    "confidence": 1.0,
                    "reasoning": "Restored from pending_modify_action after user confirmation",
                })()
                intent_action = {"action": "MODIFY_RECIPE", "data": {}}
                active_cooking_ctx = _pending_modify  # re-use the saved context
                # The MODIFY_RECIPE block below reads active_cooking_ctx for dish/steps;
                # also restore the original user_input that requested the change.
                user_input = _pending_modify.get("original_user_input", user_input)
            else:
                # If the user says something other than "yes" while there's a pending action,
                # discard the pending action so it doesn't linger.
                if _pending_modify and not _is_short_affirmative:
                    update_plan_state(owner_id=owner_id, pending_modify_action=None)
                    logger.info(
                        "[chat] Discarded pending_modify_action — user sent a non-affirmative message."
                    )

            # 1. Classify intent — inject session context so the LLM can decide
            #    stickiness intelligently instead of relying on a keyword list.
            session_mode = "PLANNING" if in_plan_ahead_flow else None
            active_cooking_ctx = plan_state.get("cooking_context")  # for DISCUSSING_RECIPE block
            if not (_pending_modify and _is_short_affirmative):
                # Skip re-classification when we already restored a pending intent above
                intent_result = await intent_classifier.classify(
                    user_input,
                    history=history,
                    session_mode=session_mode,
                    cooking_context=active_cooking_ctx,
                )

            # 1b. Lightweight safety nets for cases where the LLM misclassifies.
            #     NOTE: broad "stickiness" is handled by the LLM via session_mode above.
            cooking_step_phrases = (
                "怎么做", "怎么烹饪", "如何做", "做法", "步骤", "教我做",
                "how to cook", "how to make", "how do i cook", "how do i make",
                "cooking steps", "recipe steps", "how to prepare",
            )
            user_lower = user_input.strip().lower()
            is_cooking_query = any(phrase in user_lower for phrase in cooking_step_phrases)
            if in_plan_ahead_flow and is_cooking_query and intent_result.intent == Intent.GENERAL:
                old_intent = intent_result.intent
                intent_result = type(
                    "IntentResult",
                    (),
                    {
                        "intent": Intent.COOKING_STEPS,
                        "reasoning": "User asked how to cook while in meal planning context",
                        "confidence": 0.9,
                    },
                )()
                logger.info(f"Cooking-steps override: {old_intent} -> COOKING_STEPS for: {user_input[:50]}")

            # 1c. Meal-declaration safety net: "今天早饭吃个X / 明天晚上整个X / ..."
            #     When the user states what they intend to eat at a specific meal time /
            #     date, it is an implicit PLAN_AHEAD add request even without keywords
            #     like "add" or "plan".  The LLM sometimes routes these to GENERAL.
            #
            #     Two-tier matching to avoid false positives:
            #       Tier 1 (strong): meal-specific words (早饭/午饭/晚饭/breakfast/lunch/dinner)
            #                        + date word → always override
            #       Tier 2 (weak):   time-of-day words (早上/中午/晚上) + date + eat/drink verb
            #                        → override (avoids "明天晚上有空吗" becoming PLAN_AHEAD)
            if intent_result.intent == Intent.GENERAL:
                _meal_words = ("早饭", "早餐", "午饭", "午餐", "晚饭", "晚餐",
                               "breakfast", "lunch", "dinner")
                _time_of_day = ("早上", "中午", "晚上")   # require eat verb to trigger
                _eat_verbs   = ("吃", "喝")               # narrow eating verbs for tier-2
                _date_words = ("今天", "明天", "后天", "昨天", "大后天",
                               "周一", "周二", "周三", "周四", "周五", "周六", "周日",
                               "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
                               "monday", "tuesday", "wednesday", "thursday", "friday",
                               "saturday", "sunday", "tonight", "tomorrow", "today")
                _past_markers = ("吃了", "喝了", "做了", "已经", "刚才", "刚刚", "已吃",
                                 "ate", "had", "already")
                _has_meal_word = any(w in user_lower for w in _meal_words)
                _has_tod_with_verb = (
                    any(w in user_lower for w in _time_of_day)
                    and any(w in user_lower for w in _eat_verbs)
                )
                _has_date = any(w in user_lower for w in _date_words)
                _is_past  = any(w in user_lower for w in _past_markers)
                if (_has_meal_word or _has_tod_with_verb) and _has_date and not _is_past:
                    old_intent = intent_result.intent
                    intent_result = type(
                        "IntentResult",
                        (),
                        {
                            "intent": Intent.PLAN_AHEAD,
                            "reasoning": "Meal-declaration safety net: meal-time + date → implicit add",
                            "confidence": 0.85,
                        },
                    )()
                    logger.info(
                        f"[chat] Meal-declaration override: {old_intent} -> PLAN_AHEAD "
                        f"for: {user_input[:60]}"
                    )

            # 1d. Meal-option modification safety net.
            #     The Android client wraps follow-up refinements as:
            #       "[针对菜品：X] 用户要求：Y"
            #     This is always a PLAN_AHEAD modification of a proposed option,
            #     never GENERAL regardless of the content of Y.
            if intent_result.intent == Intent.GENERAL and user_input.startswith("[针对菜品："):
                old_intent = intent_result.intent
                intent_result = type(
                    "IntentResult",
                    (),
                    {
                        "intent": Intent.PLAN_AHEAD,
                        "reasoning": "Dish-targeting prefix '[针对菜品：]' detected → meal option modification",
                        "confidence": 0.95,
                    },
                )()
                logger.info(
                    "[chat] Dish-targeting override: %s -> PLAN_AHEAD for: %s",
                    old_intent, user_input[:60],
                )

            # 1e. Pending-options modification safety net.
            #     If the system is currently showing suggest_options (pending_options set)
            #     and the user sends any message that isn't a clear topic change,
            #     it must be a selection or refinement → PLAN_AHEAD.
            if (
                intent_result.intent == Intent.GENERAL
                and plan_state.get("pending_options")
            ):
                old_intent = intent_result.intent
                intent_result = type(
                    "IntentResult",
                    (),
                    {
                        "intent": Intent.PLAN_AHEAD,
                        "reasoning": "Active pending_options: user is selecting or refining meal options",
                        "confidence": 0.9,
                    },
                )()
                logger.info(
                    "[chat] Pending-options override: %s -> PLAN_AHEAD for: %s",
                    old_intent, user_input[:60],
                )

            # 2a. COOKING_STEPS: short-circuit before route_by_intent to pass history.
            #
            # Special case — "confirm + cooking" compound instruction:
            # If the user is in an active draft plan AND their message contains both
            # an implicit/explicit confirmation AND a cooking query (e.g. "可以，周三牛排怎么做"),
            # we first run PlanAheadPipeline to confirm/save the draft, then generate cooking steps.
            _confirm_phrases = (
                "可以", "行", "好的", "没问题", "确认", "保存", "好", "就这样", "就这个",
                "听起来不错", "挺好", "就按这个", "就这么定", "ok", "sure", "yes", "confirm",
                "looks good", "sounds good", "that works", "go ahead",
            )
            _is_draft_context = (
                in_plan_ahead_flow
                and (context and context.get("type") == "plan_ahead")
                and (context.get("data") or {}).get("is_draft", False)
            )
            _has_confirm_signal = any(p in user_lower for p in _confirm_phrases)

            if (
                intent_result.intent == Intent.COOKING_STEPS
                and _is_draft_context
                and _has_confirm_signal
            ):
                # Step 1: confirm the draft via PlanAheadPipeline
                logger.info(
                    f"[COMPOUND] Confirm+CookingSteps detected for: {user_input[:60]}"
                )
                try:
                    from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
                    from app.storage.pipeline_storage import _default_storage
                    _confirm_pipeline = PlanAheadPipeline(gemini_api_url=self.api_url)
                    _confirm_result = await _confirm_pipeline.execute(
                        owner_id=owner_id,
                        user_input="确认保存计划",  # explicit confirm signal so LLM picks action=confirm
                        history=history,
                        user_timezone=user_timezone,
                        storage_client=_default_storage,
                        context=context,
                        intent_result=intent_result,
                        cooking_level=cooking_level,
                        language=language,
                    )
                    # Update context with freshly confirmed plan data
                    context = {
                        "type": "plan_ahead",
                        "data": _confirm_result.get("action_data") or (context.get("data") or {}),
                    }
                    logger.info("[COMPOUND] Draft confirmed; proceeding to cooking steps.")
                except Exception as _conf_err:
                    logger.warning(f"[COMPOUND] Draft confirm step failed: {_conf_err}", exc_info=True)

            # RECIPE_QA: targeted parameter query about an already-discussed dish.
            # Skip CookingStepsAgent entirely — inject cooking_context and let LLM answer directly.
            if intent_result.intent == Intent.RECIPE_QA:
                if active_cooking_ctx and active_cooking_ctx.get("dish_name"):
                    _qa_dish = active_cooking_ctx["dish_name"]
                    _qa_steps = active_cooking_ctx.get("steps") or []
                    if _qa_steps:
                        _qa_steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_qa_steps))
                        _cs_injected_context = (
                            f"\n\n=== RECIPE CONTEXT FOR 「{_qa_dish}」 ===\n"
                            f"{_qa_steps_text}\n"
                            "=== END RECIPE CONTEXT ===\n\n"
                            "The user is asking a specific follow-up question about this recipe. "
                            "Answer the specific question ONLY (e.g. give the exact ratio, temperature, "
                            "timing, or substitution they asked for). "
                            "Use the recipe as the primary source, then SUPPLEMENT with your own culinary "
                            "expertise for any missing specifics — never say 'the recipe doesn't include that'. "
                            "Be concise and direct. Respond in the user's language."
                        )
                    else:
                        # No stored steps — answer from general knowledge
                        _cs_injected_context = (
                            f"\n\n[The user is asking a follow-up about 「{_qa_dish}」. "
                            "Answer from your general culinary knowledge.]\n"
                        )
                else:
                    # No cooking context — treat as general
                    intent_result = type("IR", (), {
                        "intent": Intent.GENERAL,
                        "confidence": 0.7,
                        "reasoning": "RECIPE_QA with no active cooking context; falling back to GENERAL",
                    })()

            # MODIFY_RECIPE: user wants to tweak an ingredient quantity in the existing steps.
            # Requires an active cooking context with steps already generated.
            # Safety gate: if the classifier is not confident enough, treat as RECIPE_QA first
            # and prompt the user to confirm before committing a DB write.
            if intent_result.intent == Intent.MODIFY_RECIPE:
                _mod_confidence = getattr(intent_result, "confidence", 1.0) or 1.0
                if _mod_confidence < 0.8 and active_cooking_ctx and active_cooking_ctx.get("dish_name"):
                    _qa_dish = active_cooking_ctx["dish_name"]
                    _qa_steps = active_cooking_ctx.get("steps") or []
                    _qa_steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_qa_steps)) if _qa_steps else ""
                    _cs_injected_context = (
                        f"\n\n[INTENT CONFIRMATION NEEDED]\n"
                        f"The user's message seems to be asking about modifying the recipe for 「{_qa_dish}」, "
                        "but the intent is ambiguous.\n"
                        f"Current recipe context:\n{_qa_steps_text}\n\n"
                        "INSTRUCTIONS: Answer the user's question naturally (as RECIPE_QA), "
                        "then at the end of your answer ask in the user's language: "
                        "\"要把这个更改保存到食谱中吗？\" (or "
                        "\"Would you like me to save this change to your recipe?\" in English)\n"
                        "Do NOT make any changes to the stored recipe yet."
                    )
                    # Save the deferred action so the next turn can execute it if the user confirms
                    update_plan_state(
                        owner_id=owner_id,
                        pending_modify_action={
                            "dish_name": _qa_dish,
                            "steps": _qa_steps,
                            "schedule_id": active_cooking_ctx.get("schedule_id"),
                            "original_user_input": user_input,
                        },
                    )
                    intent_result = type("IR", (), {
                        "intent": Intent.RECIPE_QA,
                        "confidence": _mod_confidence,
                        "reasoning": f"MODIFY_RECIPE confidence {_mod_confidence:.2f} < 0.8 → confirming intent first",
                    })()
                    logger.info(
                        f"[chat] MODIFY_RECIPE confidence {_mod_confidence:.2f} < 0.8 — "
                        "routing to RECIPE_QA with confirmation prompt; "
                        f"pending_modify_action saved for '{_qa_dish}'"
                    )

            if intent_result.intent == Intent.MODIFY_RECIPE:
                if active_cooking_ctx and active_cooking_ctx.get("steps") and active_cooking_ctx.get("dish_name"):
                    try:
                        from app.agents.cooking_steps_agent import CookingStepsAgent as _CSAgent
                        _mod_dish = active_cooking_ctx["dish_name"]
                        _mod_steps = active_cooking_ctx["steps"]
                        _mod_result = await _CSAgent().execute_modify(
                            user_input=user_input,
                            owner_id=owner_id,
                            current_steps=_mod_steps,
                            dish_name=_mod_dish,
                            context=context,
                            language=language,
                        )
                        _new_steps = _mod_result.get("modified_steps") or _mod_steps
                        _ing_chg = _mod_result.get("ingredient_change")
                        _chg_idx = _mod_result.get("changed_indices") or []

                        # Build a diff summary for the LLM to narrate naturally
                        _diff_lines = []
                        for idx in _chg_idx:
                            if idx < len(_mod_steps) and idx < len(_new_steps):
                                _diff_lines.append(
                                    f"  Step {idx + 1}: «{_mod_steps[idx]}»  →  «{_new_steps[idx]}»"
                                )
                        _diff_text = "\n".join(_diff_lines) if _diff_lines else "(steps updated)"
                        _ing_note = ""
                        if _ing_chg:
                            _ing_note = (
                                f"\nIngredient updated: {_ing_chg.get('name')} "
                                f"{_ing_chg.get('old_qty')} → {_ing_chg.get('new_qty')}"
                            )

                        from app.agents.cooking_steps_agent import _clean_steps_for_llm as _cfl
                        _steps_text = _cfl(_new_steps)
                        _saved_note = (
                            "\n[Changes saved to meal plan]"
                            if _mod_result.get("saved") else
                            "\n[Note: changes could not be auto-saved — user may need to regenerate]"
                        )
                        _cs_injected_context = (
                            f"\n\n=== RECIPE MODIFICATION FOR 「{_mod_dish}」 ===\n"
                            f"Changes applied:\n{_diff_text}{_ing_note}\n"
                            f"{_saved_note}\n"
                            f"\nFull updated recipe (USE THESE EXACT QUANTITIES in your reply):\n{_steps_text}\n"
                            "=== END ===\n\n"
                            "Confirm the change to the user. "
                            "CRITICAL: quote the ACTUAL new quantities from the updated recipe above — "
                            "do NOT invent or approximate numbers. "
                            "Do NOT re-list all steps unless the user asks. "
                            "Respond in the user's language."
                        )
                        _cs_action_data = {
                            "dish_name": _mod_dish,
                            "cooking_steps": _new_steps,
                            "ingredient_change": _ing_chg,
                            "saved": _mod_result.get("saved", False),
                            "schedule_id": _mod_result.get("schedule_id"),
                        }
                        # Update cooking context so subsequent turns see the new steps
                        update_plan_state(
                            owner_id=owner_id,
                            cooking_context={
                                "dish_name": _mod_dish,
                                "steps": _new_steps,
                                "schedule_id": _mod_result.get("schedule_id"),
                            },
                        )
                    except Exception as _mod_err:
                        logger.error(f"[MODIFY_RECIPE] Agent failed: {_mod_err}", exc_info=True)
                        _cs_injected_context = (
                            "\n\n[Failed to apply the recipe modification. "
                            "Apologise briefly and ask the user to try again.]\n"
                        )
                else:
                    # No active recipe context — nudge the user to generate steps first
                    _cs_injected_context = (
                        "\n\n[MODIFY_RECIPE: no active recipe context found. "
                        "Tell the user to first ask for cooking steps, then request the modification.]\n"
                    )

            # COOKING_STEPS: agent acts as a "data fetcher" — structured steps are injected
            # into the LLM system prompt so Gemini can answer naturally and handle follow-ups.
            # This replaces the old hard short-circuit that returned a fixed template string.
            if intent_result.intent == Intent.COOKING_STEPS:
                try:
                    from app.agents.cooking_steps_agent import CookingStepsAgent as _CSAgent

                    # ── "Replace-from-context" mode ──────────────────────────────────────────
                    # When the user wants to REPLACE existing steps with the already-discussed
                    # alternative method (e.g. "替换现有的方案") or confirms a pending replace
                    # ("确定" after AI asked "确认替换吗?"), skip re-generation and directly
                    # PATCH the in-context steps to the DB.
                    _replace_triggers = ("替换", "换成这个", "用这个方案", "更换步骤", "replace")
                    _confirm_triggers = ("确定", "好的", "是的", "对", "yes", "确认")
                    _has_pending_replace = bool((active_cooking_ctx or {}).get("pending_replace"))
                    _is_replace_mode = (
                        (
                            any(t in user_lower for t in _replace_triggers)
                            or (_has_pending_replace and any(t in user_lower for t in _confirm_triggers))
                        )
                        and (active_cooking_ctx or {}).get("steps")
                        and (active_cooking_ctx or {}).get("dish_name")
                    )
                    if _is_replace_mode:
                        _ctx_dish = active_cooking_ctx["dish_name"]
                        _ctx_steps = active_cooking_ctx["steps"]
                        logger.info(
                            f"[COOKING_STEPS] Replace-from-context: saving {len(_ctx_steps)} steps for '{_ctx_dish}'"
                        )
                        _repl_plan_state = get_plan_state(owner_id)
                        _repl_sched_id = _repl_plan_state.get("schedule_id")
                        _repl_slots: Dict[str, Any] = _repl_plan_state.get("meal_plan_slots") or {}
                        # Find date/meal_time, preferring today then nearest future
                        from datetime import date as _date_cls
                        _today_iso = _date_cls.today().isoformat()

                        def _repl_dp(d: str) -> tuple:
                            if d == _today_iso:
                                return (0, d)
                            return (1, d) if d > _today_iso else (2, "~" + d)

                        _repl_date: Optional[str] = None
                        _repl_mt: Optional[str] = None
                        for _rdk in sorted(_repl_slots.keys(), key=_repl_dp):
                            for _rmtt, _rdishes in (_repl_slots[_rdk] or {}).items():
                                if isinstance(_rdishes, str):
                                    _rdishes = [_rdishes]
                                if any(str(d).strip().lower() == _ctx_dish.lower() for d in (_rdishes or [])):
                                    _repl_date, _repl_mt = _rdk, _rmtt
                                    break
                            if _repl_date:
                                break
                        _repl_agent = _CSAgent()
                        _repl_saved, _repl_sid = await _repl_agent._save_steps(
                            owner_id=owner_id,
                            schedule_id=_repl_sched_id,
                            dish_name=_ctx_dish,
                            steps=_ctx_steps,
                            date=_repl_date,
                            meal_time=_repl_mt,
                            ingredients=[],
                        )
                        if _repl_saved:
                            _repl_steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_ctx_steps))
                            _cs_injected_context = (
                                f"\n\n=== REPLACED STEPS FOR 「{_ctx_dish}」 ===\n"
                                f"{_repl_steps_text}\n"
                                "=== END ===\n\n"
                                f"The cooking steps for 「{_ctx_dish}」 have been successfully REPLACED and "
                                "saved to the meal plan. Give a brief, friendly confirmation to the user. "
                                "Respond in the user's language."
                            )
                            update_plan_state(
                                owner_id=owner_id,
                                cooking_context={
                                    "dish_name": _ctx_dish,
                                    "steps": _ctx_steps,
                                    "schedule_id": _repl_sid,
                                    "pending_replace": True,  # allow one more confirmation turn
                                },
                            )
                            _cs_action_data = {
                                "dish_name": _ctx_dish,
                                "cooking_steps": _ctx_steps,
                                "saved": True,
                                "replaced": True,
                            }
                        else:
                            _cs_injected_context = (
                                f"\n\n[Failed to replace steps for 「{_ctx_dish}」. "
                                "Apologise briefly and ask the user to try again.]\n"
                            )
                        _cs_result = None  # skip normal execute() below

                    # "Save pending" fast path: compound flow previously generated context
                    # but step generation failed. Re-run batch for all pending dishes.
                    _pending_dishes = (
                        (active_cooking_ctx or {}).get("all_dishes") or []
                        if (active_cooking_ctx or {}).get("pending_save")
                        else []
                    ) if not _is_replace_mode else []
                    if _pending_dishes:
                        logger.info(
                            f"[COOKING_STEPS] Re-running batch for pending dishes: {_pending_dishes}"
                        )
                        _plan_state_data = get_plan_state(owner_id)
                        _save_ctx = {
                            "type": "plan_ahead",
                            "data": {
                                "schedule_id": _plan_state_data.get("schedule_id"),
                                "meal_plan_slots": _plan_state_data.get("meal_plan_slots") or {},
                                "dish_ingredients": _plan_state_data.get("dish_ingredients") or {},
                            },
                        }
                        _batch = await _CSAgent().execute_batch(
                            dish_names=_pending_dishes,
                            owner_id=owner_id,
                            context=_save_ctx,
                            cooking_level=cooking_level,
                            language=language,
                        )
                        _ok_batch = [r for r in _batch if not isinstance(r, Exception) and r.get("cooking_steps")]
                        if _ok_batch:
                            _steps_blocks = []
                            for r in _ok_batch:
                                _steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(r["cooking_steps"]))
                                _steps_blocks.append(
                                    f"\n**{r['dish_name']}**\n{_steps_text}"
                                )
                            _cs_injected_context = (
                                "\n\n=== RECIPE DATA (BATCH SAVE) ===\n"
                                + "".join(_steps_blocks)
                                + "\n=== END RECIPE DATA ===\n\n"
                                "The steps above have been saved to the meal plan. "
                                "Present them clearly to the user and confirm they have been saved. "
                                "Respond in the user's language."
                            )
                            # Update cooking_context to reflect saved state
                            update_plan_state(
                                owner_id=owner_id,
                                cooking_context={
                                    "dish_name": _ok_batch[-1]["dish_name"],
                                    "steps": _ok_batch[-1]["cooking_steps"],
                                    "schedule_id": _ok_batch[-1].get("schedule_id"),
                                    "all_dishes": [r["dish_name"] for r in _ok_batch],
                                },
                            )
                            _cs_action_data = {"batch_results": _ok_batch}
                        else:
                            _cs_injected_context = (
                                "\n\n[Step generation failed for all dishes. "
                                "Apologise and ask the user to try again.]\n"
                            )
                        # Skip the normal single-dish execute() below
                        intent_action = await route_by_intent(
                            intent_result.intent, user_input, owner_id, context=context
                        )
                        # Jump to LLM call by falling through (no further agent call needed)
                        _cs_result = None  # sentinel: handled above
                    else:
                        _cs_result = None  # will be set below

                    if _cs_result is None and not _cs_injected_context:
                        # Normal single-dish path
                        _cs_result = await _CSAgent().execute(
                            user_input=user_input,
                            owner_id=owner_id,
                            context=context,
                            history=history,
                            cooking_level=cooking_level,
                            language=language,
                        )

                    # Process single-dish result (batch-save path already set _cs_injected_context)
                    _cs_data: Dict[str, Any] = {}
                    _steps: list = []
                    _dish: str = ""
                    if _cs_result is not None:
                        _cs_data = _cs_result.get("data") or {}
                        _cs_action_data = _cs_data
                        _steps = _cs_data.get("cooking_steps", [])
                        _dish = _cs_data.get("dish_name", "")

                    # ASK_OVERWRITE: recipe conflict — show proposed recipe, ask user to confirm save.
                    _is_overwrite_ask = (
                        _cs_result is not None
                        and _cs_result.get("action") == "ASK_OVERWRITE"
                    )
                    if _is_overwrite_ask:
                        _proposed = _cs_data.get("proposed_steps") or _cs_data.get("cooking_steps") or []
                        _prop_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_proposed))
                        _cs_injected_context = (
                            f"\n\n=== PROPOSED NEW RECIPE FOR 「{_dish}」 ===\n"
                            f"{_prop_text}\n"
                            "=== END PROPOSED RECIPE ===\n\n"
                            f"A recipe for 「{_dish}」 already exists in the meal plan. "
                            "You have just generated a new version. "
                            "Briefly highlight the most notable changes or key characteristics of the new recipe, "
                            "then tell the user they can save this new version using the button below "
                            "or keep the existing one. Be concise and friendly. "
                            "Respond in the user's language."
                        )
                        # Persist the pending overwrite so the user can still confirm later
                        update_plan_state(
                            owner_id=owner_id,
                            pending_overwrite=_cs_data,
                            cooking_context={
                                "dish_name": _dish,
                                "steps": _proposed,
                                "schedule_id": _cs_data.get("schedule_id"),
                            },
                        )

                    if _steps and not _is_overwrite_ask:
                        _steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_steps))
                        _saved_note = (
                            "\n[Steps saved to meal plan — user can view them in the dish detail card]"
                            if _cs_data.get("saved") else ""
                        )
                        _cs_injected_context = (
                            f"\n\n=== RECIPE DATA FOR 「{_dish}」 ===\n"
                            f"{_steps_text}"
                            f"{_saved_note}\n"
                            "=== END RECIPE DATA ===\n\n"
                            "Use the recipe data above as the PRIMARY source to answer the user's question. "
                            "If the recipe data is missing specific details the user is asking about "
                            "(e.g. exact sauce ratios, temperatures, cooking times, ingredient substitutions), "
                            "SUPPLEMENT with your own culinary expertise to give a complete, helpful answer — "
                            "do NOT say 'the recipe doesn't include that information'. "
                            "Present steps clearly if asked for the full recipe, or answer only the specific "
                            "part they asked about. Respond conversationally in the user's language."
                        )
                        # Persist cooking context so follow-ups skip re-generation
                        update_plan_state(
                            owner_id=owner_id,
                            cooking_context={
                                "dish_name": _dish,
                                "steps": _steps,
                                "schedule_id": _cs_data.get("schedule_id"),
                            },
                        )
                    elif not _cs_injected_context:
                        # Agent couldn't find/generate steps — fall through to LLM with a nudge
                        _msg = _cs_result.get("message", "") if _cs_result else ""
                        _cs_injected_context = (
                            f"\n\n[CookingStepsAgent could not find steps: {_msg}]\n"
                            "Tell the user you couldn't identify the dish or find cooking steps, "
                            "and ask them to clarify which dish they mean."
                        )
                except Exception as _cs_err:
                    logger.error(f"[COOKING_STEPS] Agent failed: {_cs_err}", exc_info=True)
                    _cs_injected_context = (
                        "\n\n[CookingStepsAgent encountered an error. "
                        "Apologise briefly and ask the user to try again.]\n"
                    )

            # 2b. Get intent-specific mock action/data (pass context for e.g. plan_ahead state)
            intent_action = await route_by_intent(
                intent_result.intent, user_input, owner_id, context=context
            )

        # 3. Generate response using Gemini
        # We include the detected intent and mock action in the prompt to guide the AI's response
        system_instruction = self.SYSTEM_PROMPT.format(
            intent=intent_result.intent.value,
            reasoning=intent_result.reasoning,
            language_instruction=_language_instruction,
        )

        # Inject cooking steps data (if agent ran) into system prompt so LLM can answer naturally
        if _cs_injected_context:
            system_instruction += _cs_injected_context

        # Add context about the specific action we're taking
        context_msg = f"\nSystem Action: {intent_action['message']}"

        # Inject active cooking context for GENERAL follow-up questions about a recipe
        # (e.g. "酱料比例是多少?" after the AI showed a recipe).
        # Skip if COOKING_STEPS or RECIPE_QA already injected the context above.
        if (
            not _cs_injected_context  # skip if already injected
            and intent_result.intent == Intent.GENERAL
            and active_cooking_ctx
            and active_cooking_ctx.get("dish_name")
        ):
            _ctx_dish = active_cooking_ctx["dish_name"]
            _ctx_steps = active_cooking_ctx.get("steps") or []
            if _ctx_steps:
                _ctx_steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_ctx_steps))
                context_msg += (
                    f"\n\n=== RECIPE CONTEXT (already shared with user) ===\n"
                    f"Dish: 「{_ctx_dish}」\n"
                    f"{_ctx_steps_text}\n"
                    "=== END RECIPE CONTEXT ===\n"
                    "Use the recipe above as the PRIMARY source to answer the follow-up question. "
                    "If the user is asking for a specific detail not in the recipe "
                    "(e.g. exact ratios, temperatures, substitutions, variations), "
                    "SUPPLEMENT with your own culinary expertise — do NOT say the recipe lacks that info. "
                    "Be concise and targeted; don't re-list all steps unless explicitly asked."
                )

        # Track if this turn was a schedule lookup only (no plan-ahead modification). If so, we skip persistence in response.
        schedule_context_added_this_turn = False

        # Handle CORRECTION_MODE
        if is_correction:
             from app.pipelines.correction import correction_pipeline
             items = context.get("data", [])
             # Extract metadata from context if available
             metadata = context.get("metadata", {})
             
             # Call correction pipeline with metadata
             correction_result = await correction_pipeline.run(user_input, items, metadata if metadata else None)
             
             context_msg += f"\n\nCONTEXT: The user is viewing a list of {len(items)} items and wants to CORRECT them."
             context_msg += f"\nCURRENT LIST JSON: {json.dumps(items, indent=2)}"
             if metadata:
                 if metadata.get("ocr_text"):
                     context_msg += f"\nORIGINAL OCR TEXT AVAILABLE: {len(metadata.get('ocr_text', ''))} characters"
                 if metadata.get("vision_understanding"):
                     context_msg += "\nVISION UNDERSTANDING AVAILABLE: Yes"
             context_msg += f"\n\nCORRECTION RESULT: {json.dumps(correction_result, indent=2)}"
             context_msg += "\n\nCRITICAL: You have processed the correction."
             context_msg += "\n1. Summarize the changes you made in a friendly way."
             context_msg += "\n2. Ask the user to confirm by clicking the 'Apply Changes' button."
             
             intent_action['action'] = "APPLY_CORRECTION"
             intent_action['data'] = correction_result

        # Handle SEARCH intent results
        if intent_action['action'] == "SEARCH":
            if intent_action['data'].get('document_ids'):
                 document_ids = intent_action['data']['document_ids']
                 documents = intent_action['data'].get('documents', [])
                 num_found = len(document_ids)
                 
                 context_msg += f"\nSearch Results: Found exactly {num_found} document(s).\n"
                 context_msg += "Here are the details of the found items:\n"
                 
                 for idx, doc in enumerate(documents):
                     meta = doc.get("metadata", {})
                     # Try to find product name in various fields
                     # 1. First check document title (most reliable if set)
                     product_name = doc.get("title")
                     
                     # 2. Check metadata product_name/item_name
                     if not product_name:
                         product_name = meta.get("product_name") or meta.get("item_name")
                     
                     # 3. Fallback to OCR text if product name is missing
                     if not product_name:
                         ocr_text = doc.get("ocr_text", "")
                         if ocr_text:
                             # Use first line or first 30 chars of OCR text
                             product_name = ocr_text.split('\n')[0][:30]
                         else:
                             product_name = f"Document #{doc.get('id', 'Unknown')}"
                     
                     category = meta.get("category", "Unknown Category")
                     # Try to find location/storage info
                     # 1. First check document location_name (from DB relationship)
                     # 2. Then check metadata storage_condition/location
                     location = doc.get("location_name") or meta.get("storage_condition") or meta.get("location") or "Unknown Location"
                     qty = meta.get("quantity") or meta.get("quantity_unit") or "N/A"
                     expiry = meta.get("expiry_date") or meta.get("expiration_date") or "N/A"
                     
                     context_msg += f"{idx+1}. {product_name} [Category: {category}] [Location: {location}] [Qty: {qty}] [Expiry: {expiry}]\n"
                 
                 context_msg += "\nCRITICAL: You MUST construct your response based ONLY on the above search results. Do NOT invent items not listed here. If the details are generic (e.g. 'Document #123'), just describe what you see."
            else:
                 # No documents found
                 context_msg += "\nSearch Results: 0 documents found."
                 context_msg += "\nCRITICAL: The search returned NO results. You MUST explicitly state that no items matching the request were found in the inventory."
                 context_msg += "\nDO NOT invent or hallucinate any items. DO NOT say 'Here is what you have' if the list is empty."
        
        # Handle UPDATE intent results
        if intent_action['action'] == "UPDATE":
            if intent_action['data'].get('document_ids'):
                 document_ids = intent_action['data']['document_ids']
                 documents = intent_action['data'].get('documents', [])
                 num_found = len(document_ids)
                 changes = intent_action['data'].get('proposed_changes', {})
                 
                 context_msg += f"\nUpdate Candidates: Found {num_found} document(s) that might match the update request.\n"
                 if changes:
                     context_msg += f"Proposed Changes: {json.dumps(changes)}\n"
                 
                 context_msg += "Here are the details of the candidates:\n"
                 
                 for idx, doc in enumerate(documents):
                     meta = doc.get("metadata", {})
                     product_name = doc.get("title")
                     if not product_name:
                         product_name = meta.get("product_name") or meta.get("item_name")
                     if not product_name:
                         ocr_text = doc.get("ocr_text", "")
                         if ocr_text:
                             product_name = ocr_text.split('\n')[0][:30]
                         else:
                             product_name = f"Document #{doc.get('id', 'Unknown')}"
                     
                     category = meta.get("category", "Unknown Category")
                     location = doc.get("location_name") or meta.get("storage_condition") or meta.get("location") or "Unknown Location"
                     qty = meta.get("quantity") or meta.get("quantity_unit") or "N/A"
                     expiry = meta.get("expiry_date") or meta.get("expiration_date") or "N/A"
                     
                     context_msg += f"{idx+1}. {product_name} [Category: {category}] [Location: {location}] [Qty: {qty}] [Expiry: {expiry}] (ID: {doc.get('id', doc.get('document_id', 'Unknown'))})\n"
                 
                 context_msg += "\nCRITICAL: Tell the user to CLICK the 'UPDATE' button on the correct item below to confirm the changes."
                 context_msg += "\nDO NOT ask for text confirmation if the user can click."
                 context_msg += "\nSummarize what will be changed based on 'Proposed Changes'."
            else:
                 context_msg += "\nUpdate Search Results: 0 documents found."
                 context_msg += "\nTell the user you couldn't find the item they wanted to update."
        
        # PLAN_AHEAD: inject inventory from Plan Cook Home sub-agent (cook-at-home is a sub-flow of plan-ahead)
        if intent_action['action'] == "PLAN_AHEAD":
            try:
                from app.agents.agent_factory import agent_factory
                cook_home_result = await agent_factory.get_plan_cook_home_sub_agent().execute(
                    user_input=user_input, owner_id=owner_id
                )
                inventory_items = (cook_home_result.get("data") or {}).get("inventory_items", [])
                inventory_count = (cook_home_result.get("data") or {}).get("inventory_count", 0)
            except Exception as e:
                logger.warning(f"Plan Cook Home sub-agent failed (continuing without inventory): {e}")
                inventory_items = []
                inventory_count = 0
            if inventory_count > 0:
                context_msg += f"\n\nUSER'S ACTUAL INVENTORY ({inventory_count} items, from Plan Cook Home sub-agent):"
                context_msg += "\nCRITICAL: When suggesting recipes for cooking at home, use ONLY these actual ingredients."
                context_msg += "\nDO NOT hallucinate or invent ingredients not in this list.\n"
                items_by_category = {}
                item_count = 0
                max_items = 30
                for item in inventory_items:
                    if item_count >= max_items:
                        break
                    category = item.get('category', 'Other')
                    product_name = item.get('product_name', 'Unknown')
                    if category not in items_by_category:
                        items_by_category[category] = []
                    items_by_category[category].append(product_name)
                    item_count += 1
                for category, items in items_by_category.items():
                    context_msg += f"\n{category}:\n"
                    for item_name in items:
                        context_msg += f"  - {item_name}\n"
                if inventory_count > max_items:
                    context_msg += f"\n(Showing first {max_items} of {inventory_count} items)"
                context_msg += "\nWhen suggesting a recipe, list which items from the inventory above will be used."
            else:
                context_msg += "\n\nINVENTORY STATUS (Plan Cook Home): No food items found in the user's inventory."
                context_msg += "\nWhen suggesting recipes, acknowledge this and suggest what they might need to buy or upload a receipt first."

        # Handle schedule queries using SchedulingResponseGenerator
        # NOTE: schedule *fetch* (get_user_schedules) is temporarily disabled.
        # Time inference and context generation still work, but actual DB fetch is skipped.
        # Skip for cooking-focused intents (COOKING_STEPS, MODIFY_RECIPE, RECIPE_QA) — these
        # handle their own context via _cs_injected_context and don't benefit from schedule data.
        # Injecting schedule context into these turns causes the LLM to mix schedule information
        # into what should be a focused cooking-step confirmation, changing the response layout.
        _cooking_only_intent = intent_result.intent in (
            Intent.COOKING_STEPS,
            Intent.MODIFY_RECIPE,
            Intent.RECIPE_QA,
        )
        logger.info(
            f"[SCHEDULING AGENT] Checking if scheduling agents are available: {SCHEDULING_AGENTS_AVAILABLE}, "
            f"fetch_enabled={ENABLE_SCHEDULE_FETCH_IN_CHAT}, "
            f"cooking_only_intent={_cooking_only_intent} (skip={_cooking_only_intent})"
        )
        if SCHEDULING_AGENTS_AVAILABLE and not _cooking_only_intent:
            try:
                logger.info(f"[SCHEDULING AGENT] Processing query: {user_input[:100]}")
                logger.info(f"[SCHEDULING AGENT] User timezone: {user_timezone}")
                # Create session context from current query and history
                session_context = ScheduleSessionContext(
                    current_query=user_input,
                    history=history or [],
                    user_timezone=user_timezone,
                )
                
                # Initialize generator
                scheduling_generator = SchedulingResponseGenerator()
                
                # Log agent capabilities
                capabilities = scheduling_generator.get_agent_capabilities()
                logger.info(
                    f"[SCHEDULING AGENT] Agents available: {len(capabilities.get('agents', []))} agents"
                )
                logger.info(
                    f"[SCHEDULING AGENT] Capabilities: {capabilities.get('capabilities', [])}"
                )
                
                # Generate context to decide if we need to fetch schedules (LLM parses time when api_url provided)
                logger.info("[SCHEDULING AGENT] Calling generate_context to decide if schedules should be fetched")
                schedule_context = await scheduling_generator.generate_context(
                    context=session_context,
                    schedules=None,  # Will be fetched if needed
                    api_url=self.api_url,
                )
                
                logger.info(
                    f"[SCHEDULING AGENT] Decision result: should_fetch={schedule_context.get('should_fetch')}, "
                    f"has_time_range={schedule_context.get('time_range') is not None}"
                )
                
                # If we should fetch schedules, get them from storage
                if schedule_context.get("should_fetch") and schedule_context.get("time_range"):
                    time_range = schedule_context["time_range"]
                    logger.info(
                        f"[SCHEDULING AGENT] Should fetch schedules for range "
                        f"{time_range.start} to {time_range.end}, fetch_enabled={ENABLE_SCHEDULE_FETCH_IN_CHAT}"
                    )
                    
                    # Fetch schedules from storage via scheduling agent (by time range)
                    filtered_schedules = []
                    if ENABLE_SCHEDULE_FETCH_IN_CHAT:
                        from app.storage.pipeline_storage import _default_storage
                        filtered_schedules = await scheduling_generator.fetch_schedules_in_range(
                            owner_id=owner_id,
                            time_range=time_range,
                            storage_client=_default_storage,
                        )
                    else:
                        logger.info(
                            f"[SCHEDULING AGENT] Schedule fetch is disabled; skipping DB fetch, "
                            f"but time inference and context generation still work"
                        )
                    
                    # Only regenerate context if we found schedules OR if first call had no schedules
                    # If first call had schedules=None and we still have no schedules, reuse first result
                    if filtered_schedules:
                        # Found schedules: regenerate context with actual schedule data
                        logger.info(
                            f"[SCHEDULING AGENT] Regenerating context with {len(filtered_schedules)} fetched schedules, "
                            f"reusing time_range to avoid redundant computation"
                        )
                        schedule_context = await scheduling_generator.generate_context(
                            context=session_context,
                            schedules=filtered_schedules,
                            time_range=time_range,  # Reuse pre-computed time_range
                            api_url=self.api_url,
                        )
                    else:
                        # No schedules found: reuse first result (already has "no schedules" message)
                        logger.info(
                            f"[SCHEDULING AGENT] No schedules found, reusing first context result "
                            f"(no need to regenerate)"
                        )
                        # schedule_context already contains the correct "no schedules" message
                    
                    # Add schedule context to context_msg
                    if schedule_context.get("context_message"):
                        context_msg += schedule_context["context_message"]
                        schedule_context_added_this_turn = True
                        logger.info(
                            f"[SCHEDULING AGENT] Added schedule context to response "
                            f"(length: {len(schedule_context.get('context_message', ''))} chars)"
                        )
                    else:
                        logger.info("[SCHEDULING AGENT] No context message to add")
                else:
                    logger.info(
                        f"[SCHEDULING AGENT] No need to fetch schedules: "
                        f"should_fetch={schedule_context.get('should_fetch')}, "
                        f"has_time_range={schedule_context.get('time_range') is not None}"
                    )
            except Exception as e:
                logger.error(f"[SCHEDULING AGENT] Error: {e}", exc_info=True)
                # Continue without schedule context if there's an error
        elif _cooking_only_intent:
            logger.info(
                f"[SCHEDULING AGENT] Skipping for cooking-focused intent ({intent_result.intent.value}); "
                "schedule context is unnecessary and would pollute the cooking-step response."
            )
        else:
            logger.info("[SCHEDULING AGENT] Scheduling agents not available, skipping schedule query processing")

        if intent_action['action'] == "PLAN_AHEAD":
            # Phase 3: delegate to PlanAheadPipeline (single structured LLM call).
            if SCHEDULING_AGENTS_AVAILABLE:
                try:
                    from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
                    from app.storage.pipeline_storage import _default_storage
                    _pipeline = PlanAheadPipeline(gemini_api_url=self.api_url)

                    # Fetch user profile for meal blueprint (cuisine weights, disliked ingredients, etc.)
                    _user_profile = await _default_storage.get_user_profile(owner_id)
                    if _user_profile:
                        # Override recent_dishes with real-time data derived from schedule records
                        # so the diversity engine always reflects the user's actual meal history
                        # rather than the potentially stale User.recent_dishes JSON field.
                        _rt_recent = await _default_storage.get_recent_dishes_from_schedules(owner_id)
                        _user_profile["recent_dishes"] = _rt_recent

                        _disliked   = _user_profile.get("disliked_ingredients") or []
                        _recent     = _user_profile.get("recent_dishes") or []
                        _servings   = _user_profile.get("default_servings", 1)
                        logger.info(
                            f"[MEAL_BLUEPRINT] User {owner_id} profile loaded: "
                            f"servings={_servings}, "
                            f"disliked={_disliked if _disliked else '(none)'}, "
                            f"recent_dishes_count={len(_recent)} (from schedules)"
                            + (f", recent_sample={[e['dish'] for e in _recent[:3]]}" if _recent else "")
                        )
                    else:
                        logger.warning(f"[MEAL_BLUEPRINT] User {owner_id} profile NOT loaded — blueprint/diversity skipped.")

                    # ── COMPOUND CHECK: PLAN_AHEAD + COOKING_STEPS ──────────────────────
                    # When the user says e.g. "今天晚上加宫保鸡丁和鱼香肉丝，附上做法":
                    #   1. Run PlanAheadPipeline to add the dishes (also persists to DB).
                    #   2. Batch-generate cooking steps for the extracted dishes in parallel.
                    #   3. Synthesize both results into one natural LLM response.
                    _cmpd_intents = list(getattr(intent_result, "compound_intents", None) or [])
                    _cmpd_dishes  = list(getattr(intent_result, "extracted_items",  None) or [])
                    _is_compound_plan_cook = (
                        Intent.PLAN_AHEAD in _cmpd_intents
                        and Intent.COOKING_STEPS in _cmpd_intents
                        and bool(_cmpd_dishes)
                    )

                    if _is_compound_plan_cook:
                        logger.info(
                            f"[COMPOUND] PLAN_AHEAD+COOKING_STEPS detected. "
                            f"Dishes: {_cmpd_dishes}"
                        )
                        # Step A: run plan pipeline (adds dishes, persists to DB)
                        _cmpd_pa_context = dict(context or {})
                        if context_msg.strip():
                            _cmpd_pa_context["scheduling_context"] = context_msg.strip()
                        _plan_result = await _pipeline.execute(
                            owner_id=owner_id,
                            user_input=user_input,
                            history=history,
                            user_timezone=user_timezone,
                            storage_client=_default_storage,
                            context=_cmpd_pa_context,
                            intent_result=intent_result,
                            cooking_level=cooking_level,
                            user_profile=_user_profile,
                        )
                        _plan_data = _plan_result.get("action_data") or {}

                        # Step B: batch-generate + save steps for all dishes in parallel
                        from app.agents.cooking_steps_agent import CookingStepsAgent as _CSAgent
                        _batch_ctx = {"type": "plan_ahead", "data": _plan_data}
                        _batch_results = await _CSAgent().execute_batch(
                            dish_names=_cmpd_dishes,
                            owner_id=owner_id,
                            context=_batch_ctx,
                            cooking_level=cooking_level,
                            language=language,
                        )

                        # Step C: build synthesis context for the final LLM call
                        _synth_sys = self.SYSTEM_PROMPT.format(
                            intent="PLAN_AHEAD",
                            reasoning="Compound task: dishes added to plan + cooking steps generated.",
                            language_instruction=_language_instruction,
                        )
                        _synth_ctx = (
                            "\n=== COMPOUND ACTION RESULTS ===\n"
                            f"Step 1 — Plan update completed:\n{_plan_result.get('response', '')}\n"
                            "\nStep 2 — Cooking steps generated:\n"
                        )
                        _successful_batches = [
                            r for r in _batch_results
                            if not isinstance(r, Exception) and r.get("cooking_steps")
                        ]
                        for r in _successful_batches:
                            _synth_ctx += f"\n**{r['dish_name']}**\n"
                            for i, s in enumerate(r["cooking_steps"]):
                                _synth_ctx += f"{i+1}. {s}\n"
                        _synth_ctx += (
                            "\n=== END COMPOUND RESULTS ===\n\n"
                            "Write a single, natural reply that:\n"
                            "1. Confirms what was added to the meal plan (dish names + meal slot).\n"
                            "2. Presents the cooking steps for EACH dish in a clear, readable format.\n"
                            "3. Uses the same language as the user."
                        )
                        _synth_sys += _synth_ctx

                        # Step D: synthesis LLM call
                        _synth_contents: List[Dict[str, Any]] = []
                        if history:
                            for _msg in history:
                                _r = "user" if _msg["role"] == "user" else "model"
                                _synth_contents.append({"role": _r, "parts": [{"text": _msg["content"]}]})
                        _synth_contents.append({"role": "user", "parts": [{"text": user_input}]})
                        _synth_payload = {
                            "contents": _synth_contents,
                            "systemInstruction": {"parts": [{"text": _synth_sys}]},
                            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
                        }
                        _synth_text = _plan_result.get("response", "")
                        try:
                            async with httpx.AsyncClient(timeout=30.0) as _hc:
                                _sr = await _hc.post(
                                    self.api_url,
                                    headers={"Content-Type": "application/json"},
                                    json=_synth_payload,
                                )
                                _sr.raise_for_status()
                                _synth_text = (
                                    _sr.json()
                                    .get("candidates", [{}])[0]
                                    .get("content", {})
                                    .get("parts", [{}])[0]
                                    .get("text", _synth_text)
                                )
                        except Exception as _se:
                            logger.error(f"[COMPOUND] Synthesis LLM call failed: {_se}", exc_info=True)

                        # Persist cooking context for follow-ups.
                        # Even when step generation failed (successful_batches is empty),
                        # store the dish names so "把步骤加进去" can re-trigger generation.
                        if _successful_batches:
                            _last = _successful_batches[-1]
                            update_plan_state(
                                owner_id=owner_id,
                                cooking_context={
                                    "dish_name": _last["dish_name"],
                                    "steps": _last["cooking_steps"],
                                    "schedule_id": _last.get("schedule_id"),
                                    "all_dishes": [r["dish_name"] for r in _successful_batches],
                                },
                            )
                        elif _batch_results:
                            # Steps failed — record pending dishes so the user can ask again
                            _pending = [
                                r["dish_name"] for r in _batch_results
                                if not isinstance(r, Exception) and r.get("dish_name")
                            ]
                            if _pending:
                                update_plan_state(
                                    owner_id=owner_id,
                                    cooking_context={
                                        "dish_name": _pending[-1],
                                        "steps": [],
                                        "all_dishes": _pending,
                                        "pending_save": True,
                                    },
                                )

                        return {
                            "response": _synth_text,
                            "intent": Intent.PLAN_AHEAD,
                            "confidence": intent_result.confidence,
                            "reasoning": intent_result.reasoning,
                            "action": "COMPOUND",
                            "action_data": {
                                "plan": _plan_data,
                                "cooking_steps_results": [
                                    r for r in _batch_results if not isinstance(r, Exception)
                                ],
                            },
                        }

                    # ── Normal (non-compound) PLAN_AHEAD ────────────────────────────────
                    # Augment context with the Scheduling Agent's current-schedule
                    # summary (built earlier in this turn). The pipeline can use this
                    # to detect and avoid adding duplicate meals.
                    _pa_context = dict(context or {})
                    if context_msg.strip():
                        _pa_context["scheduling_context"] = context_msg.strip()
                    _pa_result = await _pipeline.execute(
                        owner_id=owner_id,
                        user_input=user_input,
                        history=history,
                        user_timezone=user_timezone,
                        storage_client=_default_storage,
                        context=_pa_context,
                        intent_result=intent_result,
                        cooking_level=cooking_level,
                        language=language,
                        user_profile=_user_profile,
                    )

                    # ── Log the full AI response ──────────────────────────────────
                    _pa_response_text = _pa_result.get("response", "")
                    logger.info(
                        "[chat] RESPONSE intent=PLAN_AHEAD action_data_keys=%s",
                        list((_pa_result.get("action_data") or {}).keys()),
                    )
                    logger.info("[chat] === AI RESPONSE START ===")
                    for _resp_line in _pa_response_text.splitlines():
                        logger.info("[chat] %s", _resp_line)
                    logger.info("[chat] === AI RESPONSE END ===")

                    # ── Auto-generate cooking steps for all dishes in the plan ──
                    # Runs in background so it never blocks the chat response.
                    # skip_if_exists=True prevents clobbering steps the user has
                    # already confirmed (exact-name match in DB).
                    try:
                        import asyncio as _asyncio
                        _pa_ad_bg = _pa_result.get("action_data") or {}
                        _pa_slots_bg = _pa_ad_bg.get("meal_plan_slots") or {}
                        _pa_sid_bg = _pa_ad_bg.get("schedule_id")
                        _pa_is_draft_bg = _pa_ad_bg.get("is_draft", False)
                        if _pa_slots_bg and _pa_sid_bg and not _pa_is_draft_bg:
                            _bg_dishes: List[str] = []
                            for _date_slots in _pa_slots_bg.values():
                                for _mt_dishes in (_date_slots or {}).values():
                                    if isinstance(_mt_dishes, list):
                                        _bg_dishes.extend(_mt_dishes)
                                    elif isinstance(_mt_dishes, str) and _mt_dishes.strip():
                                        _bg_dishes.append(_mt_dishes.strip())
                            _bg_dishes = list(dict.fromkeys(d.strip() for d in _bg_dishes if d and d.strip()))
                            if _bg_dishes:
                                from app.agents.cooking_steps_agent import CookingStepsAgent as _CSBg
                                _bg_ctx = {
                                    "type": "plan_ahead",
                                    "data": {
                                        "schedule_id": _pa_sid_bg,
                                        "meal_plan_slots": _pa_slots_bg,
                                        "dish_ingredients": _pa_ad_bg.get("dish_ingredients") or {},
                                    },
                                }
                                logger.info(
                                    "[chat] PLAN_AHEAD post-save: launching background step-gen "
                                    "for %s (schedule_id=%s)", _bg_dishes, _pa_sid_bg,
                                )
                                _asyncio.create_task(
                                    _CSBg().execute_batch(
                                        dish_names=_bg_dishes,
                                        owner_id=owner_id,
                                        context=_bg_ctx,
                                        cooking_level=cooking_level,
                                        language=language,
                                        skip_if_exists=True,
                                    )
                                )
                    except Exception as _bg_err:
                        logger.warning("[chat] PLAN_AHEAD background step-gen failed to start: %s", _bg_err)

                    return _pa_result
                except Exception as _pipe_err:
                    logger.error(f"[PLAN_AHEAD_PIPELINE] Pipeline failed: {_pipe_err}", exc_info=True)
                    return {
                        "response": "抱歉，饮食计划功能暂时遇到问题，请稍后重试。",
                        "intent": intent_result.intent,
                        "confidence": intent_result.confidence,
                        "reasoning": intent_result.reasoning,
                        "action": "PLAN_AHEAD",
                        "action_data": intent_action.get("data") or {},
                    }

            else:
                logger.warning("[SCHEDULING AGENT] PlanAheadAgent not available, PLAN_AHEAD functionality disabled")
        elif intent_action.get('data', {}).get('suggestion'):
            context_msg += f"\nSuggestion: {intent_action['data']['suggestion']}"
        
        # Lookup vs persistence: when this turn was only a schedule lookup (no plan modification), skip PLAN_AHEAD parse+persist in response
        schedule_lookup_only_this_turn = (
            schedule_context_added_this_turn
            and intent_action.get("action") == "PLAN_AHEAD"
            and not intent_action.get("data", {}).get("_applied_modification")
        )
        if schedule_lookup_only_this_turn:
            logger.info("[SCHEDULING AGENT] This turn is schedule lookup only; will skip PLAN_AHEAD parse/persist in response")
        system_instruction += context_msg

        # 4. Final results to return to frontend
        final_result = {
            "response": "", # Placeholder
            "intent": intent_result.intent,
            "confidence": intent_result.confidence,
            "reasoning": intent_result.reasoning,
            "action": intent_action['action'],
            "action_data": intent_action['data']
        }

        # Build chat history if provided
        contents = []
        if history:
            for msg in history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        
        # Add current user input
        contents.append({"role": "user", "parts": [{"text": user_input}]})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 4096
            }
        }

        headers = {'Content-Type': 'application/json'}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()

                # Check for errors in response first
                if 'error' in result:
                    error_msg = result['error'].get('message', str(result['error']))
                    logger.error(f"Gemini API returned error: {error_msg}")
                    raise ValueError(f"Gemini API error: {error_msg}")

                # Check if candidates exist
                candidates = result.get('candidates', [])
                if not candidates:
                    logger.error(f"Gemini API response has no candidates. Full response: {result}")
                    raise ValueError("Gemini API response missing candidates.")

                # Check for finish reason (might indicate why content is missing)
                finish_reason = candidates[0].get('finishReason', '')
                if finish_reason and finish_reason != 'STOP':
                    logger.warning(f"Gemini API finished with reason: {finish_reason}")

                # Extract text content
                content = candidates[0].get('content', {})
                parts = content.get('parts', [])
                
                if not parts:
                    # Handle MAX_TOKENS case - response was truncated
                    if finish_reason == 'MAX_TOKENS':
                        logger.warning("Gemini API response truncated due to max tokens limit. Consider increasing maxOutputTokens.")
                        # Try to provide a fallback message
                        response_text = "我的回答可能因为长度限制而被截断了。让我为您提供一个更简洁的建议："
                        # Note: In this case, we'll return the fallback message
                        # but ideally we should increase maxOutputTokens to prevent this
                    else:
                        logger.error(f"Gemini API response has no parts in content. Finish reason: {finish_reason}, Full response: {result}")
                        raise ValueError(f"Gemini API response missing parts in content (finish reason: {finish_reason}).")
                else:
                    response_text = parts[0].get('text', '')
                
                if not response_text:
                    logger.error(f"Gemini API response missing text content. Finish reason: {finish_reason}, Full response keys: {result.keys() if isinstance(result, dict) else 'not a dict'}")
                    raise ValueError("Gemini API response missing text content.")

                # For COOKING_STEPS turns, surface the structured agent data so the
                # frontend can still update the dish detail card (schedule_id, saved, etc.)
                # RECIPE_QA goes straight to LLM; action stays GENERAL (no agent data to surface).
                final_action = intent_action['action']
                final_action_data = intent_action['data']
                if intent_result.intent == Intent.COOKING_STEPS and _cs_action_data:
                    # ASK_OVERWRITE when agent detected a recipe conflict (existing steps in DB).
                    # Surface the diff data directly to the frontend instead of "COOKING_STEPS".
                    if _cs_result and _cs_result.get("action") == "ASK_OVERWRITE":
                        final_action = "ASK_OVERWRITE"
                    else:
                        final_action = "COOKING_STEPS"
                    final_action_data = _cs_action_data
                elif intent_result.intent == Intent.MODIFY_RECIPE and _cs_action_data:
                    final_action = "COOKING_STEPS"   # reuse COOKING_STEPS so frontend refreshes the card
                    final_action_data = _cs_action_data
                elif intent_result.intent == Intent.RECIPE_QA:
                    final_action = "RECIPE_QA"

                # ── Log the full AI response (one call per line so Docker shows each) ──
                logger.info(
                    "[chat] RESPONSE intent=%s action_data_keys=%s",
                    final_action,
                    list((final_action_data or {}).keys()),
                )
                logger.info("[chat] === AI RESPONSE START ===")
                for _resp_line in response_text.splitlines():
                    logger.info("[chat] %s", _resp_line)
                logger.info("[chat] === AI RESPONSE END ===")

                # ── Save-failure debug assertion (always enabled) ────────────────
                # Logs a structured ERROR when a step-save fails so the exact
                # failure reason is surfaced in logs without having to reproduce it.
                _debug_intent = getattr(intent_result, "intent", None)
                if _debug_intent in (Intent.COOKING_STEPS, Intent.MODIFY_RECIPE) and final_action != "ASK_OVERWRITE":
                    _ad = final_action_data or {}
                    if not _ad.get("saved"):
                        logger.error(
                            f"[chat] SAVE_FAILED_ASSERTION "
                            f"intent={_debug_intent} "
                            f"schedule_id={_ad.get('schedule_id')} "
                            f"dish={_ad.get('dish_name')!r} "
                            f"error={_ad.get('error')!r}"
                        )

                return {
                    "response": response_text,
                    "intent": intent_result.intent,
                    "confidence": intent_result.confidence,
                    "reasoning": intent_result.reasoning,
                    "action": final_action,
                    "action_data": final_action_data,
                }

        except Exception as e:
            logger.error(f"Chat generation failed: {e}")
            return {
                "response": "I'm sorry, I'm having some trouble processing that right now. Could you try again?",
                "intent": intent_result.intent,
                "confidence": intent_result.confidence,
                "reasoning": f"Generation error: {str(e)}",
                "action": "GENERAL",
                "action_data": {}
            }

# Singleton instance
chat_pipeline = ChatPipeline()

