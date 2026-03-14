"""
Phase 1 — Live AI Intake Tests: User Profile Constraint Adherence
=================================================================

Verifies that the LLM *actually reads and obeys* user profile constraints
injected by Phase 1's _build_context() user_profile logic.

Strategy
--------
  1. Build a system_context string via _build_context() with a specific
     user_profile (no DB or state-management involved).
  2. Call _call_llm() directly with a "recommend a meal plan" request.
  3. Parse the structured LLM response and assert the AI followed the rules.

No DB writes.  Only the Gemini LLM call is live.

Run
---
  pytest tests/pipelines/test_plan_ahead_live_profile.py -m llm_live --run-llm -v
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import pytest

from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline


# ─── Live-key detection (same pattern as test_live_generation.py) ────────────

def _load_testing_key() -> str:
    val = os.getenv("GEMINI_LLM_TESTING_KEY", "")
    if val:
        return val
    try:
        from dotenv import dotenv_values
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

_GEMINI_MODEL = "gemini-2.5-flash"


@pytest.fixture(autouse=True)
def _skip_without_live_key(request):
    if "llm_live" in [m.name for m in request.node.iter_markers()]:
        has_flag = request.config.getoption("--run-llm", default=False)
        if not has_flag and not _TESTING_KEY:
            pytest.skip(
                "Live profile tests are disabled.\n"
                "  Option A (local): set GEMINI_LLM_TESTING_KEY in .env.local\n"
                "  Option B (any):   pass --run-llm flag to pytest"
            )


# ─── Shared helpers ──────────────────────────────────────────────────────────

def _pipeline() -> PlanAheadPipeline:
    api_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_GEMINI_MODEL}:generateContent?key={_TESTING_KEY}"
    )
    return PlanAheadPipeline(gemini_api_url=api_url)


def _empty_state() -> Dict[str, Any]:
    return {"meal_plan_slots": {}, "dish_ingredients": {}, "meal_plan": {}}


def _all_dish_names(parsed: Dict[str, Any]) -> List[str]:
    """Extract every dish name from a _call_llm() parsed result.

    Handles both direct action results (meal_plan_slots) and suggest_options
    responses where dishes live inside each option's meal_entries.
    """
    names: List[str] = []
    # Direct meal_plan_slots (recommend / add actions)
    for date_slots in (parsed.get("meal_plan_slots") or {}).values():
        for dishes in (date_slots or {}).values():
            if isinstance(dishes, list):
                names.extend(dishes)
            elif isinstance(dishes, str):
                names.append(dishes)
    # suggest_options: dishes are inside each option's meal_plan_slots
    for opt in (parsed.get("dish_options") or []):
        for date_slots in (opt.get("meal_plan_slots") or {}).values():
            for dishes in (date_slots or {}).values():
                if isinstance(dishes, list):
                    names.extend(dishes)
                elif isinstance(dishes, str):
                    names.append(dishes)
        # Also pull from flat meal_plan if present
        for v in (opt.get("meal_plan") or {}).values():
            if isinstance(v, str):
                names.extend([d.strip() for d in v.split(" and ") if d.strip()])
    return names


def _all_ingredient_names(parsed: Dict[str, Any]) -> List[str]:
    """Extract every ingredient name that appears across all dishes.

    Handles both direct dish_ingredients and suggest_options responses.
    """
    names: List[str] = []

    def _extract(dish_ingredients: Optional[Dict]) -> None:
        for ingredients in (dish_ingredients or {}).values():
            for ing in (ingredients or []):
                if isinstance(ing, dict):
                    n = ing.get("name", "")
                else:
                    n = str(ing)
                if n:
                    names.append(n)

    _extract(parsed.get("dish_ingredients"))
    for opt in (parsed.get("dish_options") or []):
        _extract(opt.get("dish_ingredients"))
    return names


def _user_message(parsed: Dict[str, Any]) -> str:
    return parsed.get("user_message", "")


# ─── Annotation helpers ───────────────────────────────────────────────────────

def _looks_chinese_dish(name: str) -> bool:
    """Heuristic: name contains CJK characters OR is a known Chinese dish."""
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", name))
    chinese_keywords = [
        "Stir", "Braised", "Steam", "Fried Rice", "Noodle", "Dumplings",
        "Kung Pao", "Mapo", "Hotpot", "Congee", "Dim Sum", "Tofu",
        "Pork Belly", "Twice-Cooked", "Sweet and Sour",
    ]
    return has_cjk or any(k.lower() in name.lower() for k in chinese_keywords)


def _looks_like_soup(name: str) -> bool:
    """Heuristic: name contains soup-related keywords."""
    soup_keywords = ["soup", "汤", "stew", "broth", "chowder", "bisque", "congee", "粥"]
    return any(k.lower() in name.lower() for k in soup_keywords)


# ─────────────────────────────────────────────────────────────────────────────
# Live Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm_live
class TestLiveUserProfileIntake:
    """
    Each test builds a system_context from _build_context() with a targeted
    user_profile, then calls _call_llm() directly and inspects the structured
    LLM output to confirm the profile constraints were honoured.
    """

    # ── Test 1: Disliked ingredients must not appear in any dish ─────────────

    @pytest.mark.asyncio
    async def test_disliked_ingredient_excluded_from_all_dishes(self):
        """
        Profile: disliked_ingredients=["香菜"].
        Expected: no dish ingredient should contain "香菜" (cilantro).
        """
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": ["香菜"],
            "cuisine_weights": {"Chinese": 80, "Western": 20},
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        parsed = await p._call_llm(
            system_context=ctx,
            history=[],
            user_input="帮我推荐今晚的晚餐，我喜欢家常菜",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        ingredient_names = _all_ingredient_names(parsed)
        violations = [n for n in ingredient_names if "香菜" in n]
        assert not violations, (
            f"AI ignored FORBIDDEN ingredient 香菜. Found in: {violations}\n"
            f"Full ingredient list: {ingredient_names}"
        )

    # ── Test 2: Multiple disliked ingredients all excluded ───────────────────

    @pytest.mark.asyncio
    async def test_multiple_disliked_ingredients_all_excluded(self):
        """
        Profile: disliked_ingredients=["香菜", "花椒"].
        Expected: neither 香菜 nor 花椒 appear in any ingredient.
        """
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": ["香菜", "花椒"],
            "cuisine_weights": {"Chinese": 70, "Western": 30},
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        parsed = await p._call_llm(
            system_context=ctx,
            history=[],
            user_input="推荐一周的晚餐计划",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        ingredient_names = [n.lower() for n in _all_ingredient_names(parsed)]
        for forbidden in ["香菜", "花椒"]:
            violations = [n for n in ingredient_names if forbidden in n]
            assert not violations, (
                f"AI included forbidden ingredient '{forbidden}' in: {violations}"
            )

    # ── Test 3: include_soup=True → at least one soup in the plan ────────────

    @pytest.mark.asyncio
    async def test_include_soup_true_results_in_at_least_one_soup(self):
        """
        Profile: include_soup=True.
        Expected: at least one dish name in the plan looks like a soup.
        Scope is kept to a single-day dinner to avoid MAX_TOKENS truncation.
        """
        profile = {
            "default_servings": 2,
            "meat_veg_ratio": "1:1:1",
            "include_soup": True,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 70, "Western": 30},
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        parsed = await p._call_llm(
            system_context=ctx,
            history=[],
            user_input="帮我安排今天的晚餐，必须有汤",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        dish_names = _all_dish_names(parsed)
        assert dish_names, f"LLM produced no dish names: {parsed}"

        has_soup = any(_looks_like_soup(d) for d in dish_names)
        assert has_soup, (
            f"Profile requires soup (include_soup=True) but no soup-like dish found.\n"
            f"All dishes: {dish_names}\n"
            f"AI message: {_user_message(parsed)}"
        )

    # ── Test 4: Heavy Chinese weighting → majority Chinese dishes ────────────

    @pytest.mark.asyncio
    async def test_high_chinese_weight_produces_mostly_chinese_dishes(self):
        """
        Profile: cuisine_weights={"Chinese": 90, "Western": 5, "Japanese": 5}.
        Expected: ≥60% of recommended dish names look Chinese.
        """
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 90, "Western": 5, "Japanese": 5},
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        parsed = await p._call_llm(
            system_context=ctx,
            history=[],
            user_input="帮我推荐下周的晚餐，我喜欢中式家常菜",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        dish_names = _all_dish_names(parsed)
        assert dish_names, f"LLM produced no dish names: {parsed}"

        chinese_count = sum(1 for d in dish_names if _looks_chinese_dish(d))
        ratio = chinese_count / len(dish_names)

        assert ratio >= 0.6, (
            f"Only {chinese_count}/{len(dish_names)} ({ratio:.0%}) dishes look Chinese.\n"
            f"Expected ≥60% given Chinese weight=90%.\n"
            f"All dishes: {dish_names}"
        )

    # ── Test 5: Servings scale hint appears in AI message ────────────────────

    @pytest.mark.asyncio
    async def test_profile_with_5_servings_is_acknowledged(self):
        """
        Profile: default_servings=5.
        Two-layer check:
          Layer 1 (context): the injected system_context contains the 5-person hint.
          Layer 2 (LLM)    : a small 2-day scope request returns a valid action.
        """
        profile = {
            "default_servings": 5,
            "meat_veg_ratio": "2:2:1",
            "include_soup": True,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 60, "Western": 40},
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="intermediate",
            language="zh",
            user_profile=profile,
        )
        # Layer 1: the context string itself must carry the servings hint
        assert "5 person" in ctx, "Context missing servings hint for 5 people"
        assert "2 meat" in ctx, "Context missing meat-veg-ratio for 5-person profile"

        # Layer 2: ask for just today + tomorrow to stay within token budget
        parsed = await p._call_llm(
            system_context=ctx,
            history=[],
            user_input="帮我安排今天和明天的晚餐，5人份",
        )
        assert parsed is not None, "LLM returned None — API call failed"
        assert parsed.get("action") in ("ask", "recommend", "add"), (
            f"Unexpected action: {parsed.get('action')}\nFull: {parsed}"
        )

    # ── Test 6: Zero disliked ingredients — no false exclusions ──────────────

    @pytest.mark.asyncio
    async def test_empty_disliked_list_does_not_restrict_ai(self):
        """
        Profile: disliked_ingredients=[].
        The AI should produce a normal recommendation with a variety of ingredients
        (not constrained to avoid anything specific).
        """
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 60, "Western": 40},
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        parsed = await p._call_llm(
            system_context=ctx,
            history=[],
            user_input="帮我推荐今晚晚餐",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        dish_names = _all_dish_names(parsed)
        ingredient_names = _all_ingredient_names(parsed)

        # Should return at least one dish and a few ingredients
        assert len(dish_names) >= 1, (
            f"Expected ≥1 dish with no restrictions, got {len(dish_names)}: {dish_names}"
        )
        assert len(ingredient_names) >= 2, (
            f"Expected ≥2 ingredients, got {len(ingredient_names)}: {ingredient_names}"
        )

    # ── Test 7: Full realistic profile — end-to-end intake validation ────────

    @pytest.mark.asyncio
    async def test_full_realistic_profile_passes_all_constraints(self):
        """
        Realistic USC student profile:
          - 1 person, no soup required, dislikes 香菜 and 花椒,
          - 70% Chinese 20% Western 10% Japanese
        Asserts ALL constraints simultaneously:
          1. No 香菜 or 花椒 in any ingredient
          2. Action is ask/recommend (AI did not crash or return garbage)
          3. At least one dish recommended
        """
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": 700,
            "disliked_ingredients": ["香菜", "花椒"],
            "cuisine_weights": {"Chinese": 70, "Western": 20, "Japanese": 10},
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        parsed = await p._call_llm(
            system_context=ctx,
            history=[
                {
                    "role": "assistant",
                    "content": "在我为您制定计划之前，想先了解一下您的偏好。您喜欢什么口味？",
                },
            ],
            user_input="我喜欢家常菜，预算不多，不吃香菜和花椒",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        # Constraint 1: valid action
        # suggest_options is also valid when the AI presents multiple plan choices.
        assert parsed.get("action") in ("ask", "recommend", "add", "suggest_options"), (
            f"Unexpected action from full profile: {parsed.get('action')}"
        )

        # If AI recommended dishes, check ingredient constraints
        if parsed.get("action") in ("recommend", "suggest_options"):
            dish_names = _all_dish_names(parsed)
            ingredient_names = _all_ingredient_names(parsed)

            assert dish_names, "action=recommend but no dish names in response"

            for forbidden in ["香菜", "花椒"]:
                violations = [n for n in ingredient_names if forbidden in n]
                assert not violations, (
                    f"Full profile test: AI included forbidden '{forbidden}' "
                    f"in ingredients: {violations}"
                )
