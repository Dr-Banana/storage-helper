# -*- coding: utf-8 -*-
"""
Regression tests for two bugs fixed on 2026-03-25
===================================================

Bug 1 — Rejected-dish tracking was too broad
---------------------------------------------
When a user selected an option and a draft was created for *today's date*
(which was NOT in the previous state), the pipeline compared the new plan
against ALL dates in the state (i.e. the entire synced-from-DB schedule).
Every dish that existed on OTHER dates (e.g. "米饭" from last week's dinner)
was falsely added to draft_rejected_dishes, preventing the AI from ever
suggesting those dishes again during the session.

Fix: scope the tracking to only the dates that appear in the new plan.

Bug 2 — suggest_options ignored current-draft staple
------------------------------------------------------
When a draft was active (e.g. 可乐排骨 + 米饭) and the user asked for
new suggestions using fresh ingredients, the ACTION DIRECTIVE for
suggest_options did not instruct the LLM to:
  (a) avoid dishes in the rejected list, and
  (b) carry forward the user's confirmed staple (米饭) into the new options.

As a result the AI presented options with 馒头 despite 馒头 being in the
rejected list, and when the user selected one the draft reverted to 馒头.

Fix: inject "CARRY FORWARD" and "REJECTED dishes" notes into the directive.

Covered by this file
--------------------
  Section 1  — Pure scoping-algorithm unit tests (no mocks, no LLM)
  Section 2  — _build_context() tests for the suggest_options directive
  Section 3  — Full state-flow regression for the original user scenario
"""
from __future__ import annotations

from typing import Any, Dict, Set

import pytest

from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline


# ── Shared helpers ────────────────────────────────────────────────────────────


def _pipeline() -> PlanAheadPipeline:
    return PlanAheadPipeline(gemini_api_url="http://fake-gemini/")


def _empty_state(**overrides) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "meal_plan_slots": {},
        "dish_ingredients": {},
        "meal_plan": {},
        "is_draft": False,
        "draft_rejected_dishes": set(),
    }
    base.update(overrides)
    return base


def _draft_state(slots: Dict[str, Any], rejected: Set[str] = None) -> Dict[str, Any]:
    """Build a state dict representing an active draft with given slots."""
    return _empty_state(
        meal_plan_slots=slots,
        is_draft=True,
        draft_rejected_dishes=rejected or set(),
    )


def _compute_newly_rejected(
    current_slots: Dict[str, Any],
    new_slots: Dict[str, Any],
) -> Set[str]:
    """
    Mirror the FIXED rejected-dish computation from plan_ahead_pipeline.py.

    Only dishes from dates that appear in *new_slots* are considered "previous"
    — dishes from unrelated dates are never marked as rejected.
    """
    new_plan_dates = set(new_slots.keys())
    prev_for_tracking = {
        d: v for d, v in current_slots.items() if d in new_plan_dates
    }

    prev_dishes: Set[str] = set()
    for _pdate, _pmeals in prev_for_tracking.items():
        for _pmt, _pdishes in _pmeals.items():
            prev_dishes.update(_pdishes)

    new_dishes: Set[str] = set()
    for _ndate, _nmeals in new_slots.items():
        for _nmt, _ndishes in _nmeals.items():
            new_dishes.update(_ndishes)

    return prev_dishes - new_dishes


def _old_compute_newly_rejected(
    current_slots: Dict[str, Any],
    new_slots: Dict[str, Any],
) -> Set[str]:
    """
    Mirror the BUGGY (pre-fix) rejected-dish computation.

    Uses ALL dates in current_slots regardless of what new_slots covers,
    so dishes from unrelated dates are incorrectly marked as rejected.
    """
    prev_for_tracking = current_slots  # old: no scoping

    prev_dishes: Set[str] = set()
    for _pdate, _pmeals in prev_for_tracking.items():
        for _pmt, _pdishes in _pmeals.items():
            prev_dishes.update(_pdishes)

    new_dishes: Set[str] = set()
    for _ndate, _nmeals in new_slots.items():
        for _nmt, _ndishes in _nmeals.items():
            new_dishes.update(_ndishes)

    return prev_dishes - new_dishes


