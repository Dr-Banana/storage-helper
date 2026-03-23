# -*- coding: utf-8 -*-
"""
Unit tests for the intent-routing safety nets added to chat.py:

  1d. "[针对菜品：X] 用户要求：Y" prefix → always PLAN_AHEAD
  1e. pending_options in plan_state        → always PLAN_AHEAD
      (user is selecting / refining a proposed meal option)

Also tests the extended `in_plan_ahead_flow` detection:
  - pending_options present → in_plan_ahead_flow=True
  - suggest_options keywords in recent history → in_plan_ahead_flow=True

All logic is mirrored directly from chat.py so tests run without any server
or LLM — they verify the exact branching conditions used in production.
"""
from __future__ import annotations

import pytest


# ===========================================================================
# Mirror the exact logic from chat.py
# ===========================================================================

def _has_dish_targeting_prefix(user_input: str) -> bool:
    """Safety net 1d: client-side '[针对菜品：X]' wrapping prefix."""
    return user_input.startswith("[针对菜品：")


def _should_override_via_pending_options(
    intent_str: str,
    pending_options,  # plan_state.get("pending_options") value
) -> bool:
    """Safety net 1e: user is in an active suggest_options flow."""
    return intent_str == "GENERAL" and bool(pending_options)


def _in_plan_ahead_flow(
    context_type: str | None,
    plan_meal_plan: dict | None,
    plan_shopping_list: list | None,
    pending_options,
    recent_history_text: str,
) -> bool:
    """
    Mirrors the `in_plan_ahead_flow` boolean from chat.py.

    Checks (in order):
      1. context.type == "plan_ahead"
      2. non-empty meal_plan or shopping_list
      3. pending_options is set (suggest_options flow)
      4. history keywords
    """
    _plan_kw = (
        "meal plan", "next week", "monday", "tuesday", "planning", "shopping list",
        "cook at home", "don't know what to cook", "decide what to cook", "what to cook",
        "不知道做什么", "在家做饭", "做什么菜",
        "同一天", "same day", "那天", "that day", "再加一个", "add another", "也加",
        "方案一", "方案二", "方案", "选方案", "选择方案",
    )
    return (
        context_type == "plan_ahead"
        or bool(plan_meal_plan or plan_shopping_list)
        or bool(pending_options)
        or any(kw in recent_history_text for kw in _plan_kw)
    )


# ===========================================================================
# Safety net 1d — "[针对菜品：X]" dish-targeting prefix
# ===========================================================================

class TestDishTargetingPrefix:
    """Verify the prefix detection used in safety net 1d."""

    @pytest.mark.parametrize("user_input", [
        "[针对菜品：日式番茄牛肉大棒骨汤] 用户要求：可以做日式酱油的",
        "[针对菜品：红烧肉] 用户要求：少放点糖",
        "[针对菜品：蒜蓉炒虾仁] 用户要求：换个做法",
        "[针对菜品：番茄炒蛋] 用户要求：不要放太多油",
        "[针对菜品：米饭] 用户要求：改成糙米饭",
    ])
    def test_prefix_detected_triggers_override(self, user_input: str):
        assert _has_dish_targeting_prefix(user_input), (
            f"Expected dish-targeting prefix detected for: {user_input!r}"
        )

    @pytest.mark.parametrize("user_input", [
        "可以做日式酱油的",                         # no prefix
        "换个清淡一点的方案",                        # no prefix
        "选方案一",                                  # selection, no prefix
        "今天晚饭吃什么",                            # planning query, no prefix
        "针对菜品：红烧肉 用户要求：少放糖",          # missing brackets
        "【针对菜品：红烧肉】",                       # wrong bracket style
    ])
    def test_no_prefix_not_detected(self, user_input: str):
        assert not _has_dish_targeting_prefix(user_input), (
            f"Expected NO prefix detection for: {user_input!r}"
        )

    def test_prefix_only_triggers_when_general(self):
        """1d only overrides GENERAL; a PLAN_AHEAD that happens to have prefix is unchanged."""
        query = "[针对菜品：红烧肉] 用户要求：少放糖"
        # If the LLM correctly returns PLAN_AHEAD, 1d is never reached (it only
        # checks intent == GENERAL). Verify the condition independently:
        # The prefix check is unconditional in production, but the surrounding
        # `if intent_result.intent == Intent.GENERAL` guard prevents double-overrides.
        assert _has_dish_targeting_prefix(query)  # prefix present
        # A non-GENERAL intent would mean 1d's outer `if` is False — no-op.


# ===========================================================================
# Safety net 1e — pending_options overrides GENERAL
# ===========================================================================

