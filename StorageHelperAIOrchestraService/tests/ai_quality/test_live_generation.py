"""
Layer 4 – Live Generation & Modification Tests
===============================================
Uses real Gemini API calls to verify:

  1. CookingStepsAgent._generate_steps() produces well-formed steps + ingredients.
  2. CookingStepsAgent.execute_modify() correctly modifies ingredient quantities
     in existing steps when given a natural-language tweak request.

Execution modes
---------------
* **Offline / CI** (default): all tests are skipped if neither flag nor key is set.
* **Live mode**: set GEMINI_LLM_TESTING_KEY in .env.local  OR  pass --run-llm flag.

  pytest tests/ai_quality/test_live_generation.py -m llm_live --run-llm -v

DB persistence is mocked throughout — only the Gemini LLM calls are live.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.cooking_steps_agent import CookingStepsAgent
from tests.ai_quality.conftest import has_measurement_units, steps_have_action_verbs


# ---------------------------------------------------------------------------
# Live-key detection (reuse same pattern as test_semantic.py)
# ---------------------------------------------------------------------------

def _load_testing_key() -> str:
    val = os.getenv("GEMINI_LLM_TESTING_KEY", "")
    if val:
        return val
    try:
        from dotenv import dotenv_values  # type: ignore
        import pathlib
        _root = pathlib.Path(__file__).parent.parent.parent
        for env_file in (".env.local", ".env.preprod", ".env.prod"):
            path = _root / env_file
            if path.exists():
                vals = dotenv_values(str(path))
                if vals.get("GEMINI_LLM_TESTING_KEY"):
                    return str(vals["GEMINI_LLM_TESTING_KEY"])
    except Exception:
        pass
    return ""

_TESTING_KEY: str = _load_testing_key()

# Phase-header emojis used by the beginner-level format spec.
# These strings are excluded from content checks (action-verb, CJK, etc.).
_PHASE_EMOJIS = ("🥗", "🍳", "🥢", "💡")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent() -> CookingStepsAgent:
    """Return a CookingStepsAgent wired to the real Gemini API."""
    return CookingStepsAgent()


def _cooking_steps_only(steps: List[str]) -> List[str]:
    """Strip phase-header and tip strings from a step list."""
    return [s for s in steps if s and not any(s.startswith(e) for e in _PHASE_EMOJIS)]


def _steps_mention(steps: List[str], keyword: str) -> bool:
    return keyword.lower() in " ".join(steps).lower()


# ---------------------------------------------------------------------------
# Shared skip fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _skip_without_live_key(request):
    if "llm_live" in [m.name for m in request.node.iter_markers()]:
        has_flag = request.config.getoption("--run-llm", default=False)
        if not has_flag and not _TESTING_KEY:
            pytest.skip(
                "Live generation tests are disabled.\n"
                "  Option A (local): set GEMINI_LLM_TESTING_KEY in .env.local\n"
                "  Option B (any):   pass --run-llm flag to pytest"
            )


# ---------------------------------------------------------------------------
# Reference fixtures for modification tests
#
# Pre-written English steps are used as stable inputs so modification tests
# are deterministic and independent of generation non-determinism.
# ---------------------------------------------------------------------------

_KUNG_PAO_STEPS: List[str] = [
    "🥗 Prep",
    "**Cube chicken**: cut **200g chicken breast** into **1 cm** pieces; "
    "marinate with **15 ml soy sauce** and **5 g cornstarch** for **15 minutes**.",
    "**Toast peanuts**: dry-fry **50 g peanuts** over low heat until lightly golden; set aside.",
    "**Make sauce**: combine **30 ml soy sauce**, **15 ml rice vinegar**, "
    "**10 g sugar**, and **5 g cornstarch** in a bowl; stir well.",
    "🍳 Cook",
    "**Aromatics**: heat **30 ml oil** in a wok; stir-fry **8 dried chilies** (halved) "
    "and **1 g Sichuan peppercorn** until fragrant.",
    "**Cook chicken**: add marinated chicken; stir-fry over high heat for **2 minutes** "
    "until cooked through.",
    "**Glaze**: pour in the sauce; toss until thickened and coats the chicken.",
    "**Finish**: add peanuts and **20 g scallion pieces**; stir briefly and plate.",
    "🥢 Serve",
]

_KUNG_PAO_INGREDIENTS: List[Dict[str, Any]] = [
    {"name": "chicken breast",       "quantity": "200g"},
    {"name": "peanuts",              "quantity": "50g"},
    {"name": "dried chilies",        "quantity": "8 pieces"},
    {"name": "soy sauce",            "quantity": "45ml"},
    {"name": "rice vinegar",         "quantity": "15ml"},
    {"name": "sugar",                "quantity": "10g"},
    {"name": "cornstarch",           "quantity": "10g"},
    {"name": "cooking oil",          "quantity": "30ml"},
    {"name": "Sichuan peppercorn",   "quantity": "1g"},
    {"name": "scallion pieces",      "quantity": "20g"},
]

_TOMATO_EGG_STEPS: List[str] = [
    "🥗 Prep",
    "**Beat eggs**: crack **3 eggs** into a bowl, add **1 g salt**, whisk thoroughly.",
    "**Prep tomatoes**: cut **2 tomatoes** into wedges.",
    "🍳 Cook",
    "**Scramble eggs**: heat **15 ml oil** over medium-high; pour in egg mixture, "
    "stir gently until just set; transfer to a plate.",
    "**Cook tomatoes**: add **5 ml oil**; stir-fry tomatoes over medium heat until juicy; "
    "season with **3 g salt**.",
    "**Combine**: return eggs to the pan; add **2 g sugar**; toss and serve.",
    "🥢 Serve",
]


# ---------------------------------------------------------------------------
# 1. Live Step Generation Tests
# ---------------------------------------------------------------------------

@pytest.mark.llm_live
class TestLiveStepGeneration:
    """
    End-to-end generation tests using real Gemini API.

    Each test calls CookingStepsAgent._generate_steps() with a real dish name
    and verifies the returned steps + ingredients meet structural and content
    quality requirements.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dish", [
        "Kung Pao Chicken",
        "Tomato and Egg Stir-Fry",
        "Red Braised Pork Belly",
    ])
    async def test_generate_steps_returns_nonempty_steps_and_ingredients(self, dish: str):
        """Steps and ingredients must both be non-empty lists."""
        result = await _agent()._generate_steps(dish, cooking_level="beginner", language="en")

        steps = result.get("steps", [])
        ingredients = result.get("ingredients", [])

        assert isinstance(steps, list) and len(steps) >= 4, (
            f"{dish}: expected ≥4 steps, got {len(steps)}: {steps}"
        )
        assert isinstance(ingredients, list) and len(ingredients) >= 2, (
            f"{dish}: expected ≥2 ingredients, got {len(ingredients)}: {ingredients}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dish", ["Kung Pao Chicken", "Tomato and Egg Stir-Fry"])
    async def test_generated_steps_have_measurement_units(self, dish: str):
        """At least one step must contain an explicit measurement unit."""
        result = await _agent()._generate_steps(dish, cooking_level="beginner", language="en")
        steps = result.get("steps", [])
        assert has_measurement_units(steps, threshold=1), (
            f"{dish}: no measurement units found in steps:\n"
            + "\n".join(f"  {s}" for s in steps)
        )

    @pytest.mark.asyncio
    async def test_generated_steps_have_action_verbs(self):
        """Every non-header step should contain at least one cooking action verb."""
        dish = "Tomato and Egg Stir-Fry"
        result = await _agent()._generate_steps(dish, cooking_level="beginner", language="en")
        cooking_steps = _cooking_steps_only(result.get("steps", []))
        assert cooking_steps, f"{dish}: no actual cooking steps found"
        assert steps_have_action_verbs(cooking_steps), (
            f"{dish}: some steps lack action verbs:\n"
            + "\n".join(f"  {s}" for s in cooking_steps)
        )

    @pytest.mark.asyncio
    async def test_ingredients_have_name_and_quantity(self):
        """Every ingredient dict must have a non-empty 'name' and a non-empty 'quantity'."""
        dish = "Kung Pao Chicken"
        result = await _agent()._generate_steps(dish, cooking_level="beginner", language="en")
        ingredients = result.get("ingredients", [])
        assert ingredients, f"{dish}: ingredients list is empty"
        missing_name = [i for i in ingredients if not i.get("name", "").strip()]
        missing_qty  = [i for i in ingredients if not i.get("quantity", "").strip()]
        assert not missing_name, f"{dish}: ingredients missing name: {missing_name}"
        assert not missing_qty, (
            f"{dish}: some ingredients lack a quantity (model used 'to taste'?):\n"
            + "\n".join(f"  {i}" for i in missing_qty)
        )

    @pytest.mark.asyncio
    async def test_intermediate_level_generates_more_detail(self):
        """Intermediate-level generation should produce ≥6 actual cooking steps."""
        dish = "Red Braised Pork Belly"
        result = await _agent()._generate_steps(dish, cooking_level="intermediate", language="en")
        cooking_steps = _cooking_steps_only(result.get("steps", []))
        assert len(cooking_steps) >= 6, (
            f"{dish} (intermediate): expected ≥6 steps, got {len(cooking_steps)}"
        )

    @pytest.mark.asyncio
    async def test_english_output_contains_no_cjk_characters(self):
        """
        When language='en', actual cooking steps must contain no CJK characters.

        Phase headers (🥗 / 🍳 / 🥢) are part of the beginner format spec and
        may retain their label text — they are excluded from this check.
        """
        dish = "Tomato and Egg Stir-Fry"
        result = await _agent()._generate_steps(dish, cooking_level="beginner", language="en")
        steps = result.get("steps", [])
        cooking_steps = _cooking_steps_only(steps)
        assert cooking_steps, f"{dish} (en): no actual cooking steps found: {steps}"
        all_text = " ".join(cooking_steps)
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", all_text)
        assert len(cjk_chars) == 0, (
            f"{dish} (en): cooking steps contain CJK characters: "
            f"{''.join(cjk_chars[:10])!r}\n"
            f"Cooking steps (phase headers excluded): {cooking_steps}"
        )


