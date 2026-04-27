"""
Chat pipeline — Tool-Use architecture.

Flow per request:
  1. [Special case] correction mode  → correction_pipeline (frontend signal)
  2. Build state context             → system prompt with current session state
  3. Gemini call #1 (tool selection) → LLM picks which skill to invoke
  4. Execute skill via ToolExecutor  → calls existing agent / pipeline backend
  5a. Direct-response tools          → return result immediately (no extra LLM call)
  5b. Data tools                     → Gemini call #2 generates natural language reply

Old intent_classifier, agent_factory, intent_router, and the 19 special-case
overrides in the previous chat.py are all replaced by this flow.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.modules.plan_ahead_state import get_plan_state, update_plan_state
from app.skills.tool_registry import GEMINI_TOOLS, TOOL_TO_ACTION, tool_executor

logger = logging.getLogger(__name__)


class ChatPipeline:
    """Tool-Use chat pipeline."""

    # System prompt for the tool-selection call.
    # Kept short and directive; state context is injected at runtime.
    TOOL_SELECTION_PROMPT = """\
You are a Home AI Agent named "Storage Helper".
You assist with kitchen inventory, meal planning, document organization, and scheduling.

{state_context}

LANGUAGE: {language_instruction}

Choose the most appropriate tool based on the user's message and context above.