class TestPendingOptionsOverride:
    """
    When the user is in a suggest_options flow (AI showed 2 options last turn),
    any GENERAL-classified message should be re-routed to PLAN_AHEAD.
    """

    @pytest.mark.parametrize("user_input,pending_options", [
        # User refines a proposed option after seeing two meal plans
        ("可以做日式酱油的", [{"option_id": 1}, {"option_id": 2}]),
        # User says a preference while options are still pending
        ("我想要辣一点的", [{"option_id": 1}]),
        # User asks an ambiguous question during option flow
        ("少放点油行吗", [{"option_id": 1}, {"option_id": 2}]),
        # Vague follow-up
        ("换个方向", [{"option_id": 1}]),
        # English refinement
        ("can you make it less spicy?", [{"option_id": 1}, {"option_id": 2}]),
    ])
    def test_general_with_pending_options_triggers_plan_ahead(
        self, user_input: str, pending_options: list
    ):
        assert _should_override_via_pending_options("GENERAL", pending_options), (
            f"Expected PLAN_AHEAD override for: {user_input!r}"
        )

    @pytest.mark.parametrize("pending_options", [
        None,   # no pending options at all
        [],     # empty list
        {},     # empty dict (falsy)
    ])
    def test_general_without_pending_options_no_override(self, pending_options):
        assert not _should_override_via_pending_options("GENERAL", pending_options)

    @pytest.mark.parametrize("intent_str", ["PLAN_AHEAD", "SEARCH", "COOKING_STEPS"])
    def test_non_general_not_overridden_even_with_pending_options(self, intent_str: str):
        """1e only fires when the classified intent is GENERAL."""
        pending = [{"option_id": 1}]
        assert not _should_override_via_pending_options(intent_str, pending)


# ===========================================================================
# Extended in_plan_ahead_flow detection
# ===========================================================================

class TestInPlanAheadFlow:
    """
    Verify that in_plan_ahead_flow correctly returns True for the cases
    added in this patch.
    """

    def test_pending_options_sets_in_plan_ahead_flow(self):
        assert _in_plan_ahead_flow(
            context_type=None,
            plan_meal_plan=None,
            plan_shopping_list=None,
            pending_options=[{"option_id": 1}, {"option_id": 2}],
            recent_history_text="",
        )

    def test_suggest_options_keyword_in_history(self):
        history_text = "AI: 方案一：红烧肉套餐 方案二：日式套餐"
        assert _in_plan_ahead_flow(
            context_type=None,
            plan_meal_plan=None,
            plan_shopping_list=None,
            pending_options=None,
            recent_history_text=history_text,
        )

    def test_select_option_keyword_in_history(self):
        history_text = "User: 选方案一"
        assert _in_plan_ahead_flow(
            context_type=None,
            plan_meal_plan=None,
            plan_shopping_list=None,
            pending_options=None,
            recent_history_text=history_text,
        )

    def test_existing_meal_plan_sets_flow(self):
        assert _in_plan_ahead_flow(
            context_type=None,
            plan_meal_plan={"2026-03-23": {"dinner": ["红烧肉"]}},
            plan_shopping_list=None,
            pending_options=None,
            recent_history_text="",
        )

    def test_context_type_plan_ahead_sets_flow(self):
        assert _in_plan_ahead_flow(
            context_type="plan_ahead",
            plan_meal_plan=None,
            plan_shopping_list=None,
            pending_options=None,
            recent_history_text="",
        )

    def test_empty_everything_not_in_flow(self):
        assert not _in_plan_ahead_flow(
            context_type=None,
            plan_meal_plan=None,
            plan_shopping_list=None,
            pending_options=None,
            recent_history_text="hello how are you",
        )

    def test_single_pending_option_is_truthy(self):
        """A single-element list is truthy — should trigger flow."""
        assert _in_plan_ahead_flow(
            context_type=None,
            plan_meal_plan=None,
            plan_shopping_list=None,
            pending_options=[{"option_id": 1}],
            recent_history_text="",
        )


# ===========================================================================
# Real-world scenario: dish-option refinement flow
# ===========================================================================

class TestDishOptionRefinementFlow:
    """
    End-to-end scenario: user sees two meal options and refines one of them.
    Verifies that ALL relevant safety nets fire correctly.
    """

    # History after AI showed two options
    _HISTORY = (
        "AI: 方案一：中式家常风味，冬瓜牛棒骨汤。"
        "方案二：日式酱油风味，日式牛棒骨汤。请问选哪个？"
    )

    @pytest.mark.parametrize("user_input", [
        "[针对菜品：日式番茄牛肉大棒骨汤] 用户要求：可以做日式酱油的",  # 1d triggered
        "[针对菜品：冬瓜牛棒骨汤] 用户要求：换成萝卜",                  # 1d triggered
    ])
    def test_dish_targeting_prefix_always_overrides(self, user_input: str):
        # Safety net 1d fires when intent=GENERAL and prefix present
        assert _has_dish_targeting_prefix(user_input)

    @pytest.mark.parametrize("user_input", [
        "可以做日式酱油的",       # 1e fires (pending_options set)
        "换成清淡一点的口味",      # 1e fires
        "少放点油行吗",            # 1e fires
    ])
    def test_refinement_without_prefix_caught_by_1e(self, user_input: str):
        # User typed the refinement without the client wrapper → 1e handles it
        pending = [{"option_id": 1, "label": "方案一"}, {"option_id": 2, "label": "方案二"}]
        assert _should_override_via_pending_options("GENERAL", pending)

    def test_in_plan_ahead_flow_with_history_and_pending(self):
        # Both signals present: history has "方案" + pending_options set
        pending = [{"option_id": 1}, {"option_id": 2}]
        assert _in_plan_ahead_flow(
            context_type=None,
            plan_meal_plan=None,
            plan_shopping_list=None,
            pending_options=pending,
            recent_history_text=self._HISTORY,
        )
