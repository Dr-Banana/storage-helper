"""
PlanAheadPipeline — _init_planning_queue date-detection tests
=============================================================

Two tiers:

  Tier 1 – Offline / keyword-fallback (always run):
    Mocks the httpx call so the LLM path always fails, verifying the keyword
    fallback produces the correct date list for common Chinese natural-language
    phrases, including the "今天到下个星期" correction scenario that caused a bug.

  Tier 2 – Live LLM (requires --run-llm flag or GEMINI_LLM_TESTING_KEY):
    Calls the real Gemini API to verify that the LLM-powered parser correctly
    handles a representative set of Chinese/English date-range expressions.

Run live tests:
    pytest tests/pipelines/test_plan_ahead_date_detection.py -m llm_live --run-llm -v
"""

from __future__ import annotations

import os
import pathlib
from datetime import date, datetime, timedelta
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipeline(gemini_url: str = "http://fake") -> PlanAheadPipeline:
    return PlanAheadPipeline(gemini_api_url=gemini_url)


def _date_range(start: date, n: int) -> List[str]:
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _dates_from_slots(slots: List[str]) -> List[str]:
    """Extract unique dates (YYYY-MM-DD) from date|meal_type slot strings, preserving order."""
    return list(dict.fromkeys(s.split("|")[0] for s in slots))


def _next_monday(today: date) -> date:
    days_ahead = (7 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


def _next_sunday(today: date) -> date:
    return _next_monday(today) + timedelta(days=6)


# Pipeline uses datetime.utcnow() when user_timezone=None — tests must match.
def _utc_today() -> date:
    return datetime.utcnow().date()


# ---------------------------------------------------------------------------
# Live-key loader (mirrors test_live_generation.py)
# ---------------------------------------------------------------------------

def _load_testing_key() -> str:
    val = os.getenv("GEMINI_LLM_TESTING_KEY", "")
    if val:
        return val
    try:
        from dotenv import dotenv_values  # type: ignore
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
_LIVE_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{_GEMINI_MODEL}:generateContent?key={_TESTING_KEY}"
)


def _live_pipeline() -> PlanAheadPipeline:
    return PlanAheadPipeline(gemini_api_url=_LIVE_URL)


# ---------------------------------------------------------------------------
# Skip fixture for live tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _skip_without_live_key(request):
    if "llm_live" in [m.name for m in request.node.iter_markers()]:
        has_flag = request.config.getoption("--run-llm", default=False)
        if not has_flag and not _TESTING_KEY:
            pytest.skip(
                "Live LLM tests are disabled.\n"
                "  Option A (local): set GEMINI_LLM_TESTING_KEY in .env.local\n"
                "  Option B (any):   pass --run-llm flag to pytest"
            )


# ---------------------------------------------------------------------------
# Helper: force keyword fallback by simulating LLM network failure
# ---------------------------------------------------------------------------

def _mock_httpx_failure():
    """Context manager that makes every httpx.AsyncClient.post raise."""
    return patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        side_effect=Exception("simulated network error"),
    )


# ---------------------------------------------------------------------------
# Tier 1 — Offline keyword-fallback tests
# ---------------------------------------------------------------------------

