"""
Tests for CookingStepsAgent.

All external I/O (Gemini API, DataStorage HTTP) is mocked so the suite
runs fully offline.
"""
import json
import pytest
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.cooking_steps_agent import CookingStepsAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gemini_steps_response(steps: List[str]) -> dict:
    body = json.dumps({"steps": steps})
    return {"candidates": [{"content": {"parts": [{"text": body}]}}]}


def _gemini_intent_response(dish: Optional[str], date_ref: Optional[str], meal_time: Optional[str]) -> dict:
    body = json.dumps({"dish_hint": dish, "date_ref": date_ref, "meal_time": meal_time})
    return {"candidates": [{"content": {"parts": [{"text": body}]}}]}


def _plan_context(slots: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "plan_ahead",
        "data": {
            "meal_plan_slots": slots,
            "dish_ingredients": {},
            "schedule_id": 1,
        },
    }


@pytest.fixture
def agent():
    return CookingStepsAgent()


# ---------------------------------------------------------------------------
# _detect_meal_time (synchronous)
# ---------------------------------------------------------------------------

class TestDetectMealTime:
    def test_detects_lunch_from_input(self, agent):
        assert agent._detect_meal_time("今天中午宫保鸡丁怎么做", []) == "lunch"

    def test_detects_breakfast_from_input(self, agent):
        assert agent._detect_meal_time("早餐吃什么", []) == "breakfast"

    def test_detects_dinner_from_input(self, agent):
        assert agent._detect_meal_time("晚饭怎么做", []) == "dinner"

    def test_detects_meal_from_history_when_input_ambiguous(self, agent):
        history = [{"role": "user", "content": "今天午饭吃宫保鸡丁"}]
        assert agent._detect_meal_time("怎么做呢", history) == "lunch"

    def test_input_takes_priority_over_history(self, agent):
        history = [{"role": "user", "content": "早上的饺子"}]
        result = agent._detect_meal_time("晚上的牛排", history)
        assert result == "dinner"

    def test_returns_none_when_ambiguous(self, agent):
        assert agent._detect_meal_time("怎么做呢", []) is None


# ---------------------------------------------------------------------------
# _dish_from_context (synchronous)
# ---------------------------------------------------------------------------

class TestDishFromContext:
    def test_returns_none_for_empty_context(self, agent):
        dish, d, mt = agent._dish_from_context(None)
        assert dish is None

    def test_returns_none_when_wrong_type(self, agent):
        dish, d, mt = agent._dish_from_context({"type": "other", "data": {}})
        assert dish is None

    def test_returns_today_dinner_by_default(self, agent):
        today = date.today().isoformat()
        ctx = _plan_context({today: {"dinner": ["红烧肉"], "lunch": ["清炒白菜"]}})
        dish, d, mt = agent._dish_from_context(ctx)
        assert dish == "红烧肉"
        assert d == today
        assert mt == "dinner"

    def test_preferred_meal_time_wins(self, agent):
        today = date.today().isoformat()
        ctx = _plan_context({today: {"dinner": ["红烧肉"], "lunch": ["清炒白菜"]}})
        dish, d, mt = agent._dish_from_context(ctx, preferred_meal_time="lunch")
        assert dish == "清炒白菜"
        assert mt == "lunch"

    def test_string_dish_value_supported(self, agent):
        today = date.today().isoformat()
        ctx = _plan_context({today: {"dinner": "番茄炒蛋"}})
        dish, d, mt = agent._dish_from_context(ctx)
        assert dish == "番茄炒蛋"

    def test_falls_back_to_most_recent_date_when_no_today(self, agent):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        last_week = (date.today() - timedelta(days=7)).isoformat()
        ctx = _plan_context({
            last_week: {"dinner": ["宫保鸡丁"]},
            yesterday: {"dinner": ["蒜泥白肉"]},
        })
        dish, d, mt = agent._dish_from_context(ctx)
        assert dish == "蒜泥白肉"
        assert d == yesterday


