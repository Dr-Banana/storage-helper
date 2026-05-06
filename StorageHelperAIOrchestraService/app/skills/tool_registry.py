"""
Gemini Function Calling tool registry and executor.

Replaces the intent classifier + agent factory + manual routing in chat.py.
Each tool declaration maps to an existing agent/pipeline backend, keeping all
existing business logic intact while letting the LLM decide what to invoke.

Structural routing constraints
-------------------------------
Each declaration may carry an optional ``"routing"`` key — machine-readable
rules that the Harness RoutingGuide evaluates deterministically before the LLM
call.  This is the single source of truth for routing logic; adding or changing
a rule here automatically updates both runtime behaviour and the test suite.

Schema for ``routing``:
  {
    "force_when": [          # list of conditions; first match wins
      {
        "state_key": str,    # plan_state key that must be truthy
        "also_requires": {   # optional: additional state key/value checks
          "<key>": "<value>"
        },
        "args": {            # tool args to pass; "$user_input" is substituted
          "<arg>": "<value or $user_input>"
        },
        "reason": str        # logged and surfaced to the caller
      }
    ]
  }
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Function Declarations (Gemini Function Calling format) ───────────────────

TOOL_DECLARATIONS: List[Dict[str, Any]] = [
    {
        "name": "search_items",
        "description": (
            "Search the user's stored inventory, documents, or receipts. "
            "Use when the user wants to find or look up something that already exists in the system."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "update_item",
        "description": (
            "Update or modify an existing item in the stored inventory or documents "
            "(e.g. change quantity, expiry date, category). "
            "NOT for meal plan changes — use plan_meal for those."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language description of what to update and the new values",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "plan_meal",
        "description": (
            "Use when the user wants to CREATE, PLAN, or MODIFY the CONTENT of a meal schedule — "
            "recommending dishes, adding items, removing or swapping dishes within a plan, or building a new plan. "
            "For simply reading what is already scheduled, use view_schedule instead. "
            "For CANCELLING or DELETING an existing plan/event, use manage_schedule instead. "
            "NOT for questions about how to cook a dish or what ingredients it needs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_request": {
                    "type": "string",
                    "description": "The user's meal planning request, verbatim",
                },
            },
            "required": ["user_request"],
        },
        "routing": {
            "force_when": [
                {
                    "state_key": "pending_planning_queue",
                    "also_requires": {"last_pipeline_action": "ask_confirm_dates"},
                    "args": {"user_request": "$user_input"},
                    "reason": "User is responding to a date-confirmation prompt — any reply goes to plan_meal.",
                },
                {
                    "state_key": "pending_options",
                    "args": {"user_request": "$user_input"},
                    "reason": "User is choosing between pending meal plan options — always route to plan_meal.",
                },
                {
                    "state_key": "meal_planning_queue",
                    "args": {"user_request": "$user_input"},
                    "reason": "Active day-by-day planning queue in progress — continue via plan_meal.",
                },
            ],
            "hint_when": [
                {
                    "state_key": "pending_planning_queue",
                    "also_requires": {"last_pipeline_action": "ask_confirm_dates"},
                    "context_hint": (
                        "\n[MEAL DATE CONFIRMATION PENDING]\n"
                        "The user is responding to a date/meal confirmation prompt.\n"
                        "ALWAYS call plan_meal — even for very short replies like "
                        "'好的', '确认', '对', 'yes', 'ok', '可以', '取消', 'cancel'."
                    ),
                    "context_primary": True,
                },
                {
                    "state_key": "pending_options",
                    "context_hint": (
                        "  ⚠ PENDING OPTIONS: User has not yet chosen between meal plan options. "
                        "Call plan_meal if user selects or comments on an option."
                    ),
                },
                {
                    "state_key": "meal_planning_queue",
                    "context_hint": (
                        "  ⚠ ACTIVE PLANNING QUEUE: A day-by-day meal planning session is in progress. "
                        "Call plan_meal to continue planning (swaps, confirmations, skips). "
                        "Use view_schedule if the user is asking about existing plans."
                    ),
                },
            ],
        },
    },
    {
        "name": "get_cooking_steps",
        "description": (
            "Answer HOW to cook a dish: fetch recipe steps, ingredients, techniques, or substitutes. "
            "Use when the user asks about cooking a specific dish — including ingredient questions "
            "('口水鸡能用老母鸡么', '需要哪些食材', '怎么做', '步骤是什么'). "
            "Also use when saving/replacing steps in the meal plan. "
            "For follow-up questions about a recipe already in context, respond directly without this tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dish_name": {
                    "type": "string",
                    "description": "Name of the dish",
                },
                "save_to_plan": {
                    "type": "boolean",
                    "description": "True if the user wants to save/add/replace these steps in their meal plan",
                },
            },
            "required": ["dish_name"],
        },
    },
    {
        "name": "modify_recipe_ingredient",
        "description": (
            "Change the quantity or amount of a specific ingredient in an already-generated recipe. "
            "Use ONLY when there is an active cooking session and the user says things like "
            "'多放点酱油', '少放盐', 'more garlic', 'reduce the oil', 'increase soy sauce'. "
            "Requires an active cooking context — if no recipe is currently being discussed, "
            "respond directly instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dish_name": {
                    "type": "string",
                    "description": "Name of the dish from the active cooking session",
                },
                "change_request": {
                    "type": "string",
                    "description": "What the user wants to change (e.g. 'more soy sauce', 'less salt in step 3')",
                },
            },
            "required": ["dish_name", "change_request"],
        },
    },
    {
        "name": "plan_eat_out",
        "description": (
            "Help plan eating out: restaurant recommendations, reservations, "
            "or checking restaurant information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user's eat-out request"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "manage_schedule",
        "description": (
            "Add, delete, or modify any scheduled event — including calendar meetings, appointments, "
            "and meal plans. Use when the user wants to CANCEL, DELETE, or RESCHEDULE something. "
            "For creating or modifying the content of a meal plan (choosing dishes), use plan_meal instead. "
            "Extract start_time and end_time directly from the user's message — "
            "e.g. '明天下午2点到4点的会议' → start_time='YYYY-MM-DDT14:00:00', end_time='YYYY-MM-DDT16:00:00'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add", "delete", "modify"],
                    "description": "The operation to perform",
                },
                "title": {"type": "string", "description": "Event title"},
                "start_time": {
                    "type": "string",
                    "description": "Start datetime YYYY-MM-DDTHH:MM:SS",
                },
                "end_time": {
                    "type": "string",
                    "description": "End datetime YYYY-MM-DDTHH:MM:SS. Omit if not specified by user.",
                },
                "description": {"type": "string", "description": "Optional event description"},
                "event_type": {"type": "string", "description": "Type of event (e.g. meeting, appointment, meal_plan)"},
                "schedule_id": {
                    "type": "integer",
                    "description": "ID of schedule to delete/modify (required if known)",
                },
                "schedule_reference": {
                    "type": "string",
                    "description": (
                        "Natural language reference to an existing event when ID is unknown, "
                        "e.g. 'the meeting on Monday', 'the meal plan for today'"
                    ),
                },
                "target_date": {
                    "type": "string",
                    "description": (
                        "Target date YYYY-MM-DD for meal plan operations. "
                        "Use when cancelling or modifying a meal plan for a specific date."
                    ),
                },
                "meal_type": {
                    "type": "string",
                    "description": "Optional: 'breakfast', 'lunch', or 'dinner'. Only for meal plan operations.",
                },
            },
            "required": ["operation"],
        },
    },
    {
        "name": "view_schedule",
        "description": (
            "View the user's calendar events and meal plans for a specific date range. "
            "Use when the user wants to READ what is already scheduled — appointments, meetings, "
            "or existing meal plans. Not for creating or modifying plans."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD (inclusive)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD (exclusive — the day AFTER the last date to show)",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
]

# Gemini only accepts name/description/parameters — strip harness-internal fields.
_GEMINI_ALLOWED_KEYS = {"name", "description", "parameters"}
GEMINI_TOOLS = [
    {
        "functionDeclarations": [
            {k: v for k, v in decl.items() if k in _GEMINI_ALLOWED_KEYS}
            for decl in TOOL_DECLARATIONS
        ]
    }
]

# ─── Tool → action field mapping (backward-compat with existing frontend) ─────

TOOL_TO_ACTION: Dict[str, str] = {
    "search_items": "SEARCH",
    "update_item": "UPDATE",
    "plan_meal": "PLAN_AHEAD",
    "get_cooking_steps": "COOKING_STEPS",
    "modify_recipe_ingredient": "COOKING_STEPS",   # frontend reuses COOKING_STEPS card
    "plan_eat_out": "PLAN_EAT_OUT",
    "manage_schedule": "MANAGE_SCHEDULE",
    "view_schedule": "VIEW_SCHEDULE",
}


# ─── Tool Executor ─────────────────────────────────────────────────────────────

class ToolExecutor:
    """
    Routes a Gemini function-call to the appropriate existing agent / pipeline.

    Every handler returns a dict that always includes:
      _direct_response : bool   — True = caller returns result as-is (no 2nd LLM call)
      action           : str    — maps to ChatResponse.action
      action_data      : dict   — payload for the frontend
      tool_result      : str    — text summary passed to the 2nd LLM call (if used)
    """

    async def execute(
        self,
        function_name: str,
        args: Dict[str, Any],
        owner_id: int,
        *,
        user_input: str,
        history: List[Dict],
        context: Optional[Dict],
        user_timezone: Optional[str],
        cooking_level: str,
        language: str,
        cooking_context: Optional[Dict] = None,
        plan_state: Optional[Dict] = None,
        api_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        ambient: Dict[str, Any] = dict(
            user_input=user_input,
            history=history,
            context=context,
            user_timezone=user_timezone,
            cooking_level=cooking_level,
            language=language,
            cooking_context=cooking_context,
            plan_state=plan_state or {},
            api_url=api_url,
        )
        handlers = {
            "search_items": self._search_items,
            "update_item": self._update_item,
            "plan_meal": self._plan_meal,
            "get_cooking_steps": self._get_cooking_steps,
            "modify_recipe_ingredient": self._modify_recipe_ingredient,
            "plan_eat_out": self._plan_eat_out,
            "manage_schedule": self._manage_schedule,
            "view_schedule": self._view_schedule,
        }
        handler = handlers.get(function_name)
        if not handler:
            logger.warning("[TOOL] Unknown function: %s", function_name)
            return {
                "_direct_response": False,
                "action": "GENERAL",
                "action_data": {},
                "tool_result": f"Unknown tool: {function_name}",
            }
        try:
            return await handler(args, owner_id, **ambient)
        except Exception as exc:
            logger.error("[TOOL] %s failed: %s", function_name, exc, exc_info=True)
            return {
                "_direct_response": False,
                "action": TOOL_TO_ACTION.get(function_name, "GENERAL"),
                "action_data": {},
                "tool_result": f"Tool execution failed: {exc}",
            }

    # ── search_items ──────────────────────────────────────────────────────────

    async def _search_items(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.agents.search_agent import SearchAgent
        result = await SearchAgent().execute(
            user_input=args.get("query") or kw["user_input"],
            owner_id=owner_id,
            context=kw.get("context"),
        )
        data = result.get("data") or {}
        return {
            "_direct_response": False,
            "action": "SEARCH",
            "action_data": data,
            "tool_result": json.dumps(data, ensure_ascii=False, default=str),
        }

    # ── update_item ───────────────────────────────────────────────────────────

    async def _update_item(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.agents.update_agent import UpdateAgent
        result = await UpdateAgent().execute(
            user_input=args.get("query") or kw["user_input"],
            owner_id=owner_id,
            context=kw.get("context"),
        )
        data = result.get("data") or {}
        return {
            "_direct_response": False,
            "action": "UPDATE",
            "action_data": data,
            "tool_result": json.dumps(data, ensure_ascii=False, default=str),
        }

    # ── plan_meal ─────────────────────────────────────────────────────────────

    async def _plan_meal(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
        from app.storage.pipeline_storage import _default_storage
        from app.agents.agent_factory import agent_factory
        from app.modules.intent_classifier import IntentClassificationResult, Intent

        api_url: Optional[str] = kw.get("api_url")
        pipeline = PlanAheadPipeline(gemini_api_url=api_url)

        # Fetch user profile (including recent dishes for diversity engine)
        user_profile = await _default_storage.get_user_profile(owner_id)
        if user_profile:
            rt_recent = await _default_storage.get_recent_dishes_from_schedules(owner_id)
            user_profile["recent_dishes"] = rt_recent

        # Fetch Plan Cook Home inventory so the pipeline can suggest recipes
        inventory_msg = ""
        try:
            pch_agent = agent_factory.get_plan_cook_home_sub_agent()
            pch_result = await pch_agent.execute(
                user_input=kw["user_input"], owner_id=owner_id
            )
            pch_data = pch_result.get("data") or {}
            items = pch_data.get("inventory_items") or []
            if items:
                by_cat: Dict[str, List[str]] = {}
                for item in items[:30]:
                    cat = item.get("category", "other")
                    name = item.get("item_name") or item.get("name", "")
                    by_cat.setdefault(cat, []).append(name)
                lines = ["USER'S ACTUAL INVENTORY (Plan Cook Home):"]
                for cat, names in by_cat.items():
                    lines.append(f"  {cat}: {', '.join(names)}")
                inventory_msg = "\n".join(lines)
            else:
                inventory_msg = "USER INVENTORY: Empty (no food items found)."
        except Exception as e:
            logger.warning("[TOOL plan_meal] inventory fetch failed: %s", e)

        # Enrich context with inventory before handing to pipeline
        enriched_ctx: Dict[str, Any] = dict(kw.get("context") or {})
        if inventory_msg:
            prev = enriched_ctx.get("scheduling_context") or ""
            enriched_ctx["scheduling_context"] = (prev + "\n\n" + inventory_msg).strip()

        intent_result = IntentClassificationResult(
            intent=Intent.PLAN_AHEAD,
            confidence=0.95,
            reasoning="Routed via tool-use: plan_meal",
            compound_intents=None,
            extracted_items=None,
        )

        # BUT: when there is active planning state (pending_options, queue, draft),
        # Gemini can hallucinate dish details into user_request based on conversation history.
        # Using that hallucinated text as pipeline input causes wrong dishes to be generated.
        # In those cases, use the original user_input which is concise and accurate.
        from app.modules.plan_ahead_state import get_plan_state as _get_ps
        _cur_ps = _get_ps(owner_id)
        _has_active_state = bool(
            _cur_ps.get("pending_options")
            or _cur_ps.get("meal_planning_queue")
            or _cur_ps.get("is_draft")
            or (_cur_ps.get("pending_planning_queue") and _cur_ps.get("last_pipeline_action") == "ask_confirm_dates")
        )
        _pipeline_user_input = kw["user_input"] if _has_active_state else (args.get("user_request") or kw["user_input"])

        result = await pipeline.execute(
            owner_id=owner_id,
            user_input=_pipeline_user_input,
            history=kw.get("history") or [],
            user_timezone=kw.get("user_timezone"),
            storage_client=_default_storage,
            context=enriched_ctx,
            intent_result=intent_result,
            cooking_level=kw.get("cooking_level", "beginner"),
            language=kw.get("language", "zh"),
            user_profile=user_profile,
        )
        # If the pipeline left an empty response with a tool_result summary,
        # route through the LLM (call #2) so the reply is AI-generated.
        if not result.get("response", "").strip() and result.get("tool_result"):
            result["_direct_response"] = False
        else:
            result["_direct_response"] = True
        return result

    # ── get_cooking_steps ─────────────────────────────────────────────────────

    # Language-aware intro templates — used for direct response (no 2nd LLM call)
    _COOKING_INTRO: Dict[str, str] = {
        "zh": "好的！以下是「{dish}」的烹饪步骤：",
        "en": "Here are the cooking steps for {dish}:",
        "ja": "「{dish}」の作り方です：",
        "ko": "「{dish}」 조리법입니다:",
    }
    _COOKING_FAIL: Dict[str, str] = {
        "zh": "抱歉，暂时无法获取「{dish}」的烹饪步骤，请稍后再试。",
        "en": "Sorry, I couldn't get the cooking steps for {dish}. Please try again.",
        "ja": "「{dish}」の調理手順を取得できませんでした。後でもう一度お試しください。",
        "ko": "「{dish}」의 조리 단계를 가져올 수 없습니다. 나중에 다시 시도해 주세요.",
    }

    async def _get_cooking_steps(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.agents.cooking_steps_agent import CookingStepsAgent
        from app.modules.plan_ahead_state import get_plan_state

        dish_name: str = args.get("dish_name") or ""
        save_to_plan: bool = bool(args.get("save_to_plan", False))
        language: str = kw.get("language", "zh")

        # Build context for the agent
        ctx: Dict[str, Any] = dict(kw.get("context") or {})
        if save_to_plan:
            # Attach current plan state so the agent can save to the correct schedule
            ps = get_plan_state(owner_id)
            ctx = {
                "type": "plan_ahead",
                "data": {
                    "schedule_id": ps.get("schedule_id"),
                    "meal_plan_slots": ps.get("meal_plan_slots") or {},
                    "dish_ingredients": ps.get("dish_ingredients") or {},
                },
            }

        result = await CookingStepsAgent().execute(
            user_input=kw["user_input"],
            owner_id=owner_id,
            context=ctx,
            history=kw.get("history"),
            cooking_level=kw.get("cooking_level", "beginner"),
            language=language,
        )

        agent_action = result.get("action", "COOKING_STEPS")
        r_data = result.get("data") or {}
        steps = result.get("cooking_steps") or r_data.get("cooking_steps") or []
        d_name = result.get("dish_name") or r_data.get("dish_name") or dish_name
        action_data = {
            "dish_name": d_name,
            "cooking_steps": steps,
            "schedule_id": r_data.get("schedule_id"),
            "saved": r_data.get("saved", False),
            "error": r_data.get("error"),
        }
        # Surface ASK_OVERWRITE to the frontend (recipe conflict)
        final_action = "ASK_OVERWRITE" if agent_action == "ASK_OVERWRITE" else "COOKING_STEPS"

        # Build response directly — skips the second LLM call.
        # Includes the full formatted steps so the chat bubble shows them
        # even when the frontend has no separate cooking-steps card renderer.
        if r_data.get("needs_dish_name") or not steps:
            response = result.get("message") or self._COOKING_FAIL.get(
                language, self._COOKING_FAIL["en"]
            ).format(dish=d_name or dish_name)
        else:
            response = self._format_steps_response(d_name, steps, language)

        return {
            "_direct_response": True,
            "response": response,
            "action": final_action,
            "action_data": action_data,
            "tool_result": self._format_cooking_steps(d_name, steps),
            # Pass raw result through for state updates in chat.py
            "_cooking_result": result,
            "_cooking_action": agent_action,
        }

    @staticmethod
    def _format_cooking_steps(dish_name: str, steps: List[str]) -> str:
        if not steps:
            return f"Could not retrieve cooking steps for {dish_name}."
        lines = [f"Cooking steps for {dish_name}:"]
        for i, step in enumerate(steps, 1):
            lines.append(f"Step {i}: {step}")
        return "\n".join(lines)

    def _format_steps_response(self, dish_name: str, steps: List[str], language: str) -> str:
        """Combine the intro with the LLM-formatted steps as-is."""
        intro = self._COOKING_INTRO.get(language, self._COOKING_INTRO["en"]).format(dish=dish_name)
        return intro + "\n\n" + "\n".join(steps)

    # ── modify_recipe_ingredient ──────────────────────────────────────────────

    async def _modify_recipe_ingredient(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.agents.cooking_steps_agent import CookingStepsAgent

        dish_name: str = args.get("dish_name") or ""
        change_request: str = args.get("change_request") or kw["user_input"]
        cooking_ctx = kw.get("cooking_context") or {}
        current_steps: List[str] = cooking_ctx.get("steps") or []

        result = await CookingStepsAgent().execute_modify(
            user_input=change_request,
            owner_id=owner_id,
            current_steps=current_steps,
            dish_name=dish_name,
            context=kw.get("context"),
            language=kw.get("language", "zh"),
        )
        # execute_modify returns a complete response dict
        result["_direct_response"] = True
        return result

    # ── plan_eat_out ──────────────────────────────────────────────────────────

    async def _plan_eat_out(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.agents.plan_eat_out_agent import PlanEatOutAgent

        result = await PlanEatOutAgent().execute(
            user_input=args.get("query") or kw["user_input"],
            owner_id=owner_id,
        )
        data = result.get("data") or {}
        return {
            "_direct_response": False,
            "action": "PLAN_EAT_OUT",
            "action_data": data,
            "tool_result": data.get("suggestion") or "Eat-out plan requested.",
        }

    # ── manage_schedule ───────────────────────────────────────────────────────

    async def _manage_schedule(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.agents.scheduling_agent import (
            ScheduleMutationIntent,
            ScheduleMutationApplier,
        )
        from app.storage.pipeline_storage import _default_storage

        op: str = args.get("operation", "add")
        schedule_id: Optional[int] = args.get("schedule_id")
        schedule_reference: Optional[str] = args.get("schedule_reference")
        target_date: Optional[str] = args.get("target_date")
        meal_type: Optional[str] = args.get("meal_type")

        # Resolve ID: try explicit reference, then date-based meal plan lookup
        if op in ("delete", "modify") and schedule_id is None:
            schedule_id = await self._resolve_schedule_id(
                reference=schedule_reference or "",
                owner_id=owner_id,
                storage_client=_default_storage,
                target_date=target_date,
            )
            if schedule_id is None:
                ref_desc = target_date or schedule_reference or "unknown"
                tool_result = f"No schedule found matching '{ref_desc}'. Nothing was {op}d."
                logger.info("[TOOL manage_schedule] no schedule found for ref=%s date=%s", schedule_reference, target_date)
                return {
                    "_direct_response": False,
                    "action": "MANAGE_SCHEDULE",
                    "action_data": {"ok": False, "target_date": target_date, "meal_type": meal_type},
                    "tool_result": tool_result,
                }

        def _parse_dt(s: Optional[str]) -> Optional[datetime]:
            if not s:
                return None
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return None

        intent = ScheduleMutationIntent(
            operation=op,
            schedule_id=schedule_id,
            title=args.get("title"),
            scheduled_time=_parse_dt(args.get("start_time")),
            end_time=_parse_dt(args.get("end_time")),
            description=args.get("description"),
            event_type=args.get("event_type"),
        )
        apply_result = await ScheduleMutationApplier().apply(
            intent, owner_id, _default_storage
        )
        ok = apply_result.get("ok", False)
        sid = apply_result.get("schedule_id")
        op_label = f"meal plan for {target_date}" if target_date else f"schedule {sid}"
        tool_result = (
            f"{op_label} {op} succeeded."
            if ok
            else f"{op_label} {op} failed: {apply_result.get('error')}"
        )
        return {
            "_direct_response": False,
            "action": "MANAGE_SCHEDULE",
            "action_data": {**apply_result, "target_date": target_date, "meal_type": meal_type},
            "tool_result": tool_result,
        }

    async def _resolve_schedule_id(
        self,
        reference: str,
        owner_id: int,
        storage_client: Any,
        target_date: Optional[str] = None,
    ) -> Optional[int]:
        """Resolve a schedule ID from a natural language reference or a date-based meal plan lookup."""
        try:
            schedules = await storage_client.get_user_schedules(owner_id)
        except Exception:
            return None

        # Title match — works for meetings/appointments
        if reference:
            ref_lower = reference.lower()
            for s in schedules:
                title = (s.get("title") or "").lower()
                if title and (title in ref_lower or ref_lower in title):
                    return s.get("id")

        # Date-based match — find a meal plan schedule covering target_date
        if target_date:
            for s in schedules:
                if "meal_plan" not in (s.get("event_type") or ""):
                    continue
                meta = s.get("metadata") or {}
                # New format: metadata.meal_plan_slots = { "YYYY-MM-DD": { "lunch": [...] } }
                if target_date in (meta.get("meal_plan_slots") or {}):
                    return s.get("id")
                # Old format: metadata.meal_plan = { "YYYY-MM-DD": "dish, dish" }
                if target_date in (meta.get("meal_plan") or {}):
                    return s.get("id")
                # Features format: metadata.features[].plans[].date
                for feat in (meta.get("features") or []):
                    if feat.get("type") == "meal_plan":
                        for plan in (feat.get("plans") or []):
                            if plan.get("date") == target_date:
                                return s.get("id")

        return None

    # ── view_schedule ─────────────────────────────────────────────────────────

    async def _view_schedule(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.agents.scheduling_agent import SchedulingResponseGenerator, TimeRange
        from app.storage.pipeline_storage import _default_storage

        start_date: str = args.get("start_date", "")
        end_date: str = args.get("end_date", "")
        try:
            start_dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
            end_dt = datetime.strptime(end_date[:10], "%Y-%m-%d")
        except Exception:
            now = datetime.utcnow()
            start_dt = now
            end_dt = now + timedelta(days=7)

        time_range = TimeRange(start=start_dt, end=end_dt)
        generator = SchedulingResponseGenerator()
        schedules = await generator.fetch_schedules_in_range(
            owner_id=owner_id,
            time_range=time_range,
            storage_client=_default_storage,
        )
        return {
            "_direct_response": False,
            "action": "VIEW_SCHEDULE",
            "action_data": {
                "schedules": schedules,
                "time_range": {"start": start_date, "end": end_date},
            },
            "tool_result": (
                f"Found {len(schedules)} schedule(s) from {start_date} to {end_date}:\n"
                + json.dumps(schedules, ensure_ascii=False, default=str)
            ),
        }



# Module-level singleton
tool_executor = ToolExecutor()
