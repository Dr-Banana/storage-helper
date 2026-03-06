"""
CookingStepsAgent: Generates step-by-step cooking instructions for a dish.

When the user asks "怎么做呢" / "how to cook this?" after discussing a meal plan:
  1. Identifies the target dish from plan_ahead context (most reliable source).
  2. Falls back to a tiny Gemini call to extract the dish name from history.
  3. Calls Gemini to generate structured cooking steps.
  4. Saves the steps to the schedule via PATCH /schedule/{id}/cooking-steps.
     If the PATCH fails (name mismatch), creates a new schedule entry for that date.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.agents.base import BaseAgent
from app.core.config import settings
from app.storage.pipeline_storage import _get_storage_base_url

logger = logging.getLogger(__name__)


class CookingStepsAgent(BaseAgent):
    """Agent that generates and persists cooking steps for a meal-plan dish."""

    def __init__(self):
        super().__init__("COOKING_STEPS")
        self.api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.GEMINI_LLM_MODEL}:generateContent?key={settings.GEMINI_LLM_API_KEY}"
        )

    # ------------------------------------------------------------------
    # 1. Dish identification
    # ------------------------------------------------------------------

    # Keyword maps for meal time detection (checked in order, first match wins)
    _MEAL_TIME_KEYWORDS: List[Tuple[str, List[str]]] = [
        ("lunch",    ["中午", "午饭", "午餐", "midday", "noon", "lunch"]),
        ("breakfast",["早饭", "早上", "早餐", "morning", "breakfast"]),
        ("dinner",   ["晚饭", "晚上", "晚餐", "tonight", "evening", "dinner"]),
    ]

    def _detect_meal_time(
        self,
        user_input: str,
        history: Optional[List[Dict[str, str]]],
    ) -> Optional[str]:
        """
        Scan user_input and the last few conversation turns to detect which
        meal time the user is discussing. Returns 'breakfast', 'lunch',
        'dinner', or None if ambiguous.
        """
        # Combine recent messages (most-recent first) with the current input.
        # The current input is checked first so an explicit "晚饭怎么做" always wins.
        texts = [user_input]
        for msg in reversed((history or [])[-6:]):
            texts.append(msg.get("content", ""))

        for text in texts:
            t = text.lower()
            for meal_time, keywords in self._MEAL_TIME_KEYWORDS:
                if any(kw in t for kw in keywords):
                    return meal_time
        return None

    def _dish_from_context(
        self,
        context: Optional[Dict[str, Any]],
        preferred_meal_time: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Extract the most relevant dish from the plan_ahead context.
        Returns (dish_name, target_date, target_meal_time).

        If `preferred_meal_time` is given (detected from conversation history),
        it is tried first before the default order.
        """
        if not context or context.get("type") != "plan_ahead":
            return None, None, None

        plan_data = context.get("data") or {}
        slots: Dict[str, Any] = plan_data.get("meal_plan_slots") or {}
        if not slots:
            return None, None, None

        # Try to find today's slot first, then fall back to most-recent date
        try:
            from datetime import date as _date
            today_str = _date.today().isoformat()
        except Exception:
            today_str = None

        candidates: List[str] = []
        if today_str and today_str in slots:
            candidates.append(today_str)
        candidates.extend(d for d in sorted(slots.keys(), reverse=True) if d != today_str)

        # Build meal-time search order: put the preferred meal first
        default_order = ["dinner", "lunch", "breakfast"]
        if preferred_meal_time and preferred_meal_time in default_order:
            meal_order = [preferred_meal_time] + [m for m in default_order if m != preferred_meal_time]
        else:
            meal_order = default_order

        for date_key in candidates:
            date_slots = slots.get(date_key) or {}
            for mt in meal_order:
                dishes = date_slots.get(mt)
                if isinstance(dishes, list) and dishes:
                    return str(dishes[0]), date_key, mt
                if isinstance(dishes, str) and dishes.strip():
                    return dishes.strip(), date_key, mt

        return None, None, None

    async def _parse_cooking_intent(
        self,
        user_input: str,
        history: Optional[List[Dict[str, str]]],
        context: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Use a single structured LLM call to extract cooking intent from natural language.
        Replaces the brittle regex chain — handles any phrasing without hardcoded patterns.

        Returns (dish_hint, date_ref, meal_time) where:
          - dish_hint:  the dish name the user seems to be asking about (may be partial)
          - date_ref:   "today" | "yesterday" | "tomorrow" | "day_after_tomorrow" | None
          - meal_time:  "breakfast" | "lunch" | "dinner" | None
        """
        from datetime import date as _date
        today_str = _date.today().isoformat()

        plan_summary: Dict[str, Any] = {}
        if context and context.get("type") == "plan_ahead":
            plan_summary = (context.get("data") or {}).get("meal_plan_slots") or {}

        recent_turns = [
            f"{m['role']}: {m['content']}" for m in (history or [])[-4:]
        ]
        recent_text = "\n".join(recent_turns) if recent_turns else "(none)"

        prompt = (
            f"Today is {today_str}.\n"
            f"Recent conversation:\n{recent_text}\n"
            f"Meal plan (date→meal→dishes): {json.dumps(plan_summary, ensure_ascii=False)}\n\n"
            f"User says: {user_input}\n\n"
            "Extract the cooking intent. Reply ONLY with a JSON object matching this schema:\n"
            '{"dish_hint": string_or_null, "date_ref": "today"|"yesterday"|"tomorrow"|"day_after_tomorrow"|null, '
            '"meal_time": "breakfast"|"lunch"|"dinner"|null}\n\n'
            "Rules:\n"
            "- dish_hint: the dish the user wants to cook. Strip any date/time prefix and cooking-method suffix "
            '  (e.g. "今天速冻饺子怎么煮" → "速冻饺子"; "宫保鸡丁的做法" → "宫保鸡丁"; "大白菜呢" → "大白菜").\n'
            "  If the user is asking generically (e.g. '怎么做呢' with no dish mentioned) set dish_hint to null.\n"
            "- date_ref: only set if the user clearly references a specific date.\n"
            "- meal_time: only set if the user clearly references a meal slot.\n"
            "Reply with ONLY the JSON object, no explanation."
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 200,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                raw = (
                    resp.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                # Strip markdown code fences if present (```json ... ```)
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                    raw = raw.strip()
                # Extract the first JSON object from the text
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start == -1 or end == 0:
                    raise ValueError(f"No JSON object found in response: {raw!r}")
                parsed = json.loads(raw[start:end])
                dish_hint = parsed.get("dish_hint") or None
                date_ref  = parsed.get("date_ref")  or None
                meal_time = parsed.get("meal_time") or None
                # Sanitise date_ref to known values
                if date_ref not in ("today", "yesterday", "tomorrow", "day_after_tomorrow"):
                    date_ref = None
                if meal_time not in ("breakfast", "lunch", "dinner"):
                    meal_time = None
                self.logger.info(
                    f"[CookingStepsAgent] Intent parsed: dish={dish_hint!r}, "
                    f"date_ref={date_ref!r}, meal_time={meal_time!r}"
                )
                return dish_hint, date_ref, meal_time
        except Exception as e:
            logger.warning(f"[CookingStepsAgent] _parse_cooking_intent failed: {e}")
            return None, None, None

    def _resolve_date_ref(self, date_ref: Optional[str]) -> Optional[str]:
        """Convert a date_ref string ("today", "tomorrow" …) to ISO date."""
        from datetime import date as _date, timedelta
        offsets = {"today": 0, "yesterday": -1, "tomorrow": 1, "day_after_tomorrow": 2}
        if date_ref in offsets:
            return (_date.today() + timedelta(days=offsets[date_ref])).isoformat()
        return None

    async def _dish_from_schedule(
        self,
        owner_id: int,
        target_date: str,
        preferred_meal_time: Optional[str],
        dish_hint: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """
        Query DataStorageService for the user's meal plan on `target_date`.
        Returns (canonical_dish_name, schedule_id, meal_time).

        If dish_hint is provided, tries to find the best-matching dish via
        substring match. Falls back to the first dish in the preferred meal slot.
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return None, None, None
        try:
            from datetime import datetime as _dt, timedelta
            start = f"{target_date}T00:00:00"
            end_dt = _dt.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)
            end = end_dt.strftime("%Y-%m-%dT00:00:00")
            url = f"{base_url}/api/schedule/range"
            headers = {"Authorization": f"Bearer user_{owner_id}"}
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, params={"start_time": start, "end_time": end}, headers=headers)
                resp.raise_for_status()
                schedules = resp.json()
        except Exception as e:
            self.logger.warning(f"[CookingStepsAgent] _dish_from_schedule fetch failed: {e}")
            return None, None, None

        meal_order = (
            [preferred_meal_time] + [m for m in ("breakfast", "lunch", "dinner") if m != preferred_meal_time]
            if preferred_meal_time
            else ["breakfast", "lunch", "dinner"]
        )
        hint_lower = dish_hint.lower() if dish_hint else None

        # Collect all candidate (dish_name, schedule_id, meal_time) from the schedule
        candidates: List[Tuple[str, int, str]] = []
        for schedule in (schedules or []):
            if not (schedule.get("event_type") or "").startswith("meal_plan"):
                continue
            sid = schedule.get("id")
            features = (schedule.get("metadata") or {}).get("features") or []
            for feat in features:
                if not isinstance(feat, dict) or feat.get("type") != "meal_plan":
                    continue
                for plan in (feat.get("plans") or []):
                    if not plan.get("date", "").startswith(target_date):
                        continue
                    for meal in (plan.get("meals") or []):
                        mt = meal.get("mealTime")
                        for dish in (meal.get("dishes") or []):
                            name = dish.get("name")
                            if name:
                                candidates.append((name, sid, mt))

        if not candidates:
            return None, None, None

        # 1. If we have a dish_hint, prefer substring match
        if hint_lower:
            for name, sid, mt in candidates:
                if hint_lower in name.lower() or name.lower() in hint_lower:
                    self.logger.info(
                        f"[CookingStepsAgent] Schedule lookup (hint match): "
                        f"'{name}' ({mt}) on {target_date} in schedule {sid}"
                    )
                    return name, sid, mt

        # 2. Fallback: first dish in preferred meal_order
        for preferred_mt in meal_order:
            for name, sid, mt in candidates:
                if mt == preferred_mt:
                    self.logger.info(
                        f"[CookingStepsAgent] Schedule lookup (slot fallback): "
                        f"'{name}' ({mt}) on {target_date} in schedule {sid}"
                    )
                    return name, sid, mt

        # 3. Any dish
        name, sid, mt = candidates[0]
        self.logger.info(
            f"[CookingStepsAgent] Schedule lookup (any): '{name}' ({mt}) on {target_date} in schedule {sid}"
        )
        return name, sid, mt

    # ------------------------------------------------------------------
    # 2. Step generation
    # ------------------------------------------------------------------

    async def _generate_steps(
        self,
        dish_name: str,
        ingredients: Optional[List[str]] = None,
    ) -> List[str]:
        """Call Gemini to produce step-by-step cooking instructions."""
        ing_text = ""
        if ingredients:
            ing_text = f"\nKnown ingredients: {', '.join(i for i in ingredients if i)}"

        prompt = (
            f"Generate precise, chef-quality step-by-step cooking instructions for: {dish_name}.{ing_text}\n"
            'Respond with a JSON object: {"steps": ["step 1...", "step 2...", ...]}\n'
            "Requirements for each step:\n"
            "- Use specific measurements for every ingredient (e.g. '2 tbsp soy sauce', '1 tsp vinegar', '½ tsp sugar').\n"
            "- For sauces or marinades, state the exact ratio (e.g. 'mix 2 tbsp light soy sauce : 1 tbsp rice vinegar : ½ tbsp sugar : 1 tsp sesame oil').\n"
            "- Include cooking temperatures, times, and visual cues where relevant.\n"
            "- Each step should be a single, actionable sentence.\n"
            "- Respond in the same language as the dish name.\n"
            "- Include 6-10 steps."
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
            },
        }
        headers = {"Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self.api_url, headers=headers, json=payload)
                resp.raise_for_status()
                text = (
                    resp.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                parsed = json.loads(text)
                steps = parsed.get("steps", [])
                if steps and isinstance(steps, list):
                    return [str(s) for s in steps]
        except Exception as e:
            logger.warning(f"[CookingStepsAgent] Step generation failed: {e}")
        return []

    # ------------------------------------------------------------------
    # 3. Persistence helpers
    # ------------------------------------------------------------------

    async def _patch_schedule(
        self,
        schedule_id: int,
        owner_id: int,
        dish_name: str,
        steps: List[str],
        date: Optional[str],
        meal_time: Optional[str],
    ) -> bool:
        """PATCH the schedule's dish with generated cooking steps."""
        base_url = _get_storage_base_url()
        if not base_url:
            logger.warning("[CookingStepsAgent] Storage base URL not configured.")
            return False

        url = f"{base_url}/api/schedule/{schedule_id}/cooking-steps"
        payload: Dict[str, Any] = {"dish_name": dish_name, "steps": steps}
        if date:
            payload["date"] = date
        if meal_time:
            payload["meal_time"] = meal_time

        headers = {
            "Authorization": f"Bearer user_{owner_id}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.patch(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    logger.info(
                        f"[CookingStepsAgent] Saved {len(steps)} steps for '{dish_name}' "
                        f"in schedule {schedule_id}."
                    )
                    return True
                logger.warning(
                    f"[CookingStepsAgent] PATCH schedule {schedule_id} returned "
                    f"{resp.status_code}: {resp.text[:120]}"
                )
                return False
        except Exception as e:
            logger.warning(f"[CookingStepsAgent] PATCH failed: {e}")
            return False

    async def _create_meal_with_steps(
        self,
        owner_id: int,
        dish_name: str,
        steps: List[str],
        date: str,
        meal_time: str,
        ingredients: Optional[List[str]] = None,
    ) -> Optional[int]:
        """
        Create a new meal-plan schedule entry for the given date with this dish + steps.
        Returns the new schedule_id, or None on failure.
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        ing_objs = [
            {"id": f"ing_{ts}_{i}", "name": name, "quantity": "", "category": "other"}
            for i, name in enumerate(ingredients or [])
        ]
        dish_obj = {
            "id": f"dish_{ts}",
            "name": dish_name,
            "ingredients": ing_objs,
            "servings": None,
            "prepTime": None,
            "cookTime": None,
            "cookingSteps": steps,
        }
        meal_plan_feature = {
            "type": "meal_plan",
            "id": f"mp_{ts}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "plans": [{
                "date": date,
                "meals": [{"id": f"meal_{ts}", "mealTime": meal_time, "dishes": [dish_obj]}],
            }],
        }
        payload = {
            "title": dish_name,
            "event_type": "meal_plan_draft",
            "scheduled_time": f"{date}T12:00:00",
            "metadata": {"features": [meal_plan_feature]},
        }
        headers = {
            "Authorization": f"Bearer user_{owner_id}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{base_url}/api/schedule",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                new_id = resp.json().get("id")
                if new_id:
                    logger.info(
                        f"[CookingStepsAgent] Created new schedule {new_id} "
                        f"for '{dish_name}' on {date} ({meal_time})."
                    )
                    return new_id
        except Exception as e:
            logger.warning(f"[CookingStepsAgent] Failed to create meal schedule: {e}")
        return None

    async def _find_schedule_ids_for_date(
        self, owner_id: int, target_date: str
    ) -> List[int]:
        """
        Return IDs of all meal-plan schedules that contain `target_date` in their plans.
        Uses GET /schedule/range to avoid a full table scan.
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return []

        headers = {"Authorization": f"Bearer user_{owner_id}"}
        params = {
            "start_time": f"{target_date}T00:00:00",
            "end_time": f"{target_date}T23:59:59",
        }
        found: List[int] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{base_url}/api/schedule/range",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                schedules = resp.json()

            for s in schedules:
                if s.get("event_type") not in ("meal_plan_draft", "shopping_list"):
                    continue
                sid = s.get("id")
                if not sid:
                    continue
                # Verify this schedule actually covers the target date
                features = (s.get("metadata") or {}).get("features") or []
                if isinstance(features, list):
                    for feat in features:
                        if not isinstance(feat, dict):
                            continue
                        if feat.get("type") != "meal_plan":
                            continue
                        if any(
                            isinstance(p, dict) and p.get("date", "").startswith(target_date)
                            for p in feat.get("plans", [])
                        ):
                            found.append(sid)
                            break
        except Exception as e:
            logger.warning(f"[CookingStepsAgent] _find_schedule_ids_for_date error: {e}")

        return found

    async def _save_steps(
        self,
        owner_id: int,
        schedule_id: Optional[int],
        dish_name: str,
        steps: List[str],
        date: Optional[str],
        meal_time: Optional[str],
        ingredients: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[int]]:
        """
        Persist cooking steps without creating duplicate schedule records.

        Strategy (in order):
          1. PATCH the schedule_id from context.
          2. If that returns 404, search for OTHER schedules that cover the same
             date (the meal plan may be split across separate per-date records).
          3. PATCH the first matching schedule found in step 2.
          4. Only create a brand-new schedule if no existing record covers the date
             at all — i.e., the meal was never confirmed/saved.
        """
        # --- Attempt 1: known schedule_id ---
        if schedule_id:
            ok = await self._patch_schedule(schedule_id, owner_id, dish_name, steps, date, meal_time)
            if ok:
                return True, schedule_id

        # --- Attempt 2: search for the schedule that actually contains this date ---
        if date:
            alt_ids = await self._find_schedule_ids_for_date(owner_id, date)
            # Filter out the already-tried id
            alt_ids = [sid for sid in alt_ids if sid != schedule_id]
            for alt_sid in alt_ids:
                logger.info(
                    f"[CookingStepsAgent] Retrying PATCH on schedule {alt_sid} "
                    f"(date-range search hit for {date})"
                )
                ok = await self._patch_schedule(alt_sid, owner_id, dish_name, steps, date, meal_time)
                if ok:
                    return True, alt_sid

        # --- Attempt 3: truly no existing schedule covers this date — create one ---
        if date:
            # Guard: if we found schedules in step 2 but all PATCHes failed,
            # we still DON'T create a new record to avoid duplicates.
            if alt_ids:  # type: ignore[possibly-undefined]
                logger.warning(
                    f"[CookingStepsAgent] All PATCH attempts failed for '{dish_name}' on {date}. "
                    "Skipping creation to avoid duplicate schedule."
                )
                return False, schedule_id

            # No existing schedule for this date — safe to create
            target_mt = meal_time or "dinner"
            new_sid = await self._create_meal_with_steps(
                owner_id, dish_name, steps, date, target_mt, ingredients
            )
            if new_sid:
                return True, new_sid

        return False, schedule_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        user_input: str,
        owner_id: int,
        context: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        self.logger.info(
            f"[CookingStepsAgent] Executing for user {owner_id}: {user_input[:60]}"
        )

        # ---- 1. Resolve dish name ----
        #
        # Step 1a: structured LLM intent parse (replaces brittle regex chain).
        #   Extracts {dish_hint, date_ref, meal_time} in one fast call.
        dish_hint, date_ref, intent_mt = await self._parse_cooking_intent(
            user_input, history, context
        )
        target_date: Optional[str] = self._resolve_date_ref(date_ref)
        # Also accept meal_time detected from history keywords as fallback
        preferred_mt = intent_mt or self._detect_meal_time(user_input, history)

        dish_name: Optional[str] = None
        target_meal_time: Optional[str] = preferred_mt
        schedule_id_from_lookup: Optional[int] = None

        # Step 1b: If a date was mentioned, query the schedule.
        #   This resolves dish_hint → canonical name AND gives us the schedule_id.
        if target_date:
            sched_dish, sched_id, sched_mt = await self._dish_from_schedule(
                owner_id, target_date, preferred_mt, dish_hint=dish_hint
            )
            if sched_dish:
                dish_name = sched_dish
                target_meal_time = sched_mt
                schedule_id_from_lookup = sched_id
                self.logger.info(
                    f"[CookingStepsAgent] Resolved via schedule: dish={dish_name!r}, "
                    f"date={target_date}, meal_time={target_meal_time}"
                )

        # Step 1c: If no date mentioned (or schedule lookup failed), check plan_ahead context.
        if not dish_name:
            context_dish, context_date, context_mt = self._dish_from_context(
                context, preferred_meal_time=preferred_mt
            )
            self.logger.info(
                f"[CookingStepsAgent] From context: dish={context_dish!r}, "
                f"date={context_date}, meal_time={context_mt}"
            )
            if context_dish:
                # If user named a specific dish_hint, prefer the one matching it in context
                if dish_hint:
                    hint_lower = dish_hint.lower()
                    slots: Dict[str, Any] = (
                        (context.get("data") or {}).get("meal_plan_slots") or {}
                        if context and context.get("type") == "plan_ahead" else {}
                    )
                    found = False
                    for date_key, date_slots in slots.items():
                        for mt, dishes in (date_slots or {}).items():
                            if isinstance(dishes, str):
                                dishes = [dishes]
                            for d in (dishes or []):
                                d_str = str(d).strip()
                                if hint_lower in d_str.lower() or d_str.lower() in hint_lower:
                                    dish_name = d_str
                                    target_date = target_date or date_key
                                    target_meal_time = mt
                                    found = True
                                    break
                            if found:
                                break
                        if found:
                            break
                if not dish_name:
                    dish_name = context_dish
                    target_date = target_date or context_date
                    target_meal_time = target_meal_time or context_mt

        # Step 1d: Still no dish → use dish_hint directly (user named something not in schedule)
        if not dish_name and dish_hint:
            dish_name = dish_hint
            self.logger.info(f"[CookingStepsAgent] Using raw hint as dish name: {dish_name!r}")

        if not dish_name:
            return self.format_response(
                action="COOKING_STEPS",
                message="请告诉我您想了解哪道菜的做法？",
                data={"needs_dish_name": True},
            )

        # Wire up schedule_id from lookup into context so _save_steps can find it
        if schedule_id_from_lookup and not (context and (context.get("data") or {}).get("schedule_id")):
            context = {"type": "plan_ahead", "data": {"schedule_id": schedule_id_from_lookup}}

        if not dish_name:
            return self.format_response(
                action="COOKING_STEPS",
                message="请告诉我您想了解哪道菜的做法？",
                data={"needs_dish_name": True},
            )

        # ---- 2. Gather ingredients from context ----
        ingredients: List[str] = []
        if context and context.get("type") == "plan_ahead":
            di = (context.get("data") or {}).get("dish_ingredients") or {}
            for item in di.get(dish_name, []):
                if isinstance(item, dict):
                    n = item.get("name", "").strip()
                    if n:
                        ingredients.append(n)
                elif isinstance(item, str) and item.strip():
                    ingredients.append(item.strip())

        # ---- 3. Generate steps ----
        steps = await self._generate_steps(dish_name, ingredients)

        if not steps:
            return self.format_response(
                action="COOKING_STEPS",
                message=f"抱歉，暂时无法生成「{dish_name}」的烹饪步骤，请稍后再试。",
                data={"dish_name": dish_name, "cooking_steps": []},
            )

        # ---- 4. Persist to DB ----
        schedule_id: Optional[int] = None
        if context and context.get("type") == "plan_ahead":
            schedule_id = (context.get("data") or {}).get("schedule_id")

        saved, effective_sid = await self._save_steps(
            owner_id=owner_id,
            schedule_id=schedule_id,
            dish_name=dish_name,
            steps=steps,
            date=target_date,
            meal_time=target_meal_time,
            ingredients=ingredients,
        )

        return self.format_response(
            action="COOKING_STEPS",
            message=f"Generated cooking steps for {dish_name}",
            data={
                "dish_name": dish_name,
                "cooking_steps": steps,
                "schedule_id": effective_sid or schedule_id,
                "date": target_date,
                "meal_time": target_meal_time,
                "saved": saved,
            },
        )