class TestKeywordFallback:
    """
    Verify that _init_planning_queue returns correct date lists based solely
    on the keyword fallback (LLM path is mocked to fail).
    """

    @pytest.mark.asyncio
    async def test_next_week_returns_mon_to_sun(self):
        today = _utc_today()
        expected_start = _next_monday(today)
        expected = _date_range(expected_start, 7)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("下个星期吃什么", None)

        assert _dates_from_slots(result) == expected, (
            f"'下个星期' should map to next Mon-Sun ({expected[0]} – {expected[-1]}), got {result}"
        )

    @pytest.mark.asyncio
    async def test_next_week_alt_phrasing(self):
        today = _utc_today()
        expected_start = _next_monday(today)
        expected = _date_range(expected_start, 7)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("帮我规划下周的饮食", None)

        assert _dates_from_slots(result) == expected

    @pytest.mark.asyncio
    async def test_today_to_next_week_returns_today_through_next_sunday(self):
        """
        'Bug scenario': '今天到下个星期' should return today → next Sunday,
        NOT just Mon-Sun of next week.
        """
        today = _utc_today()
        next_sun = _next_sunday(today)
        n = (next_sun - today).days + 1
        expected = _date_range(today, n)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("我是说今天到下个星期", None)

        dates = _dates_from_slots(result)
        assert dates[0] == expected[0], (
            f"Start should be today ({expected[0]}), got {dates[0]}"
        )
        assert dates[-1] == expected[-1], (
            f"End should be next Sunday ({expected[-1]}), got {dates[-1]}"
        )

    @pytest.mark.asyncio
    async def test_from_today_to_next_week_prefix(self):
        """'从今天到下周' also triggers the today-start rule."""
        today = _utc_today()
        next_sun = _next_sunday(today)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("从今天到下周帮我规划", None)

        dates = _dates_from_slots(result)
        assert dates[0] == today.strftime("%Y-%m-%d")
        assert dates[-1] == next_sun.strftime("%Y-%m-%d")

    @pytest.mark.asyncio
    async def test_this_week_returns_today_through_sunday(self):
        today = _utc_today()
        days_to_sunday = 6 - today.weekday()
        expected = _date_range(today, days_to_sunday + 1)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("这周吃什么", None)

        assert _dates_from_slots(result) == expected

    @pytest.mark.asyncio
    async def test_week_after_next(self):
        today = _utc_today()
        days_ahead = (7 - today.weekday()) % 7 or 7
        expected_start = today + timedelta(days=days_ahead + 7)
        expected = _date_range(expected_start, 7)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("帮我规划下下周的饮食", None)

        assert _dates_from_slots(result) == expected

    @pytest.mark.asyncio
    async def test_one_week_from_today(self):
        today = _utc_today()
        expected = _date_range(today, 7)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("接下来一周的饮食计划", None)

        assert _dates_from_slots(result) == expected

    @pytest.mark.asyncio
    async def test_half_month_returns_15_days(self):
        """
        Bug scenario: '往后半个月吧' should return 15 dates, NOT 7.
        Previously '往后' matched the 7-day catch-all before '半个月' was checked.
        """
        today = _utc_today()
        expected = _date_range(today, 15)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("往后半个月吧", None)

        assert _dates_from_slots(result) == expected, (
            f"'往后半个月' should map to 15 days ({expected[0]} – {expected[-1]}), got {result}"
        )

    @pytest.mark.asyncio
    async def test_one_month_returns_30_days(self):
        today = _utc_today()
        expected = _date_range(today, 30)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("帮我规划一个月的饮食", None)

        assert _dates_from_slots(result) == expected

    @pytest.mark.asyncio
    async def test_n_days_explicit_number(self):
        """'往后10天' → exactly 10 dates from today."""
        today = _utc_today()
        expected = _date_range(today, 10)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("往后10天的计划", None)

        assert _dates_from_slots(result) == expected

    @pytest.mark.asyncio
    async def test_two_weeks_returns_14_days(self):
        today = _utc_today()
        expected = _date_range(today, 14)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("接下来两周吃什么", None)

        assert _dates_from_slots(result) == expected

    @pytest.mark.asyncio
    async def test_ming_hou_two_days(self):
        """'明后两天' → 2 dates starting from tomorrow."""
        today = _utc_today()
        tomorrow = today + timedelta(days=1)
        expected = _date_range(tomorrow, 2)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("帮我计划下明后两天吃啥", None)

        assert _dates_from_slots(result) == expected, (
            f"Expected 2 dates from tomorrow for '明后两天', got {_dates_from_slots(result)}"
        )

    @pytest.mark.asyncio
    async def test_ming_tian_hou_tian(self):
        """'明天和后天' → 2 dates starting from tomorrow."""
        today = _utc_today()
        tomorrow = today + timedelta(days=1)
        expected = _date_range(tomorrow, 2)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("明天和后天的午餐晚餐帮我规划一下", None)

        assert _dates_from_slots(result) == expected, (
            f"Expected 2 dates from tomorrow for '明天后天', got {_dates_from_slots(result)}"
        )

    @pytest.mark.asyncio
    async def test_chinese_two_days_from_today(self):
        """'两天' without '明' → 2 dates from today."""
        today = _utc_today()
        expected = _date_range(today, 2)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("接下来两天吃什么", None)

        assert _dates_from_slots(result) == expected, (
            f"Expected 2 dates from today for '两天', got {_dates_from_slots(result)}"
        )

    @pytest.mark.asyncio
    async def test_chinese_three_days(self):
        """'三天' → 3 dates from today."""
        today = _utc_today()
        expected = _date_range(today, 3)

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("帮我规划三天的饮食", None)

        assert _dates_from_slots(result) == expected, (
            f"Expected 3 dates for '三天', got {_dates_from_slots(result)}"
        )

    @pytest.mark.asyncio
    async def test_single_day_dinner_query(self):
        """'今天晚上吃什么' → single dinner slot for today (now goes through queue too)."""
        today = _utc_today()

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("今天晚上吃什么", None)

        dates = _dates_from_slots(result)
        assert dates == [today.strftime("%Y-%m-%d")], (
            f"Expected only today in result, got {dates}"
        )
        assert all(s.endswith("|dinner") for s in result), (
            f"Expected all dinner slots for '晚上吃什么', got {result}"
        )

    @pytest.mark.asyncio
    async def test_single_day_all_meals(self):
        """'今天的计划' → all three meal slots for today."""
        today = _utc_today()

        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("帮我规划今天的计划", None)

        dates = _dates_from_slots(result)
        assert dates == [today.strftime("%Y-%m-%d")], (
            f"Expected only today in result, got {dates}"
        )
        assert len(result) == 3, f"Expected 3 slots for today, got {result}"

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        with _mock_httpx_failure():
            result = await _pipeline()._init_planning_queue("", None)

        assert result == []


# ---------------------------------------------------------------------------
# Tier 1b — Phase 1b LLM classifier behaviour
# ---------------------------------------------------------------------------