Select the best tool based on the user's intent and the context above. \
Call a tool only when the tool description matches — otherwise respond directly.\
"""

    # System prompt for the final response generation call.
    RESPONSE_PROMPT = """\
You are a helpful Home AI Agent named "Storage Helper".
{state_context}
LANGUAGE: {language_instruction}

A tool was called and its result is provided in the conversation. \
Generate a clear, natural response for the user based on the tool result.\
"""

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent?key={self.api_key}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    async def run(
        self,
        user_input: str,
        owner_id: int,
        history: List[Dict[str, str]] = None,
        context: Optional[Dict[str, Any]] = None,
        user_timezone: Optional[str] = None,
        cooking_level: str = "beginner",
        language: str = "zh",
    ) -> Dict[str, Any]:
        """
        Main chat entry point.  Returns a dict compatible with ChatResponse:
          {response, intent, confidence, reasoning, action, action_data}
        """
        history = history or []

        _lang_map = {
            "zh": "Always respond in Simplified Chinese (简体中文), regardless of input language.",
            "en": "Always respond in English, regardless of input language.",
            "ja": "Always respond in Japanese (日本語), regardless of input language.",
        }
        language_instruction = _lang_map.get(language, _lang_map["zh"])

        # ── Special case: correction mode (frontend-initiated) ─────────────
        if context and context.get("type") == "correction":
            return await self._handle_correction(
                user_input, owner_id, history, context, language_instruction
            )

        # ── Read current session state ─────────────────────────────────────
        plan_state = get_plan_state(owner_id)
        cooking_context: Optional[Dict] = plan_state.get("cooking_context")

        # ── Hydrate meal plan from DB if in-memory state is empty ──────────
        # Ensures [MEAL PLAN EXISTS] appears in context so tool selection LLM
        # can correctly route "今晚吃什么" to view_schedule instead of plan_meal.
        if not plan_state.get("meal_plan") and not plan_state.get("phase"):
            _db_meals = await self._fetch_upcoming_meals(owner_id, user_timezone)
            if _db_meals:
                plan_state = {**plan_state, "meal_plan": _db_meals}

        # ── Build state context for prompts ────────────────────────────────
        state_context = self._build_state_context(
            plan_state=plan_state,
            cooking_context=cooking_context,
            context=context,
            user_timezone=user_timezone,
        )

        tool_system = self.TOOL_SELECTION_PROMPT.format(
            state_context=state_context,
            language_instruction=language_instruction,
        )
        resp_system = self.RESPONSE_PROMPT.format(
            state_context=state_context,
            language_instruction=language_instruction,
        )

        # ── Hard-route: skip LLM selection when waiting for date confirmation ─
        # When the pipeline is waiting for the user to confirm/correct a proposed
        # date range, ANY reply must go to plan_meal — even ultra-short affirmations
        # like "好的" or "确认" that the LLM would otherwise answer directly.
        _pending_confirm = (
            bool(plan_state.get("pending_planning_queue"))
            and plan_state.get("last_pipeline_action") == "ask_confirm_dates"
        )
        if _pending_confirm:
            logger.info(
                "[TOOL-USE] Hard-routing to plan_meal (pending date confirmation)."
            )
            fn_name = "plan_meal"
            fn_args = {"user_request": user_input}
            tool_result = await tool_executor.execute(
                fn_name, fn_args, owner_id,
                user_input=user_input, history=history, context=context,
                user_timezone=user_timezone, cooking_level=cooking_level,
                language=language, cooking_context=cooking_context,
                plan_state=plan_state, api_url=self.api_url,
            )
            # plan_meal always returns _direct_response=True
            result = tool_result if tool_result.get("_direct_response") else tool_result
            # Normalise to ChatResponse shape
            if "response" not in result:
                result = {
                    "response": result.get("tool_result", ""),
                    "intent": "PLAN_AHEAD",
                    "confidence": 0.95,
                    "reasoning": "Hard-routed to plan_meal (pending date confirmation).",
                    "action": result.get("action", "PLAN_AHEAD"),
                    "action_data": result.get("action_data", {}),
                }
            logger.info(
                "[chat] RESPONSE action=%s action_data_keys=%s",
                result.get("action"), list((result.get("action_data") or {}).keys()),
            )
            logger.info("[chat] === AI RESPONSE START ===")
            logger.info("[chat] %s", result.get("response", "")[:300])
            logger.info("[chat] === AI RESPONSE END ===")
            return result

        # ── Step 1: Tool selection (Gemini call #1) ────────────────────────
        selection = await self._call_gemini_with_tools(
            user_input=user_input,
            history=history,
            system_instruction=tool_system,
        )
        function_calls = selection.get("function_calls") or []
        _total_tokens = selection.get("tokens", 0)

        # ── No tool selected → direct LLM response ────────────────────────
        if not function_calls:
            response_text = selection.get("text") or ""
            if not response_text:
                # Fallback: make a plain generation call so user always gets a reply
                response_text, _plain_tokens = await self._plain_generate(
                    user_input=user_input,
                    history=history,
                    system_instruction=resp_system,
                )
                _total_tokens += _plain_tokens
            logger.info("[TOOL-USE] No tool selected; direct response.")
            return {
                "response": response_text,
                "intent": "GENERAL",
                "confidence": 0.80,
                "reasoning": "No tool required; responded directly.",
                "action": "GENERAL",
                "action_data": {},
                "_tokens": _total_tokens,
            }

        # ── Step 2: Execute the first selected tool ────────────────────────
        fc = function_calls[0]
        fn_name: str = fc.get("name", "")
        fn_args: Dict[str, Any] = fc.get("args") or {}

        logger.info("[TOOL-USE] Tool selected: %s  args=%s", fn_name, fn_args)

        tool_result = await tool_executor.execute(
            fn_name,
            fn_args,
            owner_id,
            user_input=user_input,
            history=history,
            context=context,
            user_timezone=user_timezone,
            cooking_level=cooking_level,
            language=language,
            cooking_context=cooking_context,
            plan_state=plan_state,
            api_url=self.api_url,
        )

        # ── Post-execute state updates ─────────────────────────────────────
        if fn_name == "get_cooking_steps":
            cr = tool_result.get("_cooking_result") or {}
            steps = cr.get("cooking_steps") or (cr.get("data") or {}).get("cooking_steps") or []
            d_name = cr.get("dish_name") or (cr.get("data") or {}).get("dish_name") or fn_args.get("dish_name", "")
            if steps and d_name:
                update_plan_state(
                    owner_id,
                    cooking_context={
                        "dish_name": d_name,
                        "steps": steps,
                        "schedule_id": (cr.get("data") or {}).get("schedule_id"),
                    },
                )
                logger.info("[TOOL-USE] Updated cooking_context: dish=%s steps=%d", d_name, len(steps))

        # ── Direct-response tools (pipeline already generated the reply) ───
        if tool_result.get("_direct_response"):
            result = {k: v for k, v in tool_result.items()
                      if k not in ("_direct_response", "_cooking_result", "_cooking_action")}
            result.setdefault("intent", fn_name)
            result.setdefault("confidence", 0.95)
            result.setdefault("reasoning", f"Tool-use: {fn_name}")
            result.setdefault("action", TOOL_TO_ACTION.get(fn_name, fn_name.upper()))
            result.setdefault("action_data", {})
            result["_tokens"] = _total_tokens + result.get("_tokens", 0)
            self._log_response(result.get("action", ""), result.get("action_data", {}), result.get("response", ""))
            return result

        # ── Step 3: Generate final natural language reply (Gemini call #2) ─
        response_text, _final_tokens = await self._generate_final_response(
            user_input=user_input,
            history=history,
            system_instruction=resp_system,
            function_call=fc,
            tool_result_text=tool_result.get("tool_result", ""),
        )
        _total_tokens += _final_tokens

        final_action = tool_result.get("action") or TOOL_TO_ACTION.get(fn_name, fn_name.upper())
        final_action_data = tool_result.get("action_data") or {}

        result = {
            "response": response_text,
            "intent": fn_name,
            "confidence": 0.95,
            "reasoning": f"Tool-use: {fn_name}",
            "action": final_action,
            "action_data": final_action_data,
            "_tokens": _total_tokens,
        }
        self._log_response(final_action, final_action_data, response_text)
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _fetch_upcoming_meals(
        self, owner_id: int, user_timezone: Optional[str]
    ) -> Dict:
        """Fetch this week's meal plan from DB for state_context hydration."""
        from app.storage.pipeline_storage import _default_storage
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
            today = datetime.now(tz).date() if tz else datetime.utcnow().date()
        except Exception:
            today = datetime.utcnow().date()
        end_date = today + timedelta(days=7)
        try:
            schedules = await _default_storage.get_user_schedules(owner_id)
        except Exception:
            return {}
        meal_plan: Dict = {}
        for s in schedules:
            if "meal_plan" not in s.get("event_type", ""):
                continue
            slots = (s.get("metadata") or {}).get("meal_plan_slots") or {}
            for date_str, date_slots in slots.items():
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if today <= d <= end_date:
                    meal_plan[date_str] = date_slots
        return meal_plan

    # ─────────────────────────────────────────────────────────────────────────
    # State context builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_state_context(
        self,
        plan_state: Dict,
        cooking_context: Optional[Dict],
        context: Optional[Dict],
        user_timezone: Optional[str],
    ) -> str:
        parts: List[str] = []

        # Current time
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
            now = datetime.now(tz) if tz else datetime.utcnow()
        except Exception:
            now = datetime.utcnow()
        parts.append(
            f"Current time: {now.strftime('%Y-%m-%d %H:%M')} ({user_timezone or 'UTC'})"
        )

        # Active cooking session
        if cooking_context and cooking_context.get("dish_name"):
            dish = cooking_context["dish_name"]
            steps = cooking_context.get("steps") or []
            pending_replace = cooking_context.get("pending_replace", False)
            parts.append(
                f"\n[ACTIVE COOKING SESSION: {dish}]\n"
                f"Recipe steps available: {len(steps)} step(s).\n"
                "- Follow-up questions about this dish → respond directly (no tool needed).\n"
                "- User wants to change an ingredient quantity → call modify_recipe_ingredient.\n"
                "- User wants to save/replace steps → call get_cooking_steps with save_to_plan=True.\n"
                "- User asks how to cook a DIFFERENT dish → call get_cooking_steps."
            )
            if pending_replace:
                parts.append(
                    f"  ⚠ PENDING REPLACE: User is confirming whether to replace existing steps for {dish}. "
                    "If they confirm (好的/是的/确定/yes/ok), call get_cooking_steps with save_to_plan=True."
                )

        # Active meal planning session
        meal_plan = plan_state.get("meal_plan") or {}
        phase = plan_state.get("phase")
        pending_options = plan_state.get("pending_options")
        pending_queue = plan_state.get("pending_planning_queue")
        last_action = plan_state.get("last_pipeline_action")
        if pending_queue and last_action == "ask_confirm_dates":
            parts.append(
                "\n[MEAL DATE CONFIRMATION PENDING]\n"
                "The user is responding to a proposed meal plan date/meal confirmation prompt.\n"
                "ALWAYS call plan_meal — even for very short replies like '好的', '确认', '对', "
                "'yes', 'ok', '可以', '取消', 'cancel'."
            )
        elif meal_plan or phase:
            date_list = list(meal_plan.keys()) or ["(none)"]
            # Collect all planned dish names for context
            planned_dishes: List[str] = []
            for day_meals in meal_plan.values():
                if isinstance(day_meals, dict):
                    for dishes in day_meals.values():
                        if isinstance(dishes, list):
                            planned_dishes.extend(dishes)
            dish_hint = f"Planned dishes: {', '.join(planned_dishes)}." if planned_dishes else ""
            parts.append(
                f"\n[MEAL PLAN EXISTS]\n"
                f"Planned dates: {date_list}. {dish_hint}\n"
                "- Use plan_meal to ADD, MODIFY, or REMOVE meals from the plan.\n"
                "- Use get_cooking_steps if user asks HOW to cook or about ingredients of a planned dish.\n"
                "- Do NOT call plan_meal just because a planned dish is mentioned."
            )
        if pending_options:
            parts.append(
                "  ⚠ PENDING OPTIONS: User has not yet chosen between meal plan options. "
                "Call plan_meal if user selects or comments on an option."
            )

        # Frontend context signals
        if context:
            ctx_type = context.get("type", "")
            if ctx_type == "plan_ahead":
                parts.append(
                    "\n[CONTEXT: Meal plan data attached. Use plan_meal for any modifications.]"
                )

        return "\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Gemini helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _build_contents(
        self, history: List[Dict], user_input: str
    ) -> List[Dict[str, Any]]:
        contents: List[Dict[str, Any]] = []
        for msg in history:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": user_input}]})
        return contents

    async def _call_gemini_with_tools(
        self,
        user_input: str,
        history: List[Dict],
        system_instruction: str,
    ) -> Dict[str, Any]:
        """
        Gemini call #1: tool selection.
        Returns {"function_calls": [...], "text": str}.
        """
        payload = {
            "contents": self._build_contents(history, user_input),
            "tools": GEMINI_TOOLS,
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.0},
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    self.api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            candidate = (data.get("candidates") or [{}])[0]
            parts = candidate.get("content", {}).get("parts") or []
            function_calls: List[Dict] = []
            text_parts: List[str] = []
            for part in parts:
                if "functionCall" in part:
                    function_calls.append(
                        {
                            "name": part["functionCall"].get("name", ""),
                            "args": part["functionCall"].get("args") or {},
                        }
                    )
                elif "text" in part and not part.get("thought"):
                    text_parts.append(part["text"])

            tokens = data.get("usageMetadata", {}).get("candidatesTokenCount", 0)
            return {"function_calls": function_calls, "text": "".join(text_parts), "tokens": tokens}

        except Exception as exc:
            logger.error("[TOOL-USE] Tool selection call failed: %s", exc, exc_info=True)
            return {"function_calls": [], "text": "", "tokens": 0}

    async def _generate_final_response(
        self,
        user_input: str,
        history: List[Dict],
        system_instruction: str,
        function_call: Dict,
        tool_result_text: str,
    ) -> str:
        """
        Gemini call #2: generate natural language reply from tool result.
        """
        contents = self._build_contents(history, user_input)
        # Append the model's tool-selection turn
        contents.append(
            {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "name": function_call["name"],
                            "args": function_call.get("args") or {},
                        }
                    }
                ],
            }
        )
        # Append the tool result (role="user" per Gemini spec)
        contents.append(
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": function_call["name"],
                            "response": {"result": tool_result_text},
                        }
                    }
                ],
            }
        )
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    self.api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            candidate = (data.get("candidates") or [{}])[0]
            parts = candidate.get("content", {}).get("parts") or []
            tokens = data.get("usageMetadata", {}).get("candidatesTokenCount", 0)
            return "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought")), tokens

        except Exception as exc:
            logger.error(
                "[TOOL-USE] Final response generation failed: %s", exc, exc_info=True
            )
            return "抱歉，我暂时无法生成回复，请稍后重试。", 0

    async def _plain_generate(
        self,
        user_input: str,
        history: List[Dict],
        system_instruction: str,
    ) -> str:
        """Plain generation without tools (fallback for GENERAL intent)."""
        payload = {
            "contents": self._build_contents(history, user_input),
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    self.api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            candidate = (data.get("candidates") or [{}])[0]
            parts = candidate.get("content", {}).get("parts") or []
            tokens = data.get("usageMetadata", {}).get("candidatesTokenCount", 0)
            return "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought")), tokens

        except Exception as exc:
            logger.error("[TOOL-USE] Plain generate failed: %s", exc, exc_info=True)
            return "抱歉，我暂时无法回答这个问题。", 0

    # ─────────────────────────────────────────────────────────────────────────
    # Correction mode (frontend signal, not tool-based)
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_correction(
        self,
        user_input: str,
        owner_id: int,
        history: List[Dict],
        context: Dict,
        language_instruction: str,
    ) -> Dict[str, Any]:
        from app.pipelines.correction import correction_pipeline

        items = context.get("data", [])
        corrected: Dict[str, Any] = {}
        try:
            corrected = await correction_pipeline.run(
                user_input=user_input,
                items=items,
                owner_id=owner_id,
            )
        except Exception as exc:
            logger.error("[CORRECTION] Pipeline failed: %s", exc, exc_info=True)
            corrected = {"corrected_items": items, "response": "修正时遇到错误，请重试。"}

        correction_ctx = (
            "\n=== CORRECTION MODE ===\n"
            "User is reviewing a list. Corrected items:\n"
            + json.dumps(corrected.get("corrected_items") or items, ensure_ascii=False)
            + "\nSummarize the corrections made and confirm with the user."
        )
        system_instruction = (
            f"You are a helpful Home AI Agent. {language_instruction}\n{correction_ctx}"
        )
        payload = {
            "contents": self._build_contents(history, user_input),
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.5, "maxOutputTokens": 2048},
        }
        response_text = "修正已完成。"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    self.api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            candidate = (data.get("candidates") or [{}])[0]
            parts = candidate.get("content", {}).get("parts") or []
            response_text = (
                "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
                or response_text
            )
        except Exception as exc:
            logger.error("[CORRECTION] Final LLM failed: %s", exc, exc_info=True)

        return {
            "response": response_text,
            "intent": "CORRECTION_MODE",
            "confidence": 1.0,
            "reasoning": "Correction mode (frontend signal)",
            "action": "APPLY_CORRECTION",
            "action_data": corrected,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _log_response(action: str, action_data: Dict, response_text: str) -> None:
        logger.info(
            "[chat] RESPONSE action=%s action_data_keys=%s",
            action,
            list((action_data or {}).keys()),
        )
        logger.info("[chat] === AI RESPONSE START ===")
        for line in (response_text or "").splitlines():
            logger.info("[chat] %s", line)
        logger.info("[chat] === AI RESPONSE END ===")


# Module-level singleton (imported by router as `chat.chat_pipeline`)
chat_pipeline = ChatPipeline()