# ═════════════════════════════════════════════════════════════════════════════
# Section 1 — Scoping-algorithm unit tests
# ═════════════════════════════════════════════════════════════════════════════


class TestRejectedDishScoping:
    """
    Verify that the fixed scoping logic only marks dishes as rejected when
    they were replaced on the SAME DATE as the new plan — never for dishes
    that merely exist on other dates in the current state.
    """

    # ── 1a. Core regression case ──────────────────────────────────────────────

    def test_new_date_draft_does_not_mark_other_dates_dishes_as_rejected(self):
        """
        Exact bug scenario: DB has 米饭 on 2026-03-20/22; new draft is for
        2026-03-24 (absent from DB).  米饭 must NOT end up in rejected.
        """
        db_slots = {
            "2026-03-20": {"dinner": ["芋艿炖排骨", "干煸长豇豆", "米饭"]},
            "2026-03-21": {"breakfast": ["鸡蛋灌饼", "豆浆"]},
            "2026-03-22": {"dinner": ["炸猪排", "清炒卷心菜", "米饭"]},
        }
        new_slots = {
            "2026-03-24": {"dinner": ["可乐排骨", "馒头"]},
        }

        newly_rejected = _compute_newly_rejected(db_slots, new_slots)

        assert newly_rejected == set(), (
            f"No dishes from unrelated DB dates should be rejected.\n"
            f"Got: {newly_rejected}"
        )
        assert "米饭" not in newly_rejected, (
            "米饭 from 2026-03-20/22 must NOT be marked as rejected"
        )

    def test_buggy_code_would_have_falsely_rejected_米饭(self):
        """
        Confirm the old code DID produce the bug (米饭 incorrectly rejected),
        while the new code does not.
        """
        db_slots = {
            "2026-03-20": {"dinner": ["芋艿炖排骨", "米饭"]},
            "2026-03-22": {"dinner": ["炸猪排", "米饭"]},
        }
        new_slots = {
            "2026-03-24": {"dinner": ["可乐排骨", "馒头"]},
        }

        old_rejected = _old_compute_newly_rejected(db_slots, new_slots)
        new_rejected = _compute_newly_rejected(db_slots, new_slots)

        # Old code had the bug
        assert "米饭" in old_rejected, (
            "Precondition: old code would have added 米饭 to rejected"
        )
        # New code fixes it
        assert "米饭" not in new_rejected, (
            "Fixed code must NOT add 米饭 from unrelated dates to rejected"
        )

    # ── 1b. Same-date replacement IS still rejected ───────────────────────────

    def test_replaced_dish_on_same_date_is_marked_rejected(self):
        """When a dish is swapped on the SAME date it SHOULD be rejected."""
        current = {"2026-03-24": {"dinner": ["可乐排骨", "馒头"]}}
        new = {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}}

        rejected = _compute_newly_rejected(current, new)

        assert "馒头" in rejected, "Replaced staple 馒头 must be rejected"
        assert "可乐排骨" not in rejected, "Kept dish must NOT be rejected"
        assert "米饭" not in rejected, "New staple must NOT be rejected"

    def test_all_replaced_dishes_on_same_date_are_rejected(self):
        """All dishes removed on the modified date enter the rejected set."""
        current = {"2026-03-24": {"dinner": ["可乐排骨", "蒜蓉西兰花", "馒头"]}}
        new = {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}}

        rejected = _compute_newly_rejected(current, new)

        assert "蒜蓉西兰花" in rejected
        assert "馒头" in rejected
        assert "可乐排骨" not in rejected
        assert "米饭" not in rejected

    # ── 1c. Partial date overlap ──────────────────────────────────────────────

    def test_partial_overlap_only_modified_date_scoped(self):
        """
        current_slots has 2026-03-24 and 2026-03-25;
        new plan only touches 2026-03-24 — dishes from 2026-03-25 must NOT
        be tracked.
        """
        current = {
            "2026-03-24": {"dinner": ["可乐排骨", "馒头"]},
            "2026-03-25": {"dinner": ["清炖牛肉", "米饭"]},  # not being modified
        }
        new = {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}}

        rejected = _compute_newly_rejected(current, new)

        assert "馒头" in rejected        # replaced on 2026-03-24
        assert "清炖牛肉" not in rejected  # unrelated date
        # 米饭 appears on 2026-03-25 but is also the NEW dish on 2026-03-24
        assert "米饭" not in rejected

    def test_many_unrelated_dates_none_rejected(self):
        """Five unrelated DB dates with diverse dishes — none should be rejected."""
        current = {
            "2026-03-01": {"breakfast": ["豆浆", "鸡蛋灌饼"]},
            "2026-03-02": {"lunch": ["炸猪排", "米饭"]},
            "2026-03-03": {"dinner": ["芋艿炖排骨", "干煸长豇豆", "米饭"]},
            "2026-03-04": {"dinner": ["日式咖喱牛腩", "手撕包菜"]},
            "2026-03-05": {"dinner": ["清炖牛肉"]},
        }
        new = {"2026-03-24": {"dinner": ["西红柿排骨汤", "蚝油生菜", "馒头"]}}

        rejected = _compute_newly_rejected(current, new)

        assert rejected == set(), (
            f"All current dates are unrelated to 2026-03-24; nothing should "
            f"be rejected.  Got: {rejected}"
        )

    # ── 1d. Edge cases ────────────────────────────────────────────────────────

    def test_empty_current_slots_no_rejection(self):
        """Empty state → no rejected dishes regardless of new plan."""
        rejected = _compute_newly_rejected({}, {"2026-03-24": {"dinner": ["可乐排骨"]}})
        assert rejected == set()

    def test_empty_new_slots_no_rejection(self):
        """Empty new plan → nothing to compare against → no rejections."""
        current = {"2026-03-24": {"dinner": ["可乐排骨", "馒头"]}}
        rejected = _compute_newly_rejected(current, {})
        assert rejected == set()

    def test_identical_plan_no_rejection(self):
        """Identical current and new plan → no dishes were replaced."""
        slots = {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}}
        rejected = _compute_newly_rejected(slots, slots)
        assert rejected == set()