class TestPhase1bLLMClassifier:
    """
    Phase 1b now uses _classify_date_confirmation() instead of keyword matching.
    Tests mock that method and verify the pipeline branches correctly.
    """

    def _pending_state(self, owner_id: int = 9100):
        """Seed a pending_planning_queue state and return the pending slots."""
        from app.modules.plan_ahead_state import update_plan_state, clear_plan_state
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
        today = _utc_today()
        pending_dates = _date_range(today + timedelta(days=1), 2)
        pending_queue = PlanAheadPipeline._dates_to_slots(pending_dates)
        clear_plan_state(owner_id)
        update_plan_state(
            owner_id=owner_id,
            pending_planning_queue=pending_queue,
            last_pipeline_action="ask_confirm_dates",
        )
        return pending_queue

    def _empty_db_state(self):
        return {"meal_plan": {}, "meal_plan_slots": {}, "dish_ingredients": {},
                "shopping_list": [], "schedule_id": None}

    def _suggest_options_reply(self):
        return {
            "action": "suggest_options",
            "user_message": "这是今天的菜单方案。",
            "meal_plan": {}, "meal_plan_slots": {}, "dish_ingredients": {},
            "dish_options": [{"option_id": "1", "label": "方案一",
                               "meal_plan_slots": {}, "dish_ingredients": {}}],
        }

    @pytest.mark.asyncio
    async def test_confirmed_intent_enters_queue_mode(self):
        """When classifier returns 'confirmed', pipeline must NOT re-ask and must enter queue."""
        from unittest.mock import patch as _patch, AsyncMock as _AsyncMock

        pending_queue = self._pending_state(9100)
        pipeline = _pipeline()
        _confirm_fragment = "日期没问题"

        with (
            _patch.object(pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                          return_value=self._empty_db_state()),
            _patch.object(pipeline, "_fetch_inventory", return_value=[]),
            _patch.object(pipeline, "_classify_date_confirmation", new_callable=_AsyncMock,
                          return_value={"intent": "confirmed", "new_dates": []}),
            _patch.object(pipeline, "_call_llm", new_callable=_AsyncMock,
                          return_value=self._suggest_options_reply()),
        ):
            result = await pipeline.execute(
                owner_id=9100, user_input="没问题", history=[],
                user_timezone=None, storage_client=None,
                intent_result={"intent": "PLAN_AHEAD"}, language="zh",
            )

        assert _confirm_fragment not in result.get("response", ""), (
            "Confirmed intent must NOT re-show the date confirmation message."
        )

    @pytest.mark.asyncio
    async def test_unclear_intent_re_asks(self):
        """When classifier returns 'unclear', pipeline must re-ask the date confirmation."""
        from unittest.mock import patch as _patch, AsyncMock as _AsyncMock

        pending_queue = self._pending_state(9101)
        pipeline = _pipeline()
        _confirm_fragment = "日期没问题"

        with (
            _patch.object(pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                          return_value=self._empty_db_state()),
            _patch.object(pipeline, "_fetch_inventory", return_value=[]),
            _patch.object(pipeline, "_classify_date_confirmation", new_callable=_AsyncMock,
                          return_value={"intent": "unclear", "new_dates": []}),
        ):
            result = await pipeline.execute(
                owner_id=9101, user_input="我不知道", history=[],
                user_timezone=None, storage_client=None,
                intent_result={"intent": "PLAN_AHEAD"}, language="zh",
            )

        assert _confirm_fragment in result.get("response", ""), (
            "Unclear intent must re-show the date confirmation message."
        )

    @pytest.mark.asyncio
    async def test_corrected_intent_proposes_new_dates(self):
        """When classifier returns 'corrected' with new dates, pipeline must re-ask with new dates."""
        from unittest.mock import patch as _patch, AsyncMock as _AsyncMock

        today = _utc_today()
        pending_queue = self._pending_state(9102)
        new_start = today + timedelta(days=5)
        new_end = today + timedelta(days=6)
        new_dates = _date_range(new_start, 2)

        pipeline = _pipeline()

        with (
            _patch.object(pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                          return_value=self._empty_db_state()),
            _patch.object(pipeline, "_fetch_inventory", return_value=[]),
            _patch.object(pipeline, "_classify_date_confirmation", new_callable=_AsyncMock,
                          return_value={"intent": "corrected", "new_dates": new_dates}),
        ):
            result = await pipeline.execute(
                owner_id=9102, user_input="不对，是这周末", history=[],
                user_timezone=None, storage_client=None,
                intent_result={"intent": "PLAN_AHEAD"}, language="zh",
            )

        resp = result.get("response", "")
        # Response must mention the new dates (in month-day format) and ask for re-confirmation
        assert "日期没问题" in resp, (
            f"Corrected dates must trigger a new date-confirmation message. Got: {resp!r}"
        )

    @pytest.mark.asyncio
    async def test_corrected_intent_no_dates_extracted_shows_guidance(self):
        """When classifier says 'corrected' but returns no dates/meal_times,
        a guidance message is shown instead of repeating the same confirmation."""
        from unittest.mock import patch as _patch, AsyncMock as _AsyncMock

        pending_queue = self._pending_state(9103)
        pipeline = _pipeline()

        with (
            _patch.object(pipeline.plan_ahead_agent, "sync_meal_plan_from_database",
                          return_value=self._empty_db_state()),
            _patch.object(pipeline, "_fetch_inventory", return_value=[]),
            _patch.object(pipeline, "_classify_date_confirmation", new_callable=_AsyncMock,
                          return_value={"intent": "corrected", "new_dates": [], "new_meal_times": None}),
        ):
            result = await pipeline.execute(
                owner_id=9103, user_input="换个时间", history=[],
                user_timezone=None, storage_client=None,
                intent_result={"intent": "PLAN_AHEAD"}, language="zh",
            )

        response = result.get("response", "")
        # First no-op correction: should show a targeted guidance question (not the original msg)
        assert "日期" in response or "餐次" in response or "修改" in response, (
            f"First no-op correction should show guidance message, got: {response!r}"
        )


