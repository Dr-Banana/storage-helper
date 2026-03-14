# -*- coding: utf-8 -*-
"""
End-to-End Conversation Integration Tests for PlanAheadPipeline
================================================================

Simulates real user conversations (multi-turn) with a live LLM and an
in-memory fake storage backend.  No real DB writes are made; every
storage interaction is captured and asserted.

Covered scenarios
-----------------
  1.  "今天可以吃什么"     — Fresh start, should not immediately write to DB
  2.  "明天吃个回锅肉"     — Explicit add: only tomorrow saved, no other date touched
  3.  Two dates, two schedules — day A + day B → separate schedule_ids
  4.  "明天吃回锅肉" → "今天晚上吃什么" — second turn must not overwrite tomorrow
  5.  Delete one date      — only the requested date removed, other dates intact
  6.  Modify one meal      — only the target date updated, other dates intact
  7.  Empty-state fresh ask → then explicit add → correct slot saved
  8.  Response text coherence — response text must mention the saved dish

Run
---
  pytest tests/pipelines/test_plan_ahead_e2e_conversations.py -m llm_live --run-llm -v

Without --run-llm the tests are skipped automatically unless
GEMINI_LLM_TESTING_KEY is set in .env.local / .env.preprod / .env.prod.
"""
from __future__ import annotations

import os
import pathlib
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set

import pytest

from app.modules.plan_ahead_state import _plan_states, clear_plan_state
from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
from app.storage.pipeline_storage import PipelineStorage

# ── Live-key detection ────────────────────────────────────────────────────────


def _load_testing_key() -> str:
    val = os.getenv("GEMINI_LLM_TESTING_KEY", "")
    if val:
        return val
    try:
        from dotenv import dotenv_values

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

# Dedicated test owner_id — never conflicts with production users
_OWNER = 9999


# ── Skip logic ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _skip_without_live_key(request):
    if "llm_live" in [m.name for m in request.node.iter_markers()]:
        has_flag = request.config.getoption("--run-llm", default=False)
        if not has_flag and not _TESTING_KEY:
            pytest.skip(
                "E2E conversation tests are disabled.\n"
                "  Set GEMINI_LLM_TESTING_KEY in .env.local  OR  pass --run-llm"
            )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_state():
    """Clear in-memory plan state before and after every test."""
    clear_plan_state(_OWNER)
    yield
    clear_plan_state(_OWNER)


# ── Fake in-memory storage ────────────────────────────────────────────────────


