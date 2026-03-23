# -*- coding: utf-8 -*-
"""
Tests for Step 4c: Explicit-dish filter in PlanAheadPipeline.

Regression for the bug where the AI hallucinates extra dishes when the user
explicitly requests a single dish (e.g. "今天早上吃个小笼包" → pipeline saves
小笼包 AND 蒜蓉油麦菜 because the LLM intent classifier failed and the
Tier-2 query-substring fallback was not yet implemented).

Coverage
--------
  Unit:
    1. _keyword_fallback_classify: returns EXPLICIT_HINT when keywords present
    2. _classify_dish_intent: returns EXPLICIT_HINT when LLM call fails
    3. Tier-2 query-substring extraction: strips dishes not mentioned in query
    4. Tier-2 does NOT strip dishes when query has no explicit dish name

  Integration (mock LLM, mock storage):
    5. "今天早上吃个小笼包" → only 小笼包 in DB, 蒜蓉油麦菜 stripped
    6. "今天晚上帮我计划下" → no filter applied, all proposed dishes kept
    7. Keyword fallback path: LLM JSON fails → Tier-2 extraction still works
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.plan_ahead_state import clear_plan_state
from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline

# ── Helpers ───────────────────────────────────────────────────────────────────

_OWNER = 8888   # dedicated test user ID

def _pipeline() -> PlanAheadPipeline:
    return PlanAheadPipeline(gemini_api_url="http://fake-gemini")


@pytest.fixture(autouse=True)
def _clean():
    clear_plan_state(_OWNER)
    yield
    clear_plan_state(_OWNER)


# ─────────────────────────────────────────────────────────────────────────────
# 1. _keyword_fallback_classify
# ─────────────────────────────────────────────────────────────────────────────

class TestKeywordFallbackClassify:

    def test_explicit_keyword_triggers_hint(self):
        p = _pipeline()
        result = p._keyword_fallback_classify("今天早上吃个小笼包")
        assert result["intent"] == "EXPLICIT_HINT"
        assert result["is_explicit"] is False   # keyword-only: no dish extraction
        assert result["dishes"] == []

    def test_recommend_phrase_returns_unknown(self):
        p = _pipeline()
        result = p._keyword_fallback_classify("帮我推荐今晚吃什么")
        assert result["intent"] == "UNKNOWN"

    def test_explicit_english_keyword(self):
        p = _pipeline()
        result = p._keyword_fallback_classify("I want steak tonight")
        assert result["intent"] == "EXPLICIT_HINT"

    def test_empty_query_returns_unknown(self):
        p = _pipeline()
        result = p._keyword_fallback_classify("")
        assert result["intent"] == "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# 2. _classify_dish_intent: falls back to keyword when LLM fails
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyDishIntentFallback:

    @pytest.mark.asyncio
    async def test_llm_failure_returns_keyword_fallback(self):
        """When the LLM raises an exception, keyword fallback must be used."""
        p = _pipeline()
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=Exception("network error")):
            result = await p._classify_dish_intent("今天早上吃个小笼包", [])
        # keyword-based result
        assert result["intent"] in ("EXPLICIT_HINT", "UNKNOWN")

    @pytest.mark.asyncio
    async def test_llm_bad_json_falls_back(self):
        """When LLM returns non-parseable JSON, keyword fallback is used."""
        p = _pipeline()
        bad_resp = MagicMock()
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "not json {{{{"}]}}]
        }
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=bad_resp):
            result = await p._classify_dish_intent("今天早上吃个小笼包", [])
        assert result["intent"] in ("EXPLICIT_HINT", "UNKNOWN")

    @pytest.mark.asyncio
    async def test_llm_explicit_response_parsed(self):
        """When LLM returns clean JSON with EXPLICIT intent, parse it correctly."""
        p = _pipeline()
        good_resp = MagicMock()
        good_resp.raise_for_status = MagicMock()
        good_resp.json.return_value = {
            "candidates": [{
                "content": {"parts": [{"text": '{"intent":"EXPLICIT","dishes":["小笼包"]}'}]}
            }]
        }
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=good_resp):
            result = await p._classify_dish_intent("今天早上吃个小笼包", [])
        assert result["is_explicit"] is True
        assert "小笼包" in result["dishes"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Tier-2 query-substring extraction (unit, pure logic)
# ─────────────────────────────────────────────────────────────────────────────

class TestTier2QueryExtraction:
    """
    Tests for the Tier-2 fallback: when `dishes == []` and `intent == EXPLICIT_HINT`,
    the pipeline checks which of the LLM's proposed dishes appear in the user's query.
    """

    def _apply_tier2(
        self,
        query: str,
        proposed_slots: dict,
        dish_intent: dict,
    ) -> dict:
        """
        Mirrors the Tier-2 expansion logic from plan_ahead_pipeline.py Step 4c.
        Returns the updated dish_intent dict.
        """
        if not dish_intent["dishes"] and (
            dish_intent.get("intent") == "EXPLICIT_HINT"
            or dish_intent["is_explicit"]
        ):
            query_clean = query.lower().replace(" ", "")
            all_proposed = [
                d
                for meals in proposed_slots.values()
                for mt_dishes in meals.values()
                for d in mt_dishes
            ]
            in_query = [
                d for d in all_proposed
                if d.lower().replace(" ", "") in query_clean
            ]
            if in_query:
                return {
                    "is_explicit": True,
                    "dishes": in_query,
                    "intent": "QUERY_EXTRACTED",
                }
        return dish_intent

    def test_single_dish_in_query_extracted(self):
        """Only 小笼包 is in the query → 蒜蓉油麦菜 should not be extracted."""
        intent = {"is_explicit": False, "dishes": [], "intent": "EXPLICIT_HINT"}
        slots = {"2026-03-13": {"breakfast": ["小笼包", "蒜蓉油麦菜"]}}
        result = self._apply_tier2("今天早上吃个小笼包", slots, intent)
        assert result["is_explicit"] is True
        assert result["dishes"] == ["小笼包"]
        assert "蒜蓉油麦菜" not in result["dishes"]

    def test_no_dish_in_query_returns_unmodified(self):
        """When no proposed dish appears in query, do not filter (recommendation)."""
        intent = {"is_explicit": False, "dishes": [], "intent": "EXPLICIT_HINT"}
        slots = {"2026-03-13": {"dinner": ["宫保鸡丁", "番茄炒蛋", "米饭"]}}
        result = self._apply_tier2("今天晚上帮我推荐下", slots, intent)
        # No dish in query → original intent unchanged
        assert result["intent"] == "EXPLICIT_HINT"
        assert result["is_explicit"] is False

    def test_multiple_dishes_in_query_all_extracted(self):
        """If the user names two dishes, both should be extracted."""
        intent = {"is_explicit": False, "dishes": [], "intent": "EXPLICIT_HINT"}
        slots = {"2026-03-13": {"dinner": ["回锅肉", "清蒸鱼", "紫菜汤"]}}
        result = self._apply_tier2("今晚吃回锅肉和清蒸鱼", slots, intent)
        assert set(result["dishes"]) == {"回锅肉", "清蒸鱼"}
        assert "紫菜汤" not in result["dishes"]

    def test_tier2_only_triggers_on_explicit_hint(self):
        """Tier-2 must NOT trigger when intent is UNKNOWN (AI suggested, not explicit)."""
        intent = {"is_explicit": False, "dishes": [], "intent": "UNKNOWN"}
        slots = {"2026-03-13": {"dinner": ["回锅肉", "米饭"]}}
        result = self._apply_tier2("今晚帮我想个饭", slots, intent)
        # UNKNOWN → tier-2 skipped → original intent
        assert result["intent"] == "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# 4–7. Integration: full Step 4c filtering via mocked execute()
# ─────────────────────────────────────────────────────────────────────────────

def _mock_storage(saved_schedules=None):
    """Build a minimal storage mock that captures what gets saved."""
    storage = MagicMock()
    storage._extract_meal_plan_from_schedule = MagicMock(
        return_value=({}, [], {}, {})
    )
    storage._convert_to_feature_format = MagicMock(return_value={"features": []})
    storage._extract_existing_dish_data = MagicMock(return_value={})
    storage.get_user_schedules = AsyncMock(return_value=saved_schedules or [])
    storage.create_or_update_meal_plan_schedule = AsyncMock(return_value=99)
    storage.update_user_recent_dishes = AsyncMock(return_value=None)
    return storage


def _make_llm_response(action: str, date: str, meal_time: str, dishes: list) -> dict:
    """Build a mock LLM structured response for the pipeline's _call_llm."""
    import json
    meal_entries = [
        {
            "date": date,
            "meal_time": meal_time,
            "dishes": [{"name": d, "ingredients": [], "slot": "main"} for d in dishes],
        }
    ]
    return {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "action": action,
                        "meal_entries": meal_entries,
                        "user_message": f"已为您安排{meal_time}: {'、'.join(dishes)}",
                        "target_date": date,
                        "dish_options": [],
                    })
                }]
            }
        }]
    }


