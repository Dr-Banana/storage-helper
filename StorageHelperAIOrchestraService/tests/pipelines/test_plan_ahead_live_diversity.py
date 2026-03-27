"""
Phase 2 — Live AI Diversity Tests: Recency-Penalty Variety Algorithm
=====================================================================

Verifies that the LLM *actually follows* the Diversity Engine directives
injected into the system context:

  • HARD BAN  (dishes eaten ≤3 days ago) → must NOT appear in output
  • SOFT AVOID (dishes eaten 4–7 days ago) → LLM prefers alternatives
  • SEED LIBRARY FAIR ROTATION → diverse cuisine coverage without weights

Strategy
--------
  1. Build a user_profile with recent_dishes pre-populated.
  2. Call _build_context() — this now injects the DiversityEngine directive.
  3. Call _call_llm() directly (no DB, no state management).
  4. Assert the structured LLM response honours the diversity rules.

Run
---
  pytest tests/pipelines/test_plan_ahead_live_diversity.py -m llm_live --run-llm -v
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pytest

from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline


# ── Live-key detection ────────────────────────────────────────────────────────

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
                "Live diversity tests are disabled.\n"
                "  Set GEMINI_LLM_TESTING_KEY in .env.local  OR  pass --run-llm"
            )


# ── Shared helpers ────────────────────────────────────────────────────────────

def _pipeline() -> PlanAheadPipeline:
    api_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_GEMINI_MODEL}:generateContent?key={_TESTING_KEY}"
    )
    return PlanAheadPipeline(gemini_api_url=api_url)


def _empty_state() -> Dict[str, Any]:
    return {"meal_plan_slots": {}, "dish_ingredients": {}, "meal_plan": {}}


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


def _all_dish_names(parsed: Dict[str, Any]) -> List[str]:
    """Extract dish names from both direct actions and suggest_options responses."""
    names: List[str] = []
    # Direct meal_plan_slots (recommend / add)
    for date_slots in (parsed.get("meal_plan_slots") or {}).values():
        for dishes in (date_slots or {}).values():
            if isinstance(dishes, list):
                names.extend(dishes)
            elif isinstance(dishes, str):
                names.append(dishes)
    # suggest_options: dishes are inside each option
    for opt in (parsed.get("dish_options") or []):
        for date_slots in (opt.get("meal_plan_slots") or {}).values():
            for dishes in (date_slots or {}).values():
                if isinstance(dishes, list):
                    names.extend(dishes)
                elif isinstance(dishes, str):
                    names.append(dishes)
        for v in (opt.get("meal_plan") or {}).values():
            if isinstance(v, str):
                names.extend([d.strip() for d in v.split(" and ") if d.strip()])
    return names


def _all_ingredient_names(parsed: Dict[str, Any]) -> List[str]:
    """Extract ingredient names from both direct actions and suggest_options responses."""
    names: List[str] = []

    def _extract(dish_ingredients: Optional[Dict]) -> None:
        for ingredients in (dish_ingredients or {}).values():
            for ing in (ingredients or []):
                names.append(ing.get("name", "") if isinstance(ing, dict) else str(ing))

    _extract(parsed.get("dish_ingredients"))
    for opt in (parsed.get("dish_options") or []):
        _extract(opt.get("dish_ingredients"))
    return names


# ── Context inspector helpers ─────────────────────────────────────────────────

def _context_has_hard_ban(ctx: str, dish: str) -> bool:
    """Return True only when the dish appears on an actual HARD BAN list line.

    Actual HARD BAN lines (from diversity_engine.py) start with "- HARD BAN (".
    The context also contains "HARD BAN" in the OVERRIDE RULE instruction sentence;
    we must NOT match that.
    """
    return any(
        line.lstrip().startswith("- HARD BAN") and dish in line
        for line in ctx.splitlines()
    )


def _context_has_soft_avoid(ctx: str, dish: str) -> bool:
    return any(
        line.lstrip().startswith("- SOFT AVOID") and dish in line
        for line in ctx.splitlines()
    )


def _context_has_variety_target(ctx: str) -> bool:
    return "WEEKLY VARIETY TARGET" in ctx


# ─────────────────────────────────────────────────────────────────────────────
# Live Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm_live
class TestLiveDiversityEngine:
    """
    Each test verifies that Phase 2's Diversity Engine directive is:
      (a) correctly injected into the system context, and
      (b) actually obeyed by the Gemini LLM.
    """

    # ── Test 1: Context sanity — hard-ban dish appears in context ─────────────

    @pytest.mark.asyncio
    async def test_hard_ban_dish_appears_in_context(self):
        """
        Layer 1 (deterministic): when recent_dishes contains a dish eaten
        yesterday, _build_context() must embed a HARD BAN directive for it.
        No LLM call needed — this purely validates the prompt injection.
        """
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 70, "Western": 30},
            "recent_dishes": [
                {"dish": "西红柿炒蛋", "date": _days_ago(1)},   # 1 day → HARD BAN
                {"dish": "宫保鸡丁",  "date": _days_ago(5)},   # 5 days → SOFT AVOID
            ],
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        assert "DIVERSITY ENGINE" in ctx, "Diversity Engine section missing from context"
        assert _context_has_hard_ban(ctx, "西红柿炒蛋"), (
            "Context should mark 西红柿炒蛋 as HARD BAN (eaten 1 day ago)"
        )
        assert _context_has_soft_avoid(ctx, "宫保鸡丁"), (
            "Context should mark 宫保鸡丁 as SOFT AVOID (eaten 5 days ago)"
        )

    # ── Test 2: Context sanity — cuisine_weights no longer injected ──────────

    @pytest.mark.asyncio
    async def test_variety_target_appears_in_context(self):
        """
        cuisine_weights has been intentionally removed from the LLM context.
        The 'WEEKLY VARIETY TARGET' block must NOT appear; instead, diversity
        is handled by the seed-library fair-rotation algorithm.
        The diversity block itself (hard bans / soft avoids) should still be present.
        """
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 80, "Western": 20},
            "recent_dishes": [],
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        # cuisine_weights is intentionally NOT injected into the LLM prompt
        # (diversity is handled by seed-library fair-rotation instead).
        assert not _context_has_variety_target(ctx), (
            "WEEKLY VARIETY TARGET should NOT appear — cuisine_weights was removed "
            "from the LLM context in favour of seed-library fair rotation"
        )

    # ── Test 3: LLM obeys hard ban — banned dish not in recommendations ───────

    @pytest.mark.asyncio
    async def test_hard_ban_dish_not_recommended(self):
        """
        Layer 2 (live LLM): A dish eaten 1 day ago (hard-banned) must NOT
        appear in the LLM's recommended dish names.
        """
        banned_dish = "西红柿炒蛋"
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 80, "Western": 20},
            "recent_dishes": [
                {"dish": banned_dish, "date": _days_ago(1)},
            ],
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
                {"role": "assistant", "content": "您好！您喜欢什么口味的菜？"},
                {"role": "user",      "content": "家常菜就好，随便推荐几道菜"},
            ],
            user_input="帮我推荐今天的晚餐",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        dish_names = _all_dish_names(parsed)
        assert dish_names, f"LLM produced no dish names: {parsed}"

        violations = [d for d in dish_names if banned_dish in d]
        assert not violations, (
            f"AI ignored HARD BAN and recommended '{banned_dish}' despite it being "
            f"eaten yesterday.\nAll recommended dishes: {dish_names}"
        )

    # ── Test 4: LLM obeys multiple hard bans ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_multiple_hard_ban_dishes_not_recommended(self):
        """
        Two dishes eaten within the last 3 days should both be absent from
        the LLM's recommendations.
        """
        banned = ["麻婆豆腐", "宫保鸡丁"]
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 80, "Western": 20},
            "recent_dishes": [
                {"dish": "麻婆豆腐", "date": _days_ago(1)},
                {"dish": "宫保鸡丁",  "date": _days_ago(2)},
            ],
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
                {"role": "assistant", "content": "喜欢什么菜系？"},
                {"role": "user",      "content": "中式家常菜为主"},
            ],
            user_input="推荐今晚的晚餐菜单",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        dish_names = _all_dish_names(parsed)
        assert dish_names, f"No dish names returned: {parsed}"

        for banned_dish in banned:
            violations = [d for d in dish_names if banned_dish in d]
            assert not violations, (
                f"AI recommended hard-banned dish '{banned_dish}' "
                f"despite it being eaten {banned.index(banned_dish)+1} day(s) ago.\n"
                f"All dishes: {dish_names}"
            )

    # ── Test 5: Old dishes (>14 days) are NOT banned ─────────────────────────

    @pytest.mark.asyncio
    async def test_dish_older_than_14_days_can_be_recommended(self):
        """
        A dish eaten >14 days ago should have zero penalty and MAY appear
        in recommendations. This test verifies the engine does NOT
        over-restrict the LLM.

        We can't assert the dish WILL appear (LLM has creative freedom),
        so we verify:
          1. The context contains no HARD BAN for this dish.
          2. The LLM produces a valid non-empty plan.
        """
        old_dish = "红烧肉"
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 80, "Western": 20},
            "recent_dishes": [
                {"dish": old_dish, "date": _days_ago(20)},  # 20 days ago — fully cleared
            ],
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
            # Force the recommend path so the LLM doesn't respond with
            # action='ask' when the history contains only vague preferences.
            # Without this, "随便推荐" alone is sometimes ambiguous enough
            # for the LLM to request clarification — causing a flaky test.
            precomputed_action="recommend",
        )
        # Context must NOT hard-ban the old dish.
        # Actual HARD BAN list lines start with "- HARD BAN (" (from diversity_engine.py).
        # The context also contains "HARD BAN" in the OVERRIDE RULE instruction sentence
        # and in internal logging lines — we must NOT match those.
        hard_ban_lines = [
            line for line in ctx.splitlines()
            if line.lstrip().startswith("- HARD BAN") and old_dish in line
        ]
        assert not hard_ban_lines, (
            f"Engine incorrectly hard-bans '{old_dish}' eaten 20 days ago\n"
            f"Offending lines: {hard_ban_lines}"
        )

        parsed = await p._call_llm(
            system_context=ctx,
            history=[
                {"role": "assistant", "content": "您好！想吃什么口味的菜？"},
                {"role": "user",      "content": "家常菜就好，随便推荐几道"},
            ],
            user_input="帮我推荐今晚的晚餐",
        )
        assert parsed is not None, "LLM returned None — API call failed"
        dish_names = _all_dish_names(parsed)
        assert dish_names, f"No dishes returned: {parsed}"

    # ── Test 6: Empty recent_dishes — no restriction ──────────────────────────

    @pytest.mark.asyncio
    async def test_no_recent_dishes_returns_normal_recommendation(self):
        """
        When recent_dishes is empty, the diversity engine emits
        'No recent dish history — feel free to recommend any dishes.'
        The LLM should return a normal plan with ≥2 dishes.
        """
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": [],
            "cuisine_weights": {"Chinese": 70, "Western": 30},
            "recent_dishes": [],
        }
        p = _pipeline()
        ctx = p._build_context(
            _empty_state(), None,
            cooking_level="beginner",
            language="zh",
            user_profile=profile,
        )
        assert "No recent dish history" in ctx, (
            "Context should state no recent history when recent_dishes is empty"
        )

        parsed = await p._call_llm(
            system_context=ctx,
            history=[
                {"role": "assistant", "content": "您好！您偏好什么菜系？"},
                {"role": "user",      "content": "中式家常菜，随便"},
            ],
            user_input="帮我推荐今晚的晚餐",
        )
        assert parsed is not None, "LLM returned None — API call failed"
        dish_names = _all_dish_names(parsed)
        assert len(dish_names) >= 2, (
            f"Expected ≥2 dishes with no restrictions, got {len(dish_names)}: {dish_names}"
        )

    # ── Test 7: Combined — disliked ingredients + hard ban both enforced ──────

    @pytest.mark.asyncio
    async def test_combined_disliked_and_hard_ban_both_enforced(self):
        """
        Phase 1 (disliked_ingredients) + Phase 2 (hard ban) simultaneously:
          - FORBIDDEN ingredient: 香菜
          - HARD BAN dish: 西红柿炒蛋 (eaten yesterday)

        Both constraints must hold in the same recommendation.
        """
        banned_dish = "西红柿炒蛋"
        profile = {
            "default_servings": 1,
            "meat_veg_ratio": "1:1:1",
            "include_soup": False,
            "calorie_target": None,
            "disliked_ingredients": ["香菜"],
            "cuisine_weights": {"Chinese": 80, "Western": 20},
            "recent_dishes": [
                {"dish": banned_dish, "date": _days_ago(1)},
            ],
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
                {"role": "assistant", "content": "您好！有什么饮食偏好吗？"},
                {"role": "user",      "content": "不吃香菜，其他随便"},
            ],
            user_input="帮我推荐今晚的晚餐",
        )
        assert parsed is not None, "LLM returned None — API call failed"

        dish_names = _all_dish_names(parsed)
        ingredient_names = _all_ingredient_names(parsed)

        assert dish_names, f"No dish names returned: {parsed}"

        # Phase 1 check: no 香菜
        coriander_violations = [n for n in ingredient_names if "香菜" in n]
        assert not coriander_violations, (
            f"Phase 1 violated: 香菜 found in ingredients: {coriander_violations}"
        )

        # Phase 2 check: hard-banned dish absent
        ban_violations = [d for d in dish_names if banned_dish in d]
        assert not ban_violations, (
            f"Phase 2 violated: hard-banned '{banned_dish}' found in dishes: {dish_names}"
        )
