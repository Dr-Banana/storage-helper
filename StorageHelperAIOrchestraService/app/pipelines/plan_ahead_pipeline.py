"""
PlanAheadPipeline: single-pass meal planning pipeline.

Replaces the scattered PLAN_AHEAD handling in chat.py with a clean 5-step flow:
  1. Sync state from DB  (PlanAheadAgent.sync_meal_plan_from_database)
  2. Build LLM context   (structured, no PLAN_JSON instructions)
  3. Single LLM call     (responseMimeType=application/json + responseSchema)
  4. Compute shopping list programmatically  (compute_shopping_list)
  5. Persist to DB       (PlanAheadAgent.persist_meal_plan)
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from app.agents.scheduling_agent import (
    PLAN_AHEAD_RESPONSE_SCHEMA,
    PlanAheadAgent,
    compute_shopping_list,
)
from app.modules.plan_ahead_state import _UNSET, get_plan_state, update_plan_state
from app.storage.pipeline_storage import _get_storage_base_url

logger = logging.getLogger(__name__)


class PlanAheadPipeline:
    """Single-pass PLAN_AHEAD handler: sync → context → LLM → compute → persist."""

    def __init__(self, gemini_api_url: str):
        self.gemini_api_url = gemini_api_url
        self.plan_ahead_agent = PlanAheadAgent(gemini_api_url=gemini_api_url)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _now_in_timezone(self, user_timezone: Optional[str] = None) -> datetime:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            tz = None
        return datetime.now(tz) if tz else datetime.utcnow()

    # ------------------------------------------------------------------
    # Inventory helper
    # ------------------------------------------------------------------

    async def _fetch_inventory(self, owner_id: int) -> List[Dict[str, Any]]:
        """Fetch the user's food inventory items from DataStorageService."""
        try:
            base_url = _get_storage_base_url()
            if not base_url:
                return []
            url = f"{base_url}/api/users/{owner_id}/documents"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                documents = response.json().get("documents", [])

            inventory_items: List[Dict[str, Any]] = []
            for doc in documents:
                metadata = doc.get("metadata") or {}
                if "items" in metadata and isinstance(metadata["items"], list):
                    for item in metadata["items"]:
                        if item.get("is_food", False):
                            inventory_items.append({
                                "product_name": item.get("product_name", "Unknown"),
                                "category": item.get("category", ""),
                                "quantity": item.get("quantity", "1"),
                                "estimated_shelf_life_days": item.get("estimated_shelf_life_days", 0),
                            })
                else:
                    product_name = metadata.get("product_name") or doc.get("title")
                    if metadata.get("is_food", False) and product_name:
                        inventory_items.append({
                            "product_name": product_name,
                            "category": metadata.get("category", ""),
                            "quantity": metadata.get("quantity", "1"),
                            "estimated_shelf_life_days": metadata.get("estimated_shelf_life_days", 0),
                        })
            logger.info(f"[PLAN_AHEAD_PIPELINE] Fetched {len(inventory_items)} inventory items for user {owner_id}")
            return inventory_items
        except Exception as e:
            logger.warning(f"[PLAN_AHEAD_PIPELINE] Failed to fetch inventory: {e}")
            return []

    # ------------------------------------------------------------------
    # Step 2: Build LLM context
    # ------------------------------------------------------------------

    def _build_context(
        self,
        state: Dict[str, Any],
        user_timezone: Optional[str],
        inventory_items: Optional[List[Dict[str, Any]]] = None,
        is_draft: bool = False,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
        scheduling_context: Optional[str] = None,
        user_profile: Optional[Dict[str, Any]] = None,
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
            # Sort by urgency: items expiring soonest first (nulls/0 = no expiry, sent last).
            # This ensures proteins/produce that expire soon appear within the LLM's view window
            # rather than being pushed out by condiments with no shelf-life tracking.
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
                "\n- Suggest simple, beginner-friendly dishes with minimal steps (e.g. stir-fries, fried eggs, simple soups)."
                "\n- Avoid dishes requiring advanced knife skills, precise temperatures, or complex techniques."
                "\n- Prefer dishes with fewer than 5 ingredients when possible."
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

        # --- User meal blueprint (hard constraints from DB) ---
        if user_profile:
            ctx += "\n\n=== USER MEAL BLUEPRINT (HARD CONSTRAINTS — MUST FOLLOW) ==="
            _servings = user_profile.get("default_servings", 1)
            ctx += f"\n- Default servings per dish: {_servings} person(s). Scale all ingredient quantities accordingly."
            _ratio = user_profile.get("meat_veg_ratio", "1:1:1")
            _ratio_parts = _ratio.split(":")
            if len(_ratio_parts) == 3:
                ctx += f"\n- Dish composition per meal: {_ratio_parts[0]} meat dish(es), {_ratio_parts[1]} vegetable dish(es), {_ratio_parts[2]} staple(s)."
            _soup = user_profile.get("include_soup", True)
            if _soup:
                ctx += "\n- MUST include at least one soup dish in every meal plan."
            _cal = user_profile.get("calorie_target")
            if _cal:
                ctx += f"\n- Per-meal calorie target: ~{_cal} kcal. Prefer lighter dishes if near the limit."
            _disliked = user_profile.get("disliked_ingredients") or []
            if _disliked:
                ctx += f"\n- FORBIDDEN ingredients (user dislikes): {', '.join(_disliked)}. Do NOT include any of these in any dish."
            _cw = user_profile.get("cuisine_weights") or {}
            if _cw:
                _sorted_cw = sorted(_cw.items(), key=lambda x: -x[1])
                _cw_str = ", ".join(f"{k}({v}%)" for k, v in _sorted_cw if v > 0)
                ctx += f"\n- Cuisine preference weights: {_cw_str}. Higher weight = recommend more dishes from that cuisine."

            _blueprint_summary = (
                f"servings={_servings}, soup={'yes' if _soup else 'no'}, "
                f"cal={_cal or 'none'}, "
                f"disliked={_disliked if _disliked else '(none)'}, "
                f"cuisine_weights={'yes' if _cw else 'none'}"
            )
            logger.info(f"[PLAN_AHEAD_PIPELINE] USER MEAL BLUEPRINT injected: {_blueprint_summary}")
        else:
            logger.info("[PLAN_AHEAD_PIPELINE] USER MEAL BLUEPRINT skipped (no profile)")

        # --- Phase 2: Diversity Engine directive ---
        if user_profile:
            try:
                from app.services.diversity_engine import compute_diversity_directive
                _recent = user_profile.get("recent_dishes") or []
                _cw_for_engine = user_profile.get("cuisine_weights") or {}
                _diversity_directive = compute_diversity_directive(_recent, _cw_for_engine)
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
                    f"[PLAN_AHEAD_PIPELINE] DIVERSITY ENGINE injected: "
                    f"recent_count={len(_recent)}, "
                    f"hard_bans={'yes' if _hard_bans else 'none'}, "
                    f"soft_avoids={'yes' if _soft_avoids else 'none'}, "
                    f"variety_target={'yes' if _cw_for_engine else 'none'}"
                )
            except Exception as _de_err:
                logger.warning(f"[PLAN_AHEAD_PIPELINE] DiversityEngine failed (non-fatal): {_de_err}")
        else:
            logger.info("[PLAN_AHEAD_PIPELINE] DIVERSITY ENGINE skipped (no profile)")

        # --- Phase 3: Seed Library candidate reference ---
        if user_profile:
            try:
                from app.services import seed_library as _seed_lib
                _sl_recent = user_profile.get("recent_dishes") or []
                _sl_cw = user_profile.get("cuisine_weights") or {}
                _sl_disliked = user_profile.get("disliked_ingredients") or []
                _candidates = _seed_lib.select_candidates(
                    cuisine_weights=_sl_cw,
                    disliked_ingredients=_sl_disliked,
                    recent_dishes=_sl_recent,
                )
                if _candidates:
                    ctx += f"\n\n{_seed_lib.build_seed_context(_candidates)}"
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] SEED LIBRARY injected: %d candidates",
                        len(_candidates),
                    )
            except Exception as _sl_err:
                logger.warning("[PLAN_AHEAD_PIPELINE] SeedLibrary failed (non-fatal): %s", _sl_err)
        else:
            logger.debug("[PLAN_AHEAD_PIPELINE] SEED LIBRARY skipped (no profile)")

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
                "[PLAN_AHEAD_PIPELINE] Rejected dishes injected: %d — %s",
                len(_rejected), sorted(_rejected),
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

        # Inject session flow state so the LLM knows which step it's on
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
            "\n- EXPLICIT DISH RULE: If the user explicitly names specific dish(es) they want"
            " (e.g. '吃个萝卜炖牛腩', '加个红烧肉', 'I want beef stew'):"
            "\n  1. Use action='add' and include ONLY those exact dishes — do NOT pad the meal with extra dishes."
            "\n  2. IGNORE diversity soft-avoids and hard-bans for those dishes — user's explicit choice overrides variety rules."
            "\n  3. NEVER refuse or redirect with 'you ate this recently' — honor the request directly."
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
            )
        ctx += "\n\n--- MULTI-TURN RECOMMENDATION FLOW (read carefully) ---"
        ctx += (
            "\n- Use 'ask' when the user wants a recommendation but has NOT yet told you their preferences"
            " (cuisine style, dietary restrictions, number of servings, specific dishes, etc.)."
            "\n  * Set meal_entries=[] — do NOT generate or save any plan yet."
            "\n  * In user_message, ask ONE focused, friendly question to learn their preference."
            "\n  * Example: 'What cuisine do you prefer — Chinese home-style, Japanese, or Western?' or 'Any ingredients you avoid?'"
            "\n  * NEVER jump straight to generating a full meal plan on the first request without context."
        )
        ctx += (
            "\n- Use 'suggest_options' AFTER the user has provided enough preference context (current or prior turns)."
            " This presents 2-3 alternative meal plan styles so the user can choose — NO plan is saved yet."
            "\n  * Set meal_entries=[] (the primary plan slot is empty for suggest_options)."
            "\n  * In 'dish_options', provide exactly 2-3 complete alternative plans."
            "\n    Each option MUST have: option_id ('1','2','3'), label (e.g. '方案一：家常风味'), and full meal_entries."
            "\n    Each option's meal_entries must be a fully detailed plan (same format as normal meal_entries)."
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
            "\n  (c) The user explicitly requests a FULL meal plan (not a single dish) without naming specific dishes."
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
        return ctx

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
            "\n  - Copy the selected option's meal_entries EXACTLY into the main meal_entries field."
            "\n  - If the user also requests a modification, apply it to the selected option's plan."
            "\n  - Clear pending options (they are resolved once the user selects)."
        )
        return ctx

    # ------------------------------------------------------------------
    # Lightweight intent classifier: explicit-add vs recommendation request
    # ------------------------------------------------------------------

    # Keywords that strongly signal the user is explicitly naming a dish to add/modify
    _EXPLICIT_DISH_KEYWORDS = (
        "加", "加个", "加上", "加一个", "吃个", "吃一个", "想吃", "要吃",
        "做", "做个", "做一个", "换成", "换个", "改成",
        "add", "want", "have",
    )

    @staticmethod
    def _keyword_fallback_classify(query: str) -> Dict[str, Any]:
        """Last-resort keyword-based classifier used when the LLM call fails.

        Returns same shape as _classify_dish_intent but with intent only (no dish extraction).
        """
        q_lower = query.lower()
        for kw in PlanAheadPipeline._EXPLICIT_DISH_KEYWORDS:
            if kw in q_lower:
                return {"is_explicit": False, "dishes": [], "intent": "EXPLICIT_HINT"}
        return {"is_explicit": False, "dishes": [], "intent": "UNKNOWN"}

    async def _classify_dish_intent(
        self, query: str, history: List[Dict]
    ) -> Dict[str, Any]:
        """Classify the user's query intent and extract any explicitly named dishes.

        Returns a dict:
          {
            "is_explicit": bool,        # True if user named specific dish(es)
            "dishes": List[str],        # extracted dish names (may be empty)
            "intent": str,              # "EXPLICIT" | "RECOMMEND" | "UNKNOWN"
          }

        Uses responseMimeType=application/json to force clean JSON output.
        Falls back to keyword heuristic, then to UNKNOWN on error.
        """
        import json as _json
        import re as _re

        _system = (
            "You are a meal-planning intent classifier.\n"
            "Analyze the user's latest message and return a JSON object with exactly two keys:\n"
            '  "intent": "EXPLICIT" if the user names specific dish(es) they want to eat/add/change, '
            'or "RECOMMEND" if they ask the AI to suggest or plan meals without naming a dish.\n'
            '  "dishes": an array of the explicitly named dish names (empty array [] for RECOMMEND).\n'
            "Examples:\n"
            '  Input: "加个萝卜炖牛腩"  → {"intent":"EXPLICIT","dishes":["萝卜炖牛腩"]}\n'
            '  Input: "明天晚饭及一个小笼包" → {"intent":"EXPLICIT","dishes":["小笼包"]}\n'
            '  Input: "换成照烧鸡腿"    → {"intent":"EXPLICIT","dishes":["照烧鸡腿"]}\n'
            '  Input: "帮我计划今晚吃什么" → {"intent":"RECOMMEND","dishes":[]}\n'
            '  Input: "给我推荐几个菜"  → {"intent":"RECOMMEND","dishes":[]}'
        )

        _history_turns = []
        for msg in (history or [])[-4:]:
            role = "user" if msg.get("role") == "user" else "model"
            _history_turns.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        _history_turns.append({"role": "user", "parts": [{"text": query}]})

        payload = {
            "contents": _history_turns,
            "systemInstruction": {"parts": [{"text": _system}]},
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 80,
                "responseMimeType": "application/json",
            },
        }
        _fallback = {"is_explicit": False, "dishes": [], "intent": "UNKNOWN"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.gemini_api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()
            candidates = result.get("candidates") or []
            if candidates:
                raw = (
                    candidates[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                # Try to extract JSON even if LLM added markdown fences or extra text
                _json_match = _re.search(r"\{.*\}", raw, _re.DOTALL)
                raw_json = _json_match.group(0) if _json_match else raw
                parsed = _json.loads(raw_json)
                intent = str(parsed.get("intent", "UNKNOWN")).upper()
                dishes = [str(d) for d in (parsed.get("dishes") or []) if d]
                is_explicit = intent == "EXPLICIT" and bool(dishes)
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] dish-intent classifier: query=%r → intent=%s dishes=%s",
                    query[:60], intent, dishes,
                )
                return {"is_explicit": is_explicit, "dishes": dishes, "intent": intent}
        except Exception as _clf_err:
            logger.warning(
                "[PLAN_AHEAD_PIPELINE] dish-intent classifier failed, trying keyword fallback: %s",
                _clf_err,
            )
        # Keyword fallback: can detect EXPLICIT_HINT but cannot extract dish names
        return self._keyword_fallback_classify(query)

    # Kept as a thin wrapper for the Fresh-session guard (uses only the bool result)
    async def _classify_explicit_add(self, query: str, history: List[Dict]) -> bool:
        result = await self._classify_dish_intent(query, history)
        # Also treat keyword-fallback EXPLICIT_HINT as explicit (LLM failed but keywords matched)
        if result["intent"] == "EXPLICIT_HINT":
            return True
        return result["is_explicit"]

    # ------------------------------------------------------------------
    # Step 3: Single LLM call
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        system_context: str,
        history: List[Dict],
        user_input: str,
        max_attempts: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """Single structured LLM call with up to `max_attempts` retries on transient failures."""
        import asyncio

        contents = []
        for msg in (history or []):
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": user_input}]})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_context}]},
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
                "responseSchema": PLAN_AHEAD_RESPONSE_SCHEMA,
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
                    logger.error(f"[PLAN_AHEAD_PIPELINE] Gemini API error: {result['error']}")
                    return None  # API-level errors are not transient; don't retry

                candidates = result.get("candidates") or []
                if not candidates:
                    logger.warning(
                        f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt}: no candidates — retrying"
                    )
                    last_error = ValueError("no candidates")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                candidate = candidates[0]
                finish_reason = candidate.get("finishReason", "")
                if finish_reason not in ("STOP", ""):
                    logger.warning(
                        f"[PLAN_AHEAD_PIPELINE] LLM: unexpected finishReason={finish_reason!r}"
                    )

                text = (
                    candidate.get("content", {}).get("parts", [{}])[0].get("text", "") or ""
                ).strip()
                if not text:
                    logger.warning(
                        f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt}: empty response — retrying"
                    )
                    last_error = ValueError("empty text")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                parsed = self.plan_ahead_agent.parse_structured_response(text)
                if parsed is None:
                    logger.warning(
                        f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt}: JSON parse failed — retrying"
                    )
                    last_error = ValueError("JSON parse failed")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                if attempt > 1:
                    logger.info(f"[PLAN_AHEAD_PIPELINE] LLM succeeded on attempt {attempt}")
                return parsed

            except httpx.TimeoutException as e:
                logger.warning(
                    f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt} timed out: {e}",
                    exc_info=(attempt == max_attempts),
                )
                last_error = e
                if attempt < max_attempts:
                    await asyncio.sleep(3.0 * attempt)
            except Exception as e:
                logger.warning(
                    f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt} failed: {e}",
                    exc_info=(attempt == max_attempts),
                )
                last_error = e
                if attempt < max_attempts:
                    await asyncio.sleep(1.0 * attempt)

        logger.error(
            f"[PLAN_AHEAD_PIPELINE] LLM failed after {max_attempts} attempts. Last error: {last_error}"
        )
        return None

    # ------------------------------------------------------------------
    # Step 5: Persist
    # ------------------------------------------------------------------

    async def _delete_orphaned_schedules(
        self,
        owner_id: int,
        orphaned_dates: List[str],
        storage_client: Any,
    ) -> None:
        """Remove specific dates from schedules.

        If a schedule contains ONLY the date being removed, the entire schedule is
        deleted.  If a schedule contains multiple dates (the common case — all plan
        dates share a single schedule record), only that date is stripped from the
        schedule metadata and the record is updated in-place, leaving the remaining
        dates intact.
        """
        if not orphaned_dates:
            return
        try:
            schedules = await storage_client.get_user_schedules(owner_id)
            for date_str in orphaned_dates:
                for s in schedules:
                    if s.get("event_type") not in ("meal_plan_draft", "shopping_list"):
                        continue
                    mp, _sl, di, slots = storage_client._extract_meal_plan_from_schedule(s)
                    if date_str not in mp:
                        continue

                    remaining_mp = {k: v for k, v in mp.items() if k != date_str}
                    remaining_slots = {k: v for k, v in slots.items() if k != date_str}

                    if not remaining_mp:
                        # This was the only date in the schedule — delete entirely.
                        await storage_client.delete_schedule(s.get("id"), owner_id)
                        logger.info(
                            "[PLAN_AHEAD_PIPELINE] Deleted schedule id=%s (last date %s removed)",
                            s.get("id"), date_str,
                        )
                    else:
                        # Other dates remain — update the schedule without this date.
                        _rem_dishes: set = {
                            dish
                            for d_slots in remaining_slots.values()
                            for mt_dishes in d_slots.values()
                            for dish in (mt_dishes if isinstance(mt_dishes, list) else [])
                        }
                        remaining_di = {k: v for k, v in di.items() if k in _rem_dishes}
                        existing_dd = storage_client._extract_existing_dish_data(s)
                        remaining_dd = {k: v for k, v in existing_dd.items() if k in _rem_dishes}
                        remaining_shopping = compute_shopping_list(remaining_di)
                        new_metadata = storage_client._convert_to_feature_format(
                            remaining_mp,
                            remaining_shopping,
                            dish_ingredients=remaining_di,
                            meal_plan_slots=remaining_slots if remaining_slots else None,
                            existing_dish_data=remaining_dd if remaining_dd else None,
                        )
                        ok = await storage_client.update_schedule(
                            owner_id=owner_id,
                            schedule_id=s.get("id"),
                            metadata=new_metadata,
                        )
                        if ok:
                            logger.info(
                                "[PLAN_AHEAD_PIPELINE] Removed date %s from schedule id=%s, "
                                "%d date(s) remain",
                                date_str, s.get("id"), len(remaining_mp),
                            )
                        else:
                            logger.warning(
                                "[PLAN_AHEAD_PIPELINE] Failed to update schedule id=%s "
                                "after removing date %s",
                                s.get("id"), date_str,
                            )
                    break
        except Exception as e:
            logger.warning("[PLAN_AHEAD_PIPELINE] Failed to remove orphaned date: %s", e)

    async def _delete_meal_time_from_schedules(
        self,
        owner_id: int,
        date_str: str,
        meal_time: str,
        exclude_schedule_id: Optional[int],
        storage_client: Any,
    ) -> None:
        """Remove a specific meal_time slot from all schedules on a given date.

        Used for partial-day removal (e.g. user removes only dinner but breakfast
        remains in the plan-ahead schedule).  For each matching schedule:
        - If removing the slot empties the entire date, remove the date entry.
        - If removing the date empties the entire schedule, delete the schedule.
        - Otherwise update the schedule in-place.

        ``exclude_schedule_id`` is the plan-ahead schedule that will be updated
        separately via persist_meal_plan; skip it here to avoid double-writes.
        """
        try:
            schedules = await storage_client.get_user_schedules(owner_id)
            for s in schedules:
                if s.get("event_type") not in ("meal_plan_draft", "shopping_list"):
                    continue
                if exclude_schedule_id and s.get("id") == exclude_schedule_id:
                    continue
                mp, _sl, di, slots = storage_client._extract_meal_plan_from_schedule(s)
                if date_str not in slots:
                    continue
                date_slots = slots[date_str]
                if meal_time not in date_slots:
                    continue

                # Remove the targeted meal_time from this date's slots.
                updated_date_slots = {mt: v for mt, v in date_slots.items() if mt != meal_time}
                if updated_date_slots:
                    remaining_slots = {**slots, date_str: updated_date_slots}
                    _all_dishes = [
                        d for mt_dishes in updated_date_slots.values()
                        for d in (mt_dishes if isinstance(mt_dishes, list) else [])
                    ]
                    remaining_mp = {**mp, date_str: " and ".join(_all_dishes)}
                else:
                    remaining_slots = {k: v for k, v in slots.items() if k != date_str}
                    remaining_mp = {k: v for k, v in mp.items() if k != date_str}

                if not remaining_mp:
                    await storage_client.delete_schedule(s.get("id"), owner_id)
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] Deleted schedule id=%s "
                        "(removed %s/%s, no dates left)",
                        s.get("id"), date_str, meal_time,
                    )
                else:
                    _rem_dishes: set = {
                        dish
                        for d_slots in remaining_slots.values()
                        for mt_dishes in d_slots.values()
                        for dish in (mt_dishes if isinstance(mt_dishes, list) else [])
                    }
                    remaining_di = {k: v for k, v in di.items() if k in _rem_dishes}
                    existing_dd = storage_client._extract_existing_dish_data(s)
                    remaining_dd = {k: v for k, v in existing_dd.items() if k in _rem_dishes}
                    remaining_shopping = compute_shopping_list(remaining_di)
                    new_metadata = storage_client._convert_to_feature_format(
                        remaining_mp,
                        remaining_shopping,
                        dish_ingredients=remaining_di,
                        meal_plan_slots=remaining_slots if remaining_slots else None,
                        existing_dish_data=remaining_dd if remaining_dd else None,
                    )
                    ok = await storage_client.update_schedule(
                        owner_id=owner_id,
                        schedule_id=s.get("id"),
                        metadata=new_metadata,
                    )
                    if ok:
                        logger.info(
                            "[PLAN_AHEAD_PIPELINE] Removed %s/%s from schedule id=%s",
                            date_str, meal_time, s.get("id"),
                        )
                    else:
                        logger.warning(
                            "[PLAN_AHEAD_PIPELINE] Failed to update schedule id=%s "
                            "after removing %s/%s",
                            s.get("id"), date_str, meal_time,
                        )
        except Exception as e:
            logger.warning("[PLAN_AHEAD_PIPELINE] _delete_meal_time_from_schedules failed: %s", e)

    async def _persist(
        self,
        owner_id: int,
        action: str,
        target_date: Optional[str],
        old_state: Dict[str, Any],
        new_meal_plan: Dict[str, str],
        new_meal_plan_slots: Dict[str, Any],
        dish_ingredients: Dict[str, List[str]],
        shopping_list: List[str],
        storage_client: Any,
        user_timezone: Optional[str],
        target_meal_time: Optional[str] = None,
    ) -> Optional[int]:
        """Persist changes to DB. Returns schedule_id or None."""
        if action == "remove" and target_date:
            if target_date in new_meal_plan:
                # Partial removal: only a specific meal_time was removed from this date;
                # remaining slots for the day are still in new_meal_plan.
                # Fall through to normal persist to update the plan-ahead schedule, and
                # also clean up any OTHER schedules that held the removed meal_time.
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] action=remove, target_date=%s still "
                    "present in new plan (partial removal of %s) — persisting updated schedule.",
                    target_date, target_meal_time or "?",
                )
                if target_meal_time:
                    existing_id = old_state.get("schedule_id")
                    await self._delete_meal_time_from_schedules(
                        owner_id, target_date, target_meal_time,
                        exclude_schedule_id=existing_id,
                        storage_client=storage_client,
                    )
            else:
                # Entire day was removed — delete the schedule for that date.
                await self._delete_orphaned_schedules(owner_id, [target_date], storage_client)
                return None

        # Find dates that were in the old plan but are absent from the new plan (e.g. move operation).
        # For 'add', the LLM correctly outputs ONLY the new dates — do NOT treat existing dates
        # as orphans; they should be preserved as-is.
        old_dates = set((old_state.get("meal_plan") or {}).keys())
        new_dates = set(new_meal_plan.keys())

        # HARDCODED SAFETY RULE: only delete a date if ALL conditions hold:
        #   1. It was in the old plan (i.e. it existed before this operation)
        #   2. It falls WITHIN the date range of the new plan [min_new_date, max_new_date]
        #      (dates outside that range were never "addressed" by this operation)
        #   3. It is NOT present in the new plan (i.e. it was intentionally removed)
        #   4. This is NOT an 'add' action (adds never delete)
        # This prevents "鸡蛋是给早上的" from deleting 2026-03-13 / 2026-03-14 just because
        # the LLM only output 2026-03-16.
        if action == "add" or not new_dates:
            orphaned_dates = []
        else:
            _new_min = min(new_dates)
            _new_max = max(new_dates)
            orphaned_dates = [
                d for d in (old_dates - new_dates)
                if _new_min <= d <= _new_max
            ]

        if orphaned_dates:
            logger.info(f"[PLAN_AHEAD_PIPELINE] Dates removed from plan: {orphaned_dates}")
            await self._delete_orphaned_schedules(owner_id, orphaned_dates, storage_client)

        # For 'add', only persist the newly added dates.
        # Existing dates already have their own DB schedules and must NOT be re-created here
        # (doing so would create duplicate schedules for the same date).
        # Orphan deletion is already disabled above (orphaned_dates = []), so old schedules
        # are preserved without any merging.
        if action == "add":
            logger.info(
                "[PLAN_AHEAD_PIPELINE] add: persisting only new dates (%d) → %s",
                len(new_meal_plan_slots), list(new_meal_plan_slots.keys()),
            )

        persist_meal_plan = new_meal_plan
        persist_slots = new_meal_plan_slots
        persist_ingr = dish_ingredients
        persist_shopping = shopping_list

        # For 'add': never pass existing_schedule_id — storage will do a per-date
        # lookup so each date lands in its own schedule, never merged into an old one.
        # For 'modify'/'confirm': pass existing_id as a hint so the right schedule
        # is updated in-place for the same date.
        existing_id = old_state.get("schedule_id") if action != "add" else None
        schedule_id = await self.plan_ahead_agent.persist_meal_plan(
            meal_plan=persist_meal_plan,
            shopping_list=persist_shopping,
            owner_id=owner_id,
            existing_schedule_id=existing_id,
            storage_client=storage_client,
            user_timezone=user_timezone,
            event_type="meal_plan_draft",
            dish_ingredients=persist_ingr,
            meal_plan_slots=persist_slots,
        )
        return schedule_id

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        owner_id: int,
        user_input: str,
        history: List[Dict],
        user_timezone: Optional[str],
        storage_client: Any,
        context: Optional[Dict] = None,
        intent_result: Any = None,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
        user_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the full PLAN_AHEAD pipeline and return a chat.py-compatible result dict.

        Steps:
          1. Sync DB state
          2. Build LLM context
          3. Single structured LLM call
          4. Compute shopping_list programmatically
          5. Persist to DB
        """
        # ---- Step 1: Sync DB state ----
        old_state = await self.plan_ahead_agent.sync_meal_plan_from_database(
            owner_id=owner_id,
            storage_client=storage_client,
        )

        server_state = get_plan_state(owner_id)

        if server_state.get("is_draft", False):
            # Active draft in memory — never overwrite with stale DB data.
            # The draft contains uncommitted changes; DB sync would destroy them.
            # Only backfill schedule_id from DB if the draft doesn't have one yet.
            if old_state.get("schedule_id") and not server_state.get("schedule_id"):
                update_plan_state(owner_id=owner_id, schedule_id=old_state.get("schedule_id"))
            current_state = get_plan_state(owner_id)
            logger.info(
                f"[PLAN_AHEAD_PIPELINE] Active draft preserved "
                f"({len(current_state.get('meal_plan', {}))} meals) — DB sync skipped."
            )
        elif old_state.get("schedule_id"):
            # No active draft — sync from DB as normal.
            db_mp = old_state.get("meal_plan") or {}
            db_slots = old_state.get("meal_plan_slots") or {}
            server_slots = server_state.get("meal_plan_slots") or {}
            merged_slots = {
                d: {**(db_slots.get(d) or {}), **(server_slots.get(d) or {})}
                for d in db_mp
            }
            db_di = old_state.get("dish_ingredients") or {}
            server_di = server_state.get("dish_ingredients") or {}
            merged_di: Dict[str, List[Any]] = {}
            for k in set(db_di) | set(server_di):
                by_name: Dict[str, Any] = {}
                for item in (db_di.get(k) or []) + (server_di.get(k) or []):
                    key = item.get("name") if isinstance(item, dict) else item
                    if key:
                        by_name[key] = item
                merged_di[k] = list(by_name.values())
            update_plan_state(
                owner_id=owner_id,
                meal_plan=db_mp,
                shopping_list=old_state.get("shopping_list", []),
                schedule_id=old_state.get("schedule_id"),
                meal_plan_slots=merged_slots,
                dish_ingredients=merged_di,
                merge=False,
            )
            current_state = get_plan_state(owner_id)
        else:
            # DB returned no meal plan (empty or all schedules deleted).
            # If in-memory state still carries a stale schedule_id or stale slots
            # (from a plan the user deleted via the Web UI), clear it immediately.
            # Without this, the stale state would be fed to the LLM as "CURRENT MEAL PLAN"
            # and the deleted meals would get resurrected on the next chat turn.
            if server_state.get("schedule_id") or server_state.get("meal_plan_slots"):
                logger.info(
                    f"[PLAN_AHEAD_PIPELINE] DB has no plan but in-memory has stale data "
                    f"(schedule_id={server_state.get('schedule_id')}, "
                    f"slots={list((server_state.get('meal_plan_slots') or {}).keys())}) — "
                    f"clearing stale state for user {owner_id}."
                )
                update_plan_state(
                    owner_id=owner_id,
                    meal_plan={},
                    shopping_list=[],
                    schedule_id=None,
                    meal_plan_slots={},
                    dish_ingredients={},
                    is_draft=False,
                    last_pipeline_action=None,
                    merge=False,
                )
            current_state = get_plan_state(owner_id)

        # Override with explicit context if provided
        if context and context.get("type") == "plan_ahead" and isinstance(context.get("data"), dict):
            cd = context["data"]
            if cd.get("meal_plan_slots"):
                current_state["meal_plan_slots"] = cd["meal_plan_slots"]
            if cd.get("dish_ingredients"):
                current_state["dish_ingredients"] = cd["dish_ingredients"]
            if not current_state.get("schedule_id") and cd.get("schedule_id"):
                current_state["schedule_id"] = cd["schedule_id"]

        # ---- Fetch inventory (used for recommend action & context) ----
        inventory_items = await self._fetch_inventory(owner_id)
        is_currently_draft = current_state.get("is_draft", False)

        # ---- Step 2: Build context ----
        _scheduling_ctx = (context or {}).get("scheduling_context") if context else None
        system_context = self._build_context(
            current_state, user_timezone,
            inventory_items=inventory_items,
            is_draft=is_currently_draft,
            cooking_level=cooking_level,
            language=language,
            scheduling_context=_scheduling_ctx,
            user_profile=user_profile,
        )
        # Inject pending options so the LLM can reference them when user selects
        system_context = self._inject_pending_options(system_context, current_state)

        # ---- Step 3: Single LLM call ----
        parsed = await self._call_llm(system_context, history, user_input)
        if not parsed:
            fallback_msg = "Sorry, I was unable to process your request. Please try again."
            return self._build_result(
                response_text=fallback_msg,
                meal_plan=current_state.get("meal_plan", {}),
                meal_plan_slots=current_state.get("meal_plan_slots", {}),
                dish_ingredients=current_state.get("dish_ingredients", {}),
                shopping_list=current_state.get("shopping_list", []),
                schedule_id=current_state.get("schedule_id"),
                intent_result=intent_result,
            )

        user_message = parsed["user_message"]
        new_meal_plan = parsed["meal_plan"]
        new_meal_plan_slots = parsed["meal_plan_slots"]
        new_dish_ingredients = parsed["dish_ingredients"]
        new_dish_slots = parsed.get("dish_slots") or {}
        action = parsed["action"]
        target_date = parsed.get("target_date")

        # ---- Phase 3: Guardian validation (non-blocking) ----
        try:
            from app.services import guardian as _guardian
            from app.services import seed_library as _seed_lib_g
            _all_dish_names: List[str] = []
            for _date_slots in (new_meal_plan_slots or {}).values():
                for _mt_dishes in (_date_slots or {}).values():
                    if isinstance(_mt_dishes, list):
                        _all_dish_names.extend(_mt_dishes)
                    elif isinstance(_mt_dishes, str):
                        _all_dish_names.append(_mt_dishes)
            if _all_dish_names:
                _guardian_entries = [{"dish": n} for n in _all_dish_names]
                _guardian_issues = _guardian.validate_meal_entries(_guardian_entries)
                _in_seed_count = sum(1 for n in _all_dish_names if _seed_lib_g.lookup_dish(n))
                logger.info(
                    "[PLAN_AHEAD_PIPELINE][GUARDIAN] %d dish(es): %d from seed library, %d issue(s) detected",
                    len(_all_dish_names),
                    _in_seed_count,
                    len(_guardian_issues),
                )
        except Exception as _g_err:
            logger.warning("[PLAN_AHEAD_PIPELINE][GUARDIAN] validation failed (non-fatal): %s", _g_err)

        # ---- Guard: enforce ask-before-recommend/suggest_options on fresh sessions ----
        last_action = current_state.get("last_pipeline_action")

        def _build_ask_fallback(profile: Optional[Dict[str, Any]]) -> str:
            """Build a context-aware clarifying question using what's already known from the user profile."""
            _lang_map_local = {
                "zh": "zh", "en": "en", "ja": "ja", "ko": "ko",
            }
            _is_zh = (_lang_map_local.get(language or "zh", "zh") == "zh")

            # Extract what we already know from the profile
            _disliked: List[str] = (profile or {}).get("disliked_ingredients") or []
            _cw: Dict[str, int] = (profile or {}).get("cuisine_weights") or {}
            _top_cuisines = sorted(_cw.items(), key=lambda x: -x[1])[:2] if _cw else []

            # Build "already know" acknowledgement
            _known_parts: List[str] = []
            if _top_cuisines:
                _cuisine_str = "、".join(n for n, _ in _top_cuisines) if _is_zh else " and ".join(n for n, _ in _top_cuisines)
                _known_parts.append(
                    f"您偏好 {_cuisine_str} 菜系" if _is_zh else f"you prefer {_cuisine_str} cuisine"
                )
            if _disliked:
                _dis_str = "、".join(_disliked[:4]) if _is_zh else ", ".join(_disliked[:4])
                _known_parts.append(
                    f"不吃 {_dis_str}" if _is_zh else f"avoiding {_dis_str}"
                )

            if _known_parts and _is_zh:
                _known_line = "我已知道" + "，".join(_known_parts) + "。"
                return (
                    f"{_known_line}\n"
                    "想帮您推荐几套菜单方案 😊 请问您想规划哪天的饮食？（比如今天晚饭、下周几天等）"
                )
            elif _known_parts:
                _known_line = "I see that " + " and ".join(_known_parts) + "."
                return (
                    f"{_known_line}\n"
                    "I'd love to suggest a few meal plan options! Which day(s) are you planning for?"
                )
            elif _is_zh:
                return (
                    "想帮您推荐几套菜单方案 😊\n"
                    "请问您喜欢什么口味或菜系（比如家常菜、川菜、日料）？有没有不吃的食材或饮食要求？"
                    "以及想规划哪天的饮食？"
                )
            else:
                return (
                    "I'd love to suggest a few meal plan options!\n"
                    "What cuisine style do you prefer (e.g. Chinese home-style, Japanese, Western)? "
                    "Any ingredients to avoid? And which day(s) are you planning for?"
                )

        # Fresh-plan guard: intercept LLM "recommend/suggest_options" for genuinely new dates
        # when the user is NOT in the middle of an active draft session (suggest_options →
        # selection → modify flow).  This forces the proper option-selection UX.
        #
        # DRAFT-SESSION continuations — do NOT intercept:
        #   suggest_options → user picks option  (last_action="suggest_options")
        #   recommend draft → user modifies dish (last_action="recommend")
        #   modify draft    → user modifies more (last_action="modify")
        #
        # All other states (None, "add", "confirm", "ask", "view", …) are treated as
        # "fresh planning context" and MUST go through the guard.
        #
        # EXCEPTION: If the user explicitly names a specific dish
        # (e.g. "加个萝卜炖牛腩"), downgrade recommend → add (do NOT force ask).
        # Actions where an ongoing conversation is already in progress — do NOT intercept.
        # "ask": user answered a clarification question, let the next step through.
        _DRAFT_SESSION_ACTIONS = {"suggest_options", "recommend", "modify", "ask"}
        # Only intercept "recommend" — suggest_options is ALWAYS valid and must never be blocked.
        if action == "recommend" and last_action not in _DRAFT_SESSION_ACTIONS:
            _new_dates = set(new_meal_plan_slots.keys()) if new_meal_plan_slots else set()
            _old_dates = set((old_state.get("meal_plan_slots") or {}).keys())
            # Only bother classifying if there are new meal entries and genuinely new dates
            _candidate_for_add = (
                action == "recommend"
                and bool(new_meal_plan_slots)
                and _new_dates.isdisjoint(_old_dates)
                and not current_state.get("pending_options")
            )
            _is_explicit_add = False
            if _candidate_for_add:
                _is_explicit_add = await self._classify_explicit_add(
                    query=user_input or "", history=history or []
                )
            if _is_explicit_add:
                action = "add"
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] Fresh session: classifier=EXPLICIT_ADD, intercepted recommend "
                    "with new date(s) %s → downgraded to add.",
                    sorted(_new_dates),
                )
            else:
                _orig_action = action
                action = "ask"
                # If the LLM already identified specific dates, give a targeted nudge
                # ("tell me which meals for 2026-03-18") instead of the generic
                # "which day do you want to plan?" fallback.
                if _new_dates:
                    _date_hint = "、".join(sorted(_new_dates))
                    user_message = (
                        f"好的，我来为您规划 {_date_hint} 的饮食！\n"
                        "请告诉我具体想安排哪一餐（早餐/午餐/晚餐），"
                        "我会为您推荐几套方案供您选择。"
                    )
                else:
                    user_message = _build_ask_fallback(user_profile)
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] Fresh-plan guard: classifier=RECOMMEND (or non-candidate), "
                    "intercepted %s (last_action=%s) → forced ask.", _orig_action, last_action
                )
        # Also catch: LLM returned 'ask' action but put meal data in user_message.
        # Detectable by 📅 emoji or a meal_entries payload on a fresh session.
        elif action == "ask" and last_action not in _DRAFT_SESSION_ACTIONS and (
            "📅" in user_message or new_meal_plan
        ):
            user_message = _build_ask_fallback(user_profile)
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Fresh session: ask with embedded meal data — replaced with preferences question."
            )

        # ---- Step 4: Early exits for non-plan actions ----

        # 'ask': AI is gathering preferences — no plan change, no persist.
        if action == "ask":
            # Guard: if the AI has already asked once (last_action == "ask") and the user's message
            # looks like an explicit dish request, the LLM is likely stuck because of diversity rules.
            # In this case, override to a helpful message that acknowledges the user's intent.
            if last_action == "ask" and not new_meal_plan_slots:
                _q = (user_input or "").strip()
                _explicit_add_keywords = ["加", "吃", "想要", "来个", "来一个", "做个", "加个", "add", "want"]
                if any(kw in _q for kw in _explicit_add_keywords):
                    user_message = (
                        "抱歉，您提到的菜品刚好在近期吃过，系统建议多样化饮食。"
                        "不过如果您坚持想吃，可以直接说「就要这个，帮我加上」，我会按您的意愿添加。"
                        "或者告诉我想要哪天哪餐，我来给您推荐几个新方案。"
                    )
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] action=ask (second consecutive): LLM stuck on diversity rules, "
                        "replaced with override-hint message."
                    )
            update_plan_state(owner_id=owner_id, last_pipeline_action="ask")
            logger.info("[PLAN_AHEAD_PIPELINE] action=ask: returning clarification question, skipping persist.")
            return self._build_result(
                response_text=user_message,
                meal_plan=current_state.get("meal_plan", {}),
                meal_plan_slots=current_state.get("meal_plan_slots", {}),
                dish_ingredients=current_state.get("dish_ingredients", {}),
                shopping_list=current_state.get("shopping_list", []),
                schedule_id=current_state.get("schedule_id"),
                is_draft=is_currently_draft,
                intent_result=intent_result,
            )

        # 'suggest_options': AI presents multiple plan choices — store options, no draft yet.
        if action == "suggest_options":
            dish_options = parsed.get("dish_options") or []
            if not dish_options:
                # LLM returned suggest_options but no dish_options — fall back to ask
                logger.warning(
                    "[PLAN_AHEAD_PIPELINE] suggest_options with empty dish_options — falling back to ask."
                )
                update_plan_state(owner_id=owner_id, last_pipeline_action="ask")
                if "?" not in user_message and "？" not in user_message:
                    user_message = (
                        "请告诉我您的饮食偏好（比如口味、菜系、饮食禁忌），我来为您推荐几套方案供选择。"
                    )
                return self._build_result(
                    response_text=user_message,
                    meal_plan=current_state.get("meal_plan", {}),
                    meal_plan_slots=current_state.get("meal_plan_slots", {}),
                    dish_ingredients=current_state.get("dish_ingredients", {}),
                    shopping_list=current_state.get("shopping_list", []),
                    schedule_id=current_state.get("schedule_id"),
                    is_draft=False,
                    intent_result=intent_result,
                )

            update_plan_state(
                owner_id=owner_id,
                last_pipeline_action="suggest_options",
                pending_options=dish_options,
            )
            logger.info(
                "[PLAN_AHEAD_PIPELINE] suggest_options: %d options stored, awaiting user selection.",
                len(dish_options),
            )
            return self._build_result(
                response_text=user_message,
                meal_plan=current_state.get("meal_plan", {}),
                meal_plan_slots=current_state.get("meal_plan_slots", {}),
                dish_ingredients=current_state.get("dish_ingredients", {}),
                shopping_list=current_state.get("shopping_list", []),
                schedule_id=current_state.get("schedule_id"),
                is_draft=False,
                intent_result=intent_result,
                dish_options=dish_options,
            )

        # After user selects an option: clear pending_options so they don't linger.
        if action == "recommend" and current_state.get("pending_options"):
            # Build a whitelist of (date, meal_type) pairs covered by the options.
            # This prevents the LLM from "echoing back" existing DB meals (e.g. yesterday's
            # lunch/breakfast) that appear in its context but were not part of the new plan.
            pending = current_state.get("pending_options") or []
            option_pairs: set = set()
            for opt in pending:
                for date, meals in (opt.get("meal_plan_slots") or {}).items():
                    for meal_type in meals.keys():
                        option_pairs.add((date, meal_type))

            if option_pairs:
                # Filter new_meal_plan_slots to only include whitelisted (date, meal_type) pairs
                filtered_slots: dict = {}
                for date, meals in new_meal_plan_slots.items():
                    kept_meals = {
                        mt: dishes
                        for mt, dishes in meals.items()
                        if (date, mt) in option_pairs
                    }
                    if kept_meals:
                        filtered_slots[date] = kept_meals

                removed_pairs = {
                    (d, mt)
                    for d, meals in new_meal_plan_slots.items()
                    for mt in meals
                    if (d, mt) not in option_pairs
                }
                if removed_pairs:
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] recommend after suggest_options: "
                        "stripped %d echoed-back DB (date, meal_type) pair(s): %s",
                        len(removed_pairs),
                        sorted(removed_pairs),
                    )

                new_meal_plan_slots = filtered_slots
                # Rebuild flat meal_plan from filtered slots
                new_meal_plan = {
                    date: [dish for dishes in meals.values() for dish in dishes]
                    for date, meals in new_meal_plan_slots.items()
                }
                # Re-filter dish_ingredients and dish_slots to only kept dishes
                kept_dishes: set = set()
                for meals in new_meal_plan_slots.values():
                    for dish_list in meals.values():
                        kept_dishes.update(dish_list)
                new_dish_ingredients = {d: v for d, v in new_dish_ingredients.items() if d in kept_dishes}
                new_dish_slots = {d: v for d, v in new_dish_slots.items() if d in kept_dishes}

            update_plan_state(owner_id=owner_id, pending_options=None)
            logger.info("[PLAN_AHEAD_PIPELINE] recommend after suggest_options: clearing pending_options.")

        # 'confirm': user approved the draft — persist whatever is in current in-memory state.
        if action == "confirm":
            draft_plan = current_state.get("meal_plan", {})
            draft_slots = current_state.get("meal_plan_slots", {})
            draft_di = current_state.get("dish_ingredients", {})

            # ----- Ghost-date filter (Issue 5) -----
            # If the user deleted a date from the Web UI while this draft was active, that date
            # may still be in the draft.  Filter it out to prevent "resurrection".
            # Logic: dates that were in DB when the draft was created (base_db_dates) but are
            # now absent from DB (old_state["meal_plan"]) were explicitly deleted by the user.
            base_db_dates: set = current_state.get("draft_base_db_dates") or set()
            current_db_dates: set = set((old_state.get("meal_plan") or {}).keys())
            user_deleted_dates = base_db_dates - current_db_dates
            if user_deleted_dates:
                logger.info(
                    f"[PLAN_AHEAD_PIPELINE] confirm: filtering {len(user_deleted_dates)} "
                    f"user-deleted date(s) from draft: {sorted(user_deleted_dates)}"
                )
                draft_plan = {d: v for d, v in draft_plan.items() if d not in user_deleted_dates}
                draft_slots = {d: v for d, v in draft_slots.items() if d not in user_deleted_dates}
                draft_di = {
                    dish: ings for dish, ings in draft_di.items()
                    if not any(
                        dish in (draft_slots.get(d) or {}).get(mt, [])  # type: ignore[operator]
                        for d in user_deleted_dates
                        for mt in ("breakfast", "lunch", "dinner", "snack")
                    )
                }

            draft_sl = compute_shopping_list(draft_di)

            if not draft_plan and not draft_slots:
                return self._build_result(
                    response_text=user_message or "No active draft plan found. Please request a meal recommendation first.",
                    meal_plan={}, meal_plan_slots={}, dish_ingredients={}, shopping_list=[],
                    schedule_id=current_state.get("schedule_id"),
                    is_draft=False,
                    intent_result=intent_result,
                )

            new_sid = await self._persist(
                owner_id=owner_id,
                action="add",
                target_date=None,
                old_state=current_state,
                new_meal_plan=draft_plan,
                new_meal_plan_slots=draft_slots,
                dish_ingredients=draft_di,
                shopping_list=draft_sl,
                storage_client=storage_client,
                user_timezone=user_timezone,
            )
            schedule_id = new_sid or current_state.get("schedule_id")
            update_plan_state(
                owner_id=owner_id,
                meal_plan=draft_plan,
                shopping_list=draft_sl,
                schedule_id=schedule_id,
                meal_plan_slots=draft_slots,
                dish_ingredients=draft_di,
                is_draft=False,
                last_pipeline_action=None,  # reset session after confirm
                draft_rejected_dishes=None,  # clear rejected list on confirm
                merge=False,
            )
            logger.info(f"[PLAN_AHEAD_PIPELINE] confirm: draft persisted, schedule_id={schedule_id}")

            # Phase 2: update recent_dishes after confirmed commit
            if user_profile is not None and new_sid:
                try:
                    from app.services.diversity_engine import extract_dishes_for_history
                    _new_dish_entries = extract_dishes_for_history(draft_slots)
                    if _new_dish_entries:
                        _dish_names_log = [e["dish"] for e in _new_dish_entries[:5]]
                        logger.info(
                            f"[PLAN_AHEAD_PIPELINE] confirm: writing {len(_new_dish_entries)} dishes "
                            f"to recent_dishes for user {owner_id}: {_dish_names_log}"
                            + (" ..." if len(_new_dish_entries) > 5 else "")
                        )
                        import asyncio
                        asyncio.ensure_future(
                            storage_client.update_user_recent_dishes(
                                owner_id=owner_id,
                                new_entries=_new_dish_entries,
                                existing_recent_dishes=user_profile.get("recent_dishes") or [],
                            )
                        )
                    else:
                        logger.info(f"[PLAN_AHEAD_PIPELINE] confirm: no new dish entries to write to recent_dishes")
                except Exception as _rd_err:
                    logger.warning(f"[PLAN_AHEAD_PIPELINE] recent_dishes update failed (non-fatal): {_rd_err}")

            return self._build_result(
                response_text=user_message,
                meal_plan=draft_plan,
                meal_plan_slots=draft_slots,
                dish_ingredients=draft_di,
                shopping_list=draft_sl,
                schedule_id=schedule_id,
                is_draft=False,
                intent_result=intent_result,
            )

        # ---- Step 4b: Compute shopping list ----
        shopping_list = compute_shopping_list(new_dish_ingredients)

        # ---- Step 4c: Recommend — cross-reference with inventory ----
        if action == "recommend" and inventory_items:
            inv_names_lower = {item["product_name"].lower() for item in inventory_items}
            already_have: List[str] = []
            need_to_buy: List[str] = []
            for ingredient in shopping_list:
                ing_name = ingredient["name"] if isinstance(ingredient, dict) else ingredient
                if ing_name.lower() in inv_names_lower:
                    already_have.append(ing_name)
                else:
                    need_to_buy.append(ingredient)
            shopping_list = need_to_buy
            if already_have:
                inv_highlight = "、".join(already_have)
                user_message = (
                    user_message
                    + f"\n\nInventory used: {inv_highlight}. These have been removed from the shopping list automatically."
                )
                logger.info(
                    f"[PLAN_AHEAD_PIPELINE] Recommend: {len(already_have)} ingredients "
                    f"covered by inventory, {len(need_to_buy)} still needed."
                )

        # ---- Step 4c: For explicit single-dish add/modify, strip LLM auto-completed extra dishes ----
        # When the user explicitly names specific dish(es) (e.g. "加个萝卜炖牛腩"),
        # the LLM may add extra dishes (sides, staples) that were not requested.
        # We detect this via a lightweight classifier and keep only the requested dishes.
        #
        # Two-tier detection:
        #   Tier 1 (LLM): _classify_dish_intent returns is_explicit=True + dish list → use it
        #   Tier 2 (query-substring): LLM failed / returned EXPLICIT_HINT with empty dishes →
        #       check which of the LLM's proposed dishes actually appear in the user's query.
        #       A dish the user never typed is by definition an AI hallucination; strip it.
        if action in ("add", "modify") and new_meal_plan_slots:
            _dish_intent = await self._classify_dish_intent(
                query=user_input or "", history=history or []
            )

            # Tier-2 fallback: LLM classifier returned EXPLICIT_HINT (keyword match,
            # no dish extraction) or any other state where dishes==[].
            # Derive dish list by checking which proposed dishes appear in the query.
            if not _dish_intent["dishes"] and (
                _dish_intent.get("intent") == "EXPLICIT_HINT"
                or _dish_intent["is_explicit"]
            ):
                _query_clean = (user_input or "").lower().replace(" ", "")
                _all_proposed: list = [
                    d
                    for _meals in new_meal_plan_slots.values()
                    for _mt_dishes in _meals.values()
                    for d in _mt_dishes
                ]
                _in_query = [
                    d for d in _all_proposed
                    if d.lower().replace(" ", "") in _query_clean
                ]
                if _in_query:
                    _dish_intent = {
                        "is_explicit": True,
                        "dishes": _in_query,
                        "intent": "QUERY_EXTRACTED",
                    }
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] Tier-2 dish extraction: found %s in query",
                        _in_query,
                    )

            if _dish_intent["is_explicit"] and _dish_intent["dishes"]:
                _requested = _dish_intent["dishes"]
                _explicit_slots: dict = {}
                _removed_extras: list = []
                for _date, _meals in new_meal_plan_slots.items():
                    _kept_meals_ex = {}
                    for _mt, _dishes in _meals.items():
                        # Keep a dish if it fuzzy-matches any requested dish name
                        _kept: list = []
                        _removed: list = []
                        for _d in _dishes:
                            _d_lower = _d.lower().replace(" ", "")
                            _match = any(
                                _req.lower().replace(" ", "") in _d_lower
                                or _d_lower in _req.lower().replace(" ", "")
                                for _req in _requested
                            )
                            if _match:
                                _kept.append(_d)
                            else:
                                _removed.append(_d)
                        if _kept:
                            _kept_meals_ex[_mt] = _kept
                        _removed_extras.extend(_removed)
                    if _kept_meals_ex:
                        _explicit_slots[_date] = _kept_meals_ex
                if _removed_extras:
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] explicit-dish filter: kept %s, stripped extra: %s",
                        _requested, _removed_extras,
                    )
                    new_meal_plan_slots = _explicit_slots
                    new_meal_plan = {
                        d: [dish for mt_dishes in meals.values() for dish in mt_dishes]
                        for d, meals in new_meal_plan_slots.items()
                    }
                    _kept_dishes_ex: set = {
                        dish for meals in new_meal_plan_slots.values()
                        for mt_dishes in meals.values() for dish in mt_dishes
                    }
                    new_dish_ingredients = {k: v for k, v in new_dish_ingredients.items() if k in _kept_dishes_ex}
                    new_dish_slots = {k: v for k, v in new_dish_slots.items() if k in _kept_dishes_ex}
                    shopping_list = compute_shopping_list(new_dish_ingredients)

                    # Rebuild the response message to match what was actually saved.
                    # The LLM's original message mentioned dishes that were filtered out.
                    _meal_type_labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
                    _lines: list = []
                    for _d in sorted(new_meal_plan_slots):
                        for _mt, _ds in new_meal_plan_slots[_d].items():
                            _mt_label = _meal_type_labels.get(_mt, _mt)
                            _dish_str = "、".join(_ds)
                            _lines.append(f"📅 {_d} {_mt_label}: {_dish_str}")
                    if _lines:
                        _verb = "已为您" + ("添加" if action == "add" else "调整为")
                        user_message = _verb + "\n" + "\n".join(_lines)
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] explicit-dish filter: rebuilt response message to match filtered plan."
                    )

        # ---- Step 4d: For 'add', strip echoed-back confirmed dates ----
        # The LLM may include existing confirmed dates in its output (copied from context).
        # For 'add', we only want truly new (date, meal_type) pairs — remove anything that
        # already exists in the confirmed plan with identical content.
        if action == "add" and not is_currently_draft:
            _old_slots = old_state.get("meal_plan_slots") or {}
            _add_filtered_slots: dict = {}
            _add_echoed_pairs: list = []
            for _date, _meals in new_meal_plan_slots.items():
                _old_date_meals = _old_slots.get(_date, {})
                _kept_meals = {}
                for _mt, _dishes in _meals.items():
                    _old_dishes = set(_old_date_meals.get(_mt, []))
                    _new_set = set(_dishes)
                    if _mt not in _old_date_meals or _new_set != _old_dishes:
                        _kept_meals[_mt] = _dishes
                    else:
                        _add_echoed_pairs.append((_date, _mt))
                if _kept_meals:
                    _add_filtered_slots[_date] = _kept_meals
            if _add_echoed_pairs:
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] add: stripped %d echoed-back confirmed (date, meal_type) pair(s): %s",
                    len(_add_echoed_pairs), sorted(_add_echoed_pairs),
                )
                new_meal_plan_slots = _add_filtered_slots
                new_meal_plan = {
                    d: [dish for mt_dishes in meals.values() for dish in mt_dishes]
                    for d, meals in new_meal_plan_slots.items()
                }
                _kept_add_dishes: set = {
                    dish for meals in new_meal_plan_slots.values()
                    for mt_dishes in meals.values() for dish in mt_dishes
                }
                new_dish_ingredients = {d: v for d, v in new_dish_ingredients.items() if d in _kept_add_dishes}
                new_dish_slots = {d: v for d, v in new_dish_slots.items() if d in _kept_add_dishes}
                # Recompute shopping list after filtering
                shopping_list = compute_shopping_list(new_dish_ingredients)

        # ---- Step 5: Draft-mode check ----
        # recommend always creates a draft.
        # Mutations (modify/add/remove/…) while a draft is active stay in draft — don't persist.
        _mutation_actions = {"modify", "add", "remove", "update_ingredients", "remove_ingredients"}
        is_draft_operation = (action == "recommend") or (
            is_currently_draft and action in _mutation_actions
        )

        if is_draft_operation:
            # When creating a brand-new recommend draft, snapshot which dates are currently
            # in DB.  This lets confirm() detect dates the user later deletes via the Web UI.
            base_db_dates_kwarg: Dict[str, Any] = {}
            if action == "recommend" and not is_currently_draft:
                base_db_dates_kwarg["draft_base_db_dates"] = set(old_state.get("meal_plan", {}).keys())

            # Compute which dishes were replaced/removed in this operation so we can track
            # them as "rejected" and avoid re-suggesting them in subsequent turns.
            _prev_draft_slots = current_state.get("meal_plan_slots") or {}
            _prev_dishes: set = set()
            for _pdate, _pmeals in _prev_draft_slots.items():
                for _pmt, _pdishes in _pmeals.items():
                    _prev_dishes.update(_pdishes)
            _new_dishes: set = set()
            for _ndate, _nmeals in new_meal_plan_slots.items():
                for _nmt, _ndishes in _nmeals.items():
                    _new_dishes.update(_ndishes)
            _newly_rejected = _prev_dishes - _new_dishes
            # Preserve existing rejected dishes across turns (union-merge in update_plan_state)
            _existing_rejected: set = current_state.get("draft_rejected_dishes") or set()

            update_plan_state(
                owner_id=owner_id,
                meal_plan=new_meal_plan,
                shopping_list=shopping_list,
                schedule_id=current_state.get("schedule_id"),
                meal_plan_slots=new_meal_plan_slots,
                dish_ingredients=new_dish_ingredients,
                is_draft=True,
                last_pipeline_action=action,
                draft_rejected_dishes=_existing_rejected | _newly_rejected,
                merge=False,
                **base_db_dates_kwarg,
            )
            if _newly_rejected:
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] Tracking %d newly rejected dish(es): %s",
                    len(_newly_rejected), sorted(_newly_rejected),
                )
            logger.info(
                f"[PLAN_AHEAD_PIPELINE] action={action} (draft): memory updated, DB persist deferred. "
                f"dates={list(new_meal_plan.keys())}"
            )
            return self._build_result(
                response_text=user_message,
                meal_plan=new_meal_plan,
                meal_plan_slots=new_meal_plan_slots,
                dish_ingredients=new_dish_ingredients,
                shopping_list=shopping_list,
                schedule_id=current_state.get("schedule_id"),
                is_draft=True,
                intent_result=intent_result,
                dish_slots=new_dish_slots,
            )

        # ---- Step 6: Persist (non-draft direct operations) ----
        schedule_id = current_state.get("schedule_id")
        target_meal_time = parsed.get("meal_time")  # None means remove entire day
        if action != "view":
            # For 'remove': the LLM returns action=remove + target_date but empty
            # meal_entries.  Rebuild new_meal_plan as the surviving plan:
            #   - If target_meal_time is set AND the date has other slots → only remove
            #     that specific meal_time, keeping the rest of the day intact.
            #   - Otherwise → remove the entire date.
            if action == "remove" and target_date and target_date not in new_meal_plan:
                _old_mp = current_state.get("meal_plan") or {}
                _old_slots = current_state.get("meal_plan_slots") or {}
                _old_di = current_state.get("dish_ingredients") or {}

                _old_date_slots = _old_slots.get(target_date, {})
                _remaining_mt = (
                    {mt: v for mt, v in _old_date_slots.items() if mt != target_meal_time and v}
                    if target_meal_time else {}
                )

                if target_meal_time and _remaining_mt:
                    # Partial removal: keep the date with its remaining meal slots.
                    new_meal_plan_slots = {**_old_slots, target_date: _remaining_mt}
                    _remaining_dishes: List[str] = []
                    for _mt in ("breakfast", "lunch", "dinner", "snack"):
                        _remaining_dishes.extend(_remaining_mt.get(_mt) or [])
                    new_meal_plan = {**_old_mp, target_date: " and ".join(_remaining_dishes)}
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] action=remove partial: kept %s with %s "
                        "(removed meal_time=%s)",
                        target_date, list(_remaining_mt.keys()), target_meal_time,
                    )
                else:
                    # Remove the entire date.
                    new_meal_plan = {k: v for k, v in _old_mp.items() if k != target_date}
                    new_meal_plan_slots = {k: v for k, v in _old_slots.items() if k != target_date}

                _rem_dishes: set = {
                    dish
                    for meals in new_meal_plan_slots.values()
                    for mt_dishes in meals.values()
                    for dish in (mt_dishes if isinstance(mt_dishes, list) else [])
                }
                new_dish_ingredients = {k: v for k, v in _old_di.items() if k in _rem_dishes}
                shopping_list = compute_shopping_list(new_dish_ingredients)

            new_sid = await self._persist(
                owner_id=owner_id,
                action=action,
                target_date=target_date,
                old_state=current_state,
                new_meal_plan=new_meal_plan,
                new_meal_plan_slots=new_meal_plan_slots,
                dish_ingredients=new_dish_ingredients,
                shopping_list=shopping_list,
                storage_client=storage_client,
                user_timezone=user_timezone,
                target_meal_time=target_meal_time,
            )
            if new_sid:
                schedule_id = new_sid

            # Phase 2: update recent_dishes after direct persist (add / modify)
            if user_profile is not None and new_sid and action in ("add", "modify"):
                try:
                    from app.services.diversity_engine import extract_dishes_for_history
                    _new_dish_entries = extract_dishes_for_history(new_meal_plan_slots)
                    if _new_dish_entries:
                        import asyncio
                        asyncio.ensure_future(
                            storage_client.update_user_recent_dishes(
                                owner_id=owner_id,
                                new_entries=_new_dish_entries,
                                existing_recent_dishes=user_profile.get("recent_dishes") or [],
                            )
                        )
                except Exception as _rd_err:
                    logger.warning(f"[PLAN_AHEAD_PIPELINE] recent_dishes update failed (non-fatal): {_rd_err}")

        update_plan_state(
            owner_id=owner_id,
            meal_plan=new_meal_plan,
            shopping_list=shopping_list,
            schedule_id=schedule_id,
            meal_plan_slots=new_meal_plan_slots,
            dish_ingredients=new_dish_ingredients,
            is_draft=False,
            last_pipeline_action=action,
            merge=False,
        )

        logger.info(
            f"[PLAN_AHEAD_PIPELINE] Completed: action={action}, "
            f"dates={list(new_meal_plan.keys())}, schedule_id={schedule_id}"
        )
        return self._build_result(
            response_text=user_message,
            meal_plan=new_meal_plan,
            meal_plan_slots=new_meal_plan_slots,
            dish_ingredients=new_dish_ingredients,
            shopping_list=shopping_list,
            schedule_id=schedule_id,
            is_draft=False,
            intent_result=intent_result,
        )

    @staticmethod
    def _build_result(
        response_text: str,
        meal_plan: Dict,
        meal_plan_slots: Dict,
        dish_ingredients: Dict,
        shopping_list: List,
        schedule_id: Optional[int],
        intent_result: Any,
        is_draft: bool = False,
        dish_options: Optional[List] = None,
        dish_slots: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Build chat.py-compatible response dict."""
        try:
            from app.modules.intent_classifier import Intent
            intent_val = intent_result.intent if intent_result else Intent.PLAN_AHEAD
            confidence = intent_result.confidence if intent_result else 0.95
            reasoning = intent_result.reasoning if intent_result else ""
        except Exception:
            intent_val = "PLAN_AHEAD"
            confidence = 0.95
            reasoning = ""

        action_str = "SUGGEST_OPTIONS" if dish_options else "PLAN_AHEAD"
        action_data: Dict[str, Any] = {
            "meal_plan": meal_plan,
            "shopping_list": shopping_list,
            "meal_plan_slots": meal_plan_slots,
            "dish_ingredients": dish_ingredients,
            "schedule_id": schedule_id,
            "is_draft": is_draft,
        }
        if dish_options:
            action_data["dish_options"] = dish_options
        if dish_slots:
            action_data["dish_slots"] = dish_slots

        return {
            "response": response_text,
            "intent": intent_val,
            "confidence": confidence,
            "reasoning": reasoning,
            "action": action_str,
            "action_data": action_data,
        }