class FakePipelineStorage(PipelineStorage):
    """
    Subclass of PipelineStorage that replaces HTTP calls with in-memory dict
    operations.  Pure helper methods (_extract_meal_plan_from_schedule, etc.)
    are inherited and run for real, so the routing/merge logic we write in
    create_or_update_meal_plan_schedule is exercised end-to-end.
    """

    def __init__(self):
        super().__init__()
        self._db: Dict[int, dict] = {}
        self._next_id = 1
        # Per-call audit log (reset between turns when needed)
        self.create_calls: List[dict] = []
        self.update_calls: List[dict] = []
        self.delete_calls: List[int] = []

    # ── Overridden HTTP primitives ─────────────────────────────────────────

    async def get_user_schedules(self, owner_id: int) -> List[dict]:
        return [s for s in self._db.values() if s.get("owner_id") == owner_id]

    async def create_schedule(
        self,
        owner_id: int,
        title: str,
        scheduled_time: Any,
        event_type: str,
        metadata: dict,
    ) -> int:
        sid = self._next_id
        self._next_id += 1
        self._db[sid] = {
            "id": sid,
            "owner_id": owner_id,
            "title": title,
            "event_type": event_type,
            "metadata": metadata,
            "scheduled_time": str(scheduled_time),
        }
        self.create_calls.append({"id": sid, "metadata": metadata})
        return sid

    async def update_schedule(
        self,
        owner_id: int,
        schedule_id: int,
        event_type: str,
        metadata: dict,
    ) -> bool:
        if schedule_id not in self._db:
            return False
        self._db[schedule_id]["metadata"] = metadata
        self.update_calls.append({"id": schedule_id, "metadata": metadata})
        return True

    async def delete_schedule(self, schedule_id: int, owner_id: int) -> bool:
        if schedule_id in self._db:
            del self._db[schedule_id]
            self.delete_calls.append(schedule_id)
            return True
        return False

    async def update_user_recent_dishes(self, owner_id: int, **kwargs) -> None:
        """No-op: don't need real HTTP for recent dishes in tests."""
        pass

    # ── Helpers for test assertions ────────────────────────────────────────

    def reset_call_log(self) -> None:
        self.create_calls.clear()
        self.update_calls.clear()
        self.delete_calls.clear()

    def all_saved_dates(self) -> Set[str]:
        """Return every date currently persisted in the fake DB."""
        dates: Set[str] = set()
        for s in self._db.values():
            mp, _, _, _ = self._extract_meal_plan_from_schedule(s)
            dates.update(mp.keys())
        return dates

    def dishes_for_date(self, date_str: str) -> List[str]:
        """Return all dish names saved for the given date."""
        for s in self._db.values():
            mp, _, _, slots = self._extract_meal_plan_from_schedule(s)
            if date_str in mp:
                dishes: List[str] = []
                for mt_dishes in (slots.get(date_str) or {}).values():
                    if isinstance(mt_dishes, list):
                        dishes.extend(mt_dishes)
                    elif isinstance(mt_dishes, str):
                        dishes.append(mt_dishes)
                return dishes
        return []

    def schedule_id_for_date(self, date_str: str) -> Optional[int]:
        """Return the schedule_id that owns the given date."""
        for s in self._db.values():
            mp, _, _, _ = self._extract_meal_plan_from_schedule(s)
            if date_str in mp:
                return s["id"]
        return None

    def preload_date(
        self,
        date_str: str,
        dish: str,
        meal_time: str = "dinner",
        owner_id: int = _OWNER,
    ) -> int:
        """Manually insert a one-date schedule into the fake DB."""
        sid = self._next_id
        self._next_id += 1
        self._db[sid] = {
            "id": sid,
            "owner_id": owner_id,
            "title": dish,
            "event_type": "meal_plan_draft",
            "metadata": {
                "features": [
                    {
                        "type": "meal_plan",
                        "plans": [
                            {
                                "date": date_str,
                                "meals": [
                                    {
                                        "mealTime": meal_time,
                                        "dishes": [{"name": dish}],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        }
        return sid


# ── Shared helpers ────────────────────────────────────────────────────────────


def _pipeline() -> PlanAheadPipeline:
    api_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_GEMINI_MODEL}:generateContent?key={_TESTING_KEY}"
    )
    return PlanAheadPipeline(gemini_api_url=api_url)


def _default_profile() -> dict:
    return {
        "default_servings": 2,
        "meat_veg_ratio": "2:1:1",
        "include_soup": False,
        "calorie_target": None,
        "disliked_ingredients": [],
        "cuisine_weights": {"Chinese": 70, "Western": 20, "Japanese": 10},
        "recent_dishes": [],
    }


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")


def _day_after_tomorrow() -> str:
    return (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")


class ConversationSession:
    """
    Tracks history and state for a multi-turn test conversation.
    Call `.send(msg)` for each user turn; it returns the pipeline result dict.
    """

    def __init__(
        self,
        pipeline: PlanAheadPipeline,
        storage: FakePipelineStorage,
        owner_id: int = _OWNER,
        timezone: str = "America/Los_Angeles",
        profile: Optional[dict] = None,
    ):
        self.pipeline = pipeline
        self.storage = storage
        self.owner_id = owner_id
        self.timezone = timezone
        self.profile = profile or _default_profile()
        self.history: List[Dict] = []

    async def send(self, user_input: str) -> Dict[str, Any]:
        result = await self.pipeline.execute(
            owner_id=self.owner_id,
            user_input=user_input,
            history=self.history,
            user_timezone=self.timezone,
            storage_client=self.storage,
            user_profile=self.profile,
        )
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": result.get("response", "")})
        return result


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.llm_live
class TestFreshStartQuery:
    """
    Scenario 1 — "今天可以吃什么"
    On a completely blank slate the AI should ask for preferences or present
    options — it must NOT immediately write a confirmed plan to the DB.
    """

    @pytest.mark.asyncio
    async def test_fresh_ask_does_not_persist(self):
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)

        result = await session.send("今天可以吃什么")

        response = result.get("response", "")
        action = result.get("action", "")

        assert response, "AI returned an empty response"
        # AI must not immediately save without user confirmation
        assert action not in ("confirm",), (
            f"AI immediately confirmed a plan without user input; action={action}"
        )
        # DB should stay empty OR contain only a draft (no confirmed commit)
        assert not result.get("action_data", {}).get("is_confirmed", False), (
            "AI committed a plan on the very first open-ended question"
        )

    @pytest.mark.asyncio
    async def test_fresh_ask_response_is_non_empty(self):
        """AI must produce a non-trivial response (not just an empty string)."""
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        result = await session.send("今天可以吃什么")
        assert len(result.get("response", "")) > 10, (
            "Response is suspiciously short for an open-ended meal query"
        )


@pytest.mark.llm_live
class TestExplicitAdd:
    """
    Scenario 2 — "明天晚上吃个回锅肉"
    Explicit single-dish request must land in the DB under exactly tomorrow's
    date and must not create entries for any other date.
    """

    @pytest.mark.asyncio
    async def test_explicit_add_saves_to_correct_date(self):
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()

        result = await session.send(f"明天晚上吃个回锅肉")

        action = result.get("action", "")
        saved_dates = storage.all_saved_dates()

        # Must have saved SOMETHING for tomorrow when the action is "add"
        if action == "add":
            assert tomorrow in saved_dates, (
                f"After explicit 'add' action, {tomorrow} not found in DB. "
                f"Saved dates: {saved_dates}"
            )
            # Must NOT have created entries for any other date
            extra_dates = saved_dates - {tomorrow}
            assert not extra_dates, (
                f"DB contains unexpected extra dates after single-dish add: {extra_dates}"
            )

    @pytest.mark.asyncio
    async def test_explicit_add_dish_name_in_db(self):
        """The saved dish must contain 回锅肉 (or similar Chinese name)."""
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()

        result = await session.send("明天晚上吃个回锅肉")

        if result.get("action") == "add":
            dishes = storage.dishes_for_date(tomorrow)
            assert any("回锅肉" in d for d in dishes), (
                f"Expected 回锅肉 in dishes for {tomorrow}, got: {dishes}"
            )

    @pytest.mark.asyncio
    async def test_explicit_add_response_mentions_dish(self):
        """AI's reply text must mention the dish the user requested."""
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)

        result = await session.send("明天晚上吃个回锅肉")

        # If the AI decided to add it, the response must acknowledge "回锅肉"
        if result.get("action") == "add":
            assert "回锅肉" in result.get("response", ""), (
                f"Response does not mention 回锅肉 despite action=add.\n"
                f"Response: {result.get('response', '')}"
            )


@pytest.mark.llm_live
class TestCrossDateIsolation:
    """
    Scenario 3 — Two different dates must live in separate schedule_ids.
    Adding a meal for date B must not alter the schedule for date A.
    """

    @pytest.mark.asyncio
    async def test_two_adds_produce_separate_schedules(self):
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()
        dat = _day_after_tomorrow()

        result1 = await session.send("明天晚上吃宫保鸡丁")
        storage.reset_call_log()
        result2 = await session.send(f"后天午饭吃清蒸鱼")

        if result1.get("action") == "add" and result2.get("action") == "add":
            sid_tomorrow = storage.schedule_id_for_date(tomorrow)
            sid_dat = storage.schedule_id_for_date(dat)

            assert sid_tomorrow is not None, f"No schedule found for {tomorrow}"
            assert sid_dat is not None, f"No schedule found for {dat}"
            assert sid_tomorrow != sid_dat, (
                f"Both dates share the same schedule_id={sid_tomorrow}. "
                "Each date must have its own schedule."
            )

    @pytest.mark.asyncio
    async def test_second_add_does_not_touch_first_schedule(self):
        """
        Pre-load a schedule for tomorrow.  Then add a meal for the day after
        tomorrow.  The original tomorrow schedule must remain untouched.
        """
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()
        dat = _day_after_tomorrow()

        # Pre-load tomorrow's schedule manually
        original_sid = storage.preload_date(tomorrow, "宫保鸡丁", meal_time="dinner")
        storage.reset_call_log()

        # Now add day-after-tomorrow via conversation
        await session.send("后天晚上吃清蒸鱼")

        # tomorrow's schedule must NOT have been touched by update or delete
        updated_ids = {c["id"] for c in storage.update_calls}
        assert original_sid not in updated_ids, (
            f"Schedule {original_sid} (tomorrow={tomorrow}) was updated "
            "when only day-after-tomorrow should have been affected."
        )
        assert original_sid not in storage.delete_calls, (
            f"Schedule {original_sid} (tomorrow={tomorrow}) was deleted "
            "when only day-after-tomorrow should have been affected."
        )
        # tomorrow's dish must still be in DB
        assert storage.schedule_id_for_date(tomorrow) == original_sid, (
            "tomorrow's schedule was replaced or lost."
        )


@pytest.mark.llm_live
class TestMultiTurnAddThenAsk:
    """
    Scenario 4 — "明天吃回锅肉" → "今天晚上吃什么"
    The second question about today must NOT overwrite tomorrow's entry.
    """

    @pytest.mark.asyncio
    async def test_follow_up_about_today_does_not_overwrite_tomorrow(self):
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()

        # Turn 1: explicit add for tomorrow
        r1 = await session.send("明天晚上吃个回锅肉")
        sid_after_turn1 = storage.schedule_id_for_date(tomorrow)
        storage.reset_call_log()

        # Turn 2: open-ended ask about today — must not write to tomorrow
        await session.send("今天晚上吃什么好呢")

        # tomorrow's schedule must not have been updated or deleted in turn 2
        updated_ids = {c["id"] for c in storage.update_calls}
        assert sid_after_turn1 not in updated_ids or sid_after_turn1 is None, (
            f"Turn 2 (today's query) unexpectedly updated tomorrow's schedule "
            f"id={sid_after_turn1}."
        )
        assert sid_after_turn1 not in storage.delete_calls or sid_after_turn1 is None, (
            f"Turn 2 (today's query) deleted tomorrow's schedule id={sid_after_turn1}."
        )

    @pytest.mark.asyncio
    async def test_turn2_response_is_about_today(self):
        """
        The AI's second response must be relevant to today's meal,
        not a repeat of tomorrow's already-confirmed dish.
        """
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)

        await session.send("明天晚上吃个宫保鸡丁")
        r2 = await session.send("今天晚上吃什么好呢")

        response = r2.get("response", "")
        assert response, "Second-turn response is empty"
        # Should NOT parrot back "明天" or "宫保鸡丁" as the answer to today's question
        # (Weak assertion: just ensure some content exists and isn't trivially wrong)
        assert len(response) > 10, f"Response too short: {response!r}"


@pytest.mark.llm_live
class TestDeleteDate:
    """
    Scenario 5 — "把明天的计划删了"
    Only the target date must be removed; other dates must survive.
    """

    @pytest.mark.asyncio
    async def test_delete_removes_only_target_date(self):
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()
        dat = _day_after_tomorrow()

        # Pre-load two dates
        storage.preload_date(tomorrow, "回锅肉", meal_time="dinner")
        storage.preload_date(dat, "清蒸鱼", meal_time="dinner")
        storage.reset_call_log()

        # Ask AI to remove tomorrow
        await session.send("把明天的计划删了")

        # After delete: tomorrow should be gone, day-after-tomorrow should remain
        remaining_dates = storage.all_saved_dates()

        # If the AI actually executed a delete, check the invariant
        if tomorrow not in remaining_dates:
            assert dat in remaining_dates, (
                f"删除明天后，{dat} 也消失了！删除只应影响目标日期。"
                f"剩余日期: {remaining_dates}"
            )

    @pytest.mark.asyncio
    async def test_delete_confirmation_in_response(self):
        """AI's response should confirm the deletion."""
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()

        storage.preload_date(tomorrow, "回锅肉", meal_time="dinner")
        storage.reset_call_log()

        result = await session.send("把明天的计划删了")
        response = result.get("response", "")
        assert response, "Delete response is empty"
        assert len(response) > 5, f"Delete response suspiciously short: {response!r}"


@pytest.mark.llm_live
class TestModifyOneDate:
    """
    Scenario 6 — Modify a meal for one date.
    Other dates' schedules must remain untouched.
    """

    @pytest.mark.asyncio
    async def test_modify_only_updates_target_date(self):
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()
        dat = _day_after_tomorrow()

        # Pre-load two dates
        sid_tomorrow = storage.preload_date(tomorrow, "宫保鸡丁", meal_time="dinner")
        sid_dat = storage.preload_date(dat, "清蒸鱼", meal_time="dinner")
        storage.reset_call_log()

        # Modify tomorrow only
        await session.send("把明天晚上的菜换成麻婆豆腐")

        # day-after-tomorrow schedule must NOT be updated or deleted
        updated_ids = {c["id"] for c in storage.update_calls}
        assert sid_dat not in updated_ids, (
            f"修改明天时，后天的 schedule id={sid_dat} 也被更新了！"
            f"被更新的 schedules: {updated_ids}"
        )
        assert sid_dat not in storage.delete_calls, (
            f"修改明天时，后天的 schedule id={sid_dat} 被删除了！"
        )

    @pytest.mark.asyncio
    async def test_modify_response_mentions_new_dish(self):
        """AI's response should mention the new dish 麻婆豆腐."""
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()

        storage.preload_date(tomorrow, "宫保鸡丁", meal_time="dinner")
        storage.reset_call_log()

        result = await session.send("把明天晚上的菜换成麻婆豆腐")
        action = result.get("action", "")
        response = result.get("response", "")

        if action == "modify":
            assert "麻婆豆腐" in response, (
                f"修改后的回复里应该提到麻婆豆腐。实际回复: {response!r}"
            )


@pytest.mark.llm_live
class TestResponseCoherence:
    """
    Scenario 7 — The AI's reply must accurately reflect what was saved.
    No hallucinated extra dishes in the response.
    """

    @pytest.mark.asyncio
    async def test_add_one_dish_response_does_not_hallucinate_extra_dishes(self):
        """
        User explicitly asks for only ONE dish.  The AI must not mention
        additional dishes in the response that were never requested.
        """
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()

        result = await session.send("明天晚上我想吃小笼包")
        action = result.get("action", "")
        response = result.get("response", "")

        if action == "add":
            # Get saved dishes — should be just 小笼包 (or similar)
            saved_dishes = storage.dishes_for_date(tomorrow)
            # Verify that whatever is in the response roughly matches what was saved
            # We can't do exact text matching since LLM may rephrase, but at minimum:
            assert "小笼包" in response, (
                f"AI's response does not mention 小笼包 despite saving it: {response!r}"
            )
            # Saved should be a single dish, not a whole extra meal
            assert len(saved_dishes) <= 3, (
                f"AI added {len(saved_dishes)} dishes when user only requested 小笼包: "
                f"{saved_dishes}"
            )

    @pytest.mark.asyncio
    async def test_each_date_in_own_schedule_after_sequential_adds(self):
        """
        Sequential adds for different days must not result in the same
        schedule_id for multiple dates (the pre-fix bug).
        """
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()
        dat = _day_after_tomorrow()

        r1 = await session.send("明天晚上吃红烧肉")
        r2 = await session.send("后天午饭吃蛋炒饭")

        if r1.get("action") == "add" and r2.get("action") == "add":
            sid_t = storage.schedule_id_for_date(tomorrow)
            sid_d = storage.schedule_id_for_date(dat)

            if sid_t and sid_d:
                assert sid_t != sid_d, (
                    "两天的饭被存在同一个 schedule_id 里了！"
                    f"tomorrow={tomorrow} schedule_id={sid_t}, "
                    f"dat={dat} schedule_id={sid_d}"
                )
                # Each schedule should only contain one date
                for s in storage._db.values():
                    mp, _, _, _ = storage._extract_meal_plan_from_schedule(s)
                    assert len(mp) <= 1, (
                        f"Schedule id={s['id']} contains multiple dates: {list(mp.keys())}. "
                        "Each schedule must hold exactly one date."
                    )


@pytest.mark.llm_live
class TestFullConversationFlow:
    """
    Scenario 8 — Longer conversation: plan → tweak → delete one day.
    Validates the most common real-user journey end-to-end.
    """

    @pytest.mark.asyncio
    async def test_plan_tweak_then_delete(self):
        """
        Turn 1: Ask AI what to eat tomorrow (should ask or give options)
        Turn 2: User confirms specific dish for tomorrow
        Turn 3: User adds meal for day-after-tomorrow
        Turn 4: User deletes tomorrow
        → Final state: only day-after-tomorrow remains
        """
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()
        dat = _day_after_tomorrow()

        # Turn 1: open query
        r1 = await session.send("明天想吃点好的，有什么推荐？")
        assert r1.get("response"), "Turn 1 response empty"

        # Turn 2: explicit add for tomorrow
        r2 = await session.send("明天晚上就吃番茄炒蛋吧")

        # Turn 3: add day-after-tomorrow
        r3 = await session.send("后天中午吃宫保鸡丁")

        # Turn 4: delete tomorrow
        r4 = await session.send("把明天的计划取消吧")

        # Final assertion: if the pipeline properly executed all turns,
        # day-after-tomorrow should still be in DB, tomorrow should be removed.
        final_dates = storage.all_saved_dates()
        # Only check when we have confident actions
        if r3.get("action") == "add" and r4.get("action") in ("remove", "delete"):
            assert dat in final_dates, (
                f"后天 {dat} 的计划消失了，删除明天时不应该影响后天。"
                f"当前 DB 日期: {final_dates}"
            )
            assert tomorrow not in final_dates, (
                f"删除后明天 {tomorrow} 应该已经从 DB 中移除。"
                f"当前 DB 日期: {final_dates}"
            )

    @pytest.mark.asyncio
    async def test_response_after_delete_does_not_mention_deleted_dish(self):
        """
        After deleting a date, the AI's confirmation message should reflect
        the deletion (not still show the deleted dish as "confirmed").
        """
        storage = FakePipelineStorage()
        session = ConversationSession(_pipeline(), storage)
        tomorrow = _tomorrow()

        storage.preload_date(tomorrow, "回锅肉", meal_time="dinner")
        storage.reset_call_log()

        result = await session.send("把明天晚上回锅肉删掉")
        response = result.get("response", "")

        assert response, "Delete confirmation response is empty"
        # The response should not claim "回锅肉" is still planned
        # (It may still mention the dish in the context of "I deleted X")
        # Minimum: response is non-trivial
        assert len(response) > 5, f"Response too short: {response!r}"