# ═════════════════════════════════════════════════════════════════════════════
# Section 2 — _build_context(): suggest_options ACTION DIRECTIVE
# ═════════════════════════════════════════════════════════════════════════════


class TestSuggestOptionsContext:
    """
    _build_context() with precomputed_action='suggest_options' must:
      A) Include a reminder not to use rejected dishes.
      B) Include a CARRY FORWARD note for the staple when a draft is active.
    """

    def _ctx(self, state: Dict[str, Any], is_draft: bool = False) -> str:
        return _pipeline()._build_context(
            state,
            user_timezone=None,
            precomputed_action="suggest_options",
            is_draft=is_draft,
        )

    def _directive(self, ctx: str) -> str:
        """Extract the text from ACTION DIRECTIVE onward (for focused assertions)."""
        pos = ctx.find("ACTION DIRECTIVE")
        assert pos != -1, "ACTION DIRECTIVE section missing from context"
        return ctx[pos:]

    # ── 2a. Directive is present ──────────────────────────────────────────────

    def test_directive_present_for_suggest_options(self):
        ctx = self._ctx(_empty_state())
        assert "ACTION DIRECTIVE" in ctx

    def test_directive_specifies_suggest_options_action(self):
        ctx = self._ctx(_empty_state())
        directive = self._directive(ctx)
        assert "suggest_options" in directive

    # ── 2b. Rejected-dishes reminder ──────────────────────────────────────────

    def test_directive_reminds_to_avoid_rejected_dishes(self):
        """The suggest_options directive must tell the LLM to avoid rejected dishes."""
        ctx = self._ctx(_empty_state())
        directive = self._directive(ctx)
        # Should mention the rejected list explicitly
        assert "REJECTED" in directive or "rejected" in directive.lower(), (
            "suggest_options directive must remind the LLM to avoid rejected dishes"
        )

    def test_directive_rejected_reminder_present_even_with_empty_rejected_set(self):
        """The reminder is a static instruction — present regardless of rejected set size."""
        state = _draft_state({"2026-03-24": {"dinner": ["可乐排骨"]}}, rejected=set())
        ctx = self._ctx(state, is_draft=True)
        directive = self._directive(ctx)
        assert "REJECTED" in directive or "rejected" in directive.lower()

    # ── 2c. CARRY FORWARD — positive cases ───────────────────────────────────

    def test_carry_forward_rice_staple_in_draft(self):
        """Draft contains 米饭 (rice) → CARRY FORWARD with 米饭 in directive."""
        state = _draft_state({"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}})
        ctx = self._ctx(state, is_draft=True)
        directive = self._directive(ctx)

        assert "CARRY FORWARD" in directive, (
            "CARRY FORWARD note must appear when draft has a staple"
        )
        assert "米饭" in directive[directive.find("CARRY FORWARD"):], (
            "CARRY FORWARD note must name the staple (米饭)"
        )

    def test_carry_forward_mantou_staple(self):
        """Draft contains 馒头 (steamed bun) → CARRY FORWARD names 馒头."""
        state = _draft_state({"2026-03-24": {"dinner": ["可乐排骨", "馒头"]}})
        ctx = self._ctx(state, is_draft=True)
        directive = self._directive(ctx)

        assert "CARRY FORWARD" in directive
        assert "馒头" in directive[directive.find("CARRY FORWARD"):]

    def test_carry_forward_noodle_staple(self):
        """Dishes containing '面' are detected as staple."""
        state = _draft_state({"2026-03-24": {"dinner": ["可乐排骨", "面条"]}})
        ctx = self._ctx(state, is_draft=True)
        directive = self._directive(ctx)

        assert "CARRY FORWARD" in directive
        assert "面条" in directive[directive.find("CARRY FORWARD"):]

    def test_carry_forward_congee_staple(self):
        """Dishes containing '粥' are detected as staple."""
        state = _draft_state({"2026-03-24": {"dinner": ["皮蛋瘦肉粥", "小菜"]}})
        ctx = self._ctx(state, is_draft=True)
        directive = self._directive(ctx)

        assert "CARRY FORWARD" in directive
        assert "皮蛋瘦肉粥" in directive[directive.find("CARRY FORWARD"):]

    def test_carry_forward_bing_staple(self):
        """Dishes containing '饼' are detected as staple."""
        state = _draft_state({"2026-03-24": {"dinner": ["西红柿炒蛋", "葱花饼"]}})
        ctx = self._ctx(state, is_draft=True)
        directive = self._directive(ctx)

        assert "CARRY FORWARD" in directive
        assert "葱花饼" in directive[directive.find("CARRY FORWARD"):]

    # ── 2d. CARRY FORWARD — negative cases ───────────────────────────────────

    def test_no_carry_forward_when_not_draft(self):
        """No CARRY FORWARD note when is_draft=False."""
        state = _empty_state(
            meal_plan_slots={"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}},
            is_draft=False,
        )
        ctx = self._ctx(state, is_draft=False)
        assert "CARRY FORWARD" not in ctx

    def test_no_carry_forward_when_draft_has_no_staple(self):
        """Draft has no staple-type dish → no CARRY FORWARD."""
        state = _draft_state({"2026-03-24": {"dinner": ["可乐排骨", "蒜蓉西兰花"]}})
        ctx = self._ctx(state, is_draft=True)
        assert "CARRY FORWARD" not in ctx

    def test_no_carry_forward_when_draft_slots_empty(self):
        """Empty draft slots → no CARRY FORWARD."""
        state = _draft_state({})
        ctx = self._ctx(state, is_draft=True)
        assert "CARRY FORWARD" not in ctx

    def test_no_carry_forward_when_state_has_no_meal_plan_slots(self):
        """State with no meal_plan_slots key → no CARRY FORWARD."""
        state = {"dish_ingredients": {}, "meal_plan": {}, "is_draft": True,
                 "draft_rejected_dishes": set()}
        ctx = self._ctx(state, is_draft=True)
        assert "CARRY FORWARD" not in ctx

    # ── 2e. Rejected-dishes section also visible in context body ─────────────

    def test_rejected_dishes_section_injected_in_context_body(self):
        """
        Separate from the directive, the rejected-dishes section should appear
        in the context body when draft_rejected_dishes is non-empty.
        """
        state = _draft_state(
            {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}},
            rejected={"馒头", "清炒上海青"},
        )
        ctx = self._ctx(state, is_draft=True)

        assert "馒头" in ctx
        assert "清炒上海青" in ctx
        # Should have the "do not suggest" message
        assert "Do NOT suggest" in ctx or "not suggest" in ctx.lower() or "rejected" in ctx.lower()

    def test_rejected_dishes_section_absent_when_empty(self):
        """
        No rejected-dishes section when draft_rejected_dishes is empty.
        The directive may still REFERENCE the section by name, but the actual
        section header (=== DISHES ALREADY TRIED ... ===) must not appear.
        """
        state = _draft_state({"2026-03-24": {"dinner": ["可乐排骨"]}}, rejected=set())
        ctx = self._ctx(state, is_draft=True)
        # The actual section header (with === delimiters) must not appear
        assert "=== DISHES ALREADY TRIED" not in ctx


# ═════════════════════════════════════════════════════════════════════════════
# Section 3 — Full state-flow regression
# ═════════════════════════════════════════════════════════════════════════════


class TestFullScenarioStateFlow:
    """
    Replay the exact multi-turn scenario from the user bug report and verify
    that the state invariants hold at each step after the fix.

    Scenario:
      Turn 1  user says "我今天晚上有一个猪排骨，能做些什么呢"
              → AI presents 2 options (方案一, 方案二)
      Turn 2  user says "方案二可以但是不要青菜了"
              → draft created for 2026-03-24: 可乐排骨 + 馒头
              → current state has DB dishes on 2026-03-20 / 2026-03-21 (incl. 米饭)
              BUG: 米饭 was being added to rejected list here (wrong)
              FIX: 米饭 must NOT appear in rejected
      Turn 3  user says "主食吃米饭吧"
              → draft updated: 可乐排骨 + 米饭
              → 馒头 SHOULD be rejected; 米饭 should NOT
      Turn 4  user says "排骨我有西红柿可以做点啥"
              → triggers suggest_options while draft is active
              → context must carry forward 米饭
    """

    # Simulated DB state (from sync_meal_plan_from_database)
    DB_SLOTS = {
        "2026-03-20": {"dinner": ["芋艿炖排骨", "干煸长豇豆", "米饭"]},
        "2026-03-21": {"breakfast": ["鸡蛋灌饼", "豆浆"],
                       "dinner": ["炸猪排", "清炒卷心菜", "米饭"]},
        "2026-03-22": {"breakfast": ["面包", "煎蛋", "牛奶"]},
    }

    def test_turn2_creating_new_date_draft_does_not_reject_db_rice(self):
        """Turn 2: new draft for 2026-03-24 must not mark 米饭 as rejected."""
        new_draft = {"2026-03-24": {"dinner": ["可乐排骨", "馒头"]}}

        rejected = _compute_newly_rejected(self.DB_SLOTS, new_draft)

        assert "米饭" not in rejected, (
            "Turn 2 regression: 米饭 from DB dates must not enter rejected list"
        )
        assert rejected == set(), f"Expected empty rejected set, got {rejected}"

    def test_turn3_staple_change_correctly_rejects_only_old_staple(self):
        """Turn 3: replacing 馒头→米饭 rejects 馒头 but never 米饭."""
        # State after Turn 2 (draft for 2026-03-24)
        draft_after_t2 = {"2026-03-24": {"dinner": ["可乐排骨", "馒头"]}}
        # User says "主食吃米饭吧"
        draft_after_t3 = {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}}

        rejected = _compute_newly_rejected(draft_after_t2, draft_after_t3)

        assert "馒头" in rejected, "Replaced 馒头 must be in rejected after Turn 3"
        assert "可乐排骨" not in rejected, "Kept 可乐排骨 must NOT be rejected"
        assert "米饭" not in rejected, "User's explicit choice 米饭 must NOT be rejected"

    def test_accumulated_rejected_set_never_contains_米饭(self):
        """
        Union of all newly-rejected sets across Turns 2 and 3 must NOT include 米饭.
        This is the invariant that ensures Turn 4 can carry 米饭 forward as a staple.
        """
        # Turn 2
        new_draft_t2 = {"2026-03-24": {"dinner": ["可乐排骨", "馒头"]}}
        rejected_t2 = _compute_newly_rejected(self.DB_SLOTS, new_draft_t2)

        # Turn 3
        draft_t2 = {"2026-03-24": {"dinner": ["可乐排骨", "馒头"]}}
        draft_t3 = {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}}
        rejected_t3 = _compute_newly_rejected(draft_t2, draft_t3)

        total_rejected = rejected_t2 | rejected_t3

        assert "米饭" not in total_rejected, (
            f"米饭 must never appear in accumulated rejected list after the fix.\n"
            f"  rejected_t2={rejected_t2}\n"
            f"  rejected_t3={rejected_t3}\n"
            f"  total={total_rejected}"
        )
        assert "馒头" in total_rejected, "馒头 must be in accumulated rejected list"

    def test_turn4_context_carries_forward_rice_as_staple(self):
        """
        Turn 4: when draft is active with 可乐排骨 + 米饭, the suggest_options
        directive must carry 米饭 forward so the new options preserve it.
        """
        draft_state = _draft_state(
            {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}},
            rejected={"馒头"},
        )
        ctx = _pipeline()._build_context(
            draft_state,
            user_timezone=None,
            precomputed_action="suggest_options",
            is_draft=True,
        )

        assert "CARRY FORWARD" in ctx, (
            "Turn 4: directive must carry 米饭 forward so new options preserve the staple"
        )
        carry_section = ctx[ctx.find("CARRY FORWARD"):]
        assert "米饭" in carry_section, (
            "CARRY FORWARD note must explicitly name 米饭"
        )

    def test_turn4_context_forbids_mantou_via_rejected_directive(self):
        """
        Turn 4: the rejected list contains 馒头; the suggest_options directive
        must explicitly remind the LLM not to suggest it.
        """
        draft_state = _draft_state(
            {"2026-03-24": {"dinner": ["可乐排骨", "米饭"]}},
            rejected={"馒头"},
        )
        ctx = _pipeline()._build_context(
            draft_state,
            user_timezone=None,
            precomputed_action="suggest_options",
            is_draft=True,
        )

        # The rejected-dishes body section should list 馒头
        assert "馒头" in ctx, "馒头 must appear in the rejected-dishes section"

        # The directive should remind the LLM about the rejected list
        directive = ctx[ctx.find("ACTION DIRECTIVE"):]
        assert "REJECTED" in directive or "rejected" in directive.lower(), (
            "Directive must remind LLM to avoid rejected dishes"
        )
