# -*- coding: utf-8 -*-
"""
Unit tests for the meal-declaration safety net in chat.py.

These tests verify the structural signal logic (meal-time + date, no past tense)
used to override GENERAL → PLAN_AHEAD without requiring a live LLM.

The logic being tested (extracted from chat.py):

    _has_meal_time = any(w in user_lower for w in _meal_time_words)
    _has_date      = any(w in user_lower for w in _date_words)
    _is_past       = any(w in user_lower for w in _past_markers)
    if _has_meal_time and _has_date and not _is_past → override to PLAN_AHEAD
"""

import pytest

# ── Mirror the exact word lists from chat.py ─────────────────────────────────

_MEAL_WORDS = (
    "早饭", "早餐", "午饭", "午餐", "晚饭", "晚餐",
    "breakfast", "lunch", "dinner",
)
_TIME_OF_DAY = ("早上", "中午", "晚上")
_EAT_VERBS   = ("吃", "喝")
_DATE_WORDS = (
    "今天", "明天", "后天", "昨天", "大后天",
    "周一", "周二", "周三", "周四", "周五", "周六", "周日",
    "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "tonight", "tomorrow", "today",
)
_PAST_MARKERS = (
    "吃了", "喝了", "做了", "已经", "刚才", "刚刚", "已吃",
    "ate", "had", "already",
)


def _should_override(text: str) -> bool:
    t = text.lower()
    has_meal_word    = any(w in t for w in _MEAL_WORDS)
    has_tod_with_verb = (
        any(w in t for w in _TIME_OF_DAY)
        and any(w in t for w in _EAT_VERBS)
    )
    has_date = any(w in t for w in _DATE_WORDS)
    is_past  = any(w in t for w in _PAST_MARKERS)
    return (has_meal_word or has_tod_with_verb) and has_date and not is_past


# ── Cases that SHOULD trigger PLAN_AHEAD override ────────────────────────────

@pytest.mark.parametrize("text", [
    "今天早饭吃个小笼包",
    "今天早餐吃个小笼包",
    "明天晚上吃个回锅肉",
    "明天晚饭整个回锅肉",
    "后天午饭来个宫保鸡丁",
    "周三晚餐想吃牛排",
    "周五午饭弄个炒饭",
    "今天晚饭就炒个番茄炒蛋",
    "Saturday breakfast I want pancakes",
    "tomorrow lunch have a sandwich",
    "tonight dinner steak",
    "Monday lunch noodles",
])
def test_should_trigger_plan_ahead(text):
    assert _should_override(text), (
        f"Expected PLAN_AHEAD override for: {text!r}"
    )


# ── Cases that should NOT trigger (past tense / no date / no meal time) ──────

@pytest.mark.parametrize("text", [
    "我今天早饭吃了小笼包",          # past tense (吃了)
    "我平时喜欢吃早饭",              # no date reference
    "帮我搜一下鸡蛋在哪里",          # no meal time, no date
    "明天怎么样",                    # no meal time
    "I already had breakfast",        # past (already + had)
    "I ate lunch today",              # past (ate)
    "今天已经吃了午饭",              # past (已经 + 吃了)
    "我想做饭",                       # no meal time word, no date
    "明天晚上有空吗",                # 晚上 but no eating verb → tier-2 blocked
    "明天早上开个会",                # 早上 but no eating verb
    "今天中午有个会议",              # 中午 but no eating verb
])
def test_should_not_trigger(text):
    assert not _should_override(text), (
        f"Expected NO override for: {text!r} (would be false positive)"
    )