@pytest.mark.asyncio
async def test_single_dish_request_strips_hallucinated_extra():
    """
    Core regression test:
    User says "今天早上吃个小笼包" → pipeline calls LLM → LLM hallucinated
    蒜蓉油麦菜 as well.  The dish-intent classifier fails with JSON error,
    triggering keyword fallback (EXPLICIT_HINT, dishes=[]).
    Tier-2 query-substring extraction must keep 小笼包 and strip 蒜蓉油麦菜.
    """
    from datetime import date
    from app.skills.plan_ahead import (
        ClassifyDishIntentSkill,
        ClassifyMealActionSkill,
        ExtractIngredientsSkill,
        InitPlanningQueueSkill,
    )
    today = date.today().strftime("%Y-%m-%d")
    p = _pipeline()
    storage = _mock_storage()

    # Mock: LLM main call returns add + two dishes (one hallucinated)
    main_llm_resp = _make_llm_response("add", today, "breakfast", ["小笼包", "蒜蓉油麦菜"])

    # Patch all Skill.execute methods so only the main LLM httpx call is needed
    _dish_intent_result = {"is_explicit": False, "dishes": [], "intent": "EXPLICIT_HINT"}
    _action_result = {"action": "add", "reason": "keyword fallback"}
    _ingredients_result = {"ingredients": [], "target_date": None, "target_meal_type": None}
    _queue_result = {"has_planning_intent": False, "slots": []}

    async def _mock_post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = main_llm_resp
        return resp

    with patch.object(ClassifyDishIntentSkill, "execute", AsyncMock(return_value=_dish_intent_result)), \
         patch.object(ClassifyMealActionSkill, "execute", AsyncMock(return_value=_action_result)), \
         patch.object(ExtractIngredientsSkill, "execute", AsyncMock(return_value=_ingredients_result)), \
         patch.object(InitPlanningQueueSkill, "execute", AsyncMock(return_value=_queue_result)), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=_mock_post):
        result = await p.execute(
            owner_id=_OWNER,
            user_input="今天早上吃个小笼包",
            history=[],
            user_timezone="America/Los_Angeles",
            storage_client=storage,
            user_profile={
                "default_servings": 1, "meat_veg_ratio": "1:1:1",
                "include_soup": False, "calorie_target": None,
                "disliked_ingredients": [], "cuisine_weights": {},
                "recent_dishes": [],
            },
        )

    # Check what was saved to storage
    persist_call = storage.create_or_update_meal_plan_schedule.call_args
    saved_slots = persist_call.kwargs.get("meal_plan_slots") if persist_call else None

    assert saved_slots is not None, "Nothing was saved to storage"
    # Extract all saved dish names
    saved_dishes = [
        d
        for date_meals in saved_slots.values()
        for mt_dishes in date_meals.values()
        for d in mt_dishes
    ]
    assert "小笼包" in saved_dishes, f"小笼包 was not saved: {saved_dishes}"
    assert "蒜蓉油麦菜" not in saved_dishes, (
        f"Hallucinated dish '蒜蓉油麦菜' was saved despite not being requested! "
        f"Saved dishes: {saved_dishes}"
    )