# ---------------------------------------------------------------------------
# _resolve_date_ref (synchronous)
# ---------------------------------------------------------------------------

class TestResolveDateRef:
    def test_today(self, agent):
        assert agent._resolve_date_ref("today") == date.today().isoformat()

    def test_tomorrow(self, agent):
        assert agent._resolve_date_ref("tomorrow") == (date.today() + timedelta(days=1)).isoformat()

    def test_yesterday(self, agent):
        assert agent._resolve_date_ref("yesterday") == (date.today() - timedelta(days=1)).isoformat()

    def test_day_after_tomorrow(self, agent):
        assert agent._resolve_date_ref("day_after_tomorrow") == (date.today() + timedelta(days=2)).isoformat()

    def test_invalid_ref_returns_none(self, agent):
        assert agent._resolve_date_ref("next_week") is None

    def test_none_returns_none(self, agent):
        assert agent._resolve_date_ref(None) is None


# ---------------------------------------------------------------------------
# _parse_cooking_intent (mocked LLM)
# ---------------------------------------------------------------------------

class TestParseCookingIntent:
    @pytest.mark.asyncio
    async def test_extracts_dish_hint(self, agent):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _gemini_intent_response("速冻饺子", "today", "breakfast")

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            dish, date_ref, meal_time = await agent._parse_cooking_intent(
                "今天早上的速冻饺子怎么煮", [], None
            )

        assert dish == "速冻饺子"
        assert date_ref == "today"
        assert meal_time == "breakfast"

    @pytest.mark.asyncio
    async def test_handles_markdown_fenced_json(self, agent):
        fenced = "```json\n{\"dish_hint\": \"宫保鸡丁\", \"date_ref\": null, \"meal_time\": \"lunch\"}\n```"
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": fenced}]}}]}

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            dish, date_ref, meal_time = await agent._parse_cooking_intent(
                "宫保鸡丁怎么做", [], None
            )

        assert dish == "宫保鸡丁"
        assert date_ref is None
        assert meal_time == "lunch"

    @pytest.mark.asyncio
    async def test_strips_cooking_suffixes_via_llm(self, agent):
        """The LLM should strip "怎么做" — verify we accept what it returns."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _gemini_intent_response("大白菜", None, None)

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            dish, _, _ = await agent._parse_cooking_intent("大白菜呢", [], None)

        assert dish == "大白菜"

    @pytest.mark.asyncio
    async def test_invalid_date_ref_sanitised_to_none(self, agent):
        """Unknown date_ref values should be normalised to None."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _gemini_intent_response("回锅肉", "next_week", None)

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            _, date_ref, _ = await agent._parse_cooking_intent("下周回锅肉", [], None)

        assert date_ref is None

    @pytest.mark.asyncio
    async def test_api_error_returns_nones(self, agent):
        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=Exception("timeout"))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await agent._parse_cooking_intent("怎么做呢", [], None)

        assert result == (None, None, None)


# ---------------------------------------------------------------------------
# _dish_from_schedule (mocked HTTP)
# ---------------------------------------------------------------------------

def _schedule_payload(dish_name: str, target_date: str, meal_time: str, sid: int = 10) -> dict:
    return {
        "id": sid,
        "event_type": "meal_plan_draft",
        "metadata": {
            "features": [{
                "type": "meal_plan",
                "plans": [{
                    "date": target_date,
                    "meals": [{"mealTime": meal_time, "dishes": [{"name": dish_name}]}],
                }],
            }]
        },
    }


