"""
MealPlanningAgent — Python openclaw-style agent loop.

Each turn:
  1. Build Gemini context from frontend history + user message
  2. Loop: call Gemini → if tool call, execute → repeat until text response
  3. Return final text

Tools (wrapping schedule_commands):
  fetch_meal_plan   — read plans from DB
  save_meal_plan    — create or update a plan
  delete_meal_plan  — remove a plan

The agent is stateless: no session state is kept between turns.
All context comes from the frontend history passed on each request.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.core.config import settings
from app.db import schedule_commands
from app.modules.howtocook_client import HowToCookClient

logger = logging.getLogger(__name__)

_SKILL_MD = (Path(__file__).parent.parent / "skills" / "meal_planning" / "SKILL.md").read_text(encoding="utf-8")


def _load_system_prompt() -> str:
    lines = _SKILL_MD.splitlines()
    if lines[0].strip() == "---":
        try:
            end = lines.index("---", 1)
            return "\n".join(lines[end + 1:]).strip()
        except ValueError:
            pass
    return _SKILL_MD.strip()


_SYSTEM_PROMPT = _load_system_prompt()

# ── Tool declarations ──────────────────────────────────────────────────────────

_TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "fetch_meal_plan",
                "description": (
                    "Look up what is planned for a given date. "
                    "Call this before modifying a plan to see what dishes are already there. "
                    "Omit meal_type to get all meals for the day."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Date in YYYY-MM-DD format",
                        },
                        "meal_type": {
                            "type": "string",
                            "enum": ["breakfast", "lunch", "dinner"],
                            "description": "Specific meal type. Omit to get all meals for the day.",
                        },
                    },
                    "required": ["date"],
                },
            },
            {
                "name": "save_meal_plan",
                "description": (
                    "Save or replace a meal plan. "
                    "Provide the COMPLETE final dish list — including dishes to keep from the existing plan. "
                    "Every dish must include full ingredients and cooking steps."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                        "meal_type": {
                            "type": "string",
                            "enum": ["breakfast", "lunch", "dinner"],
                        },
                        "dishes": {
                            "type": "array",
                            "description": (
                                "Complete list of dishes for this meal. "
                                "For dishes being kept unchanged, provide only {\"name\": \"...\"}. "
                                "For new or modified dishes, provide full ingredients and steps."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "ingredients": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "quantity": {"type": "string"},
                                            },
                                            "required": ["name", "quantity"],
                                        },
                                    },
                                    "steps": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Step-by-step cooking instructions",
                                    },
                                },
                                "required": ["name"],
                            },
                        },
                    },
                    "required": ["date", "meal_type", "dishes"],
                },
            },
            {
                "name": "delete_meal_plan",
                "description": "Delete the entire meal plan for a specific date and meal type.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                        "meal_type": {
                            "type": "string",
                            "enum": ["breakfast", "lunch", "dinner"],
                        },
                    },
                    "required": ["date", "meal_type"],
                },
            },
            {
                "name": "suggest_todays_menu",
                "description": (
                    "Ask the HowToCook database to recommend a dish combination for today. "
                    "Useful when the user has no specific preference and you want to offer database suggestions. "
                    "Returns a curated set of dishes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "people_count": {
                            "type": "integer",
                            "description": "Number of diners (1–10). Default 2 if not specified.",
                        },
                    },
                },
            },
            {
                "name": "get_recipe_details",
                "description": (
                    "Fetch the full recipe (ingredients + cooking steps) for a specific dish "
                    "from the HowToCook database. Supports fuzzy name matching. "
                    "Call this in Phase 2 to get the real recipe before saving. "
                    "Prefer this over inventing a recipe; fall back to generating one if this returns an error."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dish_name": {
                            "type": "string",
                            "description": "Dish name to look up (fuzzy matching supported).",
                        },
                    },
                    "required": ["dish_name"],
                },
            },
            {
                "name": "get_recipes_by_category",
                "description": (
                    "List available dishes in a specific category from the HowToCook database. "
                    "Use when the user mentions a food type like '早餐', '荤菜', '水产', '主食', '素菜' etc."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": (
                                "Category name in Chinese. Common values: "
                                "早餐, 荤菜, 素菜, 主食, 水产, 半成品加工, 汤与粥类."
                            ),
                        },
                    },
                    "required": ["category"],
                },
            },
            {
                "name": "recommend_weekly_meals",
                "description": (
                    "Generate a full one-week meal plan with a shopping list. "
                    "Use when the user explicitly asks for a weekly plan."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "people_count": {
                            "type": "integer",
                            "description": "Number of diners (1–10).",
                        },
                        "allergies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Allergens to avoid, e.g. ['大蒜', '虾'].",
                        },
                        "avoid_items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Ingredients to exclude, e.g. ['葱', '姜'].",
                        },
                    },
                    "required": ["people_count"],
                },
            },
        ]
    }
]


class MealPlanningAgent:
    def __init__(
        self,
        auth_token: str,
        owner_id: int = 0,
        cooking_level: str = "beginner",
        language: Optional[str] = None,
        user_timezone: Optional[str] = None,
    ):
        self.auth_token = auth_token
        self.owner_id = owner_id
        self.cooking_level = cooking_level
        self.language = language
        self.user_timezone = user_timezone
        if settings.HOWTOCOOK_MCP_URL:
            self._foodie = HowToCookClient(settings.HOWTOCOOK_MCP_URL)
            logger.info("[agent] HowToCookClient configured url=%s", settings.HOWTOCOOK_MCP_URL)
        else:
            self._foodie = None
            logger.info("[agent] HowToCookClient disabled (HOWTOCOOK_MCP_URL not set)")

    # ── Public ────────────────────────────────────────────────────────────────

    async def run(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_timezone: Optional[str] = None,
        on_text: Optional[Callable[[str], None]] = None,
    ) -> str:
        try:
            from zoneinfo import ZoneInfo
            _tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            _tz = None
        today = datetime.now(_tz).strftime("%Y-%m-%d")

        system_prompt = (
            f"{_SYSTEM_PROMPT}\n\n"
            f"Cooking level for this user: {self.cooking_level}."
        )

        contents = self._build_contents(history, user_input, today)

        tools_used_this_turn = 0
        reflected = False
        pending_phase2 = False   # save returned action_required; full-recipe save still owed
        phase2_nudges = 0
        interim_text = ""        # text the model emitted alongside a tool call

        for _ in range(10):
            response = await self._call_gemini(contents, system_prompt)
            candidate = (response.get("candidates") or [{}])[0]
            parts = (candidate.get("content") or {}).get("parts") or []

            finish_reason = candidate.get("finishReason", "")
            tool_calls = [p["functionCall"] for p in parts if "functionCall" in p]

            if finish_reason == "MALFORMED_FUNCTION_CALL":
                logger.warning("[agent] MALFORMED_FUNCTION_CALL — retrying with brevity hint")
                contents.append({
                    "role": "user",
                    "parts": [{"text": (
                        "Your last function call was malformed (JSON truncated). "
                        "Retry the same call but keep ingredient quantities and cooking steps shorter. "
                        "Each step must be one sentence."
                    )}],
                })
                continue

            if not tool_calls:
                text = "".join(p.get("text", "") for p in parts if "text" in p).strip()

                # Invariant: a save that returned action_required must be followed
                # by a second save_meal_plan (full recipes) before replying.
                if text and pending_phase2 and phase2_nudges < 2:
                    phase2_nudges += 1
                    logger.warning(
                        "[agent] phase-2 save still pending — nudging model (attempt %d)",
                        phase2_nudges,
                    )
                    contents.append({"role": "model", "parts": [{"text": text}]})
                    contents.append({
                        "role": "user",
                        "parts": [{"text": (
                            "[System check — the user does not see this message] "
                            "Your earlier save_meal_plan returned action_required: the dish is "
                            "saved with its NAME ONLY, the recipe is still missing from the "
                            "database. You must call save_meal_plan again now with the complete "
                            "dishes list (full ingredients and steps) before replying. "
                            "Do not tell the user it is saved until that call succeeds."
                        )}],
                    })
                    continue

                # Self-check: if the model answered without touching any tool,
                # make it verify once that no data was actually needed.
                if text and tools_used_this_turn == 0 and not reflected:
                    reflected = True
                    logger.info("[agent] no tools used — asking model to self-check, text=%s", text[:200])
                    contents.append({"role": "model", "parts": [{"text": text}]})
                    contents.append({
                        "role": "user",
                        "parts": [{"text": (
                            "[Self-check — the user does not see this message] "
                            "You answered without calling any tool. If your answer mentions dishes, "
                            "recipes, or plans, that data MUST come from a tool call — call the right "
                            "tool now. If your answer claims something is unsupported, impossible, or "
                            "not found, you must first ATTEMPT a query (e.g. get_recipe_details fuzzy "
                            "search, get_recipes_by_category) — call one now and only report failure "
                            "if it returns nothing. If your answer genuinely needs no data (greeting, "
                            "clarifying question), repeat exactly the same answer."
                        )}],
                    })
                    continue

                logger.info(
                    "[agent] final response len=%d finishReason=%s text=%s",
                    len(text), finish_reason, text[:200],
                )
                if not text and interim_text:
                    logger.info(
                        "[agent] empty final text — using interim text from tool-call turn len=%d",
                        len(interim_text),
                    )
                    text = interim_text
                elif not text:
                    logger.warning(
                        "[agent] empty text — candidate=%s promptFeedback=%s",
                        json.dumps(candidate)[:800],
                        json.dumps(response.get("promptFeedback"))[:300],
                    )
                    text = "抱歉，出了点问题，请重试。" if (self.language or "").startswith("zh") else "Sorry, something went wrong. Please try again."
                if on_text:
                    on_text(text)
                return text

            contents.append({"role": "model", "parts": parts})
            logger.info("[agent] tool calls: %s", [tc["name"] for tc in tool_calls])

            # The model sometimes writes its user-facing reply in the same
            # message as the tool call, then returns empty content afterwards.
            # Keep that text as a fallback for the final response.
            text_with_call = "".join(p.get("text", "") for p in parts if "text" in p).strip()
            if text_with_call:
                interim_text = text_with_call
                logger.info("[agent] interim text alongside tool call len=%d", len(interim_text))

            result_parts = []
            for tc in tool_calls:
                tools_used_this_turn += 1
                result = await self._execute_tool(tc["name"], tc.get("args") or {})
                logger.info("[agent] tool=%s result=%s", tc["name"], str(result)[:200])

                if tc["name"] == "save_meal_plan" and isinstance(result, dict) and result.get("success"):
                    pending_phase2 = bool(result.get("action_required"))
                response_value = result if isinstance(result, dict) else {"items": result}
                result_parts.append({
                    "functionResponse": {
                        "name": tc["name"],
                        "response": response_value,
                    }
                })

            contents.append({"role": "user", "parts": result_parts})

        logger.error("[agent] exceeded max tool call rounds")
        return "出了点问题，请重试。" if (self.language or "").startswith("zh") else "Something went wrong. Please try again."

    def reset(self) -> None:
        """No-op: agent is stateless."""

    # ── Tool execution ─────────────────────────────────────────────────────────

    async def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if name == "fetch_meal_plan":
                return await self._tool_fetch(args)
            if name == "save_meal_plan":
                return await self._tool_save(args)
            if name == "delete_meal_plan":
                return await self._tool_delete(args)
            if name == "suggest_todays_menu":
                return await self._tool_what_to_eat(args)
            if name == "get_recipe_details":
                return await self._tool_get_recipe(args)
            if name == "get_recipes_by_category":
                return await self._tool_by_category(args)
            if name == "recommend_weekly_meals":
                return await self._tool_recommend_meals(args)
            return {"error": f"Unknown tool: {name}"}
        except Exception as exc:
            logger.error("[tool:%s] failed: %s", name, exc)
            return {"error": str(exc)}

    async def _tool_fetch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        date: str = args["date"]
        meal_type: Optional[str] = args.get("meal_type")

        if meal_type:
            record = await schedule_commands.fetch_existing(date, meal_type, self.auth_token)
            if not record:
                return {"date": date, "meal_type": meal_type, "found": False}
            dishes = schedule_commands.extract_dishes_from_record(record)
            return {"date": date, "meal_type": meal_type, "found": True, "dishes": dishes}

        summaries = await schedule_commands.fetch_upcoming(
            auth_token=self.auth_token,
            from_date=date,
            days=1,
        )
        day_meals = [s for s in summaries if s["date"] == date]
        if not day_meals:
            return {"date": date, "found": False}
        return {"date": date, "found": True, "meals": day_meals}

    async def _tool_save(self, args: Dict[str, Any]) -> Dict[str, Any]:
        date: str = args["date"]
        meal_type: str = args["meal_type"]
        dishes: List[Dict[str, Any]] = args.get("dishes") or []

        existing = await schedule_commands.fetch_existing(date, meal_type, self.auth_token)

        if existing:
            stored = {d["name"]: d for d in schedule_commands.extract_dishes_from_record(existing)}
            dishes = [stored.get(d["name"], d) if not d.get("steps") else d for d in dishes]

        if existing:
            record = await schedule_commands.update_plan(
                schedule_id=existing["id"],
                date=date,
                meal_type=meal_type,
                dishes=dishes,
                auth_token=self.auth_token,
                user_timezone=self.user_timezone,
            )
        else:
            record = await schedule_commands.save_plan(
                date=date,
                meal_type=meal_type,
                dishes=dishes,
                auth_token=self.auth_token,
                user_timezone=self.user_timezone,
            )

        if record:
            pending = [d["name"] for d in dishes if not d.get("steps")]
            result: Dict[str, Any] = {
                "success": True,
                "schedule_id": record.get("id"),
                "saved": {"date": date, "meal_type": meal_type, "dish_count": len(dishes)},
            }
            if pending:
                result["action_required"] = (
                    f"INCOMPLETE — recipes not saved yet for: {', '.join(pending)}. "
                    "Do NOT reply to the user yet. "
                    "Generate full ingredients and steps for these dishes, "
                    "then call save_meal_plan again with the complete list "
                    "(kept dishes as {\"name\": \"...\"}, new dishes with full ingredients and steps)."
                )
            return result
        return {"success": False, "error": "Save failed"}

    async def _tool_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        date: str = args["date"]
        meal_type: str = args["meal_type"]

        existing = await schedule_commands.fetch_existing(date, meal_type, self.auth_token)
        if not existing:
            return {"success": False, "error": "No plan found to delete"}

        ok = await schedule_commands.delete_plan(existing["id"], self.auth_token)
        return {"success": ok, "deleted": {"date": date, "meal_type": meal_type}}

    def _foodie_unavailable(self) -> Dict[str, Any]:
        return {"error": "HowToCook MCP not configured (HOWTOCOOK_MCP_URL is empty)"}

    async def _tool_what_to_eat(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self._foodie:
            return self._foodie_unavailable()
        people = max(1, min(int(args.get("people_count") or 2), 10))
        result = await self._foodie.what_to_eat(people)
        if "error" in result:
            logger.warning("[agent] suggest_todays_menu failed — probing MCP tool list")
            await self._foodie.list_tools()
        return result

    async def _tool_get_recipe(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self._foodie:
            return self._foodie_unavailable()
        return await self._foodie.get_recipe_details(args.get("dish_name", ""))

    async def _tool_by_category(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self._foodie:
            return self._foodie_unavailable()
        result = await self._foodie.get_recipes_by_category(args.get("category", ""))
        return result if isinstance(result, dict) else {"items": result}

    async def _tool_recommend_meals(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self._foodie:
            return self._foodie_unavailable()
        people = max(1, min(int(args.get("people_count") or 2), 10))
        return await self._foodie.recommend_meals(
            people_count=people,
            allergies=args.get("allergies") or None,
            avoid_items=args.get("avoid_items") or None,
        )

    # ── Gemini call ────────────────────────────────────────────────────────────

    async def _call_gemini(self, contents: List[Dict], system_prompt: str) -> Dict[str, Any]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.GEMINI_LLM_MODEL}:generateContent?key={settings.GEMINI_LLM_API_KEY}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "tools": _TOOLS,
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 8192,
                # Let the model reason about which tool to use before acting.
                # Without an explicit budget, flash often skips thinking on
                # tool-selection turns and falls back to memorized answers.
                "thinkingConfig": {"thinkingBudget": 1024},
            },
        }

        logger.debug(
            "[gemini] --> turns=%d system_len=%d tool_count=%d last_role=%s last_text=%s",
            len(contents),
            len(system_prompt),
            sum(len(t.get("functionDeclarations", [])) for t in _TOOLS),
            contents[-1].get("role") if contents else "—",
            str(contents[-1].get("parts", [{}])[0])[:120] if contents else "—",
        )

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)

        logger.debug("[gemini] <-- status=%d body_len=%d", resp.status_code, len(resp.text))

        if resp.status_code != 200:
            logger.error("[gemini] error response: %s", resp.text[:1000])

        resp.raise_for_status()
        data = resp.json()

        candidates = data.get("candidates") or []
        if candidates:
            c = candidates[0]
            parts = (c.get("content") or {}).get("parts") or []
            tool_calls = [p["functionCall"]["name"] for p in parts if "functionCall" in p]
            text_len = sum(len(p.get("text", "")) for p in parts if "text" in p)
            logger.info(
                "[gemini] finishReason=%s parts=%d text_len=%d tool_calls=%s",
                c.get("finishReason"), len(parts), text_len, tool_calls,
            )
        else:
            logger.warning("[gemini] no candidates — full response: %s", json.dumps(data)[:800])

        return data

    # ── History conversion ─────────────────────────────────────────────────────

    def _build_contents(
        self,
        history: List[Dict[str, str]],
        user_input: str,
        today: str,
    ) -> List[Dict]:
        contents = []
        for msg in history[-10:]:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        contents.append({
            "role": "user",
            "parts": [{"text": (
                f"[Today: {today}]\n[Level: {self.cooking_level}]\n"
                "[Reminder: every dish you suggest must come from a tool result; "
                "every recipe you save must come from get_recipe_details]\n"
                f"{user_input}"
            )}],
        })
        return contents
