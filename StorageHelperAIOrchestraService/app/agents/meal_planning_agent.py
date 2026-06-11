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
        on_text: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Process one turn of user input and return the assistant reply.
        on_text: optional streaming callback, called with each text chunk.
        """
        phase = self._state.phase

        if phase == Phase.GATHER_CONTEXT:
            reply = await self._step_gather_context(user_input, history)
        elif phase == Phase.CLASSIFY_DISHES:
            reply = await self._step_classify_dishes(user_input, history)
        elif phase == Phase.GENERATE_STEPS:
            reply = await self._step_generate_steps()
        elif phase == Phase.SAVE:
            reply = await self._step_save()
        else:
            reply = "Your plan is already saved! Let me know if you'd like to make any changes."

        if on_text:
            on_text(reply)
        return reply

    def reset(self) -> None:
        """Reset state to start a new planning session."""
        self._state = PlanState()

    # ── Steps ─────────────────────────────────────────────────────────────────

    async def _step_gather_context(
        self, user_input: str, history: List[Dict[str, str]]
    ) -> str:
        result = await skill_runner.run(
            skill_dir=_SKILLS_DIR / "gather_context",
            user_message=user_input,
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
            meal_label = self._state.meal_type.capitalize() if self._state.meal_type else "meal"
            return f"Got it — {meal_label} on {self._state.date}! What would you like to eat? Any preferences or ingredients you want to use?"

        return result.get("question") or "Which day and meal are you planning for?"

    async def _step_classify_dishes(
        self, user_input: str, history: List[Dict[str, str]]
    ) -> str:
        result = await skill_runner.run(
            skill_dir=_SKILLS_DIR / "classify_dishes",
            user_message=user_input,
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
            self._state.dishes = dishes
            self._state.phase = Phase.GENERATE_STEPS
            dish_names = ", ".join(d["name"] for d in dishes)
            return f"Perfect — {dish_names} it is! Generating cooking steps now…"

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
        dishes = self._state.dishes_with_steps

        existing = await schedule_commands.fetch_existing(
            date=date,
            meal_type=meal_type,
            auth_token=self.auth_token,
        )

        if existing:
            record = await schedule_commands.update_plan(
                schedule_id=existing["id"],
                dishes=dishes,
                auth_token=self.auth_token,
            )
        else:
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