class TestDishFromSchedule:
    @pytest.mark.asyncio
    async def test_returns_dish_by_hint_match(self, agent):
        today = date.today().isoformat()
        schedules = [_schedule_payload("速冻饺子", today, "breakfast", sid=7)]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = schedules

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client
            with patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value="http://storage"):
                dish, sid, mt = await agent._dish_from_schedule(1, today, "breakfast", dish_hint="饺子")

        assert dish == "速冻饺子"
        assert sid == 7
        assert mt == "breakfast"

    @pytest.mark.asyncio
    async def test_falls_back_to_preferred_meal_time_when_no_hint(self, agent):
        """Without a hint, fall back to the dish in the preferred meal slot."""
        today = date.today().isoformat()
        schedules = [
            _schedule_payload("麻婆豆腐", today, "lunch", sid=11),
            _schedule_payload("清炒白菜", today, "dinner", sid=11),
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = schedules

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client
            with patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value="http://storage"):
                dish, sid, mt = await agent._dish_from_schedule(1, today, "dinner", dish_hint=None)

        assert dish == "清炒白菜"
        assert mt == "dinner"

    @pytest.mark.asyncio
    async def test_hint_mismatch_returns_none_not_wrong_dish(self, agent):
        """
        When a hint is given but NO dish in the schedule matches it,
        return (None, None, None) instead of falling back to an unrelated dish.
        This prevents e.g. generating steps for '牛排' when the user asked about '宫保鸡丁'.
        """
        today = date.today().isoformat()
        # Schedule has '牛排' for dinner — user is asking about '宫保鸡丁'
        schedules = [_schedule_payload("牛排", today, "dinner", sid=30)]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = schedules

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client
            with patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value="http://storage"):
                dish, sid, mt = await agent._dish_from_schedule(1, today, "dinner", dish_hint="宫保鸡丁")

        # Should return None rather than wrongly returning '牛排'
        assert dish is None
        assert sid is None

    @pytest.mark.asyncio
    async def test_hint_partial_match_is_accepted(self, agent):
        """Partial substring match should still succeed (hint '饺子' matches '速冻饺子')."""
        today = date.today().isoformat()
        schedules = [_schedule_payload("速冻饺子", today, "breakfast", sid=7)]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = schedules

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client
            with patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value="http://storage"):
                dish, sid, mt = await agent._dish_from_schedule(1, today, "breakfast", dish_hint="饺子")

        assert dish == "速冻饺子"
        assert sid == 7

    @pytest.mark.asyncio
    async def test_returns_none_when_no_storage_url(self, agent):
        with patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value=None):
            result = await agent._dish_from_schedule(1, "2026-03-01", "lunch")
        assert result == (None, None, None)

    @pytest.mark.asyncio
    async def test_returns_none_when_empty_schedule(self, agent):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client
            with patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value="http://storage"):
                result = await agent._dish_from_schedule(1, "2026-03-01", None)

        assert result == (None, None, None)


# ---------------------------------------------------------------------------
# _generate_steps (mocked LLM)
# ---------------------------------------------------------------------------

