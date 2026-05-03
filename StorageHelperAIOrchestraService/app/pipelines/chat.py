"""
Chat pipeline — Tool-Use architecture.  Agent = Model + Harness.

Flow per request:
  1. [Special case] correction mode  → correction_pipeline (frontend signal)
  2. Build state context             → system prompt with current session state
  3. Harness Guide (feedforward)     → deterministic routing rules run BEFORE LLM
     - If a guide matches → skip LLM tool-selection, go straight to Step 4
  4. Gemini call #1 (tool selection) → LLM picks which skill to invoke
  5. Harness Sensor (feedback)       → validate LLM response, detect anomalies
  6. Execute skill via ToolExecutor  → calls existing agent / pipeline backend
  7a. Direct-response tools          → return result immediately (no extra LLM call)
  7b. Data tools                     → Gemini call #2 generates natural language reply

Harness module (harness.py) owns all routing guards and response validators.
New edge cases go there as guide rules or sensor checks — not as prompt tweaks.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.modules.plan_ahead_state import get_plan_state, update_plan_state
from app.pipelines.harness import routing_guide, response_sensor
from app.skills.tool_registry import GEMINI_TOOLS, TOOL_TO_ACTION, tool_executor

logger = logging.getLogger(__name__)


class ChatPipeline:
    """Tool-Use chat pipeline."""

    # System prompt for the tool-selection call.
    # Kept short and directive; state context is injected at runtime.
    TOOL_SELECTION_PROMPT = """\
You are a Home AI Agent named "TPCrabcake".
You assist with kitchen inventory, meal planning, document organization, and scheduling.

{state_context}

LANGUAGE: {language_instruction}

## Decision rule — follow exactly:
1. Read the user's message and the state context above.
2. If one of the available tools clearly matches the user's intent → call that tool.
   - Do NOT output any text when calling a tool. No preamble, no "please wait", no acknowledgement.
   - Your entire response must be ONLY the function call.
3. If no tool matches (e.g. casual chat, follow-up question, clarification) → respond directly with text.
   - Do NOT call any tool in this case.
   - Do NOT write <tool_code> blocks, code snippets, or function calls in your text reply.

These two paths are mutually exclusive: either a tool call OR a text reply — never both.\
"""

    # System prompt for the final response generation call.
    RESPONSE_PROMPT = """\
You are a helpful Home AI Agent named "TPCrabcake".
{state_context}
LANGUAGE: {language_instruction}

A tool was called and its result is provided in the conversation. \
Generate a clear, natural response for the user based on the tool result.

