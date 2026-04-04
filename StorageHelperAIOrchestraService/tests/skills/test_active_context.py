"""
Tests for the Memory Layer:
  - ActiveContext (app/modules/active_context.py)
  - ExtractIngredientsSkill (app/skills/plan_ahead/extract_ingredients.py)
  - _build_context active_context injection in PlanAheadPipeline
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.active_context import (
    _active_contexts,
    clear_active_context,
    get_active_context,
    update_active_context,
)
from app.skills.plan_ahead.extract_ingredients import ExtractIngredientsSkill


# ===========================================================================
# ActiveContext unit tests
# ===========================================================================

class TestActiveContextGetUpdateClear:
    """Basic CRUD and TTL behaviour."""

    def setup_method(self):
        _active_contexts.clear()

    def test_get_empty_returns_default(self):
        ctx = get_active_context(999)
        assert ctx["active_ingredients"] == []
        assert ctx["avoid_dishes"] == []
        assert ctx["avoid_ingredients"] == []
        assert ctx["avoid_cuisines"] == []
        assert ctx["target_date"] is None
        assert ctx["target_meal_type"] is None

    def test_update_adds_ingredients(self):
        update_active_context(1, add_ingredients=["牛棒骨"])
        ctx = get_active_context(1)
        assert "牛棒骨" in ctx["active_ingredients"]

    def test_update_union_merges_across_turns(self):
        """Ingredients accumulate across multiple update calls."""
        update_active_context(1, add_ingredients=["牛棒骨"])
        update_active_context(1, add_ingredients=["葱花"])
        ctx = get_active_context(1)
        assert "牛棒骨" in ctx["active_ingredients"]
        assert "葱花" in ctx["active_ingredients"]
        assert len(ctx["active_ingredients"]) == 2

    def test_update_deduplicates_case_insensitive(self):
        update_active_context(1, add_ingredients=["葱花"])
        update_active_context(1, add_ingredients=["葱花"])  # same, add again
        ctx = get_active_context(1)
        assert ctx["active_ingredients"].count("葱花") == 1

    def test_update_target_date_and_meal_type(self):
        update_active_context(1, target_date="2026-03-23", target_meal_type="dinner")
        ctx = get_active_context(1)
        assert ctx["target_date"] == "2026-03-23"
        assert ctx["target_meal_type"] == "dinner"

    def test_update_target_date_preserved_across_turns(self):
        """Target date set in turn 1 survives turn 2 that only adds ingredients."""
        update_active_context(1, target_date="2026-03-23", target_meal_type="dinner")
        update_active_context(1, add_ingredients=["葱花"])  # no date override
        ctx = get_active_context(1)
        assert ctx["target_date"] == "2026-03-23"
        assert ctx["target_meal_type"] == "dinner"

    def test_update_adds_session_avoidance_preferences(self):
        update_active_context(
            1,
            add_avoid_dishes=["番茄炒蛋"],
            add_avoid_ingredients=["香菜"],
            add_avoid_cuisines=["韩餐"],
        )
        ctx = get_active_context(1)
        assert "番茄炒蛋" in ctx["avoid_dishes"]
        assert "香菜" in ctx["avoid_ingredients"]
        assert "韩餐" in ctx["avoid_cuisines"]

    def test_clear_removes_context(self):
        update_active_context(1, add_ingredients=["牛棒骨"])
        assert clear_active_context(1) is True
        ctx = get_active_context(1)
        assert ctx["active_ingredients"] == []

    def test_clear_nonexistent_returns_false(self):
        assert clear_active_context(999) is False

    def test_ttl_expiry(self):
        """Context with expired TTL should return empty on next get."""
        update_active_context(1, add_ingredients=["牛棒骨"], ttl_minutes=0)
        # Force expiry by setting expires_at to the past
        _active_contexts[1]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        ctx = get_active_context(1)
        assert ctx["active_ingredients"] == []
        assert 1 not in _active_contexts  # cleaned up

    def test_multiple_users_isolated(self):
        update_active_context(1, add_ingredients=["牛棒骨"])
        update_active_context(2, add_ingredients=["鸡蛋"])
        assert "牛棒骨" in get_active_context(1)["active_ingredients"]
        assert "鸡蛋" in get_active_context(2)["active_ingredients"]
        assert "鸡蛋" not in get_active_context(1)["active_ingredients"]


# ===========================================================================
# ExtractIngredientsSkill unit tests
# ===========================================================================

GEMINI_URL = "http://fake-gemini/api"


class TestExtractIngredientsSkillKeywordFallback:
    """Test heuristic fallback without any LLM calls."""

    def _fallback(self, query: str) -> Dict[str, Any]:
        return ExtractIngredientsSkill._keyword_fallback(query)

    def test_have_ingredient_single(self):
        result = self._fallback("有个牛棒骨能做什么")
        assert "牛棒骨" in result["ingredients"]

    def test_have_ingredient_many(self):
        result = self._fallback("我有很多葱花")
        assert "葱花" in result["ingredients"]

    def test_topic_pattern(self):
        result = self._fallback("葱花的话能做啥")
        assert "葱花" in result["ingredients"]

    def test_multiple_ingredients(self):
        result = self._fallback("有鸡蛋和番茄做什么好")
        # At least one of the two should be caught by keyword fallback
        combined = result["ingredients"]
        assert len(combined) >= 1

    def test_no_ingredient_plain_query(self):
        result = self._fallback("我今天想吃海鲜")
        # 海鲜 is not something they "have", it's something they want
        assert "海鲜" not in result["ingredients"]

    def test_meal_type_dinner(self):
        result = self._fallback("明天晚饭有个牛棒骨能做什么味道")
        assert result["target_meal_type"] == "dinner"

    def test_meal_type_breakfast(self):
        result = self._fallback("早餐有个鸡蛋怎么做")
        assert result["target_meal_type"] == "breakfast"

    def test_meal_type_none_for_no_hint(self):
        result = self._fallback("葱花的话能做啥")
        assert result["target_meal_type"] is None

    def test_empty_query(self):
        result = self._fallback("")
        assert result["ingredients"] == []

    def test_deduplication(self):
        result = self._fallback("有个葱花，葱花的话能做啥")
        assert result["ingredients"].count("葱花") == 1


class TestExtractIngredientsSkillLLMPath:
    """Test LLM path with a mocked _call."""

    @pytest.fixture
    def skill(self):
        return ExtractIngredientsSkill(GEMINI_URL)

    @pytest.mark.asyncio
    async def test_llm_returns_ingredients(self, skill):
        mock_response = '{"ingredients":["牛棒骨","葱花"],"target_date":null,"target_meal_type":"dinner"}'
        with patch.object(ExtractIngredientsSkill, "_call", new=AsyncMock(return_value=mock_response)):
            result = await skill.execute("明天晚饭有个牛棒骨能做什么，我也有葱花")
        assert "牛棒骨" in result["ingredients"]
        assert "葱花" in result["ingredients"]
        assert result["target_meal_type"] == "dinner"

    @pytest.mark.asyncio
    async def test_llm_returns_empty_for_preference(self, skill):
        mock_response = '{"ingredients":[],"target_date":null,"target_meal_type":null}'
        with patch.object(ExtractIngredientsSkill, "_call", new=AsyncMock(return_value=mock_response)):
            result = await skill.execute("我今天想吃海鲜")
        assert result["ingredients"] == []

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_keyword(self, skill):
        with patch.object(ExtractIngredientsSkill, "_call", new=AsyncMock(return_value=None)):
            result = await skill.execute("有个牛棒骨能做什么")
        # Keyword fallback should catch "有个牛棒骨"
        assert "牛棒骨" in result["ingredients"]

    @pytest.mark.asyncio
    async def test_llm_bad_json_falls_back(self, skill):
        with patch.object(ExtractIngredientsSkill, "_call", new=AsyncMock(return_value="not json")):
            result = await skill.execute("葱花的话能做啥")
        assert isinstance(result["ingredients"], list)


# ===========================================================================
# Memory Layer integration: multi-turn ingredient accumulation
# ===========================================================================

class TestMultiTurnIngredientAccumulation:
    """Simulate the cross-turn use-case that triggered the memory layer."""

    def setup_method(self):
        _active_contexts.clear()

    def test_beef_knuckle_then_scallion_accumulate(self):
        """
        Turn 1: user says they have 牛棒骨
        Turn 2: user says they also have 葱花
        → active context should contain both
        """
        # Turn 1
        update_active_context(1, add_ingredients=["牛棒骨"], target_meal_type="dinner")
        # Turn 2
        update_active_context(1, add_ingredients=["葱花"])
        ctx = get_active_context(1)
        assert "牛棒骨" in ctx["active_ingredients"]
        assert "葱花" in ctx["active_ingredients"]
        assert ctx["target_meal_type"] == "dinner"  # preserved from turn 1


# ===========================================================================
# _build_context active_context injection test
# ===========================================================================

class TestBuildContextActiveContextInjection:
    """Verify that PlanAheadPipeline._build_context injects the active context section."""

    def _make_pipeline(self):
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
        return PlanAheadPipeline(gemini_api_url=GEMINI_URL)

    def test_active_context_section_injected(self):
        pipeline = self._make_pipeline()
        ctx = pipeline._build_context(
            state={},
            user_timezone=None,
            active_context={
                "active_ingredients": ["牛棒骨", "葱花"],
                "target_date": "2026-03-23",
                "target_meal_type": "dinner",
                "updated_at": None,
            },
        )
        assert "ACTIVE CONVERSATION CONTEXT" in ctx
        assert "牛棒骨" in ctx
        assert "葱花" in ctx
        assert "2026-03-23" in ctx

    def test_empty_active_context_not_injected(self):
        pipeline = self._make_pipeline()
        ctx = pipeline._build_context(
            state={},
            user_timezone=None,
            active_context={"active_ingredients": [], "target_date": None, "target_meal_type": None, "updated_at": None},
        )
        assert "ACTIVE CONVERSATION CONTEXT" not in ctx

    def test_session_avoidance_section_injected(self):
        pipeline = self._make_pipeline()
        ctx = pipeline._build_context(
            state={},
            user_timezone=None,
            active_context={
                "active_ingredients": [],
                "avoid_dishes": ["番茄炒蛋"],
                "avoid_ingredients": ["香菜"],
                "avoid_cuisines": ["韩餐"],
                "target_date": None,
                "target_meal_type": None,
                "updated_at": None,
            },
        )
        assert "SESSION AVOIDANCE PREFERENCES" in ctx
        assert "番茄炒蛋" in ctx
        assert "香菜" in ctx
        assert "韩餐" in ctx

    def test_none_active_context_not_injected(self):
        pipeline = self._make_pipeline()
        ctx = pipeline._build_context(
            state={},
            user_timezone=None,
            active_context=None,
        )
        assert "ACTIVE CONVERSATION CONTEXT" not in ctx


class TestSessionAvoidanceExtraction:
    """Verify cuisine-level avoidance is not duplicated as dish-level avoidance."""

    def test_cuisine_avoidance_not_misclassified_as_dish(self):
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline

        parsed = PlanAheadPipeline._extract_session_avoidance_preferences("最近不想吃韩餐")
        assert "韩餐" in parsed["avoid_cuisines"]
        assert "韩餐" not in parsed["avoid_dishes"]