class TestGenerateSteps:
    @pytest.mark.asyncio
    async def test_returns_steps_list(self, agent):
        steps = [
            "将五花肉洗净冷水入锅。",
            "调酱：2汤匙生抽:1汤匙香醋:½汤匙白糖:1茶匙香油。",
            "将肉切薄片，淋酱即可。",
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _gemini_steps_response(steps)

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await agent._generate_steps("蒜泥白肉", ["五花肉", "大蒜", "生抽"])

        assert result["steps"] == steps

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_api_error(self, agent):
        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=Exception("LLM down"))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await agent._generate_steps("宫保鸡丁")

        assert result["steps"] == []

    @pytest.mark.asyncio
    async def test_prompt_contains_measurement_requirement(self, agent):
        """Verify the Chef's Precision requirements are in the payload sent to LLM."""
        captured = {}

        async def fake_post(url, headers, json):
            captured["payload"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _gemini_steps_response(["step1"])
            return mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            await agent._generate_steps("回锅肉")

        gen_cfg = captured["payload"]["generationConfig"]
        # responseMimeType must NOT be set (it causes JSON truncation for long step lists)
        assert "responseMimeType" not in gen_cfg
        # maxOutputTokens must NOT be set — let the model decide the length
        assert "maxOutputTokens" not in gen_cfg
        prompt_text = captured["payload"]["contents"][0]["parts"][0]["text"]
        assert "exact quantities" in prompt_text
        assert "specific number" in prompt_text
        assert "6–8" in prompt_text

    @pytest.mark.asyncio
    async def test_generate_steps_handles_markdown_fenced_json(self, agent):
        """_generate_steps should handle markdown-fenced JSON without responseMimeType."""
        fenced = '```json\n{"steps": ["热锅冷油。", "加蒜爆香。"]}\n```'
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": fenced}]}}]}

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await agent._generate_steps("蒜蓉炒菜")

        assert result["steps"] == ["热锅冷油。", "加蒜爆香。"]


# ---------------------------------------------------------------------------
# execute() — end-to-end with mocks
# ---------------------------------------------------------------------------

class TestExecute:
    @pytest.mark.asyncio
    async def test_uses_dish_hint_when_schedule_not_configured(self, agent):
        """When no storage URL is set, falls back to dish_hint directly."""
        steps = ["步骤1：…", "步骤2：…"]

        async def fake_post(url, headers, json):
            text = json["contents"][0]["parts"][0]["text"]
            if "Extract the cooking intent" in text:
                return _make_response(_gemini_intent_response("麻婆豆腐", None, None))
            return _make_response(_gemini_steps_response(steps))

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value=None):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await agent.execute(
                user_input="麻婆豆腐怎么做",
                owner_id=1,
            )

        assert result["action"] == "COOKING_STEPS"
        assert result["data"]["dish_name"] == "麻婆豆腐"
        assert result["data"]["cooking_steps"] == steps

    @pytest.mark.asyncio
    async def test_returns_needs_dish_name_when_no_hint(self, agent):
        """If LLM returns null dish_hint AND no context plan, ask user for dish."""
        async def fake_post(url, headers, json):
            return _make_response(_gemini_intent_response(None, None, None))

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value=None):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await agent.execute(user_input="怎么做呢", owner_id=1)

        assert result["data"].get("needs_dish_name") is True

    @pytest.mark.asyncio
    async def test_resolves_dish_from_context_when_no_hint(self, agent):
        """With no date and no dish_hint, should resolve dish from plan_ahead context."""
        today = date.today().isoformat()
        ctx = _plan_context({today: {"lunch": ["清炒大白菜"]}})
        steps = ["热锅冷油…", "大火翻炒…"]

        async def fake_post(url, headers, json):
            text = json["contents"][0]["parts"][0]["text"]
            if "Extract the cooking intent" in text:
                return _make_response(_gemini_intent_response(None, None, "lunch"))
            return _make_response(_gemini_steps_response(steps))

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value=None):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await agent.execute(
                user_input="怎么做呢",
                owner_id=1,
                context=ctx,
            )

        assert result["data"]["dish_name"] == "清炒大白菜"
        assert result["data"]["cooking_steps"] == steps

    @pytest.mark.asyncio
    async def test_returns_error_message_when_steps_empty(self, agent):
        """If LLM generates no steps, return a friendly error."""
        async def fake_post(url, headers, json):
            text = json["contents"][0]["parts"][0]["text"]
            if "Extract the cooking intent" in text:
                return _make_response(_gemini_intent_response("怪菜", None, None))
            return _make_response({"candidates": [{"content": {"parts": [{"text": '{"steps": []}'}]}}]})

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value=None):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await agent.execute(user_input="怪菜怎么做", owner_id=1)

        assert result["data"]["cooking_steps"] == []
        assert "怪菜" in result["message"]


# ---------------------------------------------------------------------------
# execute_batch()
# ---------------------------------------------------------------------------