# ---------------------------------------------------------------------------
# Tier 1 — Phase 1b correction-signal guard
# ---------------------------------------------------------------------------

class TestCorrectionSignalGuard:
    """
    Verify that '我是说今天到下个星期' is NOT treated as an implicit confirmation
    when the keyword fallback happens to return dates equal to the pending queue.
    """

    @pytest.mark.asyncio
    async def test_correction_signal_prevents_implicit_confirmation(self):
        """
        When '_corrected == _pending_queue' but the user said '我是说…',
        the pipeline must NOT auto-confirm and start meal planning.

        The response should either re-propose corrected dates or re-ask for
        confirmation — it must never fall through to LLM meal planning.
        """
        from unittest.mock import patch as _patch

        today = _utc_today()
        # Build a pending queue that exactly matches what keyword fallback
        # would return for a bare "下个星期" query (Mon-Sun of next week).
        from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
        pending_start = _next_monday(today)
        pending_queue = PlanAheadPipeline._dates_to_slots(_date_range(pending_start, 7))

        # Mock state so Phase 1b is active
        mock_state = {
            "pending_planning_queue": pending_queue,
            "last_pipeline_action": "ask_confirm_dates",
            "meal_plan": {},
            "meal_plan_slots": {},
            "dish_ingredients": {},
            "shopping_list": [],
            "schedule_id": None,
            "is_draft": False,
        }

        pipeline = _pipeline()

        def _mock_get_state(owner_id):
            return dict(mock_state)

        def _mock_update_state(owner_id, **kwargs):
            mock_state.update({k: v for k, v in kwargs.items() if v is not None})

        with (
            _mock_httpx_failure(),
            _patch("app.pipelines.plan_ahead_pipeline.get_plan_state", side_effect=_mock_get_state),
            _patch("app.pipelines.plan_ahead_pipeline.update_plan_state", side_effect=_mock_update_state),
            _patch.object(
                pipeline.plan_ahead_agent,
                "sync_meal_plan_from_database",
                new=AsyncMock(return_value={"meal_plan": {}, "meal_plan_slots": {}, "dish_ingredients": {}, "schedule_id": None}),
            ),
        ):
            result = await pipeline.execute(
                owner_id=1,
                user_input="我是说今天到下个星期",
                history=[],
                user_timezone=None,
                storage_client=None,
                intent_result={"intent": "PLAN_AHEAD"},
                language="zh",
            )

        # The response must re-propose new/corrected dates (Phase 1b correction branch)
        # or re-ask confirmation — it must NOT have fallen through to meal planning.
        # If the pipeline fell through to LLM meal planning, action_data would contain
        # an "action" key like "suggest_options" or "recommend".
        action_data = (result.get("action_data") or {})
        assert action_data.get("action") not in ("suggest_options", "recommend"), (
            f"Pipeline must NOT auto-confirm and start meal planning when user says '我是说…'. "
            f"Got action={action_data.get('action')!r}, response={result.get('response_text', '')!r}"
        )


# ---------------------------------------------------------------------------
# Tier 2 — Live LLM tests
# ---------------------------------------------------------------------------

