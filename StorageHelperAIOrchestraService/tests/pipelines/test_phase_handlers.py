# -*- coding: utf-8 -*-
"""
Tests for the PlanAheadPipeline phase handlers extracted during the
"God-class refactor".

Each handler has a clear, testable contract:
  • Input  : state dict (fully controlled — no DB needed)
  • Output : result dict  ← early exit, execute() returns this immediately
             None         ← fall through to next phase
  • Side effects: state mutations via update_plan_state()

Testing strategy
----------------
  get_phase()                    — pure function, no mocks
  _handle_queue_advance()        — synchronous, no mocks
  _handle_ask_dates_resolution() — keyword matching, no LLM mock
  _handle_date_confirmation()    — mock _classify_date_confirmation
  _handle_phase1a_queue_init()   — mock _init_planning_queue
  _sync_state()                  — mock plan_ahead_agent + get_plan_state
  execute() queue-mode variables — regression for NameError(_meal_queue / _meal_total)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

from app.modules.plan_ahead_state import (
    PipelinePhase,
    get_phase,
    _plan_states,
    update_plan_state,
    get_plan_state,
)
from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
from app.skills.plan_ahead import (
    ClassifyDateConfirmationSkill,
    ClassifyDishIntentSkill,
    ClassifyMealActionSkill,
    InitPlanningQueueSkill,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

OWNER = 9001


@pytest.fixture(autouse=True)
def _clear_state():
    """Isolate every test by wiping in-memory state before and after."""
    _plan_states.clear()
    yield
    _plan_states.clear()


def _pipeline() -> PlanAheadPipeline:
    return PlanAheadPipeline(gemini_api_url="http://fake-gemini/")


def _state(**kwargs) -> Dict[str, Any]:
    """Build a minimal state dict with sensible defaults."""
    base: Dict[str, Any] = {
        "meal_plan": {}, "meal_plan_slots": {}, "dish_ingredients": {},
        "shopping_list": [], "is_draft": False, "last_pipeline_action": None,
        "pending_planning_queue": [], "meal_planning_queue": [],
        "meal_planning_total": 0, "confirmation_retry_count": 0,
        "pending_ask_dates": [],
    }
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# 1. PipelinePhase enum  +  get_phase()
#    Pure function — no mocks, no IO.
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPhase:
    """get_phase() must derive the correct PipelinePhase from any state dict."""

    def test_idle_on_empty_state(self):
        assert get_phase(_state()) == PipelinePhase.IDLE

    def test_in_queue_when_meal_planning_queue_populated(self):
        s = _state(meal_planning_queue=["2026-03-25|lunch"])
        assert get_phase(s) == PipelinePhase.IN_QUEUE

    def test_awaiting_date_confirm(self):
        s = _state(
            pending_planning_queue=["2026-03-25|lunch"],
            last_pipeline_action="ask_confirm_dates",
        )
        assert get_phase(s) == PipelinePhase.AWAITING_DATE_CONFIRM

    def test_awaiting_clarification_when_last_action_is_ask(self):
        s = _state(last_pipeline_action="ask")
        assert get_phase(s) == PipelinePhase.AWAITING_CLARIFICATION

    def test_draft_active_when_is_draft_true(self):
        s = _state(is_draft=True)
        assert get_phase(s) == PipelinePhase.DRAFT_ACTIVE

    def test_in_queue_takes_priority_over_draft(self):
        """IN_QUEUE must win even if is_draft is somehow True."""
        s = _state(meal_planning_queue=["2026-03-25|dinner"], is_draft=True)
        assert get_phase(s) == PipelinePhase.IN_QUEUE

    def test_pending_queue_without_confirm_action_is_not_awaiting(self):
        """pending_planning_queue alone is not enough — action must match too."""
        s = _state(pending_planning_queue=["2026-03-25|lunch"], last_pipeline_action="ask")
        # last_pipeline_action="ask" → AWAITING_CLARIFICATION, not AWAITING_DATE_CONFIRM
        assert get_phase(s) == PipelinePhase.AWAITING_CLARIFICATION


# ─────────────────────────────────────────────────────────────────────────────
# 2. _handle_queue_advance()
#    Synchronous, no LLM, no DB — easiest to test exhaustively.
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleQueueAdvance:
    """_handle_queue_advance() must return the current target slot string
    (or None) after applying the correct queue transition."""

    def _call(self, user_input: str, state: Dict[str, Any]) -> Any:
        update_plan_state(
            owner_id=OWNER,
            meal_planning_queue=state.get("meal_planning_queue", []),
            meal_planning_total=state.get("meal_planning_total", 0),
            last_pipeline_action=state.get("last_pipeline_action"),
            merge=False,
        )
        return _pipeline()._handle_queue_advance(OWNER, user_input, get_plan_state(OWNER))

    def test_returns_none_when_no_queue(self):
        assert self._call("继续", _state()) is None

    def test_returns_first_slot_when_queue_active_but_no_advance_needed(self):
        """If last_action is NOT queue_day_complete, return queue[0] without advancing."""
        s = _state(meal_planning_queue=["2026-03-25|lunch", "2026-03-25|dinner"])
        result = self._call("继续", s)
        assert result == "2026-03-25|lunch"

    def test_advances_to_next_slot_on_neutral_reply(self):
        """After queue_day_complete + neutral reply → queue advances, slot[0] is still first."""
        s = _state(
            meal_planning_queue=["2026-03-25|lunch", "2026-03-25|dinner"],
            meal_planning_total=2,
            last_pipeline_action="queue_day_complete",
        )
        result = self._call("好的", s)
        # After advance, last_pipeline_action is cleared.  Queue stays intact.
        # The returned slot is queue[0] after the call (no items are popped here —
        # popping happens in the LLM-driven confirm flow).
        assert result is not None  # some slot available

    def test_stays_on_current_slot_with_modification_intent(self):
        """Modification intent ('改') → do NOT advance, return queue[0]."""
        s = _state(
            meal_planning_queue=["2026-03-25|lunch", "2026-03-25|dinner"],
            meal_planning_total=2,
            last_pipeline_action="queue_day_complete",
        )
        result = self._call("我想改一下", s)
        assert result == "2026-03-25|lunch"

    def test_save_intent_pauses_queue(self):
        """Save intent ('保存') → queue preserved in state, returns None this turn."""
        s = _state(
            meal_planning_queue=["2026-03-25|lunch", "2026-03-25|dinner"],
            meal_planning_total=2,
            last_pipeline_action="queue_day_complete",
        )
        result = self._call("保存", s)
        assert result is None  # queue paused for this turn

    def test_queue_paused_but_state_preserves_items(self):
        """After a save-intent pause, the queue in state is NOT cleared."""
        s = _state(
            meal_planning_queue=["2026-03-25|dinner"],
            meal_planning_total=1,
            last_pipeline_action="queue_day_complete",
        )
        update_plan_state(
            owner_id=OWNER,
            meal_planning_queue=s["meal_planning_queue"],
            meal_planning_total=s["meal_planning_total"],
            last_pipeline_action=s["last_pipeline_action"],
            merge=False,
        )
        _pipeline()._handle_queue_advance(OWNER, "保存", get_plan_state(OWNER))
        after = get_plan_state(OWNER)
        assert after.get("meal_planning_queue"), "Queue must survive a save-intent pause"


# ─────────────────────────────────────────────────────────────────────────────
# 3. _handle_ask_dates_resolution()
#    No LLM — pure keyword matching.  Returns None on success or result on re-ask.
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleAskDatesResolution:
    """Phase 1a-pre: user answers 'which meal?' after Fresh-plan guard asked."""

    async def _call(self, user_input: str, pending_ask_dates, *, extra_state=None):
        state = _state(
            pending_ask_dates=pending_ask_dates,
            last_pipeline_action="ask",
            **(extra_state or {}),
        )
        update_plan_state(
            owner_id=OWNER,
            pending_ask_dates=pending_ask_dates,
            last_pipeline_action="ask",
            merge=False,
        )
        return await _pipeline()._handle_ask_dates_resolution(
            OWNER, user_input, state, is_currently_draft=False, intent_result=None,
        )

    @pytest.mark.asyncio
    async def test_not_applicable_when_no_pending_ask_dates(self):
        state = _state(last_pipeline_action="ask")
        result = await _pipeline()._handle_ask_dates_resolution(
            OWNER, "晚饭", state, is_currently_draft=False, intent_result=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_not_applicable_when_last_action_not_ask(self):
        state = _state(pending_ask_dates=["2026-03-25"], last_pipeline_action="add")
        result = await _pipeline()._handle_ask_dates_resolution(
            OWNER, "晚饭", state, is_currently_draft=False, intent_result=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_dinner_keyword_activates_dinner_queue(self):
        result = await self._call("晚饭", ["2026-03-25"])
        assert result is None  # success → fall through
        after = get_plan_state(OWNER)
        assert "2026-03-25|dinner" in after.get("meal_planning_queue", [])
        assert not after.get("pending_ask_dates")

    @pytest.mark.asyncio
    async def test_lunch_keyword_activates_lunch_queue(self):
        result = await self._call("午餐", ["2026-03-25", "2026-03-26"])
        assert result is None
        after = get_plan_state(OWNER)
        queue = after.get("meal_planning_queue", [])
        assert "2026-03-25|lunch" in queue
        assert "2026-03-26|lunch" in queue

    @pytest.mark.asyncio
    async def test_all_meals_keyword_activates_three_meal_queue(self):
        result = await self._call("三餐都要", ["2026-03-25"])
        assert result is None
        after = get_plan_state(OWNER)
        queue = after.get("meal_planning_queue", [])
        for mt in ("breakfast", "lunch", "dinner"):
            assert f"2026-03-25|{mt}" in queue

    @pytest.mark.asyncio
    async def test_unclear_reply_returns_re_ask_result(self):
        result = await self._call("随便", ["2026-03-25"])
        assert result is not None, "Unclear reply should return a re-ask result"
        assert "response" in result
        assert "2026-03-25" in result["response"]

    @pytest.mark.asyncio
    async def test_re_ask_result_preserves_pending_ask_dates_in_action_data(self):
        result = await self._call("嗯", ["2026-03-25"])
        assert result is not None
        assert result.get("action_data", {}).get("pending_ask_dates") == ["2026-03-25"]
        assert result.get("action_data", {}).get("ask_type") == "meal_slot_selector"

    @pytest.mark.asyncio
    async def test_not_applicable_when_in_draft(self):
        state = _state(pending_ask_dates=["2026-03-25"], last_pipeline_action="ask", is_draft=True)
        result = await _pipeline()._handle_ask_dates_resolution(
            OWNER, "晚饭", state, is_currently_draft=True, intent_result=None,
        )
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. _handle_date_confirmation()
#    Mocks _classify_date_confirmation. Tests all 4 intent paths.
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleDateConfirmation:
    """Phase 1b: user's response to the date-range confirmation question."""

    # Must be fully-symmetric (dates × meal_times) so that the no-op correction
    # path (new_dates=[], new_meal_times=None → derived from queue) produces the
    # same SLOTS and hits the else-branch in _handle_date_confirmation.
    SLOTS = ["2026-03-25|lunch", "2026-03-25|dinner", "2026-03-26|lunch", "2026-03-26|dinner"]

    def _setup_state(self):
        update_plan_state(
            owner_id=OWNER,
            pending_planning_queue=self.SLOTS,
            last_pipeline_action="ask_confirm_dates",
            confirmation_retry_count=0,
            merge=False,
        )
        return get_plan_state(OWNER)

    async def _call(self, clf_return: dict, user_input: str = "好的", *, explicit_dish=False):
        state = self._setup_state()
        # Patch the skill class execute method (pipeline now calls skills, not self.methods)
        with patch.object(ClassifyDateConfirmationSkill, "execute", new_callable=AsyncMock, return_value=clf_return):
            p = _pipeline()
            return await p._handle_date_confirmation(
                OWNER, user_input, state,
                is_explicit_dish=explicit_dish,
                user_timezone="America/Los_Angeles",
                language="zh",
                intent_result=None,
            )

    @pytest.mark.asyncio
    async def test_returns_none_when_no_pending_queue(self):
        """No pending queue → not in this phase → None."""
        state = _state()
        result = await _pipeline()._handle_date_confirmation(
            OWNER, "好的", state,
            is_explicit_dish=False,
            user_timezone=None, language="zh", intent_result=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_explicit_dish_clears_queue_and_returns_none(self):
        """Explicit dish in pending-confirm phase → clear queue, fall through."""
        state = self._setup_state()
        result = await _pipeline()._handle_date_confirmation(
            OWNER, "想吃红烧肉", state,
            is_explicit_dish=True,
            user_timezone=None, language="zh", intent_result=None,
        )
        assert result is None  # fall through
        after = get_plan_state(OWNER)
        assert not after.get("pending_planning_queue"), "Queue must be cleared"

    @pytest.mark.asyncio
    async def test_confirmed_activates_queue_and_returns_none(self):
        """Confirmed → meal_planning_queue populated, None returned (fall through)."""
        result = await self._call({"intent": "confirmed"})
        assert result is None  # fall through
        after = get_plan_state(OWNER)
        assert after.get("meal_planning_queue") == self.SLOTS
        assert not after.get("pending_planning_queue")

    @pytest.mark.asyncio
    async def test_corrected_with_new_dates_returns_new_confirm_message(self):
        """Corrected → update queue, re-ask with new dates, return result dict."""
        new_slots = ["2026-03-27|lunch", "2026-03-27|dinner"]
        clf = {
            "intent": "corrected",
            "new_dates": ["2026-03-27"],
            "new_meal_times": ["lunch", "dinner"],
        }
        result = await self._call(clf)
        assert result is not None, "corrected intent must return a result"
        assert "response" in result
        after = get_plan_state(OWNER)
        assert after.get("pending_planning_queue") == new_slots
        assert after.get("confirmation_retry_count") == 0

    @pytest.mark.asyncio
    async def test_noop_correction_first_retry_asks_targeted_question(self):
        """No-op correction (same slots returned) → bump retry, targeted nudge."""
        clf = {"intent": "corrected", "new_dates": [], "new_meal_times": None}
        result = await self._call(clf)
        assert result is not None
        assert "response" in result
        after = get_plan_state(OWNER)
        assert after.get("confirmation_retry_count") == 1
        # Queue must still be the original
        assert after.get("pending_planning_queue") == self.SLOTS

    @pytest.mark.asyncio
    async def test_noop_correction_second_retry_degrades_to_fresh_start(self):
        """Two consecutive no-op corrections → degrade, clear queue entirely."""
        # _setup_state() puts pending_planning_queue + last_pipeline_action in place;
        # then bump confirmation_retry_count to 1 so the next noop triggers degrade.
        self._setup_state()
        update_plan_state(owner_id=OWNER, confirmation_retry_count=1)
        clf = {"intent": "corrected", "new_dates": [], "new_meal_times": None}
        state = get_plan_state(OWNER)
        p = _pipeline()
        with patch.object(ClassifyDateConfirmationSkill, "execute", new_callable=AsyncMock, return_value=clf):
            result = await p._handle_date_confirmation(
                OWNER, "不是", state,
                is_explicit_dish=False,
                user_timezone=None, language="zh", intent_result=None,
            )
        assert result is not None
        after = get_plan_state(OWNER)
        assert not after.get("pending_planning_queue"), "Queue must be cleared after degrade"
        assert not after.get("last_pipeline_action")

    @pytest.mark.asyncio
    async def test_unclear_with_recoverable_date_re_proposes_recovered_range(self):
        """Unclear + date in reply → date recovery, re-propose new range."""
        recovery_slots = ["2026-03-28|dinner"]
        state = self._setup_state()
        p = _pipeline()
        with patch.object(
            ClassifyDateConfirmationSkill, "execute",
            new_callable=AsyncMock, return_value={"intent": "unclear"},
        ), patch.object(
            InitPlanningQueueSkill, "execute",
            new_callable=AsyncMock,
            return_value={"has_planning_intent": True, "slots": recovery_slots, "raw": {}},
        ):
            result = await p._handle_date_confirmation(
                OWNER, "还有周六", state,
                is_explicit_dish=False,
                user_timezone=None, language="zh", intent_result=None,
            )
        assert result is not None
        after = get_plan_state(OWNER)
        assert after.get("pending_planning_queue") == recovery_slots

    @pytest.mark.asyncio
    async def test_unclear_without_recoverable_date_re_asks_original_question(self):
        """Unclear + no date info → re-ask the original confirmation question."""
        state = self._setup_state()
        p = _pipeline()
        with patch.object(
            ClassifyDateConfirmationSkill, "execute",
            new_callable=AsyncMock, return_value={"intent": "unclear"},
        ), patch.object(
            InitPlanningQueueSkill, "execute",
            new_callable=AsyncMock,
            return_value={"has_planning_intent": False, "slots": [], "raw": {}},
        ):
            result = await p._handle_date_confirmation(
                OWNER, "啊", state,
                is_explicit_dish=False,
                user_timezone=None, language="zh", intent_result=None,
            )
        assert result is not None
        # Queue unchanged
        after = get_plan_state(OWNER)
        assert after.get("pending_planning_queue") == self.SLOTS


# ─────────────────────────────────────────────────────────────────────────────
# 5. _handle_phase1a_queue_init()
#    Mocks _init_planning_queue.
# ─────────────────────────────────────────────────────────────────────────────

class TestHandlePhase1aQueueInit:
    """Phase 1a: detect fresh planning intent and either ask or activate queue."""

    async def _call(self, user_input: str, init_queue_return, *, is_explicit=False, extra_state=None):
        state = _state(**(extra_state or {}))
        update_plan_state(owner_id=OWNER, merge=False)  # reset
        # init_queue_return is List[str] (slots) from old API; wrap in skill result dict
        skill_return = {
            "has_planning_intent": bool(init_queue_return),
            "slots": init_queue_return,
            "raw": {},
        }
        p = _pipeline()
        with patch.object(InitPlanningQueueSkill, "execute", new_callable=AsyncMock, return_value=skill_return):
            return await p._handle_phase1a_queue_init(
                OWNER, user_input, state,
                is_currently_draft=False,
                is_explicit_dish=is_explicit,
                dish_clf={"dishes": [], "is_explicit": is_explicit},
                language="zh",
                user_timezone="America/Los_Angeles",
                intent_result=None,
            )

    @pytest.mark.asyncio
    async def test_returns_none_when_no_planning_intent(self):
        result = await self._call("今天晚上吃什么", init_queue_return=[])
        assert result is None

    @pytest.mark.asyncio
    async def test_explicit_dish_skips_queue_init(self):
        """Explicit dish → no InitPlanningQueueSkill call, return None."""
        state = _state()
        p = _pipeline()
        with patch.object(
            InitPlanningQueueSkill, "execute",
            new_callable=AsyncMock,
            return_value={"has_planning_intent": True, "slots": ["2026-03-25|dinner"], "raw": {}},
        ) as mock_exec:
            result = await p._handle_phase1a_queue_init(
                OWNER, "想吃红烧肉", state,
                is_currently_draft=False, is_explicit_dish=True,
                dish_clf={"dishes": ["红烧肉"], "is_explicit": True},
                language="zh", user_timezone=None, intent_result=None,
            )
        assert result is None
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_slot_bypasses_confirmation_and_activates_queue(self):
        """Single slot → no confirmation question, queue activated immediately."""
        result = await self._call("今天晚上", init_queue_return=["2026-03-25|dinner"])
        assert result is None  # fall through — queue activated without asking
        after = get_plan_state(OWNER)
        assert after.get("meal_planning_queue") == ["2026-03-25|dinner"]
        assert not after.get("pending_planning_queue")

    @pytest.mark.asyncio
    async def test_multi_slot_emits_confirmation_question(self):
        """Multiple slots → ask for date confirmation, return result dict."""
        slots = ["2026-03-25|lunch", "2026-03-25|dinner", "2026-03-26|lunch"]
        result = await self._call("帮我规划这两天", init_queue_return=slots)
        assert result is not None, "Multi-slot must return a confirmation question"
        assert "response" in result
        after = get_plan_state(OWNER)
        assert after.get("pending_planning_queue") == slots
        assert after.get("last_pipeline_action") == "ask_confirm_dates"

    @pytest.mark.asyncio
    async def test_skipped_when_draft_is_active(self):
        """If is_draft=True, Phase 1a must not run."""
        state = _state(is_draft=True)
        p = _pipeline()
        with patch.object(
            InitPlanningQueueSkill, "execute",
            new_callable=AsyncMock,
            return_value={"has_planning_intent": True, "slots": ["2026-03-25|dinner"], "raw": {}},
        ) as mock_exec:
            result = await p._handle_phase1a_queue_init(
                OWNER, "今天晚上", state,
                is_currently_draft=True, is_explicit_dish=False,
                dish_clf={}, language="zh", user_timezone=None, intent_result=None,
            )
        assert result is None
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_skipped_when_queue_already_active(self):
        """Already in queue mode → Phase 1a must not interfere."""
        state = _state(meal_planning_queue=["2026-03-25|dinner"])
        p = _pipeline()
        with patch.object(
            InitPlanningQueueSkill, "execute",
            new_callable=AsyncMock,
            return_value={"has_planning_intent": True, "slots": ["2026-03-26|lunch"], "raw": {}},
        ) as mock_exec:
            result = await p._handle_phase1a_queue_init(
                OWNER, "好", state,
                is_currently_draft=False, is_explicit_dish=False,
                dish_clf={}, language="zh", user_timezone=None, intent_result=None,
            )
        assert result is None
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_skipped_when_pending_queue_exists(self):
        """pending_planning_queue set → already in Phase 1b, skip Phase 1a."""
        state = _state(pending_planning_queue=["2026-03-25|lunch"], last_pipeline_action="ask_confirm_dates")
        p = _pipeline()
        with patch.object(
            InitPlanningQueueSkill, "execute",
            new_callable=AsyncMock,
            return_value={"has_planning_intent": True, "slots": ["2026-03-25|dinner"], "raw": {}},
        ) as mock_exec:
            result = await p._handle_phase1a_queue_init(
                OWNER, "好", state,
                is_currently_draft=False, is_explicit_dish=False,
                dish_clf={}, language="zh", user_timezone=None, intent_result=None,
            )
        assert result is None
        mock_exec.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 6. _sync_state()  —  regression test for "old_state not defined" bug
#    (NameError when action=recommend selected an option after suggest_options)
#
# Root cause: _sync_state() was returning only current_state; execute() still
# referenced old_state (the raw DB snapshot) at several downstream points.
# Fix: _sync_state() now returns (old_state, current_state) tuple.
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncStateReturnsTuple:
    """_sync_state() must return a 2-tuple (old_state, current_state)."""

    @pytest.mark.asyncio
    async def test_returns_tuple_of_two_dicts(self):
        """Return value must be a 2-tuple of dicts, not a bare dict."""
        p = _pipeline()
        fake_db_state = {"meal_plan": {"2026-03-20": "lunch"}, "schedule_id": 42}
        p.plan_ahead_agent = MagicMock()
        p.plan_ahead_agent.sync_meal_plan_from_database = AsyncMock(return_value=fake_db_state)
        update_plan_state(owner_id=OWNER, merge=False)  # ensure clean in-memory state

        result = await p._sync_state(OWNER, storage_client=MagicMock(), context=None)

        assert isinstance(result, tuple), "_sync_state must return a tuple"
        assert len(result) == 2, "_sync_state must return exactly 2 elements"
        old_s, cur_s = result
        assert isinstance(old_s, dict), "old_state must be a dict"
        assert isinstance(cur_s, dict), "current_state must be a dict"

    @pytest.mark.asyncio
    async def test_old_state_is_raw_db_snapshot(self):
        """old_state must be the dict returned by sync_meal_plan_from_database."""
        p = _pipeline()
        fake_db_state = {
            "meal_plan": {"2026-03-20": "lunch"},
            "meal_plan_slots": {"2026-03-20": {"lunch": ["红烧肉"]}},
            "schedule_id": 42,
        }
        p.plan_ahead_agent = MagicMock()
        p.plan_ahead_agent.sync_meal_plan_from_database = AsyncMock(return_value=fake_db_state)
        update_plan_state(owner_id=OWNER, merge=False)

        old_s, _ = await p._sync_state(OWNER, storage_client=MagicMock(), context=None)

        assert old_s.get("schedule_id") == 42
        assert "2026-03-20" in (old_s.get("meal_plan") or {})

    @pytest.mark.asyncio
    async def test_current_state_reflects_merged_in_memory(self):
        """current_state must reflect the post-sync in-memory state (from get_plan_state)."""
        p = _pipeline()
        fake_db_state = {
            "meal_plan": {"2026-03-22": "dinner"},
            "meal_plan_slots": {"2026-03-22": {"dinner": ["水煮鱼"]}},
            "dish_ingredients": {"水煮鱼": [{"name": "草鱼", "qty": "500g"}]},
            "shopping_list": [],
            "schedule_id": 99,
        }
        p.plan_ahead_agent = MagicMock()
        p.plan_ahead_agent.sync_meal_plan_from_database = AsyncMock(return_value=fake_db_state)
        update_plan_state(owner_id=OWNER, merge=False)

        _, cur_s = await p._sync_state(OWNER, storage_client=MagicMock(), context=None)

        assert cur_s.get("schedule_id") == 99
        assert "2026-03-22" in (cur_s.get("meal_plan_slots") or {})

    @pytest.mark.asyncio
    async def test_context_override_applied_to_current_state_only(self):
        """UI context override should appear in current_state but not affect old_state."""
        p = _pipeline()
        fake_db_state = {"meal_plan": {}, "schedule_id": None}
        p.plan_ahead_agent = MagicMock()
        p.plan_ahead_agent.sync_meal_plan_from_database = AsyncMock(return_value=fake_db_state)
        update_plan_state(owner_id=OWNER, merge=False)

        override_slots = {"2026-03-25": {"dinner": ["番茄炒蛋"]}}
        context = {
            "type": "plan_ahead",
            "data": {"meal_plan_slots": override_slots, "schedule_id": 77},
        }
        old_s, cur_s = await p._sync_state(OWNER, storage_client=MagicMock(), context=context)

        # Current state has the UI override
        assert cur_s.get("meal_plan_slots") == override_slots
        # old_state is the raw DB snapshot — should not contain UI-injected data
        assert (old_s.get("meal_plan_slots") or {}) != override_slots

    @pytest.mark.asyncio
    async def test_execute_unpacks_old_state_without_name_error(self):
        """
        Regression: selecting a suggest_options plan (action=recommend) must not
        raise NameError('old_state is not defined').

        This test mocks all IO boundaries and drives execute() just far enough to
        reach the post-LLM post-processing code that uses old_state.  A NameError
        would mean the _sync_state tuple-unpack fix is broken.
        """
        from unittest.mock import patch as _patch

        p = _pipeline()

        fake_db_state = {
            "meal_plan": {},
            "meal_plan_slots": {},
            "dish_ingredients": {},
            "shopping_list": [],
            "schedule_id": None,
        }
        p.plan_ahead_agent = MagicMock()
        p.plan_ahead_agent.sync_meal_plan_from_database = AsyncMock(return_value=fake_db_state)
        p.plan_ahead_agent.persist_meal_plan = AsyncMock(return_value=None)

        fake_storage = MagicMock()
        fake_storage.get_inventory_items = AsyncMock(return_value=[])

        # Stub every LLM call that execute() makes so it runs to completion
        with (
            _patch.object(p, "_classify_dish_intent", new_callable=AsyncMock,
                          return_value={"is_explicit": False, "dishes": []}),
            _patch.object(p, "_call_llm", new_callable=AsyncMock,
                          return_value=""),
            _patch.object(p, "_build_context", return_value="ctx"),
            _patch("app.pipelines.plan_ahead_pipeline.PlanAheadPipeline._fetch_inventory",
                   new_callable=AsyncMock, return_value=[]),
            _patch("app.agents.scheduling_agent.PlanAheadAgent.parse_structured_response",
                   return_value={"action": "ask", "meal_plan": {}, "meal_plan_slots": {},
                                 "dish_ingredients": {}, "shopping_list": [],
                                 "response": "请问您想吃什么？", "options": []}),
        ):
            update_plan_state(owner_id=OWNER, merge=False)
            try:
                await p.execute(
                    owner_id=OWNER,
                    user_input="今天晚上吃什么",
                    history=[],
                    user_timezone="America/Los_Angeles",
                    storage_client=fake_storage,
                )
            except NameError as exc:
                pytest.fail(f"NameError raised — old_state tuple-unpack is broken: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Queue-mode variables in execute() — regression for NameError
#
# Root cause: _meal_queue and _meal_total were defined only inside
# _handle_queue_advance() (a helper method), but execute() referenced them
# at the queue progress-tracking block (the "if _queue_target_slot and
# action == 'recommend':" block).
#
# Fix: read both variables from current_state immediately after the
# _handle_queue_advance() call, making them available in execute()'s scope.
# ─────────────────────────────────────────────────────────────────────────────

class TestQueueMealVariablesInExecute:
    """
    execute() must not raise NameError for _meal_queue or _meal_total when
    meal_planning_queue is active and the LLM returns action='recommend'.
    """

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _recommend_parsed(slot_date: str = "2026-03-22", meal: str = "dinner") -> dict:
        """Minimal structured-response dict that _call_llm would return for action=recommend."""
        return {
            "user_message": f"好的，已为您规划 {slot_date} {meal}：鸡蛋炒饭。",
            "meal_plan": {slot_date: ["鸡蛋炒饭"]},
            "meal_plan_slots": {slot_date: {meal: ["鸡蛋炒饭"]}},
            "dish_ingredients": {},
            "shopping_list": [],
            "action": "recommend",
            "target_date": slot_date,
            "options": [],
        }

    @staticmethod
    def _setup_queue(slots: list, total: int | None = None) -> dict:
        """
        Populate in-memory plan state with an active queue and return a
        fake DB state dict (used as the old_state returned by _sync_state).
        """
        _total = total if total is not None else len(slots)
        update_plan_state(
            owner_id=OWNER,
            meal_planning_queue=slots,
            meal_planning_total=_total,
            last_pipeline_action=None,
            merge=False,
        )
        return {
            "meal_plan": {},
            "meal_plan_slots": {},
            "dish_ingredients": {},
            "shopping_list": [],
            "schedule_id": 68,
        }

    @staticmethod
    def _make_pipeline_with_mocked_agent(fake_db_state: dict) -> "PlanAheadPipeline":
        p = _pipeline()
        p.plan_ahead_agent = MagicMock()
        p.plan_ahead_agent.sync_meal_plan_from_database = AsyncMock(
            return_value=fake_db_state
        )
        p.plan_ahead_agent.persist_meal_plan = AsyncMock(return_value=None)
        return p

    # ── Shared context manager for all execute() calls in this class ──────────

    @staticmethod
    def _patched(p: "PlanAheadPipeline", call_llm_return: dict):
        """
        Return a single combined context manager that patches all IO boundaries
        needed to drive execute() without any real network calls.
        """
        from contextlib import contextmanager, ExitStack
        from unittest.mock import patch as _patch

        @contextmanager
        def _cm():
            with ExitStack() as stack:
                stack.enter_context(
                    _patch.object(p, "_build_context", return_value="ctx")
                )
                stack.enter_context(
                    _patch.object(
                        p, "_call_llm",
                        new_callable=AsyncMock,
                        return_value=call_llm_return,
                    )
                )
                stack.enter_context(
                    _patch(
                        "app.pipelines.plan_ahead_pipeline.PlanAheadPipeline._fetch_inventory",
                        new_callable=AsyncMock,
                        return_value=[],
                    )
                )
                stack.enter_context(
                    _patch.object(
                        ClassifyDishIntentSkill,
                        "execute",
                        new_callable=AsyncMock,
                        return_value={"is_explicit": False, "dishes": [], "intent": "UNKNOWN"},
                    )
                )
                yield

        return _cm()

    # ── 7a. Regression: no NameError for _meal_queue / _meal_total ────────────

    @pytest.mark.asyncio
    async def test_no_name_error_when_queue_active_and_recommend(self):
        """
        Regression: NameError('name _meal_queue is not defined') must not be
        raised when meal_planning_queue is active and action='recommend'.

        Before the fix, _meal_queue and _meal_total were only defined inside
        _handle_queue_advance() and were not accessible in execute()'s scope.
        """
        fake_db_state = self._setup_queue(
            ["2026-03-22|dinner", "2026-03-23|lunch"], total=2
        )
        p = self._make_pipeline_with_mocked_agent(fake_db_state)
        fake_storage = MagicMock()
        fake_storage.get_inventory_items = AsyncMock(return_value=[])

        with self._patched(p, self._recommend_parsed()):
            try:
                await p.execute(
                    owner_id=OWNER,
                    user_input="选方案1：方案一：日式风味与家常菜",
                    history=[],
                    user_timezone="America/Los_Angeles",
                    storage_client=fake_storage,
                )
            except NameError as exc:
                pytest.fail(
                    f"NameError raised — _meal_queue/_meal_total not in execute() scope: {exc}"
                )

    # ── 7b. First slot planned → queue advances to remaining slots ────────────

    @pytest.mark.asyncio
    async def test_queue_advances_to_next_slot_after_recommend(self):
        """
        After a recommend in queue mode with 2 slots, the first slot is
        consumed and only the second slot remains in the queue.
        State must show last_pipeline_action='queue_day_complete'.
        """
        slots = ["2026-03-22|dinner", "2026-03-23|lunch"]
        fake_db_state = self._setup_queue(slots, total=2)
        p = self._make_pipeline_with_mocked_agent(fake_db_state)
        fake_storage = MagicMock()
        fake_storage.get_inventory_items = AsyncMock(return_value=[])

        with self._patched(p, self._recommend_parsed("2026-03-22", "dinner")):
            await p.execute(
                owner_id=OWNER,
                user_input="选方案1：日式风味与家常菜",
                history=[],
                user_timezone="America/Los_Angeles",
                storage_client=fake_storage,
            )

        after = get_plan_state(OWNER)
        assert after.get("meal_planning_queue") == ["2026-03-23|lunch"], (
            f"Expected ['2026-03-23|lunch'], got {after.get('meal_planning_queue')}"
        )
        assert after.get("last_pipeline_action") == "queue_day_complete"

    # ── 7c. Last slot planned → queue cleared, total reset ────────────────────

    @pytest.mark.asyncio
    async def test_queue_cleared_when_last_slot_recommended(self):
        """
        When the final slot in the queue is recommended, the queue is
        emptied and meal_planning_total is reset to 0.
        The response must contain the completion emoji (🎉).
        """
        fake_db_state = self._setup_queue(["2026-03-22|dinner"], total=1)
        p = self._make_pipeline_with_mocked_agent(fake_db_state)
        fake_storage = MagicMock()
        fake_storage.get_inventory_items = AsyncMock(return_value=[])

        with self._patched(p, self._recommend_parsed("2026-03-22", "dinner")):
            result = await p.execute(
                owner_id=OWNER,
                user_input="继续",
                history=[],
                user_timezone="America/Los_Angeles",
                storage_client=fake_storage,
            )

        after = get_plan_state(OWNER)
        assert after.get("meal_planning_queue") == [], (
            f"Expected empty queue, got {after.get('meal_planning_queue')}"
        )
        assert after.get("meal_planning_total") == 0
        assert "🎉" in result.get("response", ""), (
            f"Expected completion emoji in response: {result.get('response')!r}"
        )

    # ── 7d. Progress counter in response text ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_progress_counter_in_response_text(self):
        """
        When the first of three slots is recommended, the response text must
        contain the progress counter '第 1/3 餐' and the ✅ indicator.
        """
        slots = ["2026-03-22|dinner", "2026-03-23|lunch", "2026-03-23|dinner"]
        fake_db_state = self._setup_queue(slots, total=3)
        p = self._make_pipeline_with_mocked_agent(fake_db_state)
        fake_storage = MagicMock()
        fake_storage.get_inventory_items = AsyncMock(return_value=[])

        with self._patched(p, self._recommend_parsed("2026-03-22", "dinner")):
            result = await p.execute(
                owner_id=OWNER,
                user_input="继续",
                history=[],
                user_timezone="America/Los_Angeles",
                storage_client=fake_storage,
            )

        response = result.get("response", "")
        assert "1/3" in response, (
            f"Expected '1/3' progress counter in response, got: {response!r}"
        )
        assert "✅" in response, (
            f"Expected ✅ in response, got: {response!r}"
        )

    # ── 7e. Non-recommend action does not raise with active queue ─────────────

    @pytest.mark.asyncio
    async def test_no_error_when_queue_active_but_action_is_not_recommend(self):
        """
        When queue is active but the LLM returns action='ask' (edge case),
        the queue-mode block is skipped and no error should be raised.
        """
        fake_db_state = self._setup_queue(
            ["2026-03-22|dinner", "2026-03-23|lunch"], total=2
        )
        p = self._make_pipeline_with_mocked_agent(fake_db_state)
        fake_storage = MagicMock()
        fake_storage.get_inventory_items = AsyncMock(return_value=[])

        ask_parsed = {
            "user_message": "您想吃什么风格的菜？",
            "meal_plan": {},
            "meal_plan_slots": {},
            "dish_ingredients": {},
            "shopping_list": [],
            "action": "ask",
            "target_date": None,
            "options": [],
        }

        with self._patched(p, ask_parsed):
            try:
                await p.execute(
                    owner_id=OWNER,
                    user_input="随便",
                    history=[],
                    user_timezone="America/Los_Angeles",
                    storage_client=fake_storage,
                )
            except (NameError, KeyError, TypeError) as exc:
                pytest.fail(f"Unexpected error with non-recommend action in queue mode: {exc}")
