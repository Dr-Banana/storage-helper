"""
Gemini Function Calling tool registry and executor.

Replaces the intent classifier + agent factory + manual routing in chat.py.
Each tool declaration maps to an existing agent/pipeline backend, keeping all
existing business logic intact while letting the LLM decide what to invoke.
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
            "Handle all meal planning: add/modify/remove/view meals on specific dates, "
            "generate shopping lists, recommend dishes, or plan home cooking with available inventory. "
            "Use for any message about what to eat, meal scheduling, or food planning — "
            "including implicit declarations like '明天晚上吃红烧肉' (stating intent to eat something). "
            "When a meal planning session is active, prefer this tool for any meal-related message."
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
    },
    {
        "name": "get_cooking_steps",
        "description": (
            "Get full step-by-step cooking instructions for a dish. "
            "Also use when the user wants to save, add, or replace cooking steps in their meal plan "
            "('把步骤加进去', '保存步骤', 'save the steps', '替换步骤', 'store the recipe'). "
            "Do NOT use for follow-up questions about a recipe already being discussed — "
            "respond to those directly."
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
            "Add, delete, or modify a calendar event (non-meal). "
            "Use when the user wants to schedule a meeting, appointment, or any timed event. "
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
                "event_type": {"type": "string", "description": "Type of event (e.g. meeting, appointment)"},
                "schedule_id": {
                    "type": "integer",
                    "description": "ID of schedule to delete/modify (required if known)",
                },
                "schedule_reference": {
                    "type": "string",
                    "description": (
                        "Natural language reference to an existing event when ID is unknown, "
                        "e.g. 'the meeting on Monday', 'the Tuesday appointment'"
                    ),
                },
            },
            "required": ["operation"],
        },
    },
    {
        "name": "view_schedule",
        "description": "View the user's calendar events for a specific date range.",
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

GEMINI_TOOLS = [{"functionDeclarations": TOOL_DECLARATIONS}]

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

        result = await pipeline.execute(
            owner_id=owner_id,
            user_input=kw["user_input"],
            history=kw.get("history") or [],
            user_timezone=kw.get("user_timezone"),
            storage_client=_default_storage,
            context=enriched_ctx,
            intent_result=intent_result,
            cooking_level=kw.get("cooking_level", "beginner"),
            language=kw.get("language", "zh"),
            user_profile=user_profile,
        )
        # PlanAheadPipeline already produces a complete ChatResponse-compatible dict
        result["_direct_response"] = True
        return result

    # ── get_cooking_steps ─────────────────────────────────────────────────────

    async def _get_cooking_steps(self, args: Dict, owner_id: int, **kw) -> Dict:
        from app.agents.cooking_steps_agent import CookingStepsAgent
        from app.modules.plan_ahead_state import get_plan_state

        dish_name: str = args.get("dish_name") or ""
        save_to_plan: bool = bool(args.get("save_to_plan", False))

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
            language=kw.get("language", "zh"),
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

        return {
            "_direct_response": False,
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

        # Resolve ID from natural language reference when needed
        if op in ("delete", "modify") and schedule_id is None and schedule_reference:
            schedule_id = await self._resolve_schedule_id(
                schedule_reference, owner_id, _default_storage
            )

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
        tool_result = (
            f"Schedule {op} succeeded. schedule_id={sid}"
            if ok
            else f"Schedule {op} failed: {apply_result.get('error')}"
        )
        return {
            "_direct_response": False,
            "action": "MANAGE_SCHEDULE",
            "action_data": apply_result,
            "tool_result": tool_result,
        }

    async def _resolve_schedule_id(
        self,
        reference: str,
        owner_id: int,
        storage_client: Any,
    ) -> Optional[int]:
        """Simple title-match resolver for natural language schedule references."""
        try:
            schedules = await storage_client.get_user_schedules(owner_id)
        except Exception:
            return None
        ref_lower = reference.lower()
        for s in schedules:
            title = (s.get("title") or "").lower()
            if title and (title in ref_lower or ref_lower in title):
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
