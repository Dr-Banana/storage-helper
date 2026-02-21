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
                            dish_strs.append(f"{d} [ingredients: {', '.join(ings)}]")
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

        ctx += "\n\n=== INSTRUCTIONS ==="
        ctx += (
            "\n1. Understand the user's intent and set 'action' to one of:"
            " add, modify, remove, update_ingredients, remove_ingredients, view, ask, recommend, confirm."
        )
        ctx += (
            "\n2. Set 'target_date' (YYYY-MM-DD) for the affected date,"
            " and 'meal_time' (breakfast/lunch/dinner)."
        )
        ctx += "\n3. Apply the change and output the COMPLETE updated plan in 'meal_entries':"
        ctx += "\n   - Include ALL dates from the current plan above."
        ctx += "\n   - For unchanged dates, copy them exactly (same dishes + same ingredients)."
        ctx += "\n   - For changed dates, apply the user's modification."
        ctx += (
            "\n   - Each dish MUST include ingredients"
            " (use known ones from above, or suggest typical ones for new dishes)."
        )
        ctx += "\n   - If user removes a date, omit it from meal_entries entirely."
        ctx += "\n4. Write a brief, friendly message in 'user_message' (match user's language)."
        ctx += "\n\nRULES:"
        ctx += "\n- NEVER invent meals for dates not mentioned unless user explicitly asks."
        ctx += "\n- For 'view', meal_entries should mirror the current plan exactly."
        ctx += "\n- For 'update_ingredients'/'remove_ingredients', keep the same meals but update dish ingredients."
        ctx += "\n- For 'remove' of a date, that date MUST NOT appear in meal_entries."
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
            "\n- Use 'recommend' ONLY after the user has provided enough preference context"
            " (in current message or recent chat history). This generates a DRAFT — it is NOT saved yet."
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
                "\n- Use 'confirm' when the user explicitly approves (e.g. 'looks good', 'ok', 'confirm', 'save it',"
                " 'that works', 'yes', '可以', '确认', '保存')."
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
                async with httpx.AsyncClient(timeout=20.0) as client:
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
        """Delete schedules for dates that no longer exist in the new plan.

        Safety note: the storage layer creates one schedule record *per date*
        (see pipeline_storage.py "Creating separate schedules for N dates").
        Deleting a schedule only removes that specific day's record; other dates
        are stored in separate records and are unaffected.  A multi-meal day
        (breakfast + dinner) is stored inside the *same* per-date record, so if
        the user moved dinner to another day and breakfast remains, the pipeline
        should NOT include that date in `orphaned_dates` — orphaned_dates must
        only contain dates that are completely absent from the new plan.
        """
        if not orphaned_dates:
            return
        try:
            schedules = await storage_client.get_user_schedules(owner_id)
            for date_str in orphaned_dates:
                for s in schedules:
                    if s.get("event_type") not in ("meal_plan_draft", "shopping_list"):
                        continue
                    mp, _, _, _ = storage_client._extract_meal_plan_from_schedule(s)
                    if date_str in mp:
                        await storage_client.delete_schedule(s.get("id"), owner_id)
                        logger.info(
                            f"[PLAN_AHEAD_PIPELINE] Deleted orphaned schedule id={s.get('id')} for date {date_str}"
                        )
                        break
        except Exception as e:
            logger.warning(f"[PLAN_AHEAD_PIPELINE] Failed to delete orphaned schedules: {e}")

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
    ) -> Optional[int]:
        """Persist changes to DB. Returns schedule_id or None."""
        if action == "remove" and target_date:
            await self._delete_orphaned_schedules(owner_id, [target_date], storage_client)
            return None

        # Find dates that were in the old plan but are absent from the new plan (e.g. move operation)
        old_dates = set((old_state.get("meal_plan") or {}).keys())
        new_dates = set(new_meal_plan.keys())
        orphaned_dates = list(old_dates - new_dates)
        if orphaned_dates:
            logger.info(f"[PLAN_AHEAD_PIPELINE] Dates removed from plan: {orphaned_dates}")
            await self._delete_orphaned_schedules(owner_id, orphaned_dates, storage_client)

        existing_id = old_state.get("schedule_id")
        schedule_id = await self.plan_ahead_agent.persist_meal_plan(
            meal_plan=new_meal_plan,
            shopping_list=shopping_list,
            owner_id=owner_id,
            existing_schedule_id=existing_id,
            storage_client=storage_client,
            user_timezone=user_timezone,
            event_type="meal_plan_draft",
            dish_ingredients=dish_ingredients,
            meal_plan_slots=new_meal_plan_slots,
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
            merged_di: Dict[str, List[str]] = {}
            for k in set(db_di) | set(server_di):
                merged_di[k] = list(set((db_di.get(k) or []) + (server_di.get(k) or [])))
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
            current_state = server_state

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
        system_context = self._build_context(
            current_state, user_timezone,
            inventory_items=inventory_items,
            is_draft=is_currently_draft,
        )

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
        action = parsed["action"]
        target_date = parsed.get("target_date")

        # ---- Step 4: Early exits for non-plan actions ----

        # 'ask': AI is gathering preferences — no plan change, no persist.
        if action == "ask":
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
                merge=False,
            )
            logger.info(f"[PLAN_AHEAD_PIPELINE] confirm: draft persisted, schedule_id={schedule_id}")
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
                if ingredient.lower() in inv_names_lower:
                    already_have.append(ingredient)
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

            update_plan_state(
                owner_id=owner_id,
                meal_plan=new_meal_plan,
                shopping_list=shopping_list,
                schedule_id=current_state.get("schedule_id"),
                meal_plan_slots=new_meal_plan_slots,
                dish_ingredients=new_dish_ingredients,
                is_draft=True,
                merge=False,
                **base_db_dates_kwarg,
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
            )

        # ---- Step 6: Persist (non-draft direct operations) ----
        schedule_id = current_state.get("schedule_id")
        if action != "view":
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
            )
            if new_sid:
                schedule_id = new_sid

        update_plan_state(
            owner_id=owner_id,
            meal_plan=new_meal_plan,
            shopping_list=shopping_list,
            schedule_id=schedule_id,
            meal_plan_slots=new_meal_plan_slots,
            dish_ingredients=new_dish_ingredients,
            is_draft=False,
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
        return {
            "response": response_text,
            "intent": intent_val,
            "confidence": confidence,
            "reasoning": reasoning,
            "action": "PLAN_AHEAD",
            "action_data": {
                "meal_plan": meal_plan,
                "shopping_list": shopping_list,
                "meal_plan_slots": meal_plan_slots,
                "dish_ingredients": dish_ingredients,
                "schedule_id": schedule_id,
                "is_draft": is_draft,
            },
        }
