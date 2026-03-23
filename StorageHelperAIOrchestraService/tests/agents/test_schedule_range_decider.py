# -*- coding: utf-8 -*-
"""
Tests for ScheduleRangeDecider — time range fetching for schedule queries.

Design
------
ScheduleRangeDecider asks the LLM: "what is the MINIMUM date range of schedule
data needed to fully answer this query?"

Key property: the LLM reasons about the problem semantically, so no hardcoded
keyword lists are needed.  Any operation that references multiple dates (move,
reschedule, compare) must cause the LLM to return a range spanning ALL of them.

Bug that motivated these tests
-------------------------------
When the user said "今天早上计划移到明天" (move today's morning to tomorrow),
the LLM previously returned only tomorrow's range (2026-03-23 ~ 2026-03-24),
missing today's schedules.  The AI then said "you have no morning plan today".

Root fix: the prompt now frames the question as a data-retrieval problem, so
the LLM reasons "I need today's data (source) AND tomorrow's data (destination)".

Test strategy
-------------
These tests mock `_parse_range_llm` to return exactly what a correct LLM response
looks like, then verify that `decide()` propagates that range unchanged.
They also verify the fallback path (no api_url) and the no-fetch path.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from typing import Optional

from app.agents.scheduling_agent import ScheduleRangeDecider, ScheduleSessionContext, TimeRange


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_context(query: str, timezone: str = "America/Los_Angeles") -> ScheduleSessionContext:
    return ScheduleSessionContext(
        current_query=query,
        history=[],
        user_timezone=timezone,
    )


def _today_start(timezone: Optional[str] = None) -> datetime:
    decider = ScheduleRangeDecider()
    now = decider._now_tz(timezone)
    return datetime.combine(now.date(), datetime.min.time())


def _date_offset(days: int, timezone: Optional[str] = None) -> datetime:
    return _today_start(timezone) + timedelta(days=days)


# ── Core regression: move query must cover source + destination ───────────────

class TestMoveQueryCoversSourceAndDestination:
    """
    When the LLM correctly reasons that a move query needs both source and
    destination, decide() must pass that range through unchanged.
    """

    @pytest.mark.asyncio
    async def test_move_today_to_tomorrow_llm_returns_correct_range(self):
        """
        LLM reason: "need today (source) + tomorrow (destination)"
        → range = today ~ day-after-tomorrow
        This is the exact regression scenario.
        """
        decider = ScheduleRangeDecider()
        ctx = _make_context("今天早上计划移到明天")
        today = _today_start(ctx.user_timezone)
        correct_range = TimeRange(start=today, end=today + timedelta(days=2))

        with patch.object(decider, "_parse_range_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = correct_range
            result = await decider.decide(ctx, api_url="http://fake-llm/")

        assert result is not None
        assert result.start == today, (
            f"Range start {result.start.date()} should be today {today.date()} "
            "to include source schedule data"
        )
        assert result.end >= today + timedelta(days=2), (
            f"Range end {result.end.date()} should cover at least tomorrow"
        )

    @pytest.mark.asyncio
    async def test_reschedule_monday_to_wednesday_covers_both(self):
        """
        LLM reason: "need Monday (source) + Wednesday (destination)"
        → range spans Monday to day-after-Wednesday
        """
        decider = ScheduleRangeDecider()
        ctx = _make_context("把周一的计划改到周三")
        today = _today_start(ctx.user_timezone)
        # Simulate LLM returning Mon–Thu span
        monday = today  # simplified: just check that start < end spans at least 3 days
        wednesday_plus1 = today + timedelta(days=3)
        multi_day_range = TimeRange(start=monday, end=wednesday_plus1)

        with patch.object(decider, "_parse_range_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = multi_day_range
            result = await decider.decide(ctx, api_url="http://fake-llm/")

        assert result is not None
        span = (result.end - result.start).days
        assert span >= 3, (
            f"A Mon→Wed move needs at least 3 days of data, got span={span}"
        )

    @pytest.mark.asyncio
    async def test_move_query_range_preserved_exactly(self):
        """decide() must not modify the range returned by _parse_range_llm."""
        decider = ScheduleRangeDecider()
        ctx = _make_context("今天早上计划移到明天")
        today = _today_start(ctx.user_timezone)
        llm_range = TimeRange(start=today, end=today + timedelta(days=2))

        with patch.object(decider, "_parse_range_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = llm_range
            result = await decider.decide(ctx, api_url="http://fake-llm/")

        assert result.start == llm_range.start
        assert result.end == llm_range.end


# ── Simple single-date queries ────────────────────────────────────────────────

class TestSingleDateQueries:
    """For simple queries, the LLM returns a compact range and decide() passes it through."""

    @pytest.mark.asyncio
    async def test_today_query_returns_today_range(self):
        decider = ScheduleRangeDecider()
        ctx = _make_context("今天午饭吃什么")
        today = _today_start(ctx.user_timezone)
        today_range = TimeRange(start=today, end=today + timedelta(days=1))

        with patch.object(decider, "_parse_range_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = today_range
            result = await decider.decide(ctx, api_url="http://fake-llm/")

        assert result is not None
        assert result.start == today
        assert result.end == today + timedelta(days=1)

    @pytest.mark.asyncio
    async def test_tomorrow_query_returns_tomorrow_range(self):
        decider = ScheduleRangeDecider()
        ctx = _make_context("明天有什么计划")
        today = _today_start(ctx.user_timezone)
        tomorrow = today + timedelta(days=1)
        tomorrow_range = TimeRange(start=tomorrow, end=tomorrow + timedelta(days=1))

        with patch.object(decider, "_parse_range_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = tomorrow_range
            result = await decider.decide(ctx, api_url="http://fake-llm/")

        assert result is not None
        assert result.start == tomorrow

    @pytest.mark.asyncio
    async def test_next_week_query_returns_week_range(self):
        decider = ScheduleRangeDecider()
        ctx = _make_context("下周有什么安排")
        today = _today_start(ctx.user_timezone)
        # Simulate LLM returning a 7-day span
        next_monday = today + timedelta(days=7)
        week_range = TimeRange(start=next_monday, end=next_monday + timedelta(days=7))

        with patch.object(decider, "_parse_range_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = week_range
            result = await decider.decide(ctx, api_url="http://fake-llm/")

        assert result is not None
        span = (result.end - result.start).days
        assert span == 7


# ── Fallback and no-fetch paths ───────────────────────────────────────────────

class TestFallbackPaths:

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_default_7_day_range(self):
        """When _parse_range_llm returns None, decide() falls back to default 7-day range."""
        decider = ScheduleRangeDecider()
        ctx = _make_context("今天早上计划移到明天")

        with patch.object(decider, "_parse_range_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = None
            result = await decider.decide(ctx, api_url="http://fake-llm/")

        today = _today_start(ctx.user_timezone)
        assert result is not None
        assert result.start <= today + timedelta(seconds=1), "Fallback must start at today"
        assert (result.end - result.start).days >= 7, "Fallback must span at least 7 days"

    @pytest.mark.asyncio
    async def test_no_api_url_uses_default_range(self):
        """With no api_url, the LLM is never called; default range is used."""
        decider = ScheduleRangeDecider()
        ctx = _make_context("今天早上计划移到明天")
        result = await decider.decide(ctx, api_url=None)

        today = _today_start(ctx.user_timezone)
        assert result is not None
        assert result.start <= today + timedelta(seconds=1)
        assert (result.end - result.start).days >= 7

    @pytest.mark.asyncio
    async def test_no_fetch_intent_returns_none(self):
        """Queries with no schedule intent return None (no fetch needed)."""
        decider = ScheduleRangeDecider()
        ctx = _make_context("你好")
        result = await decider.decide(ctx, api_url="http://fake-llm/")
        assert result is None, "Greeting should not trigger a schedule fetch"

    @pytest.mark.asyncio
    async def test_precomputed_range_is_used_directly(self):
        """
        SchedulingResponseGenerator.generate_context accepts a pre-computed
        time_range to avoid double LLM calls.  Verify the range is passed
        through unchanged.
        """
        from app.agents.scheduling_agent import SchedulingResponseGenerator
        generator = SchedulingResponseGenerator()
        ctx = _make_context("今天午饭")
        today = _today_start(ctx.user_timezone)
        precomputed = TimeRange(start=today, end=today + timedelta(days=1))

        with patch.object(generator._range_decider, "_parse_range_llm", new_callable=AsyncMock) as mock_llm:
            result = await generator.generate_context(
                context=ctx,
                schedules=[],
                time_range=precomputed,
                api_url="http://fake-llm/",
            )
            mock_llm.assert_not_called()

        assert result["time_range"].start == precomputed.start
        assert result["time_range"].end == precomputed.end