@pytest.mark.llm_live
class TestLiveDateDetection:
    """
    End-to-end tests using the real Gemini API.

    These verify that _init_planning_queue's LLM path correctly interprets
    natural-language date-range expressions.
    """

    @pytest.mark.asyncio
    async def test_next_week_zh(self):
        """'下个星期吃什么' → 7 dates starting from next Monday."""
        today = _utc_today()
        expected_start = _next_monday(today)

        result = await _live_pipeline()._init_planning_queue("帮我想一想下个星期吃什么", None)

        dates = _dates_from_slots(result)
        assert len(dates) == 7, f"Expected 7 dates for '下个星期', got {len(dates)}: {dates}"
        assert dates[0] == expected_start.strftime("%Y-%m-%d"), (
            f"Expected start={expected_start}, got {dates[0]}"
        )

    @pytest.mark.asyncio
    async def test_today_to_next_week_zh(self):
        """'今天到下个星期' → today through next Sunday (>=7 days)."""
        today = _utc_today()
        next_sun = _next_sunday(today)

        result = await _live_pipeline()._init_planning_queue("今天到下个星期", None)

        dates = _dates_from_slots(result)
        assert dates, "Expected non-empty date list for '今天到下个星期'"
        assert dates[0] == today.strftime("%Y-%m-%d"), (
            f"Start date should be today ({today}), got {dates[0]}"
        )
        assert dates[-1] == next_sun.strftime("%Y-%m-%d"), (
            f"End date should be next Sunday ({next_sun}), got {dates[-1]}"
        )
        assert len(dates) >= 7, f"Expected >=7 dates, got {len(dates)}"

    @pytest.mark.asyncio
    async def test_correction_phrase_today_to_next_week(self):
        """'我是说今天到下个星期' (correction phrasing) → today through next Sunday."""
        today = _utc_today()
        next_sun = _next_sunday(today)

        result = await _live_pipeline()._init_planning_queue("我是说今天到下个星期", None)

        dates = _dates_from_slots(result)
        assert dates, "Expected non-empty date list"
        assert dates[0] == today.strftime("%Y-%m-%d"), (
            f"Start should be today ({today}), got {dates[0]}"
        )
        assert dates[-1] == next_sun.strftime("%Y-%m-%d"), (
            f"End should be next Sunday ({next_sun}), got {dates[-1]}"
        )

    @pytest.mark.asyncio
    async def test_n_days_from_today(self):
        """'接下来7天' → exactly 7 dates starting from today."""
        today = _utc_today()

        result = await _live_pipeline()._init_planning_queue("接下来7天的饮食计划", None)

        dates = _dates_from_slots(result)
        assert len(dates) == 7, f"Expected 7 dates for '接下来7天', got {len(dates)}: {dates}"
        assert dates[0] == today.strftime("%Y-%m-%d"), (
            f"Start should be today ({today}), got {dates[0]}"
        )

    @pytest.mark.asyncio
    async def test_single_day_dinner_query(self):
        """'今天晚上吃什么' → single dinner slot for today (now also uses queue flow)."""
        today = _utc_today()
        result = await _live_pipeline()._init_planning_queue("今天晚上吃什么", None)

        # LLM should detect dinner for today
        dates = _dates_from_slots(result)
        assert dates == [today.strftime("%Y-%m-%d")], (
            f"Single-day dinner query should return only today, got {dates}"
        )

    @pytest.mark.asyncio
    async def test_this_week_zh(self):
        """'这周' → from today through this Sunday."""
        today = _utc_today()
        days_to_sunday = 6 - today.weekday()

        result = await _live_pipeline()._init_planning_queue("这周吃什么帮我规划一下", None)

        dates = _dates_from_slots(result)
        assert dates, "Expected non-empty date list for '这周'"
        assert dates[0] == today.strftime("%Y-%m-%d"), (
            f"Start should be today ({today}), got {dates[0]}"
        )
        expected_end = (today + timedelta(days=days_to_sunday)).strftime("%Y-%m-%d")
        assert dates[-1] == expected_end, (
            f"End should be this Sunday ({expected_end}), got {dates[-1]}"
        )

    @pytest.mark.asyncio
    async def test_half_month_zh(self):
        """
        '往后半个月吧' → 15 dates from today.

        This is the exact correction phrase from the bug report where '往后'
        was wrongly swallowed by the 7-day catch-all before '半个月' was checked.
        The LLM should directly return 15 days.
        """
        today = _utc_today()
        expected_end = (today + timedelta(days=14)).strftime("%Y-%m-%d")

        result = await _live_pipeline()._init_planning_queue("往后半个月吧", None)

        dates = _dates_from_slots(result)
        assert dates, "Expected non-empty date list for '往后半个月'"
        assert dates[0] == today.strftime("%Y-%m-%d"), (
            f"Start should be today ({today}), got {dates[0]}"
        )
        assert len(dates) == 15, (
            f"Expected 15 dates for '往后半个月', got {len(dates)}: {dates}"
        )
        assert dates[-1] == expected_end, (
            f"End should be today+14 ({expected_end}), got {dates[-1]}"
        )

    @pytest.mark.asyncio
    async def test_explicit_n_days_zh(self):
        """'往后10天的计划' → exactly 10 dates from today."""
        today = _utc_today()

        result = await _live_pipeline()._init_planning_queue("往后10天的计划", None)

        dates = _dates_from_slots(result)
        assert len(dates) == 10, (
            f"Expected 10 dates for '往后10天', got {len(dates)}: {dates}"
        )
        assert dates[0] == today.strftime("%Y-%m-%d"), (
            f"Start should be today ({today}), got {dates[0]}"
        )

    @pytest.mark.asyncio
    async def test_two_weeks_zh(self):
        """'接下来两周' → 14 dates from today."""
        today = _utc_today()

        result = await _live_pipeline()._init_planning_queue("接下来两周吃什么", None)

        dates = _dates_from_slots(result)
        assert len(dates) == 14, (
            f"Expected 14 dates for '接下来两周', got {len(dates)}: {dates}"
        )
        assert dates[0] == today.strftime("%Y-%m-%d"), (
            f"Start should be today ({today}), got {dates[0]}"
        )

    @pytest.mark.asyncio
    async def test_one_month_zh(self):
        """'帮我规划一个月的饮食' → 30 dates from today."""
        today = _utc_today()

        result = await _live_pipeline()._init_planning_queue("帮我规划一个月的饮食", None)

        dates = _dates_from_slots(result)
        assert len(dates) == 30, (
            f"Expected 30 dates for '一个月', got {len(dates)}: {dates}"
        )
        assert dates[0] == today.strftime("%Y-%m-%d"), (
            f"Start should be today ({today}), got {dates[0]}"
        )

    @pytest.mark.asyncio
    async def test_week_after_next_zh(self):
        """'帮我规划下下周的饮食' → 7 dates starting from the Monday after next."""
        today = _utc_today()
        days_ahead = (7 - today.weekday()) % 7 or 7
        expected_start = today + timedelta(days=days_ahead + 7)

        result = await _live_pipeline()._init_planning_queue("帮我规划下下周的饮食", None)

        dates = _dates_from_slots(result)
        assert len(dates) == 7, (
            f"Expected 7 dates for '下下周', got {len(dates)}: {dates}"
        )
        assert dates[0] == expected_start.strftime("%Y-%m-%d"), (
            f"Start should be {expected_start}, got {dates[0]}"
        )

    @pytest.mark.asyncio
    async def test_ming_hou_two_days_live(self):
        """'帮我计划下明后两天吃啥' → 2 dates starting from tomorrow."""
        today = _utc_today()
        tomorrow = today + timedelta(days=1)
        expected = _date_range(tomorrow, 2)

        result = await _live_pipeline()._init_planning_queue("帮我计划下明后两天吃啥", None)

        assert _dates_from_slots(result) == expected, (
            f"Expected 2 dates from tomorrow for '明后两天', got {_dates_from_slots(result)}"
        )

    @pytest.mark.asyncio
    async def test_two_days_from_today_live(self):
        """'接下来两天吃什么' → 2 dates from today."""
        today = _utc_today()
        expected = _date_range(today, 2)

        result = await _live_pipeline()._init_planning_queue("接下来两天吃什么", None)

        assert _dates_from_slots(result) == expected, (
            f"Expected 2 dates from today for '两天', got {_dates_from_slots(result)}"
        )