class TestExecuteBatch:

    @pytest.mark.asyncio
    async def test_generates_steps_for_multiple_dishes(self, agent):
        """execute_batch should return one result per dish with steps populated."""
        today = date.today().isoformat()
        ctx = _plan_context({
            today: {
                "dinner": ["宫保鸡丁", "鱼香肉丝"],
            }
        })

        call_count = {"n": 0}

        async def fake_post(url, headers, json):
            call_count["n"] += 1
            text_input = json["contents"][0]["parts"][0]["text"]
            if "宫保鸡丁" in text_input:
                data = _gemini_steps_response(["步骤1：花生备好。", "步骤2：炒鸡丁。"])
            else:
                data = _gemini_steps_response(["步骤1：肉丝腌制。", "步骤2：爆炒。"])
            return _make_response(data)

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value=None):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            results = await agent.execute_batch(
                dish_names=["宫保鸡丁", "鱼香肉丝"],
                owner_id=1,
                context=ctx,
            )

        assert len(results) == 2
        names = {r["dish_name"] for r in results}
        assert names == {"宫保鸡丁", "鱼香肉丝"}
        for r in results:
            assert r["cooking_steps"], f"Expected steps for {r['dish_name']}"

    @pytest.mark.asyncio
    async def test_batch_resolves_date_and_meal_time_from_context(self, agent):
        """Steps for each dish should carry the correct date/meal_time from slots."""
        today = date.today().isoformat()
        ctx = _plan_context({today: {"lunch": ["清炒白菜"]}})

        async def fake_post(url, headers, json):
            return _make_response(_gemini_steps_response(["热锅，放油。", "翻炒白菜。"]))

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value=None):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            results = await agent.execute_batch(["清炒白菜"], owner_id=1, context=ctx)

        assert results[0]["date"] == today
        assert results[0]["meal_time"] == "lunch"

    @pytest.mark.asyncio
    async def test_batch_partial_failure_returns_error_entry(self, agent):
        """If one dish fails (exception), its result should have an 'error' key."""
        call_count = {"n": 0}

        async def fake_post(url, headers, json):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("LLM exploded")
            return _make_response(_gemini_steps_response(["步骤1。", "步骤2。"]))

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value=None):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            results = await agent.execute_batch(["宫保鸡丁", "番茄炒蛋"], owner_id=1)

        assert len(results) == 2
        failed = next(r for r in results if r["dish_name"] == "宫保鸡丁")
        success = next(r for r in results if r["dish_name"] == "番茄炒蛋")
        assert "error" in failed or failed["cooking_steps"] == []
        assert success["cooking_steps"] != []

    @pytest.mark.asyncio
    async def test_batch_uses_schedule_id_from_context(self, agent):
        """execute_batch should pass the schedule_id from context to _save_steps."""
        today = date.today().isoformat()
        ctx = {
            "type": "plan_ahead",
            "data": {
                "schedule_id": 42,
                "meal_plan_slots": {today: {"dinner": ["红烧肉"]}},
                "dish_ingredients": {},
            },
        }
        patched_calls: list = []

        async def fake_patch(sid, oid, dish, steps, d, mt, ingredients=None):
            patched_calls.append(sid)
            return True

        async def fake_post(url, headers, json):
            return _make_response(_gemini_steps_response(["步骤1。", "步骤2。"]))

        with patch("httpx.AsyncClient") as mock_cls, \
             patch.object(agent, "_patch_schedule", fake_patch), \
             patch("app.agents.cooking_steps_agent._get_storage_base_url", return_value="http://x"):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            await agent.execute_batch(["红烧肉"], owner_id=1, context=ctx)

        assert 42 in patched_calls

    @pytest.mark.asyncio
    async def test_batch_empty_dish_list_returns_empty(self, agent):
        results = await agent.execute_batch(dish_names=[], owner_id=1)
        assert results == []


# ---------------------------------------------------------------------------
# Small internal utility
# ---------------------------------------------------------------------------

def _make_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.json.return_value = data
    return m