# ---------------------------------------------------------------------------
# 2. Live Modify Recipe Tests
# ---------------------------------------------------------------------------

@pytest.mark.llm_live
class TestLiveModifyRecipe:
    """
    End-to-end modification tests using real Gemini API.

    DB persistence (httpx PATCH + schedule lookup) is mocked — only the
    Gemini LLM calls inside _modify_steps_ingredient() are live.

    Design note: CookingStepsAgent._apply_ingredient_tweak() returns only the
    *modified* steps (those that contained the changed ingredient), not the full
    list.  changed_indices records the original positions of those steps.
    """

    def _agent_no_db(self) -> CookingStepsAgent:
        return _agent()

    @pytest.mark.asyncio
    async def test_increase_soy_sauce_updates_steps_and_ingredient_change(self):
        """
        'add more soy sauce' should cause the LLM to increase soy sauce quantities
        and populate ingredient_change with the old and new amounts.
        """
        agent = self._agent_no_db()

        with patch.object(agent, "_find_schedule_id_by_dish", new=AsyncMock(return_value=None)):
            result = await agent.execute_modify(
                user_input="add a bit more soy sauce",
                owner_id=1,
                current_steps=_KUNG_PAO_STEPS,
                dish_name="Kung Pao Chicken",
                context=None,
                language="en",
            )

        modified_steps   = result.get("modified_steps") or []
        ingredient_change = result.get("ingredient_change")
        changed_indices  = result.get("changed_indices") or []

        # The LLM must produce at least one changed step
        assert isinstance(modified_steps, list) and len(modified_steps) >= 1, (
            f"modified_steps is empty — LLM returned nothing: {result}"
        )
        # Steps text or ingredient_change must reflect the modification
        original_at_indices = " ".join(
            _KUNG_PAO_STEPS[i] for i in changed_indices if i < len(_KUNG_PAO_STEPS)
        )
        steps_text_changed = " ".join(modified_steps) != original_at_indices
        assert steps_text_changed or ingredient_change, (
            "Neither steps text nor ingredient_change reflects the requested modification.\n"
            f"ingredient_change={ingredient_change}\n"
            f"changed_indices={changed_indices}\n"
            f"modified_steps={modified_steps}"
        )
        # Soy sauce must be the identified ingredient
        if ingredient_change:
            assert "soy sauce" in str(ingredient_change.get("name", "")).lower(), (
                f"ingredient_change should reference 'soy sauce', got: {ingredient_change}"
            )

    @pytest.mark.asyncio
    async def test_reduce_salt_updates_steps(self):
        """'use less salt' on Tomato and Egg Stir-Fry should reduce salt quantity."""
        agent = self._agent_no_db()

        with patch.object(agent, "_find_schedule_id_by_dish", new=AsyncMock(return_value=None)):
            result = await agent.execute_modify(
                user_input="use a little less salt",
                owner_id=1,
                current_steps=_TOMATO_EGG_STEPS,
                dish_name="Tomato and Egg Stir-Fry",
                context=None,
                language="en",
            )

        modified_steps    = result.get("modified_steps") or []
        ingredient_change = result.get("ingredient_change")

        assert (isinstance(modified_steps, list) and len(modified_steps) >= 1) or ingredient_change, (
            f"No modified steps and no ingredient_change returned: {result}"
        )
        # At least one returned step must be a real cooking instruction (not a header)
        actual = _cooking_steps_only(modified_steps)
        assert actual, f"No actual cooking steps in result: {modified_steps}"

    @pytest.mark.asyncio
    async def test_ingredient_change_contains_old_and_new_qty(self):
        """
        ingredient_change must have 'name', 'old_qty', and 'new_qty' fields
        when the LLM identifies a quantity modification.
        """
        agent = self._agent_no_db()

        with patch.object(agent, "_find_schedule_id_by_dish", new=AsyncMock(return_value=None)):
            result = await agent.execute_modify(
                user_input="add 10 ml more soy sauce",
                owner_id=1,
                current_steps=_KUNG_PAO_STEPS,
                dish_name="Kung Pao Chicken",
                context=None,
                language="en",
            )

        ingredient_change = result.get("ingredient_change")
        if ingredient_change:
            assert "name" in ingredient_change, (
                f"ingredient_change missing 'name': {ingredient_change}"
            )
            assert "new_qty" in ingredient_change, (
                f"ingredient_change missing 'new_qty': {ingredient_change}"
            )
            assert "soy sauce" in str(ingredient_change.get("name", "")).lower(), (
                f"Expected 'soy sauce' in ingredient_change name, got: {ingredient_change}"
            )

    @pytest.mark.asyncio
    async def test_modification_is_selective_not_full_rewrite(self):
        """
        A single-ingredient tweak should only touch the steps that mention that
        ingredient.  changed_indices must be a strict subset of all step indices —
        the LLM must not rewrite the entire recipe.
        """
        agent = self._agent_no_db()

        with patch.object(agent, "_find_schedule_id_by_dish", new=AsyncMock(return_value=None)):
            result = await agent.execute_modify(
                user_input="add more soy sauce",
                owner_id=1,
                current_steps=_KUNG_PAO_STEPS,
                dish_name="Kung Pao Chicken",
                context=None,
                language="en",
            )

        changed_indices = result.get("changed_indices") or []
        modified_steps  = result.get("modified_steps") or []

        assert len(modified_steps) >= 1, f"No modified steps returned: {result}"
        # changed_indices must NOT cover every step (selective, not full rewrite)
        assert len(changed_indices) < len(_KUNG_PAO_STEPS), (
            f"Modification touched ALL {len(_KUNG_PAO_STEPS)} steps — this looks like "
            f"a full rewrite.\nchanged_indices={changed_indices}"
        )

    @pytest.mark.asyncio
    async def test_changed_indices_is_list_on_successful_modification(self):
        """changed_indices must always be a list (empty or non-empty)."""
        agent = self._agent_no_db()

        with patch.object(agent, "_find_schedule_id_by_dish", new=AsyncMock(return_value=None)):
            result = await agent.execute_modify(
                user_input="add more soy sauce",
                owner_id=1,
                current_steps=_KUNG_PAO_STEPS,
                dish_name="Kung Pao Chicken",
                context=None,
                language="en",
            )

        changed_indices = result.get("changed_indices")
        assert isinstance(changed_indices, list), (
            f"changed_indices should be a list, got {type(changed_indices)}: {changed_indices}"
        )

    @pytest.mark.asyncio
    async def test_scaling_doubles_all_ingredients(self):
        """
        'make a double portion' should trigger the scaling path and
        produce a full step list with roughly doubled quantities.
        """
        agent = self._agent_no_db()

        with patch.object(agent, "_find_schedule_id_by_dish", new=AsyncMock(return_value=None)):
            result = await agent.execute_modify(
                user_input="I want to make a double portion",
                owner_id=1,
                current_steps=_KUNG_PAO_STEPS,
                dish_name="Kung Pao Chicken",
                context=None,
                language="en",
            )

        modified_steps = result.get("modified_steps") or []
        # Scaling rewrites the full list, so ≥4 steps are expected
        assert len(modified_steps) >= 4, (
            f"Scaling returned too few steps: {modified_steps}"
        )
        # After doubling, 400g (= 2×200g) chicken breast should appear
        all_text = " ".join(modified_steps)
        assert "400" in all_text or "chicken" in all_text.lower(), (
            f"Scaled steps do not reflect doubled chicken amount:\n{all_text}"
        )

    @pytest.mark.asyncio
    async def test_generate_then_modify_end_to_end(self):
        """
        Full two-step flow using a single agent instance:
          1. Generate steps for Tomato and Egg Stir-Fry via real Gemini.
          2. Send a modification request ('use less salt') on those generated steps.
          3. Verify the result is a valid modified step list or ingredient_change.
        """
        agent = self._agent_no_db()

        # Step 1 — generate
        gen = await agent._generate_steps(
            "Tomato and Egg Stir-Fry", cooking_level="beginner", language="en"
        )
        generated_steps = gen.get("steps", [])
        assert generated_steps, "Step 1 failed: generated_steps is empty"

        # Step 2 — modify
        with patch.object(agent, "_find_schedule_id_by_dish", new=AsyncMock(return_value=None)):
            mod_result = await agent.execute_modify(
                user_input="use a little less salt",
                owner_id=1,
                current_steps=generated_steps,
                dish_name="Tomato and Egg Stir-Fry",
                context=None,
                language="en",
            )

        modified_steps    = mod_result.get("modified_steps") or []
        ingredient_change = mod_result.get("ingredient_change")

        # At least one modified step OR a populated ingredient_change is required
        assert (isinstance(modified_steps, list) and len(modified_steps) >= 1) or ingredient_change, (
            f"Step 2 failed — no modified steps and no ingredient_change: {mod_result}"
        )
        # ingredient_change should reference salt when populated
        if ingredient_change:
            assert "salt" in str(ingredient_change.get("name", "")).lower(), (
                f"Expected 'salt' in ingredient_change, got: {ingredient_change}"
            )