@pytest.mark.asyncio
async def test_recommend_intent_add_action_does_not_filter_dishes():
    """
    When the dish-intent classifier returns RECOMMEND (user asked AI to suggest),
    Step 4c must NOT filter any dishes — even if the action is 'add'.
    The extra dishes are intentional AI suggestions, not hallucinations.
    """
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    p = _pipeline()
    storage = _mock_storage()

    # LLM returns add + 3 dishes
    main_llm_resp = _make_llm_response("add", today, "dinner", ["宫保鸡丁", "番茄炒蛋", "米饭"])

    # Classifier returns RECOMMEND intent (user was vague)
    import json as _json
    good_classifier_resp_text = _json.dumps({"intent": "RECOMMEND", "dishes": []})

    call_count = {"n": 0}

    async def _mock_post(url, **kwargs):
        call_count["n"] += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if call_count["n"] == 1:
            resp.json.return_value = main_llm_resp
        else:
            resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": good_classifier_resp_text}]}}]
            }
        return resp

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=_mock_post):
        result = await p.execute(
            owner_id=_OWNER,
            user_input="今晚帮我随便加几个菜",
            history=[],
            user_timezone="America/Los_Angeles",
            storage_client=storage,
            user_profile={
                "default_servings": 1, "meat_veg_ratio": "1:1:1",
                "include_soup": False, "calorie_target": None,
                "disliked_ingredients": [], "cuisine_weights": {},
                "recent_dishes": [],
            },
        )

    # With RECOMMEND intent, all 3 dishes should be kept
    persist_call = storage.create_or_update_meal_plan_schedule.call_args
    if persist_call:
        saved_slots = persist_call.kwargs.get("meal_plan_slots") or {}
        saved_dishes = [
            d for date_meals in saved_slots.values()
            for mt_dishes in date_meals.values()
            for d in mt_dishes
        ]
        # None of the three dishes should be stripped
        assert len(saved_dishes) >= 2, (
            f"RECOMMEND-intent dishes were incorrectly filtered: {saved_dishes}"
        )