# ---------------------------------------------------------------------------
# Tier 2b — Live LLM tests for _classify_date_confirmation
# ---------------------------------------------------------------------------

@pytest.mark.llm_live
class TestLiveDateConfirmationClassifier:
    """
    End-to-end tests for _classify_date_confirmation() using the real Gemini API.
    Verifies that common confirmation and correction phrases are classified correctly.
    """

    def _pending_2days(self):
        today = _utc_today()
        return _date_range(today + timedelta(days=1), 2)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phrase,expected_intent", [
        ("没问题",     "confirmed"),
        ("行",         "confirmed"),
        ("好的",       "confirmed"),
        ("可以",       "confirmed"),
        ("ok",         "confirmed"),
        ("sure",       "confirmed"),
        ("no problem", "confirmed"),
        ("开始吧",     "confirmed"),
        ("当然",       "confirmed"),
    ])
    async def test_confirmation_phrases(self, phrase: str, expected_intent: str):
        """Common affirmative replies must be classified as 'confirmed'."""
        pending = self._pending_2days()
        result = await _live_pipeline()._classify_date_confirmation(phrase, pending, None)
        assert result["intent"] == expected_intent, (
            f"Phrase {phrase!r} → expected intent={expected_intent!r}, got {result!r}"
        )

    @pytest.mark.asyncio
    async def test_correction_phrase_classified(self):
        """A phrase that specifies new dates must NOT be classified as 'confirmed'."""
        today = _utc_today()
        pending = _date_range(today + timedelta(days=1), 2)
        # User explicitly says "no, I mean next Monday and Tuesday"
        result = await _live_pipeline()._classify_date_confirmation(
            "不对，我是说下个星期一和星期二", pending, None
        )
        # Either "corrected" (ideal) or "unclear" (acceptable fallback) — never "confirmed"
        assert result["intent"] != "confirmed", (
            f"A clear correction phrase must NOT be 'confirmed'. Got {result!r}"
        )

    @pytest.mark.asyncio
    async def test_unclear_phrase_classified(self):
        """An off-topic or ambiguous reply must be classified as 'unclear'."""
        pending = self._pending_2days()
        result = await _live_pipeline()._classify_date_confirmation(
            "我明天要去旅游", pending, None
        )
        # "我明天要去旅游" is ambiguous — accept confirmed or unclear (not corrected)
        assert result["intent"] in ("unclear", "confirmed"), (
            f"Expected 'unclear' or 'confirmed', got {result!r}"
        )


# ---------------------------------------------------------------------------
# TestExplicitDishDetection — unit tests for _is_explicit_dish_request
# ---------------------------------------------------------------------------

class TestExplicitDishDetection:
    """
    Unit tests for PlanAheadPipeline._is_explicit_dish_request().

    This method distinguishes between:
      - Explicit dish requests ("今天晚上吃芋艿猪排骨") → True  (bypass planning flow)
      - Planning/suggestion requests ("今天晚上吃什么") → False (enter planning flow)
    """

    @pytest.mark.parametrize("text,expected", [
        # Explicit dish requests → True
        ("今天晚上吃芋艿猪排骨",     True),
        ("今晚做个红烧肉",           True),
        ("想吃火锅",                 True),
        ("来点水煮鱼",               True),
        ("做个红烧排骨",             True),
        ("吃个饺子",                 True),
        ("来个扬州炒饭",             True),
        ("整点麻辣烫",               True),
        # Planning / suggestion requests → False
        ("今天晚上吃什么",           False),
        ("今天吃什么好",             False),
        ("帮我规划今天三餐",         False),
        ("今天吃什么呢",             False),
        ("给我推荐一个菜",           False),
        ("今天早餐吃啥",             False),
        ("计划下这周的饮食",         False),
        ("这周帮我规划一下",         False),
    ])
    def test_explicit_dish_detection(self, text: str, expected: bool):
        result = PlanAheadPipeline._is_explicit_dish_request(text)
        assert result == expected, (
            f"_is_explicit_dish_request({text!r}) → expected {expected}, got {result}"
        )


