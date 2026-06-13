"""
MealPlanningAgent — single entry point, state-machine-driven meal planning conversation.

Flow:
  GATHER_CONTEXT  → confirm date + meal type
  CLASSIFY_DISHES → understand what to eat, propose options, wait for confirmation
  GENERATE_STEPS  → generate step-by-step cooking instructions for each dish
  SAVE            → write to DB (create or overwrite existing record)
  DONE            → conversation complete

LLM judgment  → skill_runner (reads SKILL.md)
DB operations → schedule_commands (hardcoded HTTP calls)
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.db import schedule_commands
from app.skills.meal_planning import skill_runner

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / "skills" / "meal_planning"


class Phase(str, Enum):
    GATHER_CONTEXT = "gather_context"
    CLASSIFY_DISHES = "classify_dishes"
    GENERATE_STEPS = "generate_steps"
    SAVE = "save"
    DONE = "done"
    CLARIFY_INTENT = "clarify_intent"  # ask user: add new dish or modify existing?


@dataclass
class PlanState:
    phase: Phase = Phase.GATHER_CONTEXT
    # Results from gather_context
    date: Optional[str] = None
    meal_type: Optional[str] = None
    # Results from classify_dishes
    dishes: List[Dict[str, Any]] = field(default_factory=list)
    # Results from generate_cooking_steps (dishes with ingredients + steps)
    dishes_with_steps: List[Dict[str, Any]] = field(default_factory=list)
    # DB schedule_id once saved (used for update / delete)
    saved_schedule_id: Optional[int] = None


class MealPlanningAgent:
    """
    One instance per user session. Call run() once per conversation turn.
    """

    def __init__(self, gemini_api_url: str, auth_token: str, cooking_level: str = "beginner"):
        self.gemini_api_url = gemini_api_url
        self.auth_token = auth_token
        self.cooking_level = cooking_level
        self._state = PlanState()

    # ── Public ────────────────────────────────────────────────────────────────

    async def run(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_timezone: Optional[str] = None,
        on_text: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Process one turn of user input and return the assistant reply.
        on_text: optional streaming callback, called with each text chunk.
        """
        # Auto-reset if the saved plan date is in the past (new day, new session)
        try:
            from zoneinfo import ZoneInfo
            _tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            _tz = None
        _today = datetime.now(_tz).strftime("%Y-%m-%d")
        if (
            self._state.phase in (Phase.DONE, Phase.CLARIFY_INTENT)
            and self._state.date
            and self._state.date < _today
        ):
            logger.info(
                "[MealPlanningAgent] saved date %s is before today %s — resetting session",
                self._state.date,
                _today,
            )
            self.reset()

        phase = self._state.phase

        if phase == Phase.GATHER_CONTEXT:
            reply = await self._step_gather_context(user_input, history, user_timezone)
        elif phase == Phase.CLASSIFY_DISHES:
            reply = await self._step_classify_dishes(user_input, history)
        elif phase == Phase.GENERATE_STEPS:
            reply = await self._step_generate_steps()
        elif phase == Phase.SAVE:
            reply = await self._step_save()
        elif phase == Phase.CLARIFY_INTENT:
            reply = await self._step_classify_dishes(user_input, history)
        else:
            # DONE phase: show current plan and ask whether to add or modify
            reply = self._step_ask_intent()

        if on_text:
            on_text(reply)
        return reply

    def reset(self) -> None:
        """Reset state to start a new planning session."""
        self._state = PlanState()

    # ── Steps ─────────────────────────────────────────────────────────────────

    async def _step_gather_context(
        self, user_input: str, history: List[Dict[str, str]], user_timezone: Optional[str] = None
    ) -> str:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            tz = None
        today = datetime.now(tz).strftime("%Y-%m-%d")
        augmented = f"[Today's date: {today}]\n{user_input}"
        result = await skill_runner.run(
            skill_dir=_SKILLS_DIR / "gather_context",
            user_message=augmented,
            history=history,
            gemini_api_url=self.gemini_api_url,
        )

        if not result:
            return "I didn't quite catch that — which day and meal are you planning for?"

        ctx = result.get("context") or {}
        self._state.date = ctx.get("date")
        self._state.meal_type = ctx.get("meal_type")

        if result.get("confirmed"):
            self._state.phase = Phase.CLASSIFY_DISHES
            # User may have already mentioned what they want in the same message —
            # run classify_dishes immediately instead of asking again.
            return await self._step_classify_dishes(user_input, history)

        return result.get("question") or "Which day and meal are you planning for?"

    def _step_ask_intent(self) -> str:
        """Show the current saved plan and ask the user what they want to do with it."""
        existing = self._state.dishes_with_steps or self._state.dishes
        self._state.phase = Phase.CLARIFY_INTENT
        if existing:
            names = ", ".join(d["name"] for d in existing)
            meal_label = (self._state.meal_type or "meal").capitalize()
            date = self._state.date or ""
            return (
                f"Your current {meal_label} plan on {date} includes: **{names}**.\n\n"
                "Would you like to **add a new dish**, or **modify one of the existing dishes**?"
            )
        return "What would you like to change about your meal plan?"

    async def _step_classify_dishes(
        self, user_input: str, history: List[Dict[str, str]]
    ) -> str:
        # Only inject saved-plan context when modifying an already-saved plan.
        # During the initial planning flow (no saved_schedule_id), skip the prefix
        # so the skill can naturally track collecting → suggesting → confirmed.
        if self._state.saved_schedule_id:
            existing = self._state.dishes_with_steps or self._state.dishes
            if existing:
                names = ", ".join(d["name"] for d in existing)
                augmented = f"[Already in plan: {names}]\n{user_input}"
            else:
                augmented = user_input
        else:
            augmented = user_input

        result = await skill_runner.run(
            skill_dir=_SKILLS_DIR / "classify_dishes",
            user_message=augmented,
            history=history,
            gemini_api_url=self.gemini_api_url,
        )

        if not result:
            return "What would you like to eat? Feel free to share any preferences or ingredients."

        stage = result.get("stage")
        dishes = result.get("dishes") or []

        if stage == "collecting":
            return result.get("question") or "Any flavor preferences or ingredients you have in mind?"

        if stage == "suggesting":
            self._state.dishes = dishes
            return result.get("suggestion_text") or "Here are some suggestions — what do you think?"

        if stage == "confirmed":
            action = result.get("action", "add")
            replaces = result.get("replaces")

            if action == "replace" and replaces:
                # Remove the dish being replaced from the current plan
                existing = self._state.dishes_with_steps or self._state.dishes
                kept = [d for d in existing if d["name"].lower() != replaces.lower()]
                self._state.dishes_with_steps = kept
                self._state.dishes = kept

            self._state.dishes = dishes
            self._state.phase = Phase.GENERATE_STEPS
            return await self._step_generate_steps()

        return "What would you like to eat? Any requirements are welcome."

    async def _step_generate_steps(self) -> str:
        msg = json.dumps(
            {"dishes": self._state.dishes, "cooking_level": self.cooking_level},
            ensure_ascii=False,
        )
        result = await skill_runner.run(
            skill_dir=_SKILLS_DIR / "generate_cooking_steps",
            user_message=msg,
            history=None,
            gemini_api_url=self.gemini_api_url,
        )

        if not result or not result.get("dishes"):
            return "Something went wrong while generating the steps. Want to try again?"

        self._state.dishes_with_steps = result["dishes"]
        self._state.phase = Phase.SAVE

        return await self._step_save()

    async def _step_save(self) -> str:
        date = self._state.date
        meal_type = self._state.meal_type
        new_dishes = self._state.dishes_with_steps

        existing = await schedule_commands.fetch_existing(
            date=date,
            meal_type=meal_type,
            auth_token=self.auth_token,
        )

        if existing:
            # Merge: keep old dishes, append new ones not already present by name
            old_dishes = schedule_commands.extract_dishes_from_record(existing)
            existing_names = {d["name"].lower() for d in old_dishes}
            dishes = old_dishes + [d for d in new_dishes if d["name"].lower() not in existing_names]
            self._state.dishes_with_steps = dishes
            record = await schedule_commands.update_plan(
                schedule_id=existing["id"],
                date=date,
                meal_type=meal_type,
                dishes=dishes,
                auth_token=self.auth_token,
            )
        else:
            dishes = new_dishes
            record = await schedule_commands.save_plan(
                date=date,
                meal_type=meal_type,
                dishes=dishes,
                auth_token=self.auth_token,
            )

        if record:
            self._state.saved_schedule_id = record.get("id")
            self._state.phase = Phase.DONE
            dish_names = ", ".join(d["name"] for d in dishes)
            meal_label = meal_type.capitalize() if meal_type else "Meal"
            return (
                f"All saved! {meal_label} on {date}: {dish_names}.\n\n"
                "Let me know if you'd like to make any changes."
            )

        self._state.phase = Phase.DONE
        return "Steps are ready, but saving failed. Please try again later."
