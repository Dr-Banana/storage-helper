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
    # Step 2: Build LLM context
    # ------------------------------------------------------------------

    def _build_context(self, state: Dict[str, Any], user_timezone: Optional[str]) -> str:
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
        ctx += "\n\n=== CURRENT MEAL PLAN (database — authoritative) ==="
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

        ctx += "\n\n=== INSTRUCTIONS ==="
        ctx += (
            "\n1. Understand the user's intent and set 'action' to one of:"
            " add, modify, remove, update_ingredients, remove_ingredients, view."
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
        ctx += "\n4. Write a brief, friendly confirmation in 'user_message' (match user's language)."
        ctx += "\n\nRULES:"
        ctx += "\n- NEVER invent meals for dates not mentioned unless user explicitly asks."
        ctx += "\n- For 'view', meal_entries should mirror the current plan exactly."
        ctx += "\n- For 'update_ingredients'/'remove_ingredients', keep the same meals but update dish ingredients."
        ctx += "\n- For 'remove' of a date, that date MUST NOT appear in meal_entries."
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
    ) -> Optional[Dict[str, Any]]:
        """Single structured LLM call. Returns parsed PlanAheadOutput or None."""
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
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
                "responseSchema": PLAN_AHEAD_RESPONSE_SCHEMA,
            },
        }

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
                return None

            candidates = result.get("candidates") or []
            if not candidates:
                logger.error("[PLAN_AHEAD_PIPELINE] LLM: no candidates in response")
                return None

            text = (
                candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "") or ""
            ).strip()
            if not text:
                logger.error("[PLAN_AHEAD_PIPELINE] LLM: empty response text")
                return None

            return self.plan_ahead_agent.parse_structured_response(text)
        except Exception as e:
            logger.error(f"[PLAN_AHEAD_PIPELINE] LLM call failed: {e}", exc_info=True)
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
        """Delete schedules for dates that no longer exist in the new plan."""
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
        if old_state.get("schedule_id"):
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

        # ---- Step 2: Build context ----
        system_context = self._build_context(current_state, user_timezone)

        # ---- Step 3: Single LLM call ----
        parsed = await self._call_llm(system_context, history, user_input)
        if not parsed:
            fallback_msg = "抱歉，我暂时无法处理您的请求，请稍后重试。"
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

        # ---- Step 4: Compute shopping list ----
        shopping_list = compute_shopping_list(new_dish_ingredients)

        # ---- Step 5: Persist ----
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

        # Update in-memory state
        update_plan_state(
            owner_id=owner_id,
            meal_plan=new_meal_plan,
            shopping_list=shopping_list,
            schedule_id=schedule_id,
            meal_plan_slots=new_meal_plan_slots,
            dish_ingredients=new_dish_ingredients,
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
            },
        }