# ---------------------------------------------------------------------------
# TestExplicitDishBypassesQueue — _init_planning_queue returns [] for explicit dishes
# ---------------------------------------------------------------------------

class TestExplicitDishBypassesQueue:
    """
    Verify that _init_planning_queue returns an empty list when the user names
    a specific dish (explicit dish request), so the query falls through to the
    main LLM pipeline for a direct 'add' action instead of entering the planning queue.
    """

    @pytest.mark.asyncio
    async def test_explicit_dish_returns_empty_queue(self):
        """'今天晚上吃芋艿猪排骨' must NOT trigger the planning queue."""
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            # Make the LLM call fail so keyword fallback is exercised
            mock_post.side_effect = Exception("network error")
            p = _pipeline()
            slots = await p._init_planning_queue("今天晚上吃芋艿猪排骨", "America/Los_Angeles")
        assert slots == [], (
            f"Explicit dish request must return empty queue, got {slots!r}"
        )

    @pytest.mark.asyncio
    async def test_explicit_dish_returns_empty_queue_various(self):
        """Various explicit dish forms must all return empty queue."""
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = Exception("network error")
            p = _pipeline()
            for text in ["想吃火锅", "做个红烧肉", "今晚来点水煮鱼"]:
                slots = await p._init_planning_queue(text, "America/Los_Angeles")
                assert slots == [], (
                    f"Explicit dish {text!r} must return empty queue, got {slots!r}"
                )

    @pytest.mark.asyncio
    async def test_planning_request_returns_nonempty_queue(self):
        """'今天晚上吃什么' must still trigger the planning queue (return slots)."""
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = Exception("network error")
            p = _pipeline()
            slots = await p._init_planning_queue("今天晚上吃什么", "America/Los_Angeles")
        assert len(slots) > 0, (
            f"Planning request must return non-empty queue, got {slots!r}"
        )

    @pytest.mark.asyncio
    async def test_planning_for_today_returns_nonempty_queue(self):
        """'帮我规划今天三餐' must return slots."""
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = Exception("network error")
            p = _pipeline()
            slots = await p._init_planning_queue("帮我规划今天三餐", "America/Los_Angeles")
        assert len(slots) > 0, (
            f"Planning request must return non-empty queue, got {slots!r}"
        )


# ---------------------------------------------------------------------------
# TestPhase1aPreResolution — pending_ask_dates meal-slot selector resolution
# ---------------------------------------------------------------------------

