# -*- coding: utf-8 -*-
"""
Unit tests for plan_ahead skills.

All tests mock the underlying LLM call (_call) so they run offline
without touching the Gemini API.

Test groups
-----------
TestClassifyDishIntentSkill  — prompt content + parsing + category guard
TestClassifyMealActionSkill  — fast-path overrides + LLM routing + fallback
TestClassifyDateConfirmationSkill — L2 guard + LLM routing + date parsing
TestInitPlanningQueueSkill   — slot expansion + meal_times handling
TestSkillIntegration         — pipeline uses skills (smoke test)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.skills.plan_ahead import (
    ClassifyDishIntentSkill,
    ClassifyMealActionSkill,
    ClassifyDateConfirmationSkill,
    InitPlanningQueueSkill,
)

URL = "http://fake-gemini-api"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _skill_with_mock_call(skill_cls, raw_response: str):
    """Return a skill instance whose _call() always returns raw_response."""
    skill = skill_cls(URL)
    skill._call = AsyncMock(return_value=raw_response)
    return skill


def _skill_with_mock_call_none(skill_cls):
    """Return a skill whose _call() returns None (simulates LLM failure)."""
    skill = skill_cls(URL)
    skill._call = AsyncMock(return_value=None)
    return skill


# ─────────────────────────────────────────────────────────────────────────────
# ClassifyDishIntentSkill
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyDishIntentSkill:

    # ── SKILL_PROMPT content ──────────────────────────────────────────────

    def test_skill_prompt_contains_critical_rule(self):
        assert "CRITICAL" in ClassifyDishIntentSkill.SKILL_PROMPT
        assert "category" in ClassifyDishIntentSkill.SKILL_PROMPT.lower()

    def test_skill_prompt_lists_seafood_as_recommend(self):
        assert "海鲜" in ClassifyDishIntentSkill.SKILL_PROMPT

    def test_skill_prompt_distinguishes_explicit_from_recommend(self):
        prompt = ClassifyDishIntentSkill.SKILL_PROMPT
        assert "EXPLICIT" in prompt
        assert "RECOMMEND" in prompt

    def test_skill_prompt_has_correct_examples(self):
        prompt = ClassifyDishIntentSkill.SKILL_PROMPT
        assert "萝卜炖牛腩" in prompt   # explicit example
        assert "我今天晚上想吃个海鲜" in prompt  # category → RECOMMEND example

    # ── LLM parsing ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_explicit_dish_parsed_correctly(self):
        skill = _skill_with_mock_call(
            ClassifyDishIntentSkill,
            '{"intent":"EXPLICIT","dishes":["红烧肉"]}'
        )
        result = await skill.execute("加个红烧肉", [])
        assert result["is_explicit"] is True
        assert result["dishes"] == ["红烧肉"]
        assert result["intent"] == "EXPLICIT"

    @pytest.mark.asyncio
    async def test_food_category_returns_recommend(self):
        skill = _skill_with_mock_call(
            ClassifyDishIntentSkill,
            '{"intent":"RECOMMEND","dishes":[]}'
        )
        result = await skill.execute("我今天晚上想吃个海鲜", [])
        assert result["is_explicit"] is False
        assert result["dishes"] == []
        assert result["intent"] == "RECOMMEND"

    @pytest.mark.asyncio
    async def test_category_word_as_dish_is_downgraded(self):
        """If LLM incorrectly returns a category word as a dish, skill downgrades to RECOMMEND."""
        skill = _skill_with_mock_call(
            ClassifyDishIntentSkill,
            '{"intent":"EXPLICIT","dishes":["海鲜"]}'
        )
        result = await skill.execute("我想吃个海鲜", [])
        assert result["is_explicit"] is False
        assert result["dishes"] == []
        assert result["intent"] == "RECOMMEND"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_keyword(self):
        skill = _skill_with_mock_call_none(ClassifyDishIntentSkill)
        result = await skill.execute("加个小笼包", [])
        # Keyword fallback detects "加个" → EXPLICIT_HINT (no dish name extracted)
        assert result["is_explicit"] is False
        assert result["intent"] in ("UNKNOWN", "EXPLICIT_HINT")

    @pytest.mark.asyncio
    async def test_preference_辣_returns_recommend(self):
        skill = _skill_with_mock_call(
            ClassifyDishIntentSkill,
            '{"intent":"RECOMMEND","dishes":[]}'
        )
        result = await skill.execute("想吃辣的东西", [])
        assert result["intent"] == "RECOMMEND"
        assert result["is_explicit"] is False

    @pytest.mark.asyncio
    async def test_preference_日式_returns_recommend(self):
        skill = _skill_with_mock_call(
            ClassifyDishIntentSkill,
            '{"intent":"RECOMMEND","dishes":[]}'
        )
        result = await skill.execute("今晚整个日式的", [])
        assert result["intent"] == "RECOMMEND"

    # ── "Replace X with similar" → RECOMMEND (prompt rule) ───────────────

    def test_prompt_contains_replace_rule(self):
        """Prompt must have explicit guidance for 'replace X with another dish' → RECOMMEND."""
        prompt = ClassifyDishIntentSkill.SKILL_PROMPT
        assert "换成另一道" in prompt or "SOURCE" in prompt or "REPLACE" in prompt.upper()

    @pytest.mark.asyncio
    async def test_replace_with_similar_returns_recommend(self):
        """'把X换成另一道类似的菜' — X is the dish being removed, not added → RECOMMEND."""
        skill = _skill_with_mock_call(
            ClassifyDishIntentSkill,
            '{"intent":"RECOMMEND","dishes":[]}'
        )
        result = await skill.execute("把'萝卜牛骨汤'换成另一道类似的菜，其他菜品保持不变", [])
        assert result["intent"] == "RECOMMEND"
        assert result["is_explicit"] is False
        assert result["dishes"] == []

    @pytest.mark.asyncio
    async def test_replace_dish_variant2_returns_recommend(self):
        skill = _skill_with_mock_call(
            ClassifyDishIntentSkill,
            '{"intent":"RECOMMEND","dishes":[]}'
        )
        result = await skill.execute("请把'牛大棒骨汤'换成另一道类似的菜", [])
        assert result["intent"] == "RECOMMEND"
        assert result["is_explicit"] is False

    @pytest.mark.asyncio
    async def test_specific_replacement_dish_named_is_explicit(self):
        """But 'replace X WITH 照烧鸡腿' — the target IS explicitly named → EXPLICIT."""
        skill = _skill_with_mock_call(
            ClassifyDishIntentSkill,
            '{"intent":"EXPLICIT","dishes":["照烧鸡腿"]}'
        )
        result = await skill.execute("换成照烧鸡腿", [])
        assert result["intent"] == "EXPLICIT"
        assert "照烧鸡腿" in result["dishes"]


# ─────────────────────────────────────────────────────────────────────────────
# Explicit-dish filter safety net (logic mirrored from plan_ahead_pipeline.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestExplicitDishFilterSafetyNet:
    """
    Verify the 'source dish absent from new plan' safety-net logic.

    When the user says "把X换成另一道菜", X is the dish to be REMOVED.
    The main LLM generates [replacement, side, staple] — X is NOT in this list.
    The filter must detect this and SKIP filtering so the replacement is kept.
    """

    @staticmethod
    def _any_requested_in_output(requested: list, new_plan_dishes: list) -> bool:
        """Mirror of the safety-net logic in plan_ahead_pipeline.py."""
        all_new = {d.lower().replace(" ", "") for d in new_plan_dishes}
        return any(
            req.lower().replace(" ", "") in d or d in req.lower().replace(" ", "")
            for req in requested
            for d in all_new
        )

    def test_replacement_scenario_skips_filter(self):
        """
        User: "把萝卜牛骨汤换成另一道菜"
        requested = ['萝卜牛骨汤']
        LLM output = ['清炖牛骨汤', '蚝油生菜', '米饭']
        → 萝卜牛骨汤 is NOT in output → filter SKIPPED.
        """
        requested = ["萝卜牛骨汤"]
        new_dishes = ["清炖牛骨汤", "蚝油生菜", "米饭"]
        assert not self._any_requested_in_output(requested, new_dishes), (
            "Filter should be SKIPPED (requested dish absent from new plan = source replacement)"
        )

    def test_add_scenario_applies_filter(self):
        """
        User: "加个红烧肉"
        requested = ['红烧肉']
        LLM output = ['红烧肉', '清炒卷心菜', '米饭']   # 红烧肉 IS in output
        → filter APPLIES (keeps 红烧肉, strips extras).
        """
        requested = ["红烧肉"]
        new_dishes = ["红烧肉", "清炒卷心菜", "米饭"]
        assert self._any_requested_in_output(requested, new_dishes), (
            "Filter should APPLY (requested dish present in new plan = user adding it)"
        )

    def test_fuzzy_match_partial_name_applies_filter(self):
        """Partial-name match (e.g. '牛骨汤' in '萝卜牛骨汤') still counts as present."""
        requested = ["牛骨汤"]
        new_dishes = ["萝卜牛骨汤", "白灼菜心", "米饭"]
        assert self._any_requested_in_output(requested, new_dishes)

    def test_completely_different_dishes_skips_filter(self):
        """Requested dish = '宫保鸡丁', new plan = ['番茄炒蛋', '米饭'] → skip."""
        requested = ["宫保鸡丁"]
        new_dishes = ["番茄炒蛋", "米饭"]
        assert not self._any_requested_in_output(requested, new_dishes)

    def test_second_replace_round_skips_filter(self):
        """
        Second replace: user says "把清炖牛骨汤换成另一道菜"
        requested = ['清炖牛骨汤']
        LLM output = ['红烧牛腩', '蚝油生菜', '米饭']
        → '清炖牛骨汤' absent from output → SKIP.
        """
        requested = ["清炖牛骨汤"]
        new_dishes = ["红烧牛腩", "蚝油生菜", "米饭"]
        assert not self._any_requested_in_output(requested, new_dishes)

    def test_multiple_requested_all_absent_skips(self):
        """All requested dishes absent → skip."""
        requested = ["萝卜牛骨汤", "蚝油生菜"]
        new_dishes = ["清炖牛骨汤", "炒空心菜", "米饭"]
        assert not self._any_requested_in_output(requested, new_dishes)

    def test_one_requested_present_applies_filter(self):
        """If at least ONE requested dish is present → apply filter normally."""
        requested = ["萝卜牛骨汤", "蚝油生菜"]
        # 蚝油生菜 IS in new plan (user kept it), 萝卜牛骨汤 is being replaced
        new_dishes = ["清炖牛骨汤", "蚝油生菜", "米饭"]
        assert self._any_requested_in_output(requested, new_dishes)


# ─────────────────────────────────────────────────────────────────────────────
# ClassifyMealActionSkill
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyMealActionSkill:

    # ── Fast-path overrides (no LLM call) ────────────────────────────────

    @pytest.mark.asyncio
    async def test_explicit_dish_fast_path_returns_add(self):
        skill = ClassifyMealActionSkill(URL)
        skill._call = AsyncMock(side_effect=AssertionError("should not call LLM"))
        result = await skill.execute("加个红烧肉", [], is_explicit_dish=True)
        assert result["action"] == "add"

    @pytest.mark.asyncio
    async def test_in_queue_fast_path_returns_recommend(self):
        skill = ClassifyMealActionSkill(URL)
        skill._call = AsyncMock(side_effect=AssertionError("should not call LLM"))
        result = await skill.execute("选第一个", [], is_explicit_dish=False, pipeline_phase="in_queue")
        assert result["action"] == "recommend"

    @pytest.mark.asyncio
    async def test_awaiting_clarification_fast_path_returns_suggest_options(self):
        skill = ClassifyMealActionSkill(URL)
        skill._call = AsyncMock(side_effect=AssertionError("should not call LLM"))
        result = await skill.execute("来点日式的", [], pipeline_phase="awaiting_clarification")
        assert result["action"] == "suggest_options"

    # ── LLM routing ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_food_category_routes_to_suggest_options(self):
        """The key bug fix: 我今天晚上想吃个海鲜 → suggest_options."""
        skill = _skill_with_mock_call(
            ClassifyMealActionSkill,
            '{"action":"suggest_options","reason":"user provided food category: 海鲜"}'
        )
        result = await skill.execute("我今天晚上想吃个海鲜", [])
        assert result["action"] == "suggest_options"

    @pytest.mark.asyncio
    async def test_no_preference_routes_to_ask(self):
        skill = _skill_with_mock_call(
            ClassifyMealActionSkill,
            '{"action":"ask","reason":"no preference given"}'
        )
        result = await skill.execute("今天吃什么", [])
        assert result["action"] == "ask"

    @pytest.mark.asyncio
    async def test_selection_routes_to_recommend(self):
        skill = _skill_with_mock_call(
            ClassifyMealActionSkill,
            '{"action":"recommend","reason":"user selected an option"}'
        )
        result = await skill.execute("选方案2", [])
        assert result["action"] == "recommend"

    # ── Fallback ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_failure_seafood_uses_keyword_fallback(self):
        skill = _skill_with_mock_call_none(ClassifyMealActionSkill)
        result = await skill.execute("我今晚想吃个海鲜", [])
        assert result["action"] == "suggest_options"

    @pytest.mark.asyncio
    async def test_llm_failure_no_preference_defaults_to_suggest_options(self):
        skill = _skill_with_mock_call_none(ClassifyMealActionSkill)
        result = await skill.execute("今天晚上吃什么呢", [])
        assert result["action"] == "suggest_options"

    @pytest.mark.asyncio
    async def test_llm_failure_selection_returns_recommend(self):
        skill = _skill_with_mock_call_none(ClassifyMealActionSkill)
        result = await skill.execute("选方案1", [])
        assert result["action"] == "recommend"

    @pytest.mark.asyncio
    async def test_invalid_llm_response_uses_fallback(self):
        skill = _skill_with_mock_call(ClassifyMealActionSkill, '{"action":"invalid_action"}')
        result = await skill.execute("我想吃个海鲜", [])
        # Invalid action should trigger fallback: 海鲜 keyword → suggest_options
        assert result["action"] in ("suggest_options", "ask")

    # ── SKILL_PROMPT content ──────────────────────────────────────────────

    def test_skill_prompt_classifies_seafood_as_suggest_options(self):
        prompt = ClassifyMealActionSkill.SKILL_PROMPT
        assert "suggest_options" in prompt
        assert "海鲜" in prompt

    def test_skill_prompt_has_all_actions(self):
        prompt = ClassifyMealActionSkill.SKILL_PROMPT
        for action in ("ask", "suggest_options", "add", "recommend"):
            assert action in prompt

    def test_skill_prompt_defines_preference_rule(self):
        prompt = ClassifyMealActionSkill.SKILL_PROMPT
        assert "preference" in prompt.lower() or "category" in prompt.lower()


# ─────────────────────────────────────────────────────────────────────────────
# ClassifyDateConfirmationSkill
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyDateConfirmationSkill:

    # ── Layer-2 fast-path ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_affirmative_no_llm_call(self):
        skill = ClassifyDateConfirmationSkill(URL)
        skill._call = AsyncMock(side_effect=AssertionError("should not call LLM"))
        result = await skill.execute("好的", ["2026-03-25|dinner"], "2026-03-25")
        assert result["intent"] == "confirmed"

    @pytest.mark.asyncio
    async def test_ok_english_affirm_no_llm_call(self):
        skill = ClassifyDateConfirmationSkill(URL)
        skill._call = AsyncMock(side_effect=AssertionError("should not call LLM"))
        result = await skill.execute("ok", ["2026-03-25|lunch"], "2026-03-25")
        assert result["intent"] == "confirmed"

    # ── LLM routing ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_travel_mention_returns_unclear(self):
        skill = _skill_with_mock_call(
            ClassifyDateConfirmationSkill,
            '{"intent":"unclear"}'
        )
        result = await skill.execute(
            "我明天要去旅游",
            ["2026-03-25|dinner", "2026-03-26|dinner"],
            "2026-03-25",
        )
        assert result["intent"] == "unclear"

    @pytest.mark.asyncio
    async def test_explicit_date_correction_returns_corrected(self):
        skill = _skill_with_mock_call(
            ClassifyDateConfirmationSkill,
            '{"intent":"corrected","new_start":"2026-03-26","new_end":"2026-03-26"}'
        )
        result = await skill.execute(
            "不对，改成明天",
            ["2026-03-25|dinner"],
            "2026-03-25",
        )
        assert result["intent"] == "corrected"
        assert "2026-03-26" in result["new_dates"]

    @pytest.mark.asyncio
    async def test_meal_scope_change_returns_corrected(self):
        skill = _skill_with_mock_call(
            ClassifyDateConfirmationSkill,
            '{"intent":"corrected","new_meal_times":null}'
        )
        result = await skill.execute(
            "一整天",
            ["2026-03-25|dinner"],
            "2026-03-25",
        )
        assert result["intent"] == "corrected"
        assert result["new_meal_times"] is None

    @pytest.mark.asyncio
    async def test_llm_failure_returns_unclear(self):
        skill = _skill_with_mock_call_none(ClassifyDateConfirmationSkill)
        result = await skill.execute("...", ["2026-03-25|dinner"], "2026-03-25")
        assert result["intent"] == "unclear"

    # ── SKILL_PROMPT content ──────────────────────────────────────────────

    def test_skill_prompt_has_travel_example(self):
        prompt = ClassifyDateConfirmationSkill.SKILL_PROMPT
        assert "旅游" in prompt or "traveling" in prompt.lower()

    def test_skill_prompt_distinguishes_mention_vs_correction(self):
        prompt = ClassifyDateConfirmationSkill.SKILL_PROMPT
        assert "merely MENTIONS" in prompt or "ACTIVELY changing" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# InitPlanningQueueSkill
# ─────────────────────────────────────────────────────────────────────────────

class TestInitPlanningQueueSkill:

    @pytest.mark.asyncio
    async def test_single_dinner_slot(self):
        skill = _skill_with_mock_call(
            InitPlanningQueueSkill,
            '{"has_planning_intent":true,"start":"2026-03-22","end":"2026-03-22","meal_times":["dinner"]}'
        )
        result = await skill.execute("我今天晚上想吃个海鲜", "2026-03-22 (Sunday)")
        assert result["has_planning_intent"] is True
        assert result["slots"] == ["2026-03-22|dinner"]

    @pytest.mark.asyncio
    async def test_no_planning_intent(self):
        skill = _skill_with_mock_call(
            InitPlanningQueueSkill,
            '{"has_planning_intent":false}'
        )
        result = await skill.execute("加个红烧肉", "2026-03-22 (Sunday)")
        assert result["has_planning_intent"] is False
        assert result["slots"] == []

    @pytest.mark.asyncio
    async def test_multi_day_expands_all_meals(self):
        skill = _skill_with_mock_call(
            InitPlanningQueueSkill,
            '{"has_planning_intent":true,"start":"2026-03-22","end":"2026-03-23","meal_times":null}'
        )
        result = await skill.execute("帮我规划明后两天三餐", "2026-03-22 (Sunday)")
        assert result["has_planning_intent"] is True
        slots = result["slots"]
        assert "2026-03-22|breakfast" in slots
        assert "2026-03-22|lunch" in slots
        assert "2026-03-22|dinner" in slots
        assert "2026-03-23|breakfast" in slots
        assert len(slots) == 6

    @pytest.mark.asyncio
    async def test_llm_failure_returns_no_intent(self):
        skill = _skill_with_mock_call_none(InitPlanningQueueSkill)
        result = await skill.execute("今天吃什么", "2026-03-22 (Sunday)")
        assert result["has_planning_intent"] is False
        assert result["slots"] == []

    def test_skill_prompt_has_dish_not_planning_rule(self):
        from app.skills.plan_ahead.init_planning_queue import InitPlanningQueueSkill as S
        assert "HAS DISH = NOT PLANNING" in S.SKILL_PROMPT_TEMPLATE

    def test_skill_prompt_has_category_planning_rule(self):
        from app.skills.plan_ahead.init_planning_queue import InitPlanningQueueSkill as S
        assert "海鲜" in S.SKILL_PROMPT_TEMPLATE


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test: pipeline correctly uses skills
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineUsesSkills:
    """Verify the pipeline imports and instantiates skills correctly."""

    def test_pipeline_imports_skills(self):
        """Pipeline should import the four skills without error."""
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline  # noqa: F401

    def test_classify_meal_action_skill_prompt_injected_in_build_context(self):
        """
        _build_context with precomputed_action='suggest_options' must include
        the ACTION DIRECTIVE in the context string.
        """
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
        pipeline = PlanAheadPipeline(gemini_api_url="http://fake")
        ctx = pipeline._build_context(
            state={},
            user_timezone=None,
            precomputed_action="suggest_options",
        )
        assert "ACTION DIRECTIVE" in ctx
        assert "suggest_options" in ctx

    def test_no_precomputed_action_no_directive(self):
        """Without precomputed_action, no ACTION DIRECTIVE should appear."""
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
        pipeline = PlanAheadPipeline(gemini_api_url="http://fake")
        ctx = pipeline._build_context(
            state={},
            user_timezone=None,
            precomputed_action=None,
        )
        assert "ACTION DIRECTIVE" not in ctx

    def test_ask_precomputed_action_directive(self):
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
        pipeline = PlanAheadPipeline(gemini_api_url="http://fake")
        ctx = pipeline._build_context(
            state={},
            user_timezone=None,
            precomputed_action="ask",
        )
        assert "ACTION DIRECTIVE" in ctx
        assert "ask" in ctx

    def test_add_precomputed_action_directive(self):
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
        pipeline = PlanAheadPipeline(gemini_api_url="http://fake")
        ctx = pipeline._build_context(
            state={},
            user_timezone=None,
            precomputed_action="add",
        )
        assert "ACTION DIRECTIVE" in ctx
        assert "add" in ctx
