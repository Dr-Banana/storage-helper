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

logger = logging.getLogger(__name__)

_SKILL_MD = (Path(__file__).parent.parent / "skills" / "meal_planning" / "SKILL.md").read_text(encoding="utf-8")

# Strip YAML frontmatter, keep only the prompt body
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
        ]
    }
]


class MealPlanningAgent:
    def __init__(
        self,
        auth_token: str,
        cooking_level: str = "beginner",
        language: Optional[str] = None,
        user_timezone: Optional[str] = None,
    ):
        self.auth_token = auth_token
        self.cooking_level = cooking_level
        self.language = language
        self.user_timezone = user_timezone

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

        # Build Gemini contents from frontend history
        contents = self._build_contents(history, user_input, today)

        # Agent loop — max 10 rounds to prevent infinite loops
        for _ in range(10):
            response = await self._call_gemini(contents, system_prompt)
            candidate = (response.get("candidates") or [{}])[0]
            parts = (candidate.get("content") or {}).get("parts") or []

            finish_reason = candidate.get("finishReason", "")
            tool_calls = [p["functionCall"] for p in parts if "functionCall" in p]

            if not tool_calls:
                text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
                logger.info("[agent] final response len=%d finishReason=%s", len(text), finish_reason)
                if not text:
                    logger.warning("[agent] empty text, parts=%s", [list(p.keys()) for p in parts])
                    text = "抱歉，出了点问题，请重试。" if (self.language or "").startswith("zh") else "Sorry, something went wrong. Please try again."
                if on_text:
                    on_text(text)
                return text

            # Add model turn (with tool calls) to context
            contents.append({"role": "model", "parts": parts})
            logger.info("[agent] tool calls: %s", [tc["name"] for tc in tool_calls])

            # Execute tools and collect results
            result_parts = []
            for tc in tool_calls:
                result = await self._execute_tool(tc["name"], tc.get("args") or {})
                logger.info("[agent] tool=%s result=%s", tc["name"], str(result)[:200])
                result_parts.append({
                    "functionResponse": {
                        "name": tc["name"],
                        "response": result,
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

        # No meal_type — return all meals for the day
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

        # Enrich kept dishes: if the model provides only {"name": "..."} (no steps),
        # fill in the full recipe data from the existing DB record.
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
            # Check post-enrichment: dishes still missing steps need a Phase 2 save
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
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 4096,
            },
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

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
            "parts": [{"text": f"[Today: {today}]\n[Level: {self.cooking_level}]\n{user_input}"}],
        })
        return contents