class TestPhase1aPreResolution:
    """
    Tests for the Phase 1a-pre block that resolves the user's reply to the
    meal-slot selector.

    When the Fresh-plan guard captures specific dates (stored in state as
    pending_ask_dates) and the user then specifies which meals they want,
    the pipeline must:
      1. Parse meal types from the user's natural-language reply.
      2. Build a planning queue = [date|meal_type, ...] from the cross-product.
      3. Clear pending_ask_dates from state.
      4. Fall through to queue-mode planning (NOT generate a fresh suggest_options).

    These are unit tests that seed _plan_states directly and mock LLM calls.
    """

    _OWNER = 42
    _TZ = "America/Los_Angeles"

    def setup_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def teardown_method(self):
        from app.modules.plan_ahead_state import _plan_states
        _plan_states.clear()

    def _seed_state(self, dates: list, **extra):
        """Seed state with pending_ask_dates + last_pipeline_action='ask'."""
        from app.modules.plan_ahead_state import update_plan_state
        update_plan_state(
            owner_id=self._OWNER,
            last_pipeline_action="ask",
            pending_ask_dates=dates,
            **extra,
        )

    def _make_mock_post(self, dish_clf_resp: dict, main_llm_resp: dict):
        """
        Build an AsyncMock for httpx.AsyncClient.post.
        Call order:
          1st  → _classify_dish_intent  (Layer 0)
          2nd+ → main LLM planning calls
        """
        call_count = {"n": 0}
        dish_json = __import__("json").dumps(dish_clf_resp)
        main_json = __import__("json").dumps(main_llm_resp)

        async def _mock(url, **kwargs):
            call_count["n"] += 1
            resp = MagicMock()
            resp.status_code = 200
            if call_count["n"] == 1:
                # Layer 0: dish-intent classifier
                resp.json.return_value = {
                    "candidates": [{"content": {"parts": [{"text": dish_json}]},
                                    "finishReason": "STOP"}]
                }
            else:
                resp.json.return_value = {
                    "candidates": [{"content": {"parts": [{"text": main_json}]},
                                    "finishReason": "STOP"}]
                }
            return resp
        return AsyncMock(side_effect=_mock)

    # ------------------------------------------------------------------
    # Helper to run execute() with a seeded state and captured queue
    # ------------------------------------------------------------------

    async def _run_execute(self, user_input: str) -> dict:
        from app.modules.plan_ahead_state import get_plan_state
        from unittest.mock import AsyncMock, MagicMock, patch
        import json

        dish_clf = {"is_explicit": False, "intent": "RECOMMEND", "dishes": []}
        # Minimal valid main-LLM reply that won't require persistence
        main_llm = {
            "action": "ask",
            "user_message": "哪天哪餐？",
        }

        mock_post = self._make_mock_post(dish_clf, main_llm)

        with patch("httpx.AsyncClient.post", mock_post):
            p = _pipeline()
            result = await p.execute(
                owner_id=self._OWNER,
                user_input=user_input,
                history=[],
                user_timezone=self._TZ,
                storage_client=None,
                intent_result=None,
                user_profile={},
            )

        state_after = get_plan_state(self._OWNER)
        return {"result": result, "state": state_after}

    @pytest.mark.asyncio
    async def test_dinner_only_builds_correct_queue(self):
        """
        '只计划晚饭' with pending_ask_dates=['2026-03-27','2026-03-28']
        must build queue ['2026-03-27|dinner','2026-03-28|dinner']
        and clear pending_ask_dates.
        """
        from app.modules.plan_ahead_state import get_plan_state
        self._seed_state(["2026-03-27", "2026-03-28"])

        out = await self._run_execute("只计划晚饭")
        state = out["state"]

        assert state["pending_ask_dates"] == [], (
            "pending_ask_dates must be cleared after resolution"
        )
        # The pipeline enters queue mode: meal_planning_queue is set
        queue = state.get("meal_planning_queue", [])
        assert "2026-03-27|dinner" in queue, f"Expected 2026-03-27|dinner in queue, got {queue!r}"
        assert "2026-03-28|dinner" in queue, f"Expected 2026-03-28|dinner in queue, got {queue!r}"
        assert not any(m in str(queue) for m in ["|breakfast", "|lunch"]), (
            f"Only dinner should be queued, got {queue!r}"
        )

    @pytest.mark.asyncio
    async def test_breakfast_and_dinner_builds_correct_queue(self):
        """
        '早餐和晚饭' with two pending dates must produce 4 slots.
        """
        from app.modules.plan_ahead_state import get_plan_state
        self._seed_state(["2026-03-27", "2026-03-28"])

        out = await self._run_execute("早餐和晚饭")
        state = out["state"]

        queue = state.get("meal_planning_queue", [])
        expected = {
            "2026-03-27|breakfast", "2026-03-27|dinner",
            "2026-03-28|breakfast", "2026-03-28|dinner",
        }
        assert set(queue) == expected, (
            f"Expected exactly {expected}, got {set(queue)!r}"
        )

    @pytest.mark.asyncio
    async def test_all_meals_keyword_queues_three_meals(self):
        """
        '三餐都要' with two pending dates must queue 6 slots (3 meals × 2 days).
        """
        from app.modules.plan_ahead_state import get_plan_state
        self._seed_state(["2026-03-27", "2026-03-28"])

        out = await self._run_execute("三餐都要")
        state = out["state"]

        queue = state.get("meal_planning_queue", [])
        assert len(queue) == 6, f"Expected 6 slots for 三餐都要 × 2 days, got {queue!r}"
        for date in ["2026-03-27", "2026-03-28"]:
            for meal in ["breakfast", "lunch", "dinner"]:
                assert f"{date}|{meal}" in queue, (
                    f"Missing {date}|{meal} in {queue!r}"
                )

    @pytest.mark.asyncio
    async def test_all_keyword_全天_queues_three_meals(self):
        """'全天' is also a three-meals-all keyword."""
        from app.modules.plan_ahead_state import get_plan_state
        self._seed_state(["2026-03-27"])

        out = await self._run_execute("全天都计划")
        state = out["state"]

        queue = state.get("meal_planning_queue", [])
        assert len(queue) == 3, f"Expected 3 slots for 全天 × 1 day, got {queue!r}"

    @pytest.mark.asyncio
    async def test_unclear_meal_type_returns_reask(self):
        """
        When pending_ask_dates is set but the user reply has no meal keyword,
        the pipeline must re-ask (action=ask) and keep pending_ask_dates in state.
        """
        from app.modules.plan_ahead_state import get_plan_state
        self._seed_state(["2026-03-27", "2026-03-28"])

        out = await self._run_execute("随便")  # no meal keyword
        state = out["state"]

        # pending_ask_dates must still be set (not consumed)
        assert state["pending_ask_dates"] == ["2026-03-27", "2026-03-28"], (
            "pending_ask_dates must remain when meal type is unclear"
        )
        # Queue mode must NOT have been entered
        assert state.get("meal_planning_queue", []) == [], (
            "meal_planning_queue must stay empty when meal type is unclear"
        )

    @pytest.mark.asyncio
    async def test_no_pending_ask_dates_skips_phase1apre(self):
        """
        When pending_ask_dates is empty, Phase 1a-pre must NOT fire, and the
        normal pipeline flow continues (state is unchanged by Phase 1a-pre).
        """
        from app.modules.plan_ahead_state import get_plan_state
        # No pending_ask_dates seeded — last_action is None (fresh session)
        # This should fall through to Phase 1a normally.
        out = await self._run_execute("只计划晚饭")
        state = out["state"]
        # pending_ask_dates should remain []
        assert state["pending_ask_dates"] == [], (
            "pending_ask_dates must remain [] when not seeded"
        )
