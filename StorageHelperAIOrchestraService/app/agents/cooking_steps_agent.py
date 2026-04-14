"""
CookingStepsAgent: Generates step-by-step cooking instructions for a dish.

When the user asks "怎么做呢" / "how to cook this?" after discussing a meal plan:
  1. Identifies the target dish from plan_ahead context (most reliable source).
  2. Falls back to a tiny Gemini call to extract the dish name from history.
  3. Calls Gemini to generate structured cooking steps.
  4. Saves the steps to the schedule via PATCH /schedule/{id}/cooking-steps.
     If the PATCH fails (name mismatch), creates a new schedule entry for that date.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.agents.base import BaseAgent
from app.core.config import settings
from app.storage.pipeline_storage import _get_storage_base_url


def _strip_markup(step: str) -> str:
    """Remove UI-formatting noise (**bold** markers) from a step string.

    Phase-header lines (starting with a cooking emoji like 🥗/🍳/🥢) are kept
    as plain text since they carry structural meaning, but any **...**
    wrapping is stripped everywhere.
    """
    return re.sub(r'\*\*([^*]+)\*\*', r'\1', step)


def _clean_steps_for_llm(steps: List[str]) -> str:
    """Return a numbered plain-text block suitable for LLM context.

    Strips **bold** markers so the LLM doesn't see noisy markdown tokens.
    Phase-header emoji lines (🥗 / 🍳 / 🥢) are preserved as section labels.
    """
    lines = []
    step_num = 0
    for s in steps:
        clean = _strip_markup(s)
        # Detect phase-header emoji (U+1F300–U+1FBFF) via Python ord
        first_cp = ord(clean.strip()[0]) if clean.strip() else 0
        if 0x1F300 <= first_cp <= 0x1FBFF:
            lines.append(f"\n[{clean.strip()}]")
        else:
            step_num += 1
            lines.append(f"{step_num}. {clean.strip()}")
    return "\n".join(lines)

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
                "maxOutputTokens": 2048,
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
            # Hint was given but nothing in the schedule matches it.
            # Return None so the caller falls back to using dish_hint directly,
            # rather than picking an unrelated dish from the schedule.
            self.logger.info(
                f"[CookingStepsAgent] Schedule lookup: hint '{dish_hint}' not found in"
                f" schedule for {target_date} — returning None to avoid wrong dish."
            )
            return None, None, None

        # 2. Fallback (no hint): first dish in preferred meal_order
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
    # 2. Step modification (ingredient quantity tweak)
    # ------------------------------------------------------------------

    # ---------------------------------------------------------------
    # Helpers for two-phase deterministic scaling (Fix 2)
    # ---------------------------------------------------------------

    async def _call_llm_json(self, prompt: str, timeout: float = 20.0) -> Dict[str, Any]:
        """Send a single prompt to Gemini and return the parsed JSON response."""
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 4096},
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
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
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"No JSON object in LLM response: {raw[:120]!r}")
        return json.loads(raw[start:end])

    @staticmethod
    def _fmt_qty(value: float, unit: str) -> str:
        """Format a scaled numeric quantity as a human-friendly string."""
        if value == int(value):
            return f"{int(value)}{unit}"
        if value < 10:
            return f"{round(value, 1)}{unit}"
        return f"{round(value)}{unit}"

    @staticmethod
    def _compute_scaled_ingredients(
        ingredients: List[Dict[str, Any]], scale_factor: float
    ) -> List[Dict[str, Any]]:
        """Return [{name, old_qty, new_qty}, ...] after Python-side multiplication."""
        result = []
        for ing in ingredients:
            name = ing.get("name", "")
            value = float(ing.get("value", 1))
            unit = ing.get("unit", "")
            old_qty = CookingStepsAgent._fmt_qty(value, unit)
            new_qty = CookingStepsAgent._fmt_qty(value * scale_factor, unit)
            result.append({"name": name, "old_qty": old_qty, "new_qty": new_qty})
        return result

    # ---------------------------------------------------------------
    # Phase 1 — Structured intent analysis
    # ---------------------------------------------------------------

    async def _analyze_modification_intent(
        self,
        user_input: str,
        steps_text: str,
        dish_name: str,
        ing_text: str,
    ) -> Dict[str, Any]:
        """Ask the LLM to classify the modification and extract structured data.

        Returns:
          {
            "type": "scaling" | "tweak",
            "scale_factor": 2.0,              # scaling only
            "current_servings": 1,            # scaling only
            "target_servings": 2,             # scaling only
            "ingredients": [                  # scaling only – ALL current ingredients
              {"name": "鸡蛋", "value": 3, "unit": "个"}, ...
            ],
            "target_ingredient": "酱油",      # tweak only
            "direction": "increase"|"decrease"|"set",
            "multiplier": 1.5,                # for increase / decrease
            "exact_value": null,              # number, for "set"
            "exact_unit": null
          }
        """
        prompt = (
            f"Classify the modification request for recipe 「{dish_name}」.\n\n"
            f"RECIPE STEPS:\n{steps_text}\n"
            f"{ing_text}\n\n"
            f"USER REQUEST: {user_input}\n\n"
            "TASK: Identify which type of modification the user wants and extract the relevant data.\n\n"
            "TYPE A — SCALING: user wants a different number of servings\n"
            "  Triggers: 两人份, N人份, N份, for N people, double, 加倍, 三倍, serve N\n"
            "  You must: detect the CURRENT serving size from the recipe (default 1 if not stated),\n"
            "            determine the TARGET serving size from the request,\n"
            "            list EVERY ingredient found in the steps with its current value and unit.\n\n"
            "TYPE B — TWEAK: user wants to change one specific ingredient\n"
            "  Triggers: 多放X, 少放X, X再多一点, more X, less X, reduce X, add more X\n"
            "  You must: extract the ingredient name, direction (increase/decrease/set),\n"
            "            and either a multiplier or an exact_value + exact_unit.\n\n"
            "Return ONLY this JSON (no markdown fences):\n"
            "{\n"
            '  "type": "scaling" or "tweak",\n'
            '  "scale_factor": <number or null>,\n'
            '  "current_servings": <number or null>,\n'
            '  "target_servings": <number or null>,\n'
            '  "ingredients": [{"name":"...","value":<number>,"unit":"..."}] or null,\n'
            '  "target_ingredient": "<name>" or null,\n'
            '  "direction": "increase" or "decrease" or "set" or null,\n'
            '  "multiplier": <number or null>,\n'
            '  "exact_value": <number or null>,\n'
            '  "exact_unit": "<unit or empty string>" or null\n'
            "}"
        )
        try:
            return await self._call_llm_json(prompt)
        except Exception as e:
            self.logger.warning(f"[CookingStepsAgent] _analyze_modification_intent failed: {e}")
            return {"type": "tweak"}

    # ---------------------------------------------------------------
    # Phase 2a — Rewrite steps with pre-computed exact quantities (scaling)
    # ---------------------------------------------------------------

    @staticmethod
    def _safe_replace_qty(text: str, old_qty: str, new_qty: str) -> str:
        """Replace old_qty with new_qty without clobbering larger quantity strings.

        Uses a negative lookbehind for digits and dots so that "5克" inside
        "15克" or "1.5克" is NOT accidentally replaced.

        Example:
            _safe_replace_qty("加入15克淀粉和5克盐", "5克", "25克")
            → "加入15克淀粉和25克盐"   (not "加入125克淀粉和25克盐")
        """
        m = re.match(r'^([\d.]+)(.+)$', old_qty)
        if m:
            number = re.escape(m.group(1))
            unit = re.escape(m.group(2))
            pattern = rf'(?<![\d.]){number}{unit}'
            return re.sub(pattern, new_qty, text)
        # Fallback for non-numeric old_qty strings
        return text.replace(old_qty, new_qty)

    def _rewrite_steps_with_scaling(
        self,
        current_steps: List[str],
        scaled: List[Dict[str, Any]],
        scale_factor: float,
        dish_name: str,
        language: str,
    ) -> Dict[str, Any]:
        """Apply quantity substitutions using Python string replacement.

        No LLM call — Python handles the substitution while perfectly preserving
        all formatting: **bold** markers, 🥗/🍳/🥢 phase headers, 💡 tip lines,
        and any other special characters.

        Substitutions are applied longest-first to prevent partial matches
        (e.g. "150ml" before "50ml" before "0ml").
        """
        # Sort substitutions by length of old_qty descending to prevent partial matches
        subs = sorted(
            [(s["old_qty"], s["new_qty"]) for s in scaled if s.get("old_qty") and s.get("new_qty")],
            key=lambda x: -len(x[0]),
        )

        modified_steps: List[str] = []
        changed_indices: List[int] = []

        for idx, step in enumerate(current_steps):
            new_step = step
            for old, new in subs:
                new_step = self._safe_replace_qty(new_step, old, new)
            modified_steps.append(new_step)
            if new_step != step:
                changed_indices.append(idx)

        sf_str = str(int(scale_factor)) if scale_factor == int(scale_factor) else str(round(scale_factor, 2))
        self.logger.info(
            f"[CookingStepsAgent] Python scaling applied: "
            f"{len(changed_indices)}/{len(current_steps)} steps changed"
        )

        modified_ingredients = [
            {"name": s["name"], "quantity": s["new_qty"]} for s in scaled
        ]
        ingredient_change = {
            "name": f"all ({sf_str}×)",
            "old_qty": "1× serving",
            "new_qty": f"{sf_str}× serving",
        }

        return {
            "modified_steps": modified_steps,
            "changed_indices": changed_indices,
            "ingredient_change": ingredient_change,
            "modified_ingredients": modified_ingredients,
        }

    # ---------------------------------------------------------------
    # Phase 2b — Single-ingredient tweak (original one-call approach)
    # ---------------------------------------------------------------

    async def _apply_ingredient_tweak(
        self,
        user_input: str,
        current_steps: List[str],
        analysis: Dict[str, Any],
        dish_name: str,
        language: str,
        current_ingredients: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """LLM modifies only the steps relevant to the target ingredient."""
        steps_text = _clean_steps_for_llm(current_steps)
        ing_text = ""
        if current_ingredients:
            parts = [
                f"{ing.get('name', '')} {ing.get('quantity', '')}".strip()
                for ing in current_ingredients
                if ing.get("name")
            ]
            if parts:
                ing_text = "\nIngredients list: " + ", ".join(parts)

        _lang_map = {"zh": "用简体中文", "en": "in English", "ja": "日本語で", "ko": "한국어로"}
        lang_instr = _lang_map.get(language, "用简体中文")

        target = analysis.get("target_ingredient", "")
        direction = analysis.get("direction", "increase")
        if analysis.get("exact_value") is not None:
            qty_hint = f"Set to exactly {analysis['exact_value']}{analysis.get('exact_unit', '')}"
        elif direction == "increase":
            qty_hint = f"Multiply current quantity by {analysis.get('multiplier', 1.5)}"
        else:
            qty_hint = f"Multiply current quantity by {analysis.get('multiplier', 0.7)}"

        prompt = (
            f"You are modifying the recipe for 「{dish_name}」.\n\n"
            f"CURRENT STEPS:\n{steps_text}\n{ing_text}\n\n"
            f"USER REQUEST: {user_input}\n"
            f"TARGET INGREDIENT: {target}\n"
            f"CHANGE: {qty_hint}\n\n"
            "INSTRUCTIONS:\n"
            "  - Update ONLY the step(s) that mention the target ingredient.\n"
            "  - Keep all other steps exactly as-is.\n"
            "  - Preserve phase-header lines (lines starting with cooking emojis) unchanged.\n"
            f"  - Write {lang_instr}.\n\n"
            "Return ONLY this JSON (no markdown fences):\n"
            "{\n"
            '  "modified_steps": ["step text", ...],\n'
            '  "changed_indices": [0-based indices of changed steps],\n'
            '  "ingredient_change": {"name": "...", "old_qty": "...", "new_qty": "..."},\n'
            '  "modified_ingredients": [{"name": "...", "quantity": "new quantity"}, ...]\n'
            "}\n"
            "CRITICAL: modified_ingredients MUST list EVERY ingredient (even unchanged ones) with its current or new quantity."
        )
        try:
            return await self._call_llm_json(prompt)
        except Exception as e:
            self.logger.warning(f"[CookingStepsAgent] _apply_ingredient_tweak failed: {e}")
            return {}

    # ---------------------------------------------------------------
    # Public entry-point (orchestrates Phase 1 → Python math → Phase 2)
    # ---------------------------------------------------------------

    async def _modify_steps_ingredient(
        self,
        user_input: str,
        current_steps: List[str],
        dish_name: str,
        current_ingredients: Optional[List[Dict[str, Any]]] = None,
        language: Optional[str] = "zh",
    ) -> Dict[str, Any]:
        """Two-phase deterministic modification pipeline.

        Phase 1 — LLM extracts structured intent (scaling vs. tweak) and current quantities.
        Phase 2a (scaling) — Python computes exact new quantities; LLM rewrites text only.
        Phase 2b (tweak)   — LLM applies a focused single-ingredient change.
        """
        lang = language or "zh"
        steps_text = _clean_steps_for_llm(current_steps)
        ing_text = ""
        if current_ingredients:
            parts = [
                f"{ing.get('name', '')} {ing.get('quantity', '')}".strip()
                for ing in current_ingredients
                if ing.get("name")
            ]
            if parts:
                ing_text = "\nIngredients list: " + ", ".join(parts)

        # --- Phase 1: classify + extract ---
        analysis = await self._analyze_modification_intent(
            user_input, steps_text, dish_name, ing_text
        )
        self.logger.info(
            f"[CookingStepsAgent] Modification analysis for '{dish_name}': {analysis}"
        )

        if analysis.get("type") == "scaling":
            ingredients = analysis.get("ingredients") or []
            scale_factor = float(analysis.get("scale_factor") or 1.0)
            if scale_factor <= 0:
                scale_factor = 1.0

            if not ingredients and current_ingredients:
                # Fall back to the ingredients list from the DB if LLM didn't extract them
                for ing in current_ingredients:
                    name = ing.get("name", "")
                    qty_str = ing.get("quantity", "1")
                    # Try to parse the numeric part
                    m = re.match(r"([\d.]+)\s*(.*)", str(qty_str).strip())
                    if m:
                        ingredients.append({"name": name, "value": float(m.group(1)), "unit": m.group(2).strip()})
                    else:
                        ingredients.append({"name": name, "value": 1.0, "unit": qty_str})

            # --- Python math (no LLM arithmetic) ---
            scaled = self._compute_scaled_ingredients(ingredients, scale_factor)
            self.logger.info(
                f"[CookingStepsAgent] Scaling ×{scale_factor}: "
                + ", ".join(f"{s['name']} {s['old_qty']}→{s['new_qty']}" for s in scaled)
            )

            # --- Phase 2a: Python-based text rewrite (no LLM, preserves formatting) ---
            return self._rewrite_steps_with_scaling(
                current_steps, scaled, scale_factor, dish_name, lang
            )

        else:
            # --- Phase 2b: single-ingredient tweak ---
            return await self._apply_ingredient_tweak(
                user_input, current_steps, analysis, dish_name, lang, current_ingredients
            )

    async def _find_schedule_id_by_dish(
        self,
        owner_id: int,
        dish_name: str,
        window_days: int = 14,
    ) -> Optional[int]:
        """Search DataStorageService for the best-matching schedule entry for dish_name.

        Scanning window: 2 days in the past → window_days days ahead.

        Candidate priority (highest wins):
          name_score  2 = exact name match, 1 = substring match
          date_score  0 = today, 1 = closest future date, 2 = past dates (most recent first)

        If multiple candidates share the same (name_score, date_score), the one whose
        meal-plan date is closest to today wins, so the current session's dish is
        preferred over an older occurrence of the same dish name.
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return None
        try:
            from datetime import date as _date, timedelta
            today = _date.today()
            start = (today - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
            end = (today + timedelta(days=window_days)).strftime("%Y-%m-%dT00:00:00")
            headers = {"Authorization": f"Bearer user_{owner_id}"}
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"{base_url}/api/schedule/range",
                    params={"start_time": start, "end_time": end},
                    headers=headers,
                )
                resp.raise_for_status()
                schedules = resp.json()
        except Exception as e:
            self.logger.warning(f"[CookingStepsAgent] _find_schedule_id_by_dish fetch failed: {e}")
            return None

        from datetime import date as _date, timedelta
        today = _date.today()
        dish_lower = dish_name.lower().strip()

        # candidates: list of (name_score, date_proximity_abs_days, date_is_future, sched_id)
        # We want: highest name_score, then closest to today (smallest abs diff)
        candidates: List[Tuple[int, int, bool, int]] = []

        for sched in schedules if isinstance(schedules, list) else []:
            sched_id = sched.get("id") or sched.get("schedule_id")
            if not sched_id:
                continue

            # Navigate the real meal_plan JSON structure:
            # metadata.features[].plans[].meals[].dishes[].name
            metadata = sched.get("metadata") or {}
            features = metadata.get("features") or []
            for feat in features:
                if feat.get("type") != "meal_plan":
                    continue
                for day_plan in feat.get("plans") or []:
                    day_date_str = day_plan.get("date", "")
                    try:
                        day_date = _date.fromisoformat(day_date_str)
                    except (ValueError, TypeError):
                        day_date = today  # fallback

                    abs_days = abs((day_date - today).days)
                    is_future = day_date >= today

                    for meal in day_plan.get("meals") or []:
                        for dish in meal.get("dishes") or []:
                            dish_stored = (dish.get("name") or "").lower().strip()
                            if dish_stored == dish_lower:
                                name_score = 2
                            elif dish_lower in dish_stored or dish_stored in dish_lower:
                                name_score = 1
                            else:
                                continue
                            candidates.append((name_score, abs_days, is_future, sched_id))

        if not candidates:
            return None

        # Sort: descending name_score, then ascending absolute date distance
        # (ties broken by preferring future dates over past)
        candidates.sort(key=lambda c: (-c[0], c[1], not c[2]))
        best = candidates[0]
        best_id = best[3]
        self.logger.info(
            f"[CookingStepsAgent] DB hydration: found schedule_id={best_id} for '{dish_name}' "
            f"(name_score={best[0]}, days_from_today={best[1]}, future={best[2]})"
        )
        return best_id

    async def execute_modify(
        self,
        user_input: str,
        owner_id: int,
        current_steps: List[str],
        dish_name: str,
        context: Optional[Dict[str, Any]] = None,
        language: Optional[str] = "zh",
    ) -> Dict[str, Any]:
        """Modify ingredient quantities in existing steps, then persist to DB.

        Returns a result dict with:
          modified_steps, ingredient_change, changed_indices, saved, schedule_id
        """
        # Gather current ingredients from context
        current_ingredients: List[Dict[str, Any]] = []
        if context and context.get("type") == "plan_ahead":
            di = (context.get("data") or {}).get("dish_ingredients") or {}
            for item in di.get(dish_name, []):
                if isinstance(item, dict):
                    current_ingredients.append(item)
                elif isinstance(item, str) and item.strip():
                    current_ingredients.append({"name": item.strip(), "quantity": ""})

        result = await self._modify_steps_ingredient(
            user_input=user_input,
            current_steps=current_steps,
            dish_name=dish_name,
            current_ingredients=current_ingredients,
            language=language,
        )

        if not result or not result.get("modified_steps"):
            return {
                "dish_name": dish_name,
                "modified_steps": current_steps,
                "ingredient_change": None,
                "changed_indices": [],
                "saved": False,
                "error": "LLM failed to produce modified steps",
            }

        modified_steps: List[str] = result["modified_steps"]
        modified_ingredients: Optional[List[Dict[str, Any]]] = result.get("modified_ingredients")

        # Fallback: construct minimal ingredient update from ingredient_change if LLM omitted the list
        if not modified_ingredients and result.get("ingredient_change"):
            ic = result["ingredient_change"]
            if ic.get("name") and ic.get("new_qty"):
                modified_ingredients = [{"name": ic["name"], "quantity": ic["new_qty"]}]

        # Resolve schedule_id (priority order):
        #   1. Active cooking_context in plan_state (set when steps were generated this session)
        #   2. Explicit schedule_id in the request context payload
        #   3. Top-level schedule_id on the plan_state object
        #   4. DB Hydration — query DataStorageService by dish name (session-agnostic fallback)
        from app.modules.plan_ahead_state import get_plan_state as _get_ps
        _ps = _get_ps(owner_id)
        _cooking_ctx = _ps.get("cooking_context") or {}

        schedule_id: Optional[int] = (
            _cooking_ctx.get("schedule_id")
            or (((context or {}).get("data") or {}).get("schedule_id"))
            or _ps.get("schedule_id")
        )

        if not schedule_id:
            self.logger.info(
                f"[CookingStepsAgent] No schedule_id in memory for '{dish_name}' — querying DB"
            )
            schedule_id = await self._find_schedule_id_by_dish(owner_id, dish_name)

        # Resolve date/meal_time from plan_state slots first, then from context
        target_date: Optional[str] = None
        target_meal_time: Optional[str] = None

        def _resolve_slots(slots: Dict[str, Any]) -> None:
            nonlocal target_date, target_meal_time
            if target_date:
                return
            dish_lower = dish_name.lower()
            from datetime import date as _date
            _today = _date.today().isoformat()

            def _prio(d: str) -> tuple:
                if d == _today:
                    return (0, d)
                return (1, d) if d > _today else (2, "~" + d)

            for dk in sorted(slots.keys(), key=_prio):
                for mt, dishes in (slots[dk] or {}).items():
                    if isinstance(dishes, str):
                        dishes = [dishes]
                    if any(str(d).strip().lower() == dish_lower for d in (dishes or [])):
                        target_date, target_meal_time = dk, mt
                        return

        _resolve_slots(_ps.get("meal_plan_slots") or {})
        if not target_date and context and context.get("type") == "plan_ahead":
            _resolve_slots(((context.get("data") or {}).get("meal_plan_slots")) or {})

        # Build PATCH payload — same endpoint as normal step save, extended with ingredients
        base_url = _get_storage_base_url()
        saved = False
        effective_sid = schedule_id

        if base_url and schedule_id:
            patch_payload: Dict[str, Any] = {
                "dish_name": dish_name,
                "steps": modified_steps,
            }
            if target_date:
                patch_payload["date"] = target_date
            if target_meal_time:
                patch_payload["meal_time"] = target_meal_time
            if modified_ingredients:
                patch_payload["ingredients"] = modified_ingredients

            headers = {
                "Authorization": f"Bearer user_{owner_id}",
                "Content-Type": "application/json",
            }
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.patch(
                        f"{base_url}/api/schedule/{schedule_id}/cooking-steps",
                        headers=headers,
                        json=patch_payload,
                    )
                    saved = resp.status_code == 200
                    if not saved:
                        _resp_body = resp.text[:300]
                        self.logger.error(
                            f"[CookingStepsAgent.modify] SAVE_FAILED "
                            f"intent=MODIFY_RECIPE "
                            f"schedule_id={schedule_id} dish='{dish_name}' "
                            f"http_status={resp.status_code} "
                            f"response_body={_resp_body!r} "
                            f"patch_payload_keys={list(patch_payload.keys())} "
                            f"date={target_date!r} meal_time={target_meal_time!r}"
                        )
            except Exception as e:
                self.logger.error(
                    f"[CookingStepsAgent.modify] SAVE_FAILED "
                    f"intent=MODIFY_RECIPE "
                    f"schedule_id={schedule_id} dish='{dish_name}' "
                    f"error={type(e).__name__}: {e}"
                )

        if saved:
            self.logger.info(
                f"[CookingStepsAgent.modify] SAVED '{dish_name}' "
                f"schedule_id={schedule_id} changed={result.get('changed_indices')}"
            )
        else:
            _missing_reason = "no_schedule_id" if not schedule_id else "patch_failed"
            self.logger.error(
                f"[CookingStepsAgent.modify] SAVE_FAILED "
                f"intent=MODIFY_RECIPE dish='{dish_name}' "
                f"schedule_id={schedule_id} reason={_missing_reason}"
            )
        return {
            "dish_name": dish_name,
            "modified_steps": modified_steps,
            "ingredient_change": result.get("ingredient_change"),
            "changed_indices": result.get("changed_indices", []),
            "modified_ingredients": modified_ingredients,
            "saved": saved,
            "schedule_id": effective_sid,
        }

    # ------------------------------------------------------------------
    # 3. Step generation
    # ------------------------------------------------------------------

    @staticmethod
    async def _build_howtocook_context(dish_name: str) -> str:
        """Look up HowToCook-MCP for the dish and return a prompt context block.

        Returns an empty string when the MCP service is unavailable or the recipe
        is not found — the caller should proceed with pure LLM generation.
        """
        try:
            from app.services.howtocook_service import get_howtocook_service
            svc = get_howtocook_service()
            recipe = await svc.find_recipe(dish_name)
            if not recipe:
                return ""

            lines = [
                "REFERENCE RECIPE (from HowToCook — a trusted Chinese home-cooking resource):",
            ]

            if recipe.get("estimated_time"):
                lines.append(f"Estimated time: {recipe['estimated_time']}")

            if recipe.get("ingredients"):
                lines.append("Ingredients:")
                for ing in recipe["ingredients"]:
                    qty = f" {ing['quantity']}" if ing.get("quantity") else ""
                    lines.append(f"  - {ing['name']}{qty}")

            if recipe.get("steps"):
                lines.append("Original steps:")
                for i, step in enumerate(recipe["steps"], 1):
                    lines.append(f"  {i}. {step}")

            lines.append("")
            lines.append(
                "IMPORTANT: Base your response on this reference recipe. "
                "Use the EXACT ingredient names and quantities listed above. "
                "Reformat and adapt the steps to match the audience level and formatting rules below."
            )
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("[CookingStepsAgent] HowToCook lookup skipped: %s", exc)
            return ""

    async def _generate_steps(
        self,
        dish_name: str,
        ingredients: Optional[List[str]] = None,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
    ) -> Dict[str, Any]:
        """Generate step-by-step cooking instructions AND a structured ingredient list.

        When HowToCookService has the recipe, its ingredients and steps are injected
        into the LLM prompt as a trusted reference — ensuring accuracy while still
        adapting formatting and detail level to the user's cooking skill.
        Falls back to pure LLM generation when no recipe is found.

        Returns a dict:
          {
            "steps": ["step 1...", "step 2...", ...],
            "ingredients": [{"name": "...", "quantity": "..."}, ...]
          }
        Falls back to {"steps": [], "ingredients": []} on failure.
        """
        # HowToCook reference context (empty string = not found, pure LLM fallback)
        htc_context = await self._build_howtocook_context(dish_name)
        if htc_context:
            self.logger.info(
                "[CookingStepsAgent] HowToCook reference found for '%s'", dish_name
            )
        else:
            from app.services.howtocook_service import FOODIE_ERR_005
            self.logger.warning(
                "[%s] No HowToCook recipe found for '%s' — cooking steps will be pure LLM. "
                "If unexpected, check FoodieService is running and the dish name matches HowToCook catalog.",
                FOODIE_ERR_005, dish_name,
            )

        ing_text = ""
        if ingredients:
            ing_text = f"\nKnown ingredients: {', '.join(i for i in ingredients if i)}"

        # Phase header labels, example steps, and tip labels for each language.
        # These are injected into the beginner FORMAT RULES so that the LLM's
        # in-context examples match the requested output language and never leak
        # Chinese into English (or other language) responses.
        _lang = language or "zh"
        _phase_prep, _phase_cook, _phase_serve = {
            "zh": ("🥗 准备阶段", "🍳 开始烹饪", "🥢 享用"),
            "en": ("🥗 Prep",     "🍳 Cook",       "🥢 Serve"),
            "ja": ("🥗 準備",     "🍳 調理",        "🥢 盛り付け"),
            "ko": ("🥗 준비",     "🍳 조리",         "🥢 서빙"),
        }.get(_lang, ("🥗 准备阶段", "🍳 开始烹饪", "🥢 享用"))

        _example_step, _tip_label, _example_tip = {
            "zh": (
                "**打散**鸡蛋: 取 **3个鸡蛋** 加入 **1克盐**，充分搅打均匀。",
                "💡 小贴士: ",
                "**调水淀粉**: 碗中混合 **15g淀粉** + **30ml水**。', '💡 小贴士: 淀粉容易沉淀，下锅前记得再搅一下！",
            ),
            "en": (
                "**Beat** eggs: crack **3 eggs** into a bowl, add **1 g salt**, whisk thoroughly.",
                "💡 Tip: ",
                "**Mix cornstarch**: combine **15 g cornstarch** + **30 ml water** in a bowl.', '💡 Tip: Starch settles fast — stir again right before adding to the pan!",
            ),
            "ja": (
                "卵を**溶く**: **卵3個**をボウルに割り、**塩1g**を加えてよく混ぜる。",
                "💡 ヒント: ",
                "**片栗粉を溶く**: ボウルに **片栗粉15g** と **水30ml** を混ぜる。', '💡 ヒント: でんぷんは沈殿しやすいので、鍋に入れる直前にもう一度かき混ぜましょう！",
            ),
            "ko": (
                "달걀을 **풀기**: 그릇에 **달걀 3개**를 깨뜨리고 **소금 1g**을 넣어 잘 섞는다.",
                "💡 팁: ",
                "**전분물 만들기**: 그릇에 **전분 15g** + **물 30ml**를 섞는다.', '💡 팁: 전분은 가라앉기 쉬우니 팬에 넣기 직전에 한 번 더 저어주세요!",
            ),
        }.get(_lang, (
            "**打散**鸡蛋: 取 **3个鸡蛋** 加入 **1克盐**，充分搅打均匀。",
            "💡 小贴士: ",
            "**调水淀粉**: 碗中混合 **15g淀粉** + **30ml水**。', '💡 小贴士: 淀粉容易沉淀，下锅前记得再搅一下！",
        ))

        _level_instructions = {
            # Entry (入门级): 消除不确定性，追求成功率与速度
            "beginner": (
                "The user is an ENTRY-LEVEL home cook — prioritise foolproof success, clear structure, and low cognitive load.\n"
                "FORMAT RULES (the frontend parses these markers — follow them exactly):\n"
                "\n"
                "1. PHASE HEADERS: Divide the recipe into phases. Output each phase header as a standalone string in the steps array:\n"
                f"   - '{_phase_prep}' for all cutting, measuring, and sauce-mixing BEFORE the stove is on\n"
                f"   - '{_phase_cook}' for all active heat steps\n"
                f"   - '{_phase_serve}' for plating and serving\n"
                "   Only include phases that actually apply. Each header string must start with the emoji.\n"
                "\n"
                "2. BOLD FORMATTING: In every cooking step, wrap with **double asterisks**:\n"
                "   - The main action verb\n"
                "   - The primary ingredient\n"
                "   - Every measurement\n"
                f"   Example step: '{_example_step}'\n"
                "\n"
                f"3. TIPS: When a step needs a safety or success clarification, add a SEPARATE string IMMEDIATELY AFTER that step, starting with '{_tip_label}'.\n"
                f"   Example: ['{_example_tip}']\n"
                "\n"
                "4. MEASUREMENTS: NEVER use vague amounts — always specify exact quantities.\n"
                "5. AVOID high-skill techniques: no deep-frying, caramelising, flambéing, or anything requiring precise heat mastery.\n"
                "6. Total 6–8 actual cooking steps (phase header strings and tip strings do NOT count toward this total).\n"
            ),
            # Intermediate (进阶级): 引入风味开发技巧，追求风味与效率平衡
            "intermediate": (
                "The user is an INTERMEDIATE cook who knows basic techniques — focus on flavour development.\n"
                "- Introduce techniques like searing (爆香/煎), deglazing (收汁), or emulsifying where relevant, with brief context on why they matter.\n"
                "- Use sensory and visual doneness cues instead of just timers (e.g. 'cook until the garlic turns lightly golden and fragrant', 'stir-fry until the onions are translucent').\n"
                "- Provide specific measurements AND timing, but allow some flexibility (e.g. '1–2 tbsp').\n"
                "- Mention texture goals and flavour-balancing tips (e.g. 'taste and adjust salt/acid balance before plating').\n"
                "- Include 6–9 steps."
            ),
            # Expert (专家级): 极简说明，高阶参数，追求精准与艺术
            "expert": (
                "The user is an EXPERT cook — use concise, high-precision professional language.\n"
                "- Skip explanations of standard techniques (knife skills, mise en place, basic heat control are assumed).\n"
                "- Focus on critical parameters: internal temperatures, exact oil temperatures, sauce reduction ratios, specific textures (e.g. 'al dente', 'silky nappe consistency').\n"
                "- Reference advanced concepts freely: Maillard reaction, carry-over cooking, acid/fat/heat balance, emulsion stability.\n"
                "- Where appropriate, offer a brief gourmet variation or professional plating suggestion as an optional note.\n"
                "- Keep steps concise and precise — each step should convey professional-grade intent.\n"
                "- Include 7–10 steps."
            ),
        }
        level_instr = _level_instructions.get(cooking_level or "beginner", _level_instructions["beginner"])

        _lang_instr_map = {
            "zh": ("Simplified Chinese (简体中文)", "你必须用简体中文写所有步骤。"),
            "en": ("English", "You MUST write every single step in English. Do NOT use Chinese or any other language, even if the dish name is Chinese."),
            "ja": ("Japanese (日本語)", "すべてのステップを日本語で書いてください。"),
            "ko": ("Korean (한국어)", "모든 단계를 한국어로 작성하세요."),
        }
        _lang_name, _lang_enforce = _lang_instr_map.get(language or "zh", _lang_instr_map["zh"])

        htc_block = f"\n{htc_context}\n" if htc_context else ""

        prompt = (
            f"CRITICAL LANGUAGE RULE: You MUST write ALL cooking steps and ingredient names exclusively in {_lang_name}. "
            f"{_lang_enforce} "
            f"This rule overrides everything else — even if the dish name is in a different language, the steps and ingredient names must be in {_lang_name}.\n\n"
            f"Generate step-by-step cooking instructions AND a complete ingredient list for: {dish_name}.{ing_text}\n"
            f"{htc_block}"
            f"AUDIENCE: {level_instr}\n"
            'Respond with a JSON object:\n'
            '{\n'
            '  "steps": ["step 1...", "step 2...", ...],\n'
            '  "ingredients": [{"name": "ingredient name", "quantity": "exact amount"}, ...]\n'
            '}\n\n'
            "Requirements:\n"
            "- steps: follow the audience-level formatting rules above.\n"
            "- ingredients: list EVERY ingredient used, with exact quantities (e.g. {\"name\": \"鸡蛋\", \"quantity\": \"3个\"}).\n"
            "- NEVER use '适量', 'to taste', or vague amounts in the ingredients list — always give a specific number.\n"
            "- Each step should be a single, actionable sentence.\n"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {
                "parts": [{"text": f"You are a cooking assistant. You MUST always respond in {_lang_name} only. {_lang_enforce}"}]
            },
            "generationConfig": {
                "temperature": 0.3,
                # responseMimeType intentionally omitted: strict JSON mode can truncate
                # long step lists, causing "Unterminated string" parse errors.
                # maxOutputTokens intentionally omitted: let the model decide the length.
            },
        }
        headers = {"Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self.api_url, headers=headers, json=payload)
                resp.raise_for_status()
                raw = (
                    resp.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                # Strip markdown code fences if present
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                    raw = raw.strip()
                # Extract the first {...} JSON object
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start == -1 or end == 0:
                    raise ValueError(f"No JSON object in response: {raw[:80]!r}")
                parsed = json.loads(raw[start:end])
                steps = parsed.get("steps", [])
                raw_ingredients = parsed.get("ingredients", [])
                # Normalise ingredient entries to {"name": str, "quantity": str}
                normalised_ingredients: List[Dict[str, str]] = []
                for item in raw_ingredients:
                    if isinstance(item, dict):
                        name = str(item.get("name") or "").strip()
                        qty  = str(item.get("quantity") or "").strip()
                        if name:
                            normalised_ingredients.append({"name": name, "quantity": qty})
                if steps and isinstance(steps, list):
                    return {
                        "steps": [str(s) for s in steps],
                        "ingredients": normalised_ingredients,
                    }
        except Exception as e:
            logger.warning(f"[CookingStepsAgent] Step generation failed: {e}")
        return {"steps": [], "ingredients": []}

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
        ingredients: Optional[List[Dict[str, str]]] = None,
    ) -> bool:
        """PATCH the schedule's dish with cooking steps and (optionally) ingredient quantities."""
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
        if ingredients:
            payload["ingredients"] = ingredients

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
                logger.error(
                    f"[CookingStepsAgent] SAVE_FAILED intent=COOKING_STEPS "
                    f"schedule_id={schedule_id} dish='{dish_name}' "
                    f"http_status={resp.status_code} "
                    f"response_body={resp.text[:300]!r} "
                    f"date={date!r} meal_time={meal_time!r}"
                )
                return False
        except Exception as e:
            logger.error(
                f"[CookingStepsAgent] SAVE_FAILED intent=COOKING_STEPS "
                f"schedule_id={schedule_id} dish='{dish_name}' "
                f"error={type(e).__name__}: {e}"
            )
            return False

    async def _get_existing_recipe(
        self,
        schedule_id: int,
        owner_id: int,
        dish_name: str,
        date: Optional[str] = None,
        meal_time: Optional[str] = None,
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Fetch the current cookingSteps and ingredients for a dish from the DB.

        Returns (steps, ingredients). Both are empty lists when not found or on error.
        Matching order: exact name → date+meal_time slot → fuzzy name.
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return [], []
        try:
            headers = {"Authorization": f"Bearer user_{owner_id}"}
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"{base_url}/api/schedule/{schedule_id}",
                    headers=headers,
                )
                resp.raise_for_status()
                schedule_data = resp.json()
        except Exception as e:
            logger.warning(f"[CookingStepsAgent] _get_existing_recipe fetch failed: {e}")
            return [], []

        features = (schedule_data.get("metadata") or {}).get("features") or []
        dish_name_lower = dish_name.strip().lower()
        best_dish: Optional[Dict[str, Any]] = None

        for feat in features:
            if not isinstance(feat, dict) or feat.get("type") != "meal_plan":
                continue
            for plan in feat.get("plans") or []:
                if date and not plan.get("date", "").startswith(date):
                    continue
                for meal in plan.get("meals") or []:
                    if meal_time and meal.get("mealTime") != meal_time:
                        continue
                    for dish in meal.get("dishes") or []:
                        stored = dish.get("name", "").strip().lower()
                        if stored == dish_name_lower:
                            # Exact match — return immediately
                            return (
                                dish.get("cookingSteps") or [],
                                dish.get("ingredients") or [],
                            )
                        # Keep as fuzzy fallback
                        if stored in dish_name_lower or dish_name_lower in stored:
                            best_dish = dish

        if best_dish is not None:
            return (
                best_dish.get("cookingSteps") or [],
                best_dish.get("ingredients") or [],
            )
        return [], []

    async def _create_meal_with_steps(
        self,
        owner_id: int,
        dish_name: str,
        steps: List[str],
        date: str,
        meal_time: str,
        ingredients: Optional[List] = None,
    ) -> Optional[int]:
        """
        Create a new meal-plan schedule entry for the given date with this dish + steps.
        Returns the new schedule_id, or None on failure.
        ingredients may be List[str] or List[Dict] ({"name": ..., "quantity": ...}).
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        ing_objs = []
        for i, item in enumerate(ingredients or []):
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                qty  = str(item.get("quantity") or "").strip()
                cat  = str(item.get("category") or "other").strip()
            elif isinstance(item, str):
                name = item.strip()
                qty  = ""
                cat  = "other"
            else:
                continue
            if name:
                ing_objs.append({"id": f"ing_{ts}_{i}", "name": name, "quantity": qty, "category": cat})
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
        ingredients: Optional[List[Dict[str, str]]] = None,
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
            ok = await self._patch_schedule(
                schedule_id, owner_id, dish_name, steps, date, meal_time, ingredients
            )
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
                ok = await self._patch_schedule(
                    alt_sid, owner_id, dish_name, steps, date, meal_time, ingredients
                )
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
    # Batch API
    # ------------------------------------------------------------------

    async def _generate_and_save_for_dish(
        self,
        dish_name: str,
        owner_id: int,
        schedule_id: Optional[int],
        context: Optional[Dict[str, Any]],
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
        skip_if_exists: bool = False,
    ) -> Dict[str, Any]:
        """Generate cooking steps for a single dish and persist them.

        When ``skip_if_exists=True`` the method returns early (saved=False, skipped=True)
        if the dish already has cooking steps in the DB.  This is used during
        auto-generation after PLAN_AHEAD so that existing steps are never silently
        overwritten by a background task.
        """
        self.logger.info(
            "[CookingStepsAgent.batch] Starting generation for '%s' "
            "(owner=%s, schedule_id=%s, skip_if_exists=%s)",
            dish_name, owner_id, schedule_id, skip_if_exists,
        )
        # Extract ingredients from plan context
        ingredients: List[str] = []
        if context and context.get("type") == "plan_ahead":
            di = (context.get("data") or {}).get("dish_ingredients") or {}
            for item in (di.get(dish_name) or []):
                if isinstance(item, dict):
                    n = item.get("name", "").strip()
                    if n:
                        ingredients.append(n)
                elif isinstance(item, str) and item.strip():
                    ingredients.append(item.strip())

        # Find the date/meal_time for this dish from the plan slots.
        # Sort dates so that today comes first, then nearest future dates, then past dates —
        # this prevents an old entry (e.g. 宫保鸡丁 on 2026-03-01) from shadowing a newly
        # added entry on the same name (e.g. 宫保鸡丁 on 2026-03-06 added today).
        target_date: Optional[str] = None
        target_meal_time: Optional[str] = None
        if context and context.get("type") == "plan_ahead":
            slots: Dict[str, Any] = (context.get("data") or {}).get("meal_plan_slots") or {}
            dish_lower = dish_name.lower()
            from datetime import date as _date
            _today = _date.today().isoformat()

            def _date_priority(d: str) -> tuple:
                if d == _today:
                    return (0, d)
                if d > _today:
                    return (1, d)          # near future (ascending)
                return (2, "~" + d)        # past (also ascending, but lower priority)

            for date_key in sorted(slots.keys(), key=_date_priority):
                date_slots = slots[date_key] or {}
                for mt, dishes in date_slots.items():
                    if isinstance(dishes, str):
                        dishes = [dishes]
                    for d in (dishes or []):
                        if str(d).strip().lower() == dish_lower:
                            target_date = date_key
                            target_meal_time = mt
                            break
                    if target_date:
                        break
                if target_date:
                    break

        # Optionally skip if the dish already has steps in the DB (silent auto-generation mode).
        # Multi-date plans can be split across separate schedule records, so we check both the
        # primary schedule_id and any other schedules that cover target_date.
        if skip_if_exists:
            existing_steps: List[str] = []
            found_in_sid: Optional[int] = None
            if schedule_id:
                existing_steps, _ = await self._get_existing_recipe(
                    schedule_id=schedule_id,
                    owner_id=owner_id,
                    dish_name=dish_name,
                    date=target_date,
                    meal_time=target_meal_time,
                )
                if existing_steps:
                    found_in_sid = schedule_id
            if not existing_steps and target_date:
                alt_sids = await self._find_schedule_ids_for_date(owner_id, target_date)
                for alt_sid in alt_sids:
                    if alt_sid == schedule_id:
                        continue
                    existing_steps, _ = await self._get_existing_recipe(
                        schedule_id=alt_sid,
                        owner_id=owner_id,
                        dish_name=dish_name,
                        date=target_date,
                        meal_time=target_meal_time,
                    )
                    if existing_steps:
                        found_in_sid = alt_sid
                        break
            if existing_steps:
                self.logger.info(
                    f"[CookingStepsAgent.batch] '{dish_name}' already has steps in schedule "
                    f"{found_in_sid} — skipping auto-generation."
                )
                return {
                    "dish_name": dish_name,
                    "cooking_steps": existing_steps,
                    "saved": False,
                    "skipped": True,
                    "schedule_id": found_in_sid,
                }

        recipe = await self._generate_steps(dish_name, ingredients, cooking_level=cooking_level, language=language)
        steps = recipe.get("steps", [])
        generated_ingredients = recipe.get("ingredients", [])
        if not steps:
            self.logger.warning(f"[CookingStepsAgent.batch] No steps generated for '{dish_name}'")
            return {"dish_name": dish_name, "cooking_steps": [], "saved": False, "schedule_id": None}

        saved, effective_sid = await self._save_steps(
            owner_id=owner_id,
            schedule_id=schedule_id,
            dish_name=dish_name,
            steps=steps,
            date=target_date,
            meal_time=target_meal_time,
            ingredients=generated_ingredients,
        )
        self.logger.info(
            f"[CookingStepsAgent.batch] '{dish_name}': {len(steps)} steps, "
            f"{len(generated_ingredients)} ingredients, saved={saved}, sid={effective_sid}"
        )
        return {
            "dish_name": dish_name,
            "cooking_steps": steps,
            "ingredients": generated_ingredients,
            "saved": saved,
            "schedule_id": effective_sid,
            "date": target_date,
            "meal_time": target_meal_time,
        }

    async def execute_batch(
        self,
        dish_names: List[str],
        owner_id: int,
        context: Optional[Dict[str, Any]] = None,
        schedule_id: Optional[int] = None,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
        skip_if_exists: bool = False,
    ) -> List[Dict[str, Any]]:
        """Generate and persist cooking steps for multiple dishes in parallel.

        Args:
            dish_names:      List of canonical dish names to process.
            owner_id:        User/owner ID.
            context:         plan_ahead context (meal_plan_slots, dish_ingredients, schedule_id).
            schedule_id:     Override schedule_id (if not already in context).
            skip_if_exists:  When True, silently skip dishes that already have steps in the DB.
                             Used for background auto-generation after PLAN_AHEAD so existing
                             steps are never silently overwritten.

        Returns:
            List of result dicts, one per dish, in the same order as dish_names.
            Any dish that raises an exception returns {"dish_name": ..., "error": "..."}.
        """
        # Prefer schedule_id from context if available
        ctx_sid = None
        if context and context.get("type") == "plan_ahead":
            ctx_sid = (context.get("data") or {}).get("schedule_id")
        effective_sid = ctx_sid or schedule_id

        self.logger.info(
            "[CookingStepsAgent.batch] execute_batch started: %d dish(es) for owner=%s, "
            "schedule_id=%s, skip_if_exists=%s — %s",
            len(dish_names), owner_id, effective_sid, skip_if_exists, dish_names,
        )

        tasks = [
            self._generate_and_save_for_dish(
                dish_name=dish,
                owner_id=owner_id,
                schedule_id=effective_sid,
                context=context,
                cooking_level=cooking_level,
                language=language,
                skip_if_exists=skip_if_exists,
            )
            for dish in dish_names
        ]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results: List[Dict[str, Any]] = []
        saved_count = 0
        skipped_count = 0
        failed_count = 0
        for dish, outcome in zip(dish_names, raw):
            if isinstance(outcome, Exception):
                self.logger.error("[CookingStepsAgent.batch] Error for '%s': %s", dish, outcome, exc_info=outcome)
                results.append({"dish_name": dish, "cooking_steps": [], "saved": False, "error": str(outcome)})
                failed_count += 1
            else:
                results.append(outcome)
                if outcome.get("skipped"):
                    skipped_count += 1
                elif outcome.get("saved"):
                    saved_count += 1
                else:
                    failed_count += 1
        self.logger.info(
            "[CookingStepsAgent.batch] execute_batch complete: saved=%d, skipped=%d, failed=%d",
            saved_count, skipped_count, failed_count,
        )
        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        user_input: str,
        owner_id: int,
        context: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
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

        # Step 1e: Have dish_name but still no target_date/schedule — search today & tomorrow in DB.
        #   Handles queries like "비빔밥 만드는 법?" after a plan was just added, where the
        #   frontend didn't pass plan_ahead context so Steps 1b/1c couldn't resolve the date.
        if dish_name and not target_date and not schedule_id_from_lookup:
            from datetime import date as _dt_date, timedelta
            for _check_date in [
                _dt_date.today().isoformat(),
                (_dt_date.today() + timedelta(days=1)).isoformat(),
            ]:
                _sd, _sid, _smt = await self._dish_from_schedule(
                    owner_id, _check_date, preferred_mt, dish_hint=dish_name
                )
                if _sd:
                    dish_name = _sd
                    target_date = _check_date
                    target_meal_time = _smt or target_meal_time
                    schedule_id_from_lookup = _sid
                    self.logger.info(
                        f"[CookingStepsAgent] Step1e schedule lookup: "
                        f"dish={dish_name!r}, date={target_date}, sid={_sid}"
                    )
                    break

        if not dish_name:
            _no_dish_msg = {
                "zh": "请告诉我您想了解哪道菜的做法？",
                "en": "Which dish would you like the cooking steps for?",
                "ja": "どの料理の作り方を知りたいですか？",
                "ko": "어떤 요리의 조리법을 알고 싶으신가요?",
            }.get(language or "zh", "Which dish would you like the cooking steps for?")
            return self.format_response(
                action="COOKING_STEPS",
                message=_no_dish_msg,
                data={"needs_dish_name": True},
            )

        # Wire up schedule_id from lookup into context so _save_steps can find it
        if schedule_id_from_lookup and not (context and (context.get("data") or {}).get("schedule_id")):
            context = {"type": "plan_ahead", "data": {"schedule_id": schedule_id_from_lookup}}

        if not dish_name:
            return self.format_response(
                action="COOKING_STEPS",
                message=_no_dish_msg,  # type: ignore[possibly-undefined]
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

        # ---- 3. Resolve schedule_id; return existing steps if already saved ----
        # Check the DB *before* calling the LLM.  If steps already exist (e.g. from a
        # previous explicit request or the background auto-gen after PLAN_AHEAD), return
        # them immediately — no regeneration needed.
        # Regeneration / modification is handled by the MODIFY_RECIPE intent instead.
        schedule_id: Optional[int] = None
        if context and context.get("type") == "plan_ahead":
            schedule_id = (context.get("data") or {}).get("schedule_id")

        if schedule_id:
            existing_steps, existing_ingredients = await self._get_existing_recipe(
                schedule_id=schedule_id,
                owner_id=owner_id,
                dish_name=dish_name,
                date=target_date,
                meal_time=target_meal_time,
            )
            if existing_steps:
                self.logger.info(
                    f"[CookingStepsAgent] Found existing recipe for '{dish_name}' in schedule "
                    f"{schedule_id} ({len(existing_steps)} steps) — returning directly."
                )
                return self.format_response(
                    action="COOKING_STEPS",
                    message=f"Here are the cooking steps for {dish_name}",
                    data={
                        "dish_name": dish_name,
                        "cooking_steps": existing_steps,
                        "ingredients": existing_ingredients,
                        "schedule_id": schedule_id,
                        "date": target_date,
                        "meal_time": target_meal_time,
                        "saved": True,
                    },
                )

        # ---- 4. No existing steps — generate a new recipe and save ----
        recipe = await self._generate_steps(dish_name, ingredients, cooking_level=cooking_level, language=language)
        steps = recipe.get("steps", [])
        generated_ingredients: List[Dict[str, str]] = recipe.get("ingredients", [])

        if not steps:
            _fail_msg = {
                "zh": f"抱歉，暂时无法生成「{dish_name}」的烹饪步骤，请稍后再试。",
                "en": f"Sorry, I couldn't generate cooking steps for {dish_name}. Please try again.",
                "ja": f"{dish_name}の調理手順を生成できませんでした。後でもう一度お試しください。",
                "ko": f"{dish_name}의 조리 단계를 생성할 수 없습니다. 나중에 다시 시도해 주세요.",
            }.get(language or "zh", f"Sorry, I couldn't generate cooking steps for {dish_name}. Please try again.")
            return self.format_response(
                action="COOKING_STEPS",
                message=_fail_msg,
                data={"dish_name": dish_name, "cooking_steps": []},
            )

        saved, effective_sid = await self._save_steps(
            owner_id=owner_id,
            schedule_id=schedule_id,
            dish_name=dish_name,
            steps=steps,
            date=target_date,
            meal_time=target_meal_time,
            ingredients=generated_ingredients,
        )

        return self.format_response(
            action="COOKING_STEPS",
            message=f"Generated cooking steps for {dish_name}",
            data={
                "dish_name": dish_name,
                "cooking_steps": steps,
                "ingredients": generated_ingredients,
                "schedule_id": effective_sid or schedule_id,
                "date": target_date,
                "meal_time": target_meal_time,
                "saved": saved,
            },
        )