IMPORTANT: Output plain text or markdown only. \
Do NOT write any tool calls, code blocks, <tool_code> tags, \
or function invocations — those are internal and must never appear in your reply.\
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
        on_thinking_step: Optional[Callable[[str], None]] = None,
        on_text_chunk: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Main chat entry point.  Returns a dict compatible with ChatResponse:
          {response, intent, confidence, reasoning, action, action_data, thinking}

        thinking accumulates pipeline steps so the user can inspect the full
        decision trace: state check → guide routing → LLM selection → tool execution.
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

        # ── Hydrate plan dates from DB (query-aware date range) ──────────────
        # Always runs so the LLM knows which dates have confirmed DB plans, even
        # while an active planning session (meal_plan / phase) is in progress.
        # Full dish details are NOT loaded here — view_schedule loads them on demand.
        from app.agents.scheduling_agent import (
            ScheduleRangeDecider,
            SchedulingResponseGenerator,
            ScheduleSessionContext,
        )
        from app.storage.pipeline_storage import PipelineStorage, _default_storage

        _session_ctx = ScheduleSessionContext(
            current_query=user_input,
            history=history,
            user_timezone=user_timezone,
        )
        _decider = ScheduleRangeDecider()
        _time_range = await _decider.decide(_session_ctx, api_url=self.api_url)
        if _time_range is None:
            _time_range = _decider._default_range(user_timezone)

        _resp_gen = SchedulingResponseGenerator()
        _schedules = await _resp_gen.fetch_schedules_in_range(
            owner_id, _time_range, _default_storage
        )

        _plan_dates: List[str] = []
        for _s in _schedules:
            _, _, _, _slots = PipelineStorage._extract_meal_plan_from_schedule(_s)
            for _d in _slots:
                if _d not in _plan_dates:
                    _plan_dates.append(_d)
        _plan_dates = sorted(_plan_dates)

        if _plan_dates:
            plan_state = {**plan_state, "plan_dates": _plan_dates}
            # Clear stale pending_options only when NOT in an active planning session,
            # to avoid disrupting mid-flow confirmation steps.
            if not plan_state.get("meal_plan") and not plan_state.get("phase"):
                if plan_state.get("pending_options"):
                    update_plan_state(owner_id, pending_options=None)
                    plan_state = {**plan_state, "pending_options": None}

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

        # ── Pipeline trace — accumulated throughout and returned as thinking ─
        thinking_steps: List[str] = []

        def _emit(step: str) -> None:
            if step:
                thinking_steps.append(step)
                if on_thinking_step:
                    on_thinking_step(step)

        def _finish_thinking(extra: Optional[str] = None) -> str:
            parts = [s for s in thinking_steps if s]
            if extra:
                parts.append(extra)
            return "\n\n".join(parts)

        _emit(self._describe_state(plan_state, cooking_context, lang=language))

        # ── Harness: Guide (feedforward) ──────────────────────────────────
        # Deterministic routing rules run before the LLM.  Any rule match
        # skips the LLM call entirely and goes straight to tool execution.
        guide_result = routing_guide.evaluate(user_input, plan_state)
        if guide_result:
            label = self._tool_label(guide_result.tool, language)
            _emit(self._t("guide_triggered", language, label=label, reason=guide_result.reason))
            _emit(self._build_tool_thinking(guide_result.tool, guide_result.args, lang=language))
            tool_result = await tool_executor.execute(
                guide_result.tool, guide_result.args, owner_id,
                user_input=user_input, history=history, context=context,
                user_timezone=user_timezone, cooking_level=cooking_level,
                language=language, cooking_context=cooking_context,
                plan_state=plan_state, api_url=self.api_url,
            )
            result = tool_result if tool_result.get("_direct_response") else tool_result
            if "response" not in result:
                result = {
                    "response": result.get("tool_result", ""),
                    "intent": guide_result.tool.upper(),
                    "confidence": 0.95,
                    "reasoning": f"[Guide] {guide_result.reason}",
                    "action": result.get("action", guide_result.tool.upper()),
                    "action_data": result.get("action_data", {}),
                }
            clean, extracted = self._extract_thinking(result.get("response", ""))
            result["response"] = clean
            result["thinking"] = _finish_thinking(extracted)
            self._log_response(result.get("action", ""), result.get("action_data", {}), clean)
            return result

        # ── Step 1: Tool selection (Gemini call #1) ────────────────────────
        _emit(self._t("analyzing", language))
        selection = await self._call_gemini_with_tools(
            user_input=user_input,
            history=history,
            system_instruction=tool_system,
        )
        function_calls = selection.get("function_calls") or []
        _total_tokens = selection.get("tokens", 0)
        clarification_hint = selection.get("clarification_hint")

        # ── Retry with forced tool call when AUTO mode returned empty ──────────
        # When Gemini is indecisive (no text, no function call), retry with
        # mode=ANY to force a tool selection instead of falling through to a
        # plain "please wait" response that never delivers.
        if not function_calls and not selection.get("text") and not clarification_hint:
            logger.info("[HARNESS] empty_response — retrying with mode=ANY (forced tool call)")
            _emit(self._t("retrying", language))
            _retry = await self._call_gemini_force_tool(
                user_input=user_input,
                history=history,
                system_instruction=tool_system,
            )
            _total_tokens += _retry.get("tokens", 0)
            if _retry.get("function_calls"):
                function_calls = _retry["function_calls"]
                selection = _retry
                clarification_hint = _retry.get("clarification_hint")
                logger.info("[HARNESS] Retry succeeded: tool=%s", function_calls[0].get("name"))
            else:
                logger.info("[HARNESS] Retry also returned no tool — falling through to plain generate")

        # ── Harness: Sensor flagged a user-facing anomaly → ask for clarification ─
        # When the sensor cannot auto-correct (unknown_tool, missing_args,
        # empty_response), the user's intent was unclear.  Instead of silently
        # failing, generate a clarifying question so the user knows what to do next.
        if clarification_hint:
            logger.info("[HARNESS sensor] Generating clarification for anomaly.")
            _emit(self._t("sensor_anomaly", language, hint=clarification_hint))
            clarify_system = (
                f"You are a helpful Home AI Agent named 'TPCrabcake'. "
                f"{language_instruction}\n\n"
                f"Context: {clarification_hint}\n"
                f"User said: {user_input!r}\n\n"
                f"Generate a single, short, polite clarifying question in the user's language. "
                f"Do not make assumptions about what they want. Do not apologise excessively."
            )
            clarify_text, _clarify_tokens = await self._plain_generate(
                user_input=user_input,
                history=history,
                system_instruction=clarify_system,
            )
            _total_tokens += _clarify_tokens
            logger.info("[HARNESS sensor] Clarification generated.")
            return {
                "response": clarify_text,
                "intent": "CLARIFICATION",
                "confidence": 0.50,
                "reasoning": f"Sensor anomaly — clarification requested: {clarification_hint}",
                "action": "GENERAL",
                "action_data": {},
                "_tokens": _total_tokens,
                "thinking": _finish_thinking(),
            }

        # ── No tool selected → direct LLM response ────────────────────────
        if not function_calls:
            response_text = selection.get("text") or ""
            if not response_text:
                # Fallback: make a plain generation call so user always gets a reply
                if on_text_chunk:
                    response_text, _plain_tokens = await self._plain_generate_streaming(
                        user_input=user_input,
                        history=history,
                        system_instruction=resp_system,
                        on_text_chunk=on_text_chunk,
                    )
                else:
                    response_text, _plain_tokens = await self._plain_generate(
                        user_input=user_input,
                        history=history,
                        system_instruction=resp_system,
                    )
                _total_tokens += _plain_tokens
            logger.info("[TOOL-USE] No tool selected; direct response.")
            _emit(self._t("direct_reply", language))
            clean, extracted = self._extract_thinking(response_text)
            r: Dict[str, Any] = {
                "response": clean,
                "intent": "GENERAL",
                "confidence": 0.80,
                "reasoning": "No tool required; responded directly.",
                "action": "GENERAL",
                "action_data": {},
                "_tokens": _total_tokens,
                "thinking": _finish_thinking(extracted),
            }
            return r

        # ── Step 2: Execute the first selected tool ────────────────────────
        fc = function_calls[0]
        fn_name: str = fc.get("name", "")
        fn_args: Dict[str, Any] = fc.get("args") or {}

        logger.info("[TOOL-USE] Tool selected: %s  args=%s", fn_name, fn_args)

        label = self._tool_label(fn_name, language)
        _emit(self._t("tool_selected", language, label=label, name=fn_name))
        _emit(self._build_tool_thinking(fn_name, fn_args, lang=language))

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
            clean, extracted = self._extract_thinking(result.get("response", ""))
            result["response"] = clean
            result["thinking"] = _finish_thinking(extracted)
            self._log_response(result.get("action", ""), result.get("action_data", {}), clean)
            return result

        # ── Step 3: Generate final natural language reply (Gemini call #2) ─
        _emit(self._t("generating", language))
        if on_text_chunk:
            response_text, _final_tokens = await self._generate_final_response_streaming(
                user_input=user_input,
                history=history,
                system_instruction=resp_system,
                function_call=fc,
                tool_result_text=tool_result.get("tool_result", ""),
                on_text_chunk=on_text_chunk,
            )
            # Fallback: if streaming returned empty, retry with non-streaming
            if not response_text.strip():
                logger.warning("[TOOL-USE] Streaming returned empty; falling back to non-streaming")
                response_text, _final_tokens = await self._generate_final_response(
                    user_input=user_input,
                    history=history,
                    system_instruction=resp_system,
                    function_call=fc,
                    tool_result_text=tool_result.get("tool_result", ""),
                )
                if response_text.strip():
                    on_text_chunk(response_text)
        else:
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
        clean, extracted = self._extract_thinking(response_text)

        result = {
            "response": clean,
            "intent": fn_name,
            "confidence": 0.95,
            "reasoning": f"Tool-use: {fn_name}",
            "action": final_action,
            "action_data": final_action_data,
            "_tokens": _total_tokens,
            "thinking": _finish_thinking(extracted),
        }
        self._log_response(final_action, final_action_data, clean)
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
        plan_dates: List[str] = plan_state.get("plan_dates") or []
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
                "- Call plan_meal when user wants to CREATE a new plan, ADD dishes, MODIFY or REMOVE meals.\n"
                "- Call plan_meal when user asks to PLAN meals — even if a plan already exists.\n"
                "- Use view_schedule when user asks WHAT is planned, 'what to eat', or wants to SEE the plan.\n"
                "- Use get_cooking_steps if user asks HOW to cook or about ingredients of a planned dish.\n"
                "- Do NOT call plan_meal just because a planned dish is mentioned in passing."
            )
        elif plan_dates:
            # Lightweight hydration: only date list available, dish details are in the DB.
            parts.append(
                f"\n[MEAL PLAN EXISTS — dates: {', '.join(plan_dates)}]\n"
                "Dish details are stored in the database and NOT loaded yet.\n"
                "- Call view_schedule when user asks WHAT is planned, 'what to eat tonight/today/this week'.\n"
                "- Call plan_meal only when user explicitly wants to CREATE, ADD, or CHANGE meals.\n"
                "- Do NOT invent or assume dish names — use view_schedule to retrieve them."
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
            tokens = data.get("usageMetadata", {}).get("candidatesTokenCount", 0)

            # ── Harness: Sensor (feedback) ─────────────────────────────────
            # Validates the LLM response, auto-corrects anomalies, and logs
            # anything surprising so it can inform a future guide rule.
            sensor = response_sensor.inspect(parts, user_input, tokens)
            return {
                "function_calls": sensor.function_calls,
                "text": sensor.text,
                "tokens": tokens,
                "clarification_hint": sensor.clarification_hint,
            }

        except Exception as exc:
            logger.error("[TOOL-USE] Tool selection call failed: %s", exc, exc_info=True)
            return {"function_calls": [], "text": "", "tokens": 0}

    async def _call_gemini_force_tool(
        self,
        user_input: str,
        history: List[Dict],
        system_instruction: str,
    ) -> Dict[str, Any]:
        """
        Gemini retry with mode=ANY — forces the model to select a tool.

        Called only when the initial AUTO call returned empty (no text, no
        function call).  ANY mode prevents Gemini from giving an empty
        response, making it commit to a tool even when it was previously
        indecisive.  The sensor still validates the result.
        """
        payload = {
            "contents": self._build_contents(history, user_input),
            "tools": GEMINI_TOOLS,
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
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
            tokens = data.get("usageMetadata", {}).get("candidatesTokenCount", 0)

            sensor = response_sensor.inspect(parts, user_input, tokens)
            return {
                "function_calls": sensor.function_calls,
                "text": sensor.text,
                "tokens": tokens,
                "clarification_hint": sensor.clarification_hint,
            }
        except Exception as exc:
            logger.error("[TOOL-USE] Force-tool retry failed: %s", exc, exc_info=True)
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
            "toolConfig": {"functionCallingConfig": {"mode": "NONE"}},
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
    # Streaming Gemini helpers (character-by-character output)
    # ─────────────────────────────────────────────────────────────────────────

    def _stream_url(self) -> str:
        return self.api_url.replace(":generateContent", ":streamGenerateContent")

    async def _consume_gemini_stream(
        self,
        payload: Dict[str, Any],
        on_text_chunk: Callable[[str], None],
        timeout: float = 30.0,
    ) -> Tuple[str, int]:
        """
        Call streamGenerateContent and invoke on_text_chunk for every text fragment.
        Returns (full_text, total_tokens).

        Gemini streams a JSON array delivered incrementally; each element is a
        GenerateContentResponse.  We parse line-by-line, stripping array delimiters.
        """
        full_text = ""
        tokens = 0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    self._stream_url(),
                    headers={"Content-Type": "application/json"},
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line or line in ("[", "]"):
                            continue
                        if line.startswith(","):
                            line = line[1:].strip()
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        candidate = (chunk.get("candidates") or [{}])[0]
                        finish_reason = candidate.get("finishReason")
                        parts = candidate.get("content", {}).get("parts") or []
                        has_text = any("text" in p and not p.get("thought") for p in parts)
                        has_fn = any("functionCall" in p for p in parts)
                        if not has_text:
                            logger.info(
                                "[STREAM] chunk no text: finishReason=%s has_fn=%s parts=%s",
                                finish_reason, has_fn, [list(p.keys()) for p in parts],
                            )
                        for part in parts:
                            if "text" in part and not part.get("thought"):
                                text = part["text"]
                                full_text += text
                                on_text_chunk(text)
                        tokens = chunk.get("usageMetadata", {}).get(
                            "candidatesTokenCount", tokens
                        )
        except Exception as exc:
            logger.error("[TOOL-USE] Gemini stream failed: %s", exc, exc_info=True)
            if not full_text:
                fallback = "抱歉，生成回复时出现问题，请重试。"
                on_text_chunk(fallback)
                full_text = fallback
        return full_text, tokens

    async def _plain_generate_streaming(
        self,
        user_input: str,
        history: List[Dict],
        system_instruction: str,
        on_text_chunk: Callable[[str], None],
    ) -> Tuple[str, int]:
        payload = {
            "contents": self._build_contents(history, user_input),
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
        }
        return await self._consume_gemini_stream(payload, on_text_chunk, timeout=25.0)

    async def _generate_final_response_streaming(
        self,
        user_input: str,
        history: List[Dict],
        system_instruction: str,
        function_call: Dict,
        tool_result_text: str,
        on_text_chunk: Callable[[str], None],
    ) -> Tuple[str, int]:
        contents = self._build_contents(history, user_input)
        contents.append({
            "role": "model",
            "parts": [{"functionCall": {
                "name": function_call["name"],
                "args": function_call.get("args") or {},
            }}],
        })
        contents.append({
            "role": "user",
            "parts": [{"functionResponse": {
                "name": function_call["name"],
                "response": {"result": tool_result_text},
            }}],
        })
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
            # Explicitly disable tool calls so Gemini outputs text, not a function call.
            # Without this, the streaming endpoint can return an empty candidates array
            # when it encounters a functionResponse in the history but no tools are declared.
            "toolConfig": {"functionCallingConfig": {"mode": "NONE"}},
        }
        return await self._consume_gemini_stream(payload, on_text_chunk, timeout=30.0)

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

    # ─────────────────────────────────────────────────────────────────────────
    # Thinking builder + extractor
    # ─────────────────────────────────────────────────────────────────────────

    # ── i18n strings for the thinking trace ──────────────────────────────────
    # Fallback language is "en".

    _TOOL_LABELS_I18N: Dict[str, Dict[str, str]] = {
        "zh": {
            "plan_meal":                "规划餐食",
            "view_schedule":            "查询日程",
            "search_items":             "搜索库存",
            "update_item":              "更新库存",
            "get_cooking_steps":        "获取烹饪步骤",
            "modify_recipe_ingredient": "修改食材用量",
            "plan_eat_out":             "规划外出就餐",
            "manage_schedule":          "管理日程",
        },
        "en": {
            "plan_meal":                "Plan meal",
            "view_schedule":            "View schedule",
            "search_items":             "Search inventory",
            "update_item":              "Update item",
            "get_cooking_steps":        "Get cooking steps",
            "modify_recipe_ingredient": "Modify ingredient",
            "plan_eat_out":             "Plan eat out",
            "manage_schedule":          "Manage schedule",
        },
        "ja": {
            "plan_meal":                "食事プラン",
            "view_schedule":            "スケジュール確認",
            "search_items":             "在庫検索",
            "update_item":              "アイテム更新",
            "get_cooking_steps":        "調理手順取得",
            "modify_recipe_ingredient": "食材変更",
            "plan_eat_out":             "外食プラン",
            "manage_schedule":          "スケジュール管理",
        },
    }

    _THINKING_I18N: Dict[str, Dict[str, str]] = {
        "state_header":      {"zh": "📊 Current state:",          "en": "📊 Current state:",          "ja": "📊 現在の状態:"},
        "no_meal_plan":      {"zh": "   · No meal plan",                          "en": "   · No meal plan",                          "ja": "   · 食事プランなし"},
        "meal_plan_n_days":  {"zh": "   · Meal plan ({n} days, loaded)",          "en": "   · Meal plan ({n} days, loaded)",          "ja": "   · 食事プランあり ({n} 日, 読込済)"},
        "plan_dates_exist":  {"zh": "   · Plan dates ({n}): {dates}",             "en": "   · Plan dates ({n}): {dates}",             "ja": "   · プラン日程 ({n} 日): {dates}"},
        "cooking_session":   {"zh": "   · Cooking: {dish}",       "en": "   · Cooking: {dish}",       "ja": "   · 料理中: {dish}"},
        "planning_phase":    {"zh": "   · Phase: {phase}",        "en": "   · Phase: {phase}",        "ja": "   · フェーズ: {phase}"},
        "awaiting_confirm":  {"zh": "   · Awaiting confirmation", "en": "   · Awaiting confirmation", "ja": "   · 確認待ち"},
        "guide_triggered":   {
            "zh": "🧭 Routing rule matched → {label}\n   Reason: {reason}",
            "en": "🧭 Routing rule matched → {label}\n   Reason: {reason}",
            "ja": "🧭 ルーティング規則 → {label}\n   理由: {reason}",
        },
        "tool_call_header":  {"zh": "🔧 Calling tool: {label}",   "en": "🔧 Calling tool: {label}",   "ja": "🔧 ツール呼び出し: {label}"},
        "analyzing":         {"zh": "🤖 Analyzing intent...",      "en": "🤖 Analyzing intent...",      "ja": "🤖 意図を分析中..."},
        "retrying":          {"zh": "⚠️ Ambiguous intent, retrying...", "en": "⚠️ Ambiguous intent, retrying...", "ja": "⚠️ 意図不明確、再試行中..."},
        "sensor_anomaly":    {"zh": "⚠️ Sensor anomaly: {hint}",  "en": "⚠️ Sensor anomaly: {hint}",  "ja": "⚠️ センサー異常: {hint}"},
        "direct_reply":      {"zh": "💬 No tool → direct reply",   "en": "💬 No tool → direct reply",   "ja": "💬 ツール不要 → 直接返答"},
        "tool_selected":     {
            "zh": "🤖 Tool selected: {label} ({name})",
            "en": "🤖 Tool selected: {label} ({name})",
            "ja": "🤖 ツール選択: {label} ({name})",
        },
        "generating":        {"zh": "📝 Generating response...",   "en": "📝 Generating response...",   "ja": "📝 応答を生成中..."},
    }

    @classmethod
    def _t(cls, key: str, lang: str, **kwargs: Any) -> str:
        """Look up a thinking-trace string and format it."""
        lang_map = cls._THINKING_I18N.get(key, {})
        template = lang_map.get(lang) or lang_map.get("en", key)
        return template.format(**kwargs) if kwargs else template

    @classmethod
    def _tool_label(cls, tool_name: str, lang: str) -> str:
        return cls._TOOL_LABELS_I18N.get(lang, cls._TOOL_LABELS_I18N["en"]).get(tool_name, tool_name)

    @classmethod
    def _describe_state(
        cls,
        plan_state: Dict[str, Any],
        cooking_context: Optional[Dict] = None,
        lang: str = "en",
    ) -> str:
        parts: List[str] = [cls._t("state_header", lang)]
        meal_plan = plan_state.get("meal_plan") or {}
        plan_dates: List[str] = plan_state.get("plan_dates") or []
        if meal_plan:
            parts.append(cls._t("meal_plan_n_days", lang, n=len(meal_plan)))
        elif plan_dates:
            parts.append(cls._t("plan_dates_exist", lang, n=len(plan_dates), dates=", ".join(plan_dates)))
        else:
            parts.append(cls._t("no_meal_plan", lang))
        if cooking_context and cooking_context.get("dish_name"):
            parts.append(cls._t("cooking_session", lang, dish=cooking_context["dish_name"]))
        phase = plan_state.get("phase")
        if phase:
            parts.append(cls._t("planning_phase", lang, phase=phase))
        if plan_state.get("pending_planning_queue") and plan_state.get("last_pipeline_action") == "ask_confirm_dates":
            parts.append(cls._t("awaiting_confirm", lang))
        return "\n".join(parts)

    @classmethod
    def _build_tool_thinking(cls, tool_name: str, tool_args: Dict[str, Any], lang: str = "en") -> str:
        label = cls._tool_label(tool_name, lang)
        lines = [cls._t("tool_call_header", lang, label=label)]
        for k, v in tool_args.items():
            if v is not None and v != "":
                lines.append(f"   · {k}: {v}")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Thinking extractor
    # ─────────────────────────────────────────────────────────────────────────

    # Matches literal <tool_code>…</tool_code> blocks that some LLMs emit when
    # they describe tool invocations in plain text instead of using function-call
    # protocol.  These blocks are never meaningful to the user.
    _TOOL_CODE_RE = re.compile(r'<tool_code>.*?</tool_code>', re.DOTALL | re.IGNORECASE)

    # Lines that are clearly progress indicators ("正在…", "I'm generating…").
    # Captured only when they immediately precede a tool_code block or are the
    # sole content of the text (i.e. no real answer follows).
    _PROGRESS_LINE_RE = re.compile(
        r'^[\s\S]*?(正在[^\n]*\.{2,}|[Gg]enerating[^\n]*\.{2,}|[Pp]lease wait[^\n]*\.{2,})\n?',
        re.MULTILINE,
    )

    @staticmethod
    def _extract_thinking(text: str) -> Tuple[str, Optional[str]]:
        """
        Separate thinking/intermediate content from the user-facing answer.

        Returns (clean_response, thinking_or_None).
        thinking is non-None only when the original text contained artefacts
        that should not be shown as the main answer.
        """
        if not text:
            return text, None

        thinking_parts: List[str] = []

        # 1. Extract <tool_code> blocks — always intermediate artefacts.
        tool_code_blocks = ChatPipeline._TOOL_CODE_RE.findall(text)
        if tool_code_blocks:
            thinking_parts.extend(tool_code_blocks)
            text = ChatPipeline._TOOL_CODE_RE.sub('', text).strip()

        # 2. If tool_code blocks were found, also extract any progress-indicator
        #    lines that preceded them (now orphaned after block removal).
        if tool_code_blocks:
            progress = ChatPipeline._PROGRESS_LINE_RE.match(text)
            if progress:
                thinking_parts.insert(0, progress.group(0).strip())
                text = text[progress.end():].strip()

        thinking = '\n\n'.join(thinking_parts) if thinking_parts else None
        return text, thinking

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
