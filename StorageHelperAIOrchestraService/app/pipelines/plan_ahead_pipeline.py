"""
PlanAheadPipeline: single-pass meal planning pipeline.

Replaces the scattered PLAN_AHEAD handling in chat.py with a clean 5-step flow:
  1. Sync state from DB  (PlanAheadAgent.sync_meal_plan_from_database)
  2. Build LLM context   (structured, no PLAN_JSON instructions)
  3. Single LLM call     (responseMimeType=application/json + responseSchema)
  4. Compute shopping list programmatically  (compute_shopping_list)
  5. Persist to DB       (PlanAheadAgent.persist_meal_plan)
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from app.agents.scheduling_agent import (
    PLAN_AHEAD_RESPONSE_SCHEMA,
    PlanAheadAgent,
    compute_shopping_list,
)
from app.modules.active_context import get_active_context, update_active_context
from app.modules.plan_ahead_state import _UNSET, get_phase, get_plan_state, update_plan_state
from app.skills.plan_ahead import (
    ClassifyDateConfirmationSkill,
    ClassifyDishIntentSkill,
    ClassifyMealActionSkill,
    ExtractIngredientsSkill,
    InitPlanningQueueSkill,
)
from app.storage.pipeline_storage import _get_storage_base_url

logger = logging.getLogger(__name__)


class PlanAheadPipeline:
    """Single-pass PLAN_AHEAD handler: sync → context → LLM → compute → persist."""

    def __init__(self, gemini_api_url: str):
        self.gemini_api_url = gemini_api_url
        self.plan_ahead_agent = PlanAheadAgent(gemini_api_url=gemini_api_url)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Keywords that indicate the user is confirming the proposed date range.

    def _now_in_timezone(self, user_timezone: Optional[str] = None) -> datetime:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            tz = None
        return datetime.now(tz) if tz else datetime.utcnow()

    def _log_pending_dates(
        self,
        queue: List[str],
        existing_slots: Optional[Dict[str, Any]],
        header: str,
    ) -> None:
        """Log the queued slots with per-date meal breakdown.

        Args:
            queue: List of "YYYY-MM-DD|meal_type" strings.
            existing_slots: Already-planned meal slots (not used in new format, kept for compat).
            header: Opening log line (e.g. "Multi-day planning detected" or "Corrected to").
        """
        from datetime import date as _dt
        from collections import defaultdict

        _day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
        _targets = []

        # Group meal_types by date preserving order
        date_meals: Dict[str, List[str]] = defaultdict(list)
        for item in queue:
            if "|" in item:
                _d_part, _mt_part = item.split("|", 1)
                date_meals[_d_part].append(_mt_part)
            else:
                date_meals[item]  # bare date with no meals listed

        for _d, _meals in date_meals.items():
            try:
                _day_name = _day_names.get(_dt.fromisoformat(_d).weekday(), "")
            except Exception:
                _day_name = ""
            _label = f"{_d}({_day_name})" if _day_name else _d
            _targets.append(f"{_label} [{', '.join(_meals)}]" if _meals else _label)

        logger.info(
            "[PLAN_AHEAD_PIPELINE] %s: %d slot(s) queued, awaiting user confirmation.\n  %s",
            header,
            len(queue),
            "\n  ".join(_targets),
        )

    def _build_date_confirm_message(
        self, slots: List[str], language: Optional[str] = "zh"
    ) -> str:
        """Build the user-facing date confirmation question.

        ``slots`` is a list of "YYYY-MM-DD|meal_type" strings (the new unified format).
        Generates different wording for single-meal, single-day, or multi-day cases.
        """
        if not slots:
            return ""

        from datetime import date as _d

        _day_zh = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
        _meal_zh = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
        is_zh = (language or "zh") == "zh"

        # Extract unique dates and meal_times preserving order
        unique_dates: List[str] = list(dict.fromkeys(
            s.split("|")[0] for s in slots
        ))
        unique_meals: List[str] = list(dict.fromkeys(
            s.split("|")[1] for s in slots if "|" in s
        ))

        start, end = unique_dates[0], unique_dates[-1]
        is_single_day = (start == end)
        all_meal_set = {"breakfast", "lunch", "dinner"}
        is_all_meals = set(unique_meals) >= all_meal_set

        def _fmt(date_str: str):
            try:
                _dt = _d.fromisoformat(date_str)
                return f"{_dt.month}月{_dt.day}日", _day_zh.get(_dt.weekday(), "")
            except Exception:
                return date_str, ""

        _s_short, _s_day = _fmt(start)
        _e_short, _e_day = _fmt(end)

        if is_zh:
            suffix = "没问题的话，我们马上开始！如果有误，告诉我一声就好。"
            if is_single_day and not is_all_meals:
                meals_str = "、".join(_meal_zh.get(m, m) for m in unique_meals)
                return f"您是说规划 {_s_short}（{_s_day}）的{meals_str}吗？{suffix}"
            if is_single_day:
                return f"您是说规划 {_s_short}（{_s_day}）的三餐吗？{suffix}"
            return (
                f"您是说规划 {_s_short}（{_s_day}）到 {_e_short}（{_e_day}）的餐食吗？"
                "日期没问题的话，我们马上开始！如果日期有误，告诉我一声就好。"
            )
        # English fallback
        if is_single_day and not is_all_meals:
            meals_en = " and ".join(unique_meals)
            return (
                f"Planning {meals_en} for {start} ({_s_day}) — is that right? "
                "Just say 'no, tomorrow' or name a different date to adjust."
            )
        if is_single_day:
            return (
                f"Planning all meals for {start} ({_s_day}) — is that right? "
                "Just say 'no, tomorrow' or name a different date to adjust."
            )
        return (
            f"Planning meals for {start} ({_s_day}) through {end} ({_e_day}) — is that right? "
            "Just say 'no, next week' or 'no, this week' if you'd like to adjust."
        )

    @staticmethod
    def _dates_to_slots(dates: List[str], meal_times: Optional[List[str]] = None) -> List[str]:
        """Convert a list of YYYY-MM-DD strings to date|meal_type slot strings."""
        _valid = {"breakfast", "lunch", "dinner"}
        _mt = [m for m in (meal_times or []) if m in _valid] or ["breakfast", "lunch", "dinner"]
        return [f"{d}|{m}" for d in dates for m in _mt]

    @staticmethod
    def _is_explicit_dish_request(user_input: str) -> bool:
        """Return True when the user is specifying a concrete dish to add, NOT requesting
        AI to plan/suggest meals.

        Examples that return True (explicit dish):
          "今天晚上吃芋艿猪排骨"
          "想吃火锅"
          "做个红烧肉"
          "来点水煮鱼"
          "今晚做芋艿猪排骨"    ← verb + dish (no 个/点 suffix)
          "来个小笼包"
          "做水煮鱼"

        Examples that return False (planning / suggestion request):
          "今天晚上吃什么"
          "帮我规划今天三餐"
          "吃什么好"
          "给我推荐一个菜"
          "明天做什么"
        """
        import re as _re

        # Time/date/quantity words that must NOT follow a bare verb for it to count as a dish
        _TIME_NUM = (
            "今天", "今日", "今晚", "今早", "今午", "明天", "明日", "后天",
            "昨天", "早上", "早餐", "早饭", "中午", "午餐", "午饭",
            "晚上", "晚餐", "晚饭", "下午", "上午",
            "一", "两", "三", "四", "五", "六", "七", "八", "九", "十",
            "几", "多少", "什么", "啥", "嘛", "呢", "吗", "？", "?",
        )

        q = user_input.strip()

        # Pattern A: high-confidence verb phrases that almost always precede a dish name
        #   e.g. "想吃火锅", "要吃红烧肉", "吃个小笼包", "做个红烧肉", "来点水煮鱼"
        _pattern_a = _re.search(
            r"(?:想吃|要吃|打算吃|就吃|吃个|吃了|做个|做了|做点|来个|来点|整个|整点)",
            q,
        )

        # Pattern B: bare verb "吃/做/来" followed immediately by ≥2 non-punctuation chars
        #   but NOT followed by time/question words.
        #   e.g. "今晚做芋艿猪排骨", "来水煮鱼", "吃芋艿猪排骨"
        _pattern_b = _re.search(
            r"(?:吃|做|来)(?=\s*[^\s，。！？,!?\n\d]{2,})",
            q,
        )

        _match = _pattern_a or _pattern_b
        if not _match:
            return False

        # Sanity check: the content immediately after the verb must not be a time/question word
        _verb_end = _match.end()
        _tail = q[_verb_end:].lstrip()
        if any(_tail.startswith(w) for w in _TIME_NUM):
            return False

        # Must have at least 2 substantial characters following
        return len(_tail) >= 2

    async def _init_planning_queue(
        self, user_input: str, user_timezone: Optional[str]
    ) -> List[str]:
        """Parse meal-planning intent and return a list of date|meal_type slot strings.

        Primary: lightweight LLM call that extracts:
          - whether the user is requesting meal planning (has_planning_intent)
          - what date range (single day or multi-day; same date for single day)
          - which meal times specifically (or null for all three)

        Returns a list of "YYYY-MM-DD|meal_type" strings, or [] if no planning intent.
        """
        import json as _json

        now = self._now_in_timezone(user_timezone)
        today = now.date()
        today_str = now.strftime("%Y-%m-%d (%A)")

        # ---- LLM-based detection (primary) ----
        system_prompt = (
            f"Today is {today_str}. You are a date-range parser for a meal-planning app.\n"
            "Decide if the user wants to PLAN meals (not view/delete/modify existing plans).\n"
            "If yes, extract the date range and specific meal times (if mentioned).\n\n"
            "Respond with JSON only — no markdown, no explanation.\n"
            "Format:\n"
            '  {"has_planning_intent": false}  — not a planning request\n'
            '  {"has_planning_intent": true, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "meal_times": null}  — plan all meals\n'
            '  {"has_planning_intent": true, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "meal_times": ["dinner"]}  — specific meals\n\n'
            "meal_times values: breakfast, lunch, dinner\n\n"
            "Rules:\n"
            "- view/delete/modify requests → has_planning_intent: false\n"
            "- CRITICAL RULE — 'HAS DISH = NOT PLANNING': If the user's message contains a "
            "SPECIFIC DISH NAME (a concrete food item, e.g. '芋艿猪排骨', '红烧肉', '火锅', "
            "'水煮鱼', '小笼包', '饺子'), you MUST return has_planning_intent: false. "
            "The user is telling you what they want to eat — do NOT treat it as a planning "
            "request. The main system will handle adding the dish directly.\n"
            "  Examples: '今天晚上吃芋艿猪排骨' → false; '做个红烧肉' → false; "
            "'今晚来个火锅' → false; '吃个小笼包' → false.\n"
            "  Contrast (no specific dish → planning): '今天晚上吃什么' → true; "
            "'帮我规划今天三餐' → true; '计划一下这周饮食' → true.\n"
            "- If the user asks the AI to SUGGEST or PLAN meals without naming a specific dish → "
            "has_planning_intent: true, meal_times must reflect the mentioned meal time "
            "(e.g. '晚上/晚餐/dinner' → ['dinner']; '早上/早餐' → ['breakfast']; "
            "'中午/午餐/lunch' → ['lunch']); if no meal time mentioned, use null.\n"
            "- '今天的午餐' → start=today, end=today, meal_times: ['lunch']\n"
            "- '今天晚上/今晚/晚上' → start=today, end=today, meal_times: ['dinner']\n"
            "- '今天早上/今早' → start=today, end=today, meal_times: ['breakfast']\n"
            "- '今天中午' → start=today, end=today, meal_times: ['lunch']\n"
            "- '今天的计划' / '今天三餐' → start=today, end=today, meal_times: null\n"
            "- '明天' → start=tomorrow, end=tomorrow, meal_times: null\n"
            "- '明后两天' → start=tomorrow, end=day-after-tomorrow, meal_times: null\n"
            "- '下周' / 'next week' → Mon–Sun of next calendar week, meal_times: null\n"
            "- '这周' / 'this week' → today through this Sunday, meal_times: null\n"
            "- '今天往后N天' / 'from today for N days' → start=today, end=today+N-1, meal_times: null\n"
            "- '一周' / 'a week' / '七天' / '7天' → start=today, end=today+6, meal_times: null\n"
            "- '两周' / 'two weeks' / '14天' → start=today, end=today+13, meal_times: null\n"
            "- '半个月' / '15天' → start=today, end=today+14, meal_times: null\n"
            "- '一个月' / '30天' / 'a month' → start=today, end=today+29, meal_times: null\n"
            "- end date must be >= start date\n"
            "- CRITICAL — MULTI-DAY: If the user mentions TWO OR MORE specific days "
            "(e.g. '周五周六', '周五和周六', 'Friday and Saturday', '3月27日和28日'), "
            "you MUST set 'end' to the LAST day mentioned. NEVER collapse multiple days into a single date.\n"
            "  Example: '下周五周六' → start=next_Friday, end=next_Saturday\n"
            "  Example: '周三周四' → start=this_Wed, end=this_Thu\n"
            "- CRITICAL: If the user mentions '晚上', '今晚', '晚餐', '晚饭', meal_times MUST be ['dinner']. "
            "If the user mentions '早上', '早餐', '早饭', meal_times MUST be ['breakfast']. "
            "If the user mentions '中午', '午餐', '午饭', meal_times MUST be ['lunch']. "
            "NEVER return meal_times: null when a specific meal time was mentioned."
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_input}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 256,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "object",
                    "properties": {
                        "has_planning_intent": {"type": "boolean"},
                        "start": {"type": "string", "nullable": True},
                        "end": {"type": "string", "nullable": True},
                        "meal_times": {
                            "type": "array",
                            "items": {"type": "string"},
                            "nullable": True,
                        },
                    },
                    "required": ["has_planning_intent"],
                },
            },
        }
        llm_result: Optional[List[str]] = None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.gemini_api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                raw = resp.json()
            candidate = (raw.get("candidates") or [{}])[0]
            finish_reason = candidate.get("finishReason", "")
            parts = (candidate.get("content") or {}).get("parts") or []
            if not parts:
                raise ValueError(
                    f"Empty parts in LLM response (finishReason={finish_reason!r})"
                )
            text = parts[0].get("text", "")
            if not text:
                raise ValueError(
                    f"Empty text in LLM response (finishReason={finish_reason!r})"
                )
            parsed = _json.loads(text)
            logger.info(
                "[PLAN_AHEAD_PIPELINE] _init_planning_queue LLM result: %s", parsed
            )
            if parsed.get("has_planning_intent") and parsed.get("start"):
                from datetime import date as _d

                def _parse_date_str(s: str) -> "_d":
                    """Normalize LLM date strings to YYYY-MM-DD before parsing.

                    LLM may return e.g. '2026-03-18T00:00:00.000Z[UTC]' or
                    '2026-03-18T00:00:00Z' — take only the date portion.
                    """
                    return _d.fromisoformat(s[:10])

                _start = _parse_date_str(parsed["start"])
                _end_raw = parsed.get("end") or parsed["start"]
                _end = _parse_date_str(_end_raw)
                if _end < _start:
                    _end = _start
                _dates = [
                    (_start + timedelta(days=i)).strftime("%Y-%m-%d")
                    for i in range((_end - _start).days + 1)
                ]
                # If LLM didn't return meal_times, supplement with keyword detection.
                # LLM is good at date ranges but often omits meal_times even when the
                # user clearly says e.g. "晚饭" / "午餐" — keywords are more reliable.
                _llm_meal_times = parsed.get("meal_times") or None
                if not _llm_meal_times:
                    _q_lower = (user_input or "").lower()
                    _meal_kw_detect = [
                        (("早餐", "早饭", "早上吃", "早上的", "早上", "今早", "明早", "早晨"), "breakfast"),
                        (("午餐", "午饭", "中餐", "中饭", "中午吃", "中午的", "中午"), "lunch"),
                        (("晚餐", "晚饭", "晚上吃", "晚上的", "晚上", "今晚", "明晚", "晚间"), "dinner"),
                    ]
                    for _kws, _mt in _meal_kw_detect:
                        if any(kw in _q_lower for kw in _kws):
                            _llm_meal_times = [_mt]
                            break
                llm_result = self._dates_to_slots(_dates, _llm_meal_times)
        except Exception as exc:
            logger.warning(
                "[PLAN_AHEAD_PIPELINE] _init_planning_queue LLM call failed: %s — using keyword fallback",
                exc,
            )

        if llm_result is not None:
            # Sanity check: if LLM returned a single date but the input implies a
            # multi-day range, the LLM likely missed the duration.
            # In that case, defer to the keyword fallback which handles this reliably.
            import re as _check_re
            _unique_dates_llm = list(dict.fromkeys(s.split("|")[0] for s in llm_result))
            if len(_unique_dates_llm) == 1:
                _llm_fallback_reason: Optional[str] = None

                # Case A: Arabic numeral N天 with N>1 (e.g. "往后10天")
                _n_match = _check_re.search(r"(\d+)\s*天", user_input or "")
                if _n_match and int(_n_match.group(1)) > 1:
                    _llm_fallback_reason = f"Arabic '{_n_match.group(1)}天' span"

                # Case B: Week-range keywords that imply 2–7 days
                # (e.g. "这周", "本周", "下下周", "下个星期") — LLM often returns only start day.
                elif _check_re.search(
                    r"这周|本周|这星期|下下周|下下个星期|下个星期|下星期|week after next|next week",
                    user_input or "",
                    _check_re.IGNORECASE,
                ):
                    _llm_fallback_reason = "week-range keyword"

                # Case C: Chinese numeral day span (e.g. "两天", "三天" … "九天")
                elif _check_re.search(r"[两三四五六七八九]天", user_input or ""):
                    _llm_fallback_reason = "Chinese-numeral day span"

                # Case D: Month / long-span keywords (e.g. "一个月", "整月", "半个月")
                # — LLM often returns only the start date for these.
                elif _check_re.search(r"一个月|整月|两个月|三个月|半个月", user_input or ""):
                    _llm_fallback_reason = "month span"

                # Case E: Multiple distinct Chinese weekday characters (e.g. "周五周六", "周三和周四")
                # — LLM often collapses these to a single date, losing the second day.
                elif len(set(_check_re.findall(r"周([一二三四五六日天])", user_input or ""))) >= 2:
                    _llm_fallback_reason = "multiple weekday mentions"

                # Case F: Chinese/Arabic numeral + 周 / 个星期 spans (e.g. "两周", "接下来两周",
                # "三个星期") — LLM often returns only start date without the end.
                elif _check_re.search(
                    r"[两三四五六七八九\d]+\s*(?:周|个星期|个礼拜)|two\s+weeks|three\s+weeks",
                    user_input or "",
                    _check_re.IGNORECASE,
                ):
                    _llm_fallback_reason = "multi-week span"

                if _llm_fallback_reason:
                    logger.debug(
                        "[PLAN_AHEAD_PIPELINE] _init_planning_queue: LLM returned single date but "
                        "input has %s — using keyword fallback for duration.",
                        _llm_fallback_reason,
                    )
                    llm_result = None

            if llm_result is not None:
                return llm_result

        # ---- Keyword fallback (used when LLM call fails) ----
        import re as _re

        q = (user_input or "").lower()

        # Short-circuit: remove/delete operations are never planning requests.
        # Without this guard, "去掉今天晚上的计划" would match "今天"+"计划" below
        # and return a dinner slot, incorrectly triggering queue mode.
        _REMOVE_PHRASES = (
            "去掉", "删除", "取消", "清除", "移除", "清空", "删掉", "去除",
            "remove", "delete", "cancel",
        )
        if any(kw in q for kw in _REMOVE_PHRASES):
            return []

        # Detect specific single-meal keywords for today/tomorrow
        _meal_kw_map = {
            ("早餐", "早饭", "早上吃", "早上", "今早", "明早", "早晨"): "breakfast",
            ("午餐", "午饭", "中餐", "中饭", "中午"): "lunch",
            ("晚餐", "晚饭", "晚上吃", "晚上", "今晚", "明晚", "晚间"): "dinner",
        }
        _detected_meal: Optional[str] = None
        for _kws, _mt in _meal_kw_map.items():
            if any(kw in q for kw in _kws):
                _detected_meal = _mt
                break

        def _mk_slots(dates: List[str]) -> List[str]:
            return self._dates_to_slots(dates, [_detected_meal] if _detected_meal else None)

        tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        today_str_plain = today.strftime("%Y-%m-%d")

        # "明天" alone (single day) — must check BEFORE multi-day patterns
        if ("明天" in q or "tomorrow" in q) and not ("明后" in q or "后天" in q):
            return _mk_slots([tomorrow_str])

        # Multiple specific weekdays (e.g. "下周五周六", "周三和周四")
        # Must be checked BEFORE the generic "下周"→7-day catch-all below.
        _wd_char_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        _wd_found = list(dict.fromkeys(_re.findall(r"周([一二三四五六日天])", q)))
        if len(_wd_found) >= 2:
            # Determine base week: "下周" / "下个星期" → next Monday; else → this week's Monday
            _is_next_wk = any(kw in q for kw in ("下周", "下个星期", "下星期", "next week"))
            _is_next_next_wk = any(kw in q for kw in ("下下周", "下下个星期", "week after next"))
            _days_to_monday = (7 - today.weekday()) % 7 or 7
            if _is_next_next_wk:
                _base_monday = today + timedelta(days=_days_to_monday + 7)
            elif _is_next_wk:
                _base_monday = today + timedelta(days=_days_to_monday)
            else:
                _base_monday = today - timedelta(days=today.weekday())  # this week's Monday
            _specific_dates = []
            for _ch in _wd_found:
                _target = _base_monday + timedelta(days=_wd_char_map[_ch])
                if _target >= today:
                    _specific_dates.append(_target.strftime("%Y-%m-%d"))
            if len(_specific_dates) >= 2:
                return _mk_slots(_specific_dates)

        if any(kw in q for kw in ("下下周", "下下个星期", "下下星期", "week after next")):
            days_ahead = (7 - today.weekday()) % 7 or 7
            start = today + timedelta(days=days_ahead + 7)
            return _mk_slots([(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)])
        # "今天到下个星期/下周" — from today through next Sunday
        _has_today_start = any(kw in q for kw in ("今天到", "从今天到", "today to next"))
        _has_next_week = any(kw in q for kw in ("下周", "下个星期", "下星期", "next week"))
        if _has_today_start and _has_next_week:
            days_to_next_monday = (7 - today.weekday()) % 7 or 7
            next_sunday = today + timedelta(days=days_to_next_monday + 6)
            n = (next_sunday - today).days + 1
            return _mk_slots([(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)])
        if _has_next_week:
            days_ahead = (7 - today.weekday()) % 7 or 7
            start = today + timedelta(days=days_ahead)
            return _mk_slots([(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)])
        if any(kw in q for kw in ("这周", "本周", "这个星期", "this week")):
            days_to_sunday = 6 - today.weekday()
            return _mk_slots([(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_to_sunday + 1)])
        # "明后" / "明天后天" — tomorrow + day after = 2 days starting tomorrow
        if "明后" in q or ("明天" in q and "后天" in q):
            return _mk_slots([(today + timedelta(days=1 + i)).strftime("%Y-%m-%d") for i in range(2)])

        # Specific N-day durations — must be checked BEFORE the generic "往后/接下来" catch-all
        if any(kw in q for kw in ("一个月", "整月", "30天", "三十天", "a month")):
            return _mk_slots([(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)])
        if any(kw in q for kw in ("两周", "两个星期", "14天", "十四天", "two weeks")):
            return _mk_slots([(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14)])
        if any(kw in q for kw in ("半个月", "半月", "15天", "十五天", "half a month")):
            return _mk_slots([(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(15)])

        # Chinese numeral + 天 patterns (e.g. "两天", "三天", "五天")
        _cn_day_map = {"两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        _start_off = 1 if "明" in q else 0
        for _cn, _n in _cn_day_map.items():
            if _cn + "天" in q:
                return _mk_slots([(today + timedelta(days=_start_off + i)).strftime("%Y-%m-%d") for i in range(_n)])

        # Generic Arabic "N天" pattern (e.g. "往后10天", "接下来5天")
        _n_match = _re.search(r"(\d+)\s*天", q)
        if _n_match:
            n = int(_n_match.group(1))
            if n >= 1:
                _start_off_arab = 1 if "明" in q else 0
                return _mk_slots([(today + timedelta(days=_start_off_arab + i)).strftime("%Y-%m-%d") for i in range(n)])

        # Generic week-length catch-all (no specific duration qualifier)
        if any(kw in q for kw in ("整周", "一周", "一个星期", "七天", "7天", "接下来", "未来", "往后", "从今")):
            return _mk_slots([(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)])

        # Single-day planning keywords (today implied) — skip if user is naming a specific dish
        if any(kw in q for kw in ("今天", "今日", "today", "规划", "计划", "安排", "吃什么")):
            if not self._is_explicit_dish_request(q):
                return _mk_slots([today_str_plain])

        # Fuzzy trigger: time word + food/desire word (handles creative/dialect expressions
        # like "吃几天好的", "想好好吃", "这周好好补一补" that the primary LLM may miss)
        # Skip if user is explicitly naming a dish (e.g. "今天晚上吃芋艿猪排骨")
        _time_kw = ("今天", "明天", "后天", "这周", "这星期", "下周", "下星期", "最近", "接下来", "这几天", "往后")
        _food_desire_kw = ("吃", "餐", "饭", "菜", "补", "好好", "好好吃", "吃点好的", "大餐", "改善")
        if (
            any(kw in q for kw in _time_kw)
            and any(kw in q for kw in _food_desire_kw)
            and not self._is_explicit_dish_request(q)
        ):
            return _mk_slots([today_str_plain])

        return []

    # ------------------------------------------------------------------
    # Phase 1b: LLM-based date confirmation classifier
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_date_confirmation_layer2_guard(
        user_input: str,
        today_str: str,
    ) -> Optional[Dict[str, Any]]:
        """Layer 2: fast regex guard executed BEFORE the LLM call.

        Intercepts responses that are 100% certain without needing semantics:
          - Very short / single-token affirmatives (e.g. "好", "嗯", "是", "ok")
          - Explicit date numbers like "18号" or "改成明天" that can be resolved deterministically
          - All-meals keywords (一整天 / 三餐 / 全天)
          - Only-one-meal keywords (只吃早餐 / 就午餐)

        Returns a result dict or None (→ proceed to LLM).
        """
        import re as _re
        _q = user_input.strip()

        # --- Absolute affirmatives (very short, unambiguous) ---
        _short_confirm = {"好", "行", "嗯", "是", "对", "ok", "yes", "好的", "是的", "对的"}
        if _q.lower() in _short_confirm:
            return {"intent": "confirmed", "new_dates": [], "new_meal_times": None}

        # --- Explicit "N号" date in current month → corrected with date ---
        _day_match = _re.search(r"(\d{1,2})\s*号", _q)
        if _day_match:
            try:
                from datetime import date as _d
                _today = _d.fromisoformat(today_str)
                _day = int(_day_match.group(1))
                _target = _today.replace(day=_day)
                if _target < _today:
                    # Roll to next month
                    import calendar as _cal
                    _nm = _today.month % 12 + 1
                    _ny = _today.year + (_today.month // 12)
                    _target = _today.replace(year=_ny, month=_nm, day=_day)
                _ds = _target.strftime("%Y-%m-%d")
                return {"intent": "corrected", "new_dates": [_ds], "new_meal_times": None}
            except (ValueError, OverflowError):
                pass  # Malformed day number → fall through to LLM

        # --- All-meals keywords ---
        _all_meals_kw = ("一整天", "一日三餐", "三餐", "全天")
        if any(kw in _q for kw in _all_meals_kw):
            return {"intent": "corrected", "new_dates": [], "new_meal_times": None}

        # --- Single-meal override (e.g. "只要午餐", "就晚饭") ---
        _solo_meal_pattern = r"(?:只|就|仅)(?:吃|要|有|是)?\s*(早餐|早饭|午餐|午饭|晚餐|晚饭)"
        _solo = _re.search(_solo_meal_pattern, _q)
        if _solo:
            _mt_map = {"早餐": "breakfast", "早饭": "breakfast",
                       "午餐": "lunch", "午饭": "lunch",
                       "晚餐": "dinner", "晚饭": "dinner"}
            _mt = _mt_map.get(_solo.group(1))
            if _mt:
                return {"intent": "corrected", "new_dates": [], "new_meal_times": [_mt]}

        return None  # Not intercepted → proceed to LLM

    @staticmethod
    def _classify_date_confirmation_keyword_fallback(
        user_input: str,
    ) -> Optional[Dict[str, Any]]:
        """Layer 3 (last resort): keyword fallback when both Layer 2 guard and LLM fail.

        Returns a dict with intent / new_meal_times if recognizable, else None.
        """
        _q = user_input.strip()

        # Correction check MUST come before confirmation check:
        # "不对，我是说…" contains "对" which is also a confirm keyword.
        # Detecting the negation/correction intent first prevents false "confirmed".
        # Also treat addendum signals ("还有周六", "加上周三") as corrections so the
        # Phase 1b corrected-handler can extract the new date via _extract_correction_details.
        _correction_kw = (
            "我是说", "不对", "不是", "应该是", "是说", "改成", "换成",
            "还有", "加上", "还要", "另外",   # addendum: "还有周六" = add Saturday
        )
        _all_meals_kw = ("一整天", "一日三餐", "三餐", "全天", "all meals", "all day")
        _is_correction = any(kw in _q for kw in _correction_kw)
        _wants_all_meals = any(kw in _q for kw in _all_meals_kw)

        if _is_correction or _wants_all_meals:
            new_meal_times: Optional[List[str]] = None
            _meal_kw_map = [
                (("早餐", "早饭", "早上"), "breakfast"),
                (("午餐", "午饭", "中餐", "中饭", "中午"), "lunch"),
                (("晚餐", "晚饭", "晚上"), "dinner"),
            ]
            _detected: List[str] = []
            for _kws, _mt in _meal_kw_map:
                if any(kw in _q for kw in _kws):
                    _detected.append(_mt)
            if _wants_all_meals and not _detected:
                new_meal_times = None
            elif _detected:
                new_meal_times = _detected
            return {"intent": "corrected", "new_dates": [], "new_meal_times": new_meal_times}

        # Confirm check comes AFTER correction so that "不对…对…" doesn't false-fire here.
        _confirm_kw = (
            "好", "行", "是的", "对", "没问题", "可以", "确认", "开始", "嗯", "好的",
            "yes", "ok", "sure", "correct", "right", "start", "begin",
        )
        if any(kw in _q for kw in _confirm_kw):
            return {"intent": "confirmed", "new_dates": [], "new_meal_times": None}

        return None

    async def _classify_date_confirmation(
        self,
        user_input: str,
        pending_queue: List[str],
        user_timezone: Optional[str],
    ) -> Dict[str, Any]:
        """Use a small LLM call to classify the user's response to the date confirmation prompt.

        Returns:
            {"intent": "confirmed" | "corrected" | "unclear",
             "new_dates": List[str],       # populated for corrected (date change)
             "new_meal_times": List[str] | None}  # populated for corrected (meal-type change)
        """
        import json as _json

        if not user_input:
            return {"intent": "unclear", "new_dates": [], "new_meal_times": None}

        now = self._now_in_timezone(user_timezone)
        today = now.date()
        today_str = today.strftime("%Y-%m-%d")

        # ── Layer 2: fast regex guard (no LLM needed for trivially clear cases) ──
        _l2 = self._classify_date_confirmation_layer2_guard(user_input, today_str)
        if _l2 is not None:
            logger.info(
                "[PLAN_AHEAD_PIPELINE] _classify_date_confirmation Layer2 guard: intent=%r for %r",
                _l2["intent"], user_input,
            )
            return _l2

        # pending_queue items are "YYYY-MM-DD|meal_type"; extract date portion for the prompt
        pending_start = (pending_queue[0].split("|")[0] if pending_queue else "")
        pending_end = (pending_queue[-1].split("|")[0] if pending_queue else "")
        _pending_meals = list(dict.fromkeys(
            s.split("|")[1] for s in pending_queue if "|" in s
        ))
        _n_slots = len(pending_queue)

        system_prompt = (
            f"Today is {today.strftime('%Y-%m-%d (%A)')}.\n"
            "You are a classifier for a meal-planning chatbot.\n"
            "The chatbot proposed a meal plan (dates + meals) and asked the user to confirm.\n\n"
            "Classify the user's reply as one of:\n"
            '  "confirmed" — user accepts the proposed plan as-is\n'
            '  "corrected" — user wants different dates OR different meal types\n'
            '  "unclear"   — cannot determine intent\n\n'
            "Output JSON with these fields:\n"
            "  intent: string  (required)\n"
            "  new_start: string | null  — YYYY-MM-DD, set when user specifies a new start date\n"
            "  new_end:   string | null  — YYYY-MM-DD, set when user specifies a new end date\n"
            "  new_meal_times: [string] | null  — subset of ['breakfast','lunch','dinner'],\n"
            "                  null means 'all three meals', set when user changes meal scope\n\n"
            "Rules:\n"
            "- Affirmative (好/行/是/对/没问题/可以/当然/开始/嗯/ok/yes/sure/right) → confirmed\n"
            "- User EXPLICITLY corrects the dates (不对是明天/改成下周/换成周六/etc.) → corrected,\n"
            "  fill new_start+new_end\n"
            "- User changes meal scope (一整天/三餐/一日三餐/全天/只要午餐/etc.) → corrected,\n"
            "  fill new_meal_times (null = all meals; ['dinner'] = dinner only; etc.)\n"
            "- Both date AND meal change → corrected, fill all applicable fields\n"
            "- IMPORTANT: A reply that merely MENTIONS a date (e.g. 'I'm traveling tomorrow',\n"
            "  '我明天要出差', '这几天不在') is NOT a date correction — classify as unclear.\n"
            "  'corrected' requires the user to be ACTIVELY changing the proposed plan dates.\n"
            "- Ambiguous/off-topic/irrelevant context → unclear\n\n"
            "Examples:\n"
            '  Proposed dinner only → user says "一整天"  → {"intent":"corrected","new_meal_times":null}\n'
            '  Proposed dinner only → user says "三餐都要"  → {"intent":"corrected","new_meal_times":null}\n'
            '  Proposed dinner only → user says "我是说今天一整天" → {"intent":"corrected","new_meal_times":null}\n'
            '  Proposed today → user says "不对，是明天" → {"intent":"corrected","new_start":"YYYY-MM-DD","new_end":"YYYY-MM-DD"}\n'
            '  Proposed 7 days → user says "没问题" → {"intent":"confirmed"}\n'
            '  Proposed 7 days → user says "开始吧" → {"intent":"confirmed"}\n'
            '  Proposed Mon-Tue → user says "我明天要去旅游" → {"intent":"unclear"}\n'
            '  Proposed Mon-Tue → user says "这几天我不在家" → {"intent":"unclear"}\n\n'
            "Return JSON only — no markdown, no extra text."
        )
        user_msg = (
            f"Proposed: {pending_start} to {pending_end} "
            f"(meals: {', '.join(_pending_meals) or 'all'}, {_n_slots} slot(s)).\n"
            f'User reply: "{user_input}"'
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 350,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "object",
                    "properties": {
                        "intent": {"type": "string"},
                        "new_start": {"type": "string", "nullable": True},
                        "new_end": {"type": "string", "nullable": True},
                        "new_meal_times": {
                            "type": "array",
                            "items": {"type": "string"},
                            "nullable": True,
                        },
                    },
                    "required": ["intent"],
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.gemini_api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                raw = resp.json()
            candidate = (raw.get("candidates") or [{}])[0]
            finish_reason = candidate.get("finishReason", "")
            parts = (candidate.get("content") or {}).get("parts") or []
            if not parts:
                raise ValueError(f"Empty parts (finishReason={finish_reason!r})")
            text = parts[0].get("text", "").strip()
            if not text:
                raise ValueError(f"Empty text (finishReason={finish_reason!r})")
            # Strip markdown code fences if present (LLM sometimes wraps JSON in ```json ... ```)
            import re as _re
            _json_match = _re.search(r"\{.*\}", text, _re.DOTALL)
            if not _json_match:
                raise ValueError(f"No JSON object found in response: {text[:80]!r}")
            parsed = _json.loads(_json_match.group())
            intent = str(parsed.get("intent", "unclear")).lower().strip()
            if intent not in ("confirmed", "corrected", "unclear"):
                intent = "unclear"

            if intent == "corrected":
                new_start = (parsed.get("new_start") or "")[:10]
                new_end = (parsed.get("new_end") or "")[:10]
                new_dates: List[str] = []
                if new_start and new_end:
                    from datetime import date as _d
                    try:
                        s = _d.fromisoformat(new_start)
                        e = _d.fromisoformat(new_end)
                        if e >= s:
                            new_dates = [
                                (s + timedelta(days=i)).strftime("%Y-%m-%d")
                                for i in range((e - s).days + 1)
                            ]
                    except ValueError:
                        pass
                _new_meal_times = parsed.get("new_meal_times") or None
                if isinstance(_new_meal_times, list) and not _new_meal_times:
                    _new_meal_times = None

                # If corrected but nothing was extracted, make a second targeted LLM call
                # to specifically extract what the user wants to change.
                if not new_dates and _new_meal_times is None:
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] corrected but empty extraction — "
                        "running second targeted extraction for %r",
                        user_input,
                    )
                    _extract_result = await self._extract_correction_details(
                        user_input, pending_start, pending_end, today_str
                    )
                    new_dates = _extract_result.get("new_dates") or new_dates
                    _new_meal_times = _extract_result.get("new_meal_times") or _new_meal_times

                logger.info(
                    "[PLAN_AHEAD_PIPELINE] _classify_date_confirmation: intent='corrected',"
                    " new_dates=%r, new_meal_times=%r for %r",
                    new_dates, _new_meal_times, user_input,
                )
                return {"intent": "corrected", "new_dates": new_dates, "new_meal_times": _new_meal_times}

            logger.info(
                "[PLAN_AHEAD_PIPELINE] _classify_date_confirmation: intent=%r for %r",
                intent, user_input,
            )
            return {"intent": intent, "new_dates": [], "new_meal_times": None}
        except Exception as exc:
            logger.warning(
                "[PLAN_AHEAD_PIPELINE] _classify_date_confirmation LLM failed: %s — using keyword fallback",
                exc,
            )
            # Keyword fallback is the last resort; covers clear signals when LLM is unavailable
            _kw = self._classify_date_confirmation_keyword_fallback(user_input)
            if _kw is not None:
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] _classify_date_confirmation keyword fallback: intent=%r for %r",
                    _kw["intent"], user_input,
                )
                return _kw
            return {"intent": "unclear", "new_dates": [], "new_meal_times": None}

    async def _extract_correction_details(
        self,
        user_input: str,
        current_start: str,
        current_end: str,
        today_str: str,
    ) -> Dict[str, Any]:
        """Second-pass targeted extraction when the primary classifier returned 'corrected'
        but failed to populate new_dates or new_meal_times.

        Focuses entirely on structured extraction (not classification), asking the LLM
        to interpret the user's message as a date/meal change relative to the current proposal.
        """
        import json as _json
        import re as _re

        system_prompt = (
            f"Today is {today_str}. Current proposal: {current_start} to {current_end}.\n"
            "The user wants to change the meal plan dates or meals. Extract what they want.\n\n"
            "Return JSON with:\n"
            "  new_start: YYYY-MM-DD | null   (if user specifies a new start date)\n"
            "  new_end:   YYYY-MM-DD | null   (if user specifies a new end date)\n"
            "  new_meal_times: ['breakfast','lunch','dinner'] subset | null  "
            "(null = all three meals, set only if user restricts/expands meals)\n\n"
            "Date resolution rules:\n"
            "- '明天' = today + 1 day\n"
            "- '后天' = today + 2 days\n"
            "- '下周X' = next week's Monday/Tuesday/... (weekday names: 一=Mon,二=Tue,...,日=Sun)\n"
            "- '这周X' = this week's corresponding weekday\n"
            "- '大后天' = today + 3 days\n"
            "If user says 'until X' or 'to X', resolve that as new_end.\n"
            "If only one endpoint is mentioned, set both new_start and new_end to that date.\n"
            "Return JSON only — no explanation, no markdown."
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": f'User said: "{user_input}"'}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 120,
                "responseMimeType": "application/json",
            },
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    self.gemini_api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                raw = resp.json()
            candidate = (raw.get("candidates") or [{}])[0]
            parts = (candidate.get("content") or {}).get("parts") or []
            text = (parts[0].get("text", "") if parts else "").strip()
            _match = _re.search(r"\{.*\}", text, _re.DOTALL)
            if not _match:
                return {"new_dates": [], "new_meal_times": None}
            extracted = _json.loads(_match.group())
            new_start = (extracted.get("new_start") or "")[:10]
            new_end = (extracted.get("new_end") or "")[:10]
            new_dates: List[str] = []
            if new_start and new_end:
                from datetime import date as _d
                try:
                    s = _d.fromisoformat(new_start)
                    e = _d.fromisoformat(new_end)
                    if e >= s:
                        new_dates = [
                            (s + timedelta(days=i)).strftime("%Y-%m-%d")
                            for i in range((e - s).days + 1)
                        ]
                except ValueError:
                    pass
            _new_meal_times = extracted.get("new_meal_times") or None
            if isinstance(_new_meal_times, list) and not _new_meal_times:
                _new_meal_times = None
            logger.info(
                "[PLAN_AHEAD_PIPELINE] _extract_correction_details: new_dates=%r, meal_times=%r",
                new_dates, _new_meal_times,
            )
            return {"new_dates": new_dates, "new_meal_times": _new_meal_times}
        except Exception as exc:
            logger.warning(
                "[PLAN_AHEAD_PIPELINE] _extract_correction_details failed: %s", exc
            )
            return {"new_dates": [], "new_meal_times": None}

    # ------------------------------------------------------------------
    # Inventory helper
    # ------------------------------------------------------------------

    async def _fetch_inventory(self, owner_id: int) -> List[Dict[str, Any]]:
        """Fetch the user's food inventory items from DataStorageService."""
        try:
            base_url = _get_storage_base_url()
            if not base_url:
                return []
            url = f"{base_url}/api/users/{owner_id}/documents"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                documents = response.json().get("documents", [])

            inventory_items: List[Dict[str, Any]] = []
            for doc in documents:
                metadata = doc.get("metadata") or {}
                if "items" in metadata and isinstance(metadata["items"], list):
                    for item in metadata["items"]:
                        if item.get("is_food", False):
                            inventory_items.append({
                                "product_name": item.get("product_name", "Unknown"),
                                "category": item.get("category", ""),
                                "quantity": item.get("quantity", "1"),
                                "estimated_shelf_life_days": item.get("estimated_shelf_life_days", 0),
                            })
                else:
                    product_name = metadata.get("product_name") or doc.get("title")
                    if metadata.get("is_food", False) and product_name:
                        inventory_items.append({
                            "product_name": product_name,
                            "category": metadata.get("category", ""),
                            "quantity": metadata.get("quantity", "1"),
                            "estimated_shelf_life_days": metadata.get("estimated_shelf_life_days", 0),
                        })
            logger.info(f"[PLAN_AHEAD_PIPELINE] Fetched {len(inventory_items)} inventory items for user {owner_id}")
            return inventory_items
        except Exception as e:
            logger.warning(f"[PLAN_AHEAD_PIPELINE] Failed to fetch inventory: {e}")
            return []

    # ------------------------------------------------------------------
    # Step 2: Build LLM context
    # ------------------------------------------------------------------

    def _build_context(
        self,
        state: Dict[str, Any],
        user_timezone: Optional[str],
        inventory_items: Optional[List[Dict[str, Any]]] = None,
        is_draft: bool = False,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
        scheduling_context: Optional[str] = None,
        user_profile: Optional[Dict[str, Any]] = None,
        queue_target_date: Optional[str] = None,
        precomputed_action: Optional[str] = None,
        active_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build system context string for the single-shot structured LLM call."""
        now = self._now_in_timezone(user_timezone)
        today = now.date()
        days_ahead = (7 - today.weekday()) % 7 or 7
        next_monday = today + timedelta(days=days_ahead)
        next_sunday = next_monday + timedelta(days=6)

        meal_plan_slots: Dict[str, Any] = state.get("meal_plan_slots") or {}
        dish_ingredients: Dict[str, List[str]] = state.get("dish_ingredients") or {}

        ctx = f"TODAY: {today.strftime('%Y-%m-%d (%A)')}"
        ctx += f"\nNEXT WEEK: Monday {next_monday.strftime('%Y-%m-%d')} to Sunday {next_sunday.strftime('%Y-%m-%d')}"
        if is_draft:
            ctx += "\n\n=== DRAFT MEAL PLAN (proposed — NOT saved to calendar yet) ==="
            ctx += "\n(This is a proposal shown to the user. They may still request changes or confirm to save.)"
        else:
            ctx += "\n\n=== CURRENT MEAL PLAN (saved in calendar — authoritative) ==="
        if meal_plan_slots:
            for date_str in sorted(meal_plan_slots.keys()):
                slots = meal_plan_slots[date_str] or {}
                for mt in ("breakfast", "lunch", "dinner"):
                    slot_val = slots.get(mt)
                    if not slot_val:
                        continue
                    dishes: List[str] = slot_val if isinstance(slot_val, list) else [slot_val]
                    dish_strs = []
                    for d in dishes:
                        ings = dish_ingredients.get(d)
                        if ings:
                            ing_names = [
                                i["name"] if isinstance(i, dict) else i
                                for i in ings if i
                            ]
                            dish_strs.append(f"{d} [ingredients: {', '.join(ing_names)}]")
                        else:
                            dish_strs.append(d)
                    ctx += f"\n{date_str} {mt}: {', '.join(dish_strs)}"
        else:
            ctx += "\n(no plan yet)"

        # --- Inventory section ---
        ctx += "\n\n=== USER'S CURRENT FOOD INVENTORY ==="
        if inventory_items:
            ctx += "\nThese ingredients are ALREADY in the kitchen. Prioritize using them to reduce waste."
            ctx += "\nMark items with shelf life ≤3 days as urgent (should be consumed soon).\n"
            # Sort by urgency: items expiring soonest first (nulls/0 = no expiry, sent last).
            # This ensures proteins/produce that expire soon appear within the LLM's view window
            # rather than being pushed out by condiments with no shelf-life tracking.
            _INF = 9999
            sorted_items = sorted(
                inventory_items,
                key=lambda x: (x.get("estimated_shelf_life_days") or _INF),
            )
            _LIMIT = 50
            for item in sorted_items[:_LIMIT]:
                name = item.get("product_name", "Unknown")
                qty = item.get("quantity", "")
                shelf = item.get("estimated_shelf_life_days") or 0
                line = f"  - {name}"
                if qty:
                    line += f" (qty: {qty})"
                if shelf > 0:
                    urgency = " [USE SOON]" if shelf <= 3 else f" [~{shelf}d shelf life]"
                    line += urgency
                ctx += line + "\n"
            if len(inventory_items) > _LIMIT:
                ctx += f"  (... and {len(inventory_items) - _LIMIT} more items)\n"
        else:
            ctx += "\n(No food items found — recommend based on common ingredients)\n"

        # --- Active Conversation Context (Memory Layer) ---
        _active_ingredients: List[str] = (active_context or {}).get("active_ingredients", [])
        _active_target_date: Optional[str] = (active_context or {}).get("target_date")
        _active_meal_type: Optional[str] = (active_context or {}).get("target_meal_type")
        if _active_ingredients:
            ctx += "\n\n=== ACTIVE CONVERSATION CONTEXT (remembered from this session) ==="
            ctx += (
                "\nThe user mentioned having these ingredients on hand during this conversation."
                "\nIncorporate them into your suggestions where appropriate:"
            )
            ctx += f"\n  Ingredients available: {', '.join(_active_ingredients)}"
            if _active_target_date or _active_meal_type:
                _slot_hint = " | ".join(
                    filter(None, [_active_target_date, _active_meal_type])
                )
                ctx += f"\n  Planning context: {_slot_hint}"
            ctx += (
                "\nCRITICAL: When the user says things like '葱花的话能做啥' or 'what else can I make',"
                "\n  assume they are ADDING to the previously mentioned ingredients, not starting fresh."
                "\n  Combine all on-hand ingredients into a coherent meal suggestion."
            )
            logger.info(
                "[PLAN_AHEAD_PIPELINE] ACTIVE CONTEXT injected: ingredients=%s",
                _active_ingredients,
            )

        # --- Cooking level section ---
        _level_map = {
            "beginner": "Complete Beginner (no cooking experience)",
            "intermediate": "Some Experience (knows basic techniques)",
            "expert": "Experienced Cook (can handle complex recipes)",
        }
        _level_label = _level_map.get(cooking_level or "beginner", _level_map["beginner"])
        ctx += f"\n\n=== USER COOKING LEVEL: {_level_label} ==="
        if cooking_level == "beginner":
            ctx += (
                "\nThis user is a complete beginner. When recommending meals:"
                "\n- Suggest simple, beginner-friendly dishes with minimal steps (e.g. stir-fries, fried eggs, simple soups)."
                "\n- Avoid dishes requiring advanced knife skills, precise temperatures, or complex techniques."
                "\n- Prefer dishes with fewer than 5 ingredients when possible."
            )
        elif cooking_level == "intermediate":
            ctx += (
                "\nThis user has some cooking experience. When recommending meals:"
                "\n- Suggest moderately complex dishes (e.g. braised meats, dumplings, multi-component stir-fries)."
                "\n- Include dishes that require basic techniques like marinating, blanching, or sauce-making."
                "\n- Balance simplicity with variety."
            )
        elif cooking_level == "expert":
            ctx += (
                "\nThis user is an experienced cook. When recommending meals:"
                "\n- Feel free to suggest complex, multi-step dishes (e.g. Peking duck, hand-made noodles, elaborate stews)."
                "\n- Include dishes requiring advanced techniques like deep-frying, precise timing, or complex flavor layering."
                "\n- Prioritize variety, creativity, and culinary challenge."
            )

        # --- Language section ---
        _lang_name_map = {
            "zh": "Simplified Chinese (简体中文)",
            "en": "English",
            "ja": "Japanese (日本語)",
            "ko": "Korean (한국어)",
        }
        _lang_name = _lang_name_map.get(language or "zh", _lang_name_map["zh"])
        ctx += f"\n\n=== LANGUAGE REQUIREMENT (CRITICAL) ==="
        ctx += (
            f"\nYou MUST use {_lang_name} for ALL text content in your response — this includes:"
            f"\n- 'user_message': must be in {_lang_name}."
            f"\n- ALL ingredient 'name' fields inside meal_entries → dishes → ingredients: must be in {_lang_name}."
            f"\n- ALL dish names inside meal_entries: must be in {_lang_name}."
            f"\n- 'recommendation_reason' if present: must be in {_lang_name}."
            f"\nDo NOT use English (or any other language) for ingredient names or dish names unless {_lang_name} is English."
            f"\nThis rule overrides everything else, regardless of what language the user writes in."
        )

        # --- User meal blueprint (soft defaults from DB, overridable by user in conversation) ---
        if user_profile:
            ctx += "\n\n=== USER MEAL BLUEPRINT (DEFAULT PREFERENCES — can be overridden by user's explicit requests) ==="
            ctx += (
                "\n⚠️  OVERRIDE RULE: If the user explicitly says they DON'T want something"
                " (e.g. '不要蔬菜', '不要汤', '去掉这道菜', 'no soup today', 'remove the vegetable dish'),"
                " ALWAYS honor that request immediately — modify or remove the dish and confirm the change."
                " These defaults are guides for AI-generated plans, NOT mandatory rules that override user intent."
            )
            _servings = user_profile.get("default_servings", 1)
            ctx += f"\n- Default servings per dish: {_servings} person(s). Scale all ingredient quantities accordingly."
            _ratio = user_profile.get("meat_veg_ratio", "1:1:1")
            _ratio_parts = _ratio.split(":")
            if len(_ratio_parts) == 3:
                ctx += f"\n- Preferred dish composition per meal: {_ratio_parts[0]} meat dish(es), {_ratio_parts[1]} vegetable dish(es), {_ratio_parts[2]} staple(s). (User can override this for any specific meal.)"
            _soup = user_profile.get("include_soup", True)
            if _soup:
                ctx += "\n- Prefers at least one soup dish per meal — suggest one when generating plans, but skip if the user says they don't want soup."
            _cal = user_profile.get("calorie_target")
            if _cal:
                ctx += f"\n- Per-meal calorie target: ~{_cal} kcal. Prefer lighter dishes if near the limit."
            _disliked = user_profile.get("disliked_ingredients") or []
            if _disliked:
                ctx += f"\n- FORBIDDEN ingredients (user dislikes): {', '.join(_disliked)}. Do NOT include any of these in any dish."
            _cw = user_profile.get("cuisine_weights") or {}
            if _cw:
                _sorted_cw = sorted(_cw.items(), key=lambda x: -x[1])
                _cw_str = ", ".join(f"{k}({v}%)" for k, v in _sorted_cw if v > 0)
                ctx += f"\n- Cuisine preference weights: {_cw_str}. Higher weight = recommend more dishes from that cuisine."

            _blueprint_summary = (
                f"servings={_servings}, soup={'yes' if _soup else 'no'}, "
                f"cal={_cal or 'none'}, "
                f"disliked={_disliked if _disliked else '(none)'}, "
                f"cuisine_weights={'yes' if _cw else 'none'}"
            )
            logger.info(f"[PLAN_AHEAD_PIPELINE] USER MEAL BLUEPRINT injected: {_blueprint_summary}")
        else:
            logger.info("[PLAN_AHEAD_PIPELINE] USER MEAL BLUEPRINT skipped (no profile)")

        # --- Phase 2: Diversity Engine directive ---
        if user_profile:
            try:
                from app.services.diversity_engine import compute_diversity_directive
                _recent = user_profile.get("recent_dishes") or []
                _cw_for_engine = user_profile.get("cuisine_weights") or {}
                _diversity_directive = compute_diversity_directive(_recent, _cw_for_engine)
                ctx += "\n\n=== DIVERSITY ENGINE: VARIETY DIRECTIVE (applies to AI-generated suggestions only) ==="
                ctx += "\nTo ensure meal variety when YOU are recommending dishes, prefer avoiding repetition:"
                ctx += f"\n{_diversity_directive}"
                ctx += (
                    "\n\n⚠️  OVERRIDE RULE: These diversity constraints apply ONLY when you are autonomously"
                    " choosing dishes. If the user explicitly names a specific dish they want"
                    " (e.g. '我想吃萝卜炖牛腩', '加个红烧肉'), you MUST honor their request"
                    " and add that exact dish — even if it appears in a SOFT AVOID or HARD BAN list above."
                    " User's explicit choice always takes priority over diversity rules."
                )
                _hard_bans = [l for l in _diversity_directive.splitlines() if "HARD BAN" in l]
                _soft_avoids = [l for l in _diversity_directive.splitlines() if "SOFT AVOID" in l]
                logger.info(
                    f"[PLAN_AHEAD_PIPELINE] DIVERSITY ENGINE injected: "
                    f"recent_count={len(_recent)}, "
                    f"hard_bans={'yes' if _hard_bans else 'none'}, "
                    f"soft_avoids={'yes' if _soft_avoids else 'none'}, "
                    f"variety_target={'yes' if _cw_for_engine else 'none'}"
                )
            except Exception as _de_err:
                logger.warning(f"[PLAN_AHEAD_PIPELINE] DiversityEngine failed (non-fatal): {_de_err}")
        else:
            logger.info("[PLAN_AHEAD_PIPELINE] DIVERSITY ENGINE skipped (no profile)")

        # --- Phase 3: Seed Library candidate reference ---
        if user_profile:
            try:
                from app.services import seed_library as _seed_lib
                _sl_recent = user_profile.get("recent_dishes") or []
                _sl_cw = user_profile.get("cuisine_weights") or {}
                _sl_disliked = user_profile.get("disliked_ingredients") or []
                _candidates = _seed_lib.select_candidates(
                    cuisine_weights=_sl_cw,
                    disliked_ingredients=_sl_disliked,
                    recent_dishes=_sl_recent,
                )
                if _candidates:
                    ctx += f"\n\n{_seed_lib.build_seed_context(_candidates)}"
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] SEED LIBRARY injected: %d candidates",
                        len(_candidates),
                    )
            except Exception as _sl_err:
                logger.warning("[PLAN_AHEAD_PIPELINE] SeedLibrary failed (non-fatal): %s", _sl_err)
        else:
            logger.debug("[PLAN_AHEAD_PIPELINE] SEED LIBRARY skipped (no profile)")

        # --- Phase 3b: Rejected / already-tried dishes (current draft session) ---
        _rejected: set = state.get("draft_rejected_dishes") or set()
        if _rejected:
            ctx += "\n\n=== DISHES ALREADY TRIED / REJECTED THIS SESSION ==="
            ctx += (
                "\nThe user has already replaced or rejected the following dishes during this planning session. "
                "Do NOT suggest any of them again:"
            )
            for _rd in sorted(_rejected):
                ctx += f"\n- {_rd}"
            ctx += (
                "\n\nWhen suggesting a replacement dish, FIRST pick from the SEED LIBRARY CANDIDATES above "
                "that have not been used in the current plan and are not in the rejected list. "
                "Only freely invent a new dish if no suitable unused seed candidate exists."
            )
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Rejected dishes injected: %d — %s",
                len(_rejected), sorted(_rejected),
            )

        # --- Live schedule context from SchedulingAgent ---
        if scheduling_context:
            ctx += "\n\n=== LIVE CALENDAR CONTEXT (from SchedulingAgent — most up-to-date) ==="
            ctx += f"\n{scheduling_context}"
            ctx += (
                "\n\nCRITICAL ANTI-DUPLICATION RULES:"
                "\n- Compare ANY dish you are about to add against BOTH the CURRENT MEAL PLAN above AND the LIVE CALENDAR CONTEXT."
                "\n- If the same dish already exists in the same meal slot (date + meal_time), do NOT add it again."
                "\n- If the user explicitly asks to add a dish that is already there, acknowledge the duplicate and ask for confirmation."
            )
        else:
            ctx += (
                "\n\nCRITICAL ANTI-DUPLICATION RULE:"
                "\n- Do NOT add a dish to a meal slot if that slot already contains the same dish in the CURRENT MEAL PLAN above."
            )

        # Inject session flow state so the LLM knows which step it's on
        _last_pa_action = state.get("last_pipeline_action")
        if _last_pa_action == "ask":
            ctx += (
                "\n\n=== SESSION FLOW STATE ==="
                "\nPREVIOUS TURN: You asked the user for their meal preferences (action=ask)."
                "\nCURRENT TURN: The user has now provided their preferences."
                "\nNEXT STEP REQUIRED: Use action='suggest_options' to present 2-3 alternative meal plan options."
                "\nDO NOT use 'recommend' directly — present options first so the user can choose."
                "\nDO NOT use 'ask' again — the user already provided their preferences."
            )
        elif _last_pa_action == "suggest_options":
            ctx += (
                "\n\n=== SESSION FLOW STATE ==="
                "\nPREVIOUS TURN: You presented meal plan options (action=suggest_options)."
                "\nCURRENT TURN: The user is selecting/modifying an option."
                "\nNEXT STEP REQUIRED: Use action='recommend' with the selected option's meal_entries as the draft."
            )

        if queue_target_date:
            _day_labels = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            from datetime import date as _qdate_cls
            _meal_zh_map = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
            _meal_en_map = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner"}

            if "|" in queue_target_date:
                # Per-meal-slot format: "YYYY-MM-DD|meal_type"
                _slot_date_str, _slot_meal = queue_target_date.split("|", 1)
                _qd = _qdate_cls.fromisoformat(_slot_date_str)
                _qday_name = _day_labels[_qd.weekday()]
                _meal_zh = _meal_zh_map.get(_slot_meal, _slot_meal)
                ctx += (
                    f"\n\n=== QUEUE PLANNING MODE — SINGLE MEAL ==="
                    f"\nYou are planning one meal at a time to keep each response short."
                    f"\nCURRENT TASK: Plan {_slot_meal} ({_meal_zh}) for {_slot_date_str} ({_qday_name}) ONLY."
                    f"\n• Use action='suggest_options' with EXACTLY 2 compact options for {_slot_meal}."
                    f"\n• Each option: 2-3 dishes suitable for {_slot_meal} (no more)."
                    f"\n• meal_entries MUST only contain date={_slot_date_str}, meal_time={_slot_meal}."
                    f"\n• Do NOT plan any other meal_time or date."
                    f"\n• Do NOT use action='confirm' or action='recommend' directly."
                    f"\n• Be concise in user_message — one short line per option."
                )
            else:
                # Legacy per-day format
                _qd = _qdate_cls.fromisoformat(queue_target_date)
                _qday_name = _day_labels[_qd.weekday()]
                ctx += (
                    f"\n\n=== QUEUE PLANNING MODE — ACTIVE ==="
                    f"\nCURRENT TASK: Generate a meal plan for {queue_target_date} ({_qday_name}) ONLY."
                    f"\n• Use action='suggest_options' with 2 alternatives for {queue_target_date}."
                    f"\n• Do NOT include meal entries for any other date."
                    f"\n• Do NOT use action='confirm' — the queue is still in progress."
                )

        # ---- Skill-pre-routed action directive ----
        # ClassifyMealActionSkill already determined the action — inject as a hard directive
        # so the main LLM follows it instead of guessing from the giant prompt.
        if precomputed_action == "suggest_options" and not queue_target_date:
            _suggest_directive = (
                "\n\n=== ACTION DIRECTIVE (mandatory — do not override) ==="
                "\nThe system pre-analyzed the user's request and determined:"
                "\n  The user expressed a FOOD PREFERENCE or CATEGORY (not a specific dish)."
                "\n  They want to CHOOSE from multiple options — not receive a direct plan."
                "\nMandatory action: use 'suggest_options' with EXACTLY 2 compact alternative plans."
                "\n• Each option: 2-3 dish names only, ingredients=[] (filled when user selects)."
                "\n• Do NOT use action='add', 'recommend', or 'ask'."
                "\n• Do NOT save anything yet — present options for the user to choose from."
                "\n• CRITICAL — REJECTED DISHES: Do NOT include ANY dish from the"
                " 'DISHES ALREADY TRIED / REJECTED THIS SESSION' list above in any option."
                " If 馒头, 米饭, or any other dish is in the rejected list, do NOT suggest it."
            )
            # If a draft is currently active, tell the LLM to carry forward confirmed preferences.
            if is_draft:
                _draft_slots = state.get("meal_plan_slots") or {}
                _draft_staples: List[str] = []
                _draft_mains: List[str] = []
                for _ds_date, _ds_meals in _draft_slots.items():
                    for _ds_mt, _ds_dishes in _ds_meals.items():
                        for _ds_dish in (_ds_dishes or []):
                            _ds_slot = (state.get("dish_ingredients") or {}).get(_ds_dish)
                            # Rough staple detection by dish name keywords
                            if any(kw in _ds_dish for kw in ("饭", "面", "粥", "馒", "包", "饼", "米", "noodle", "rice", "bread")):
                                _draft_staples.append(_ds_dish)
                            else:
                                _draft_mains.append(_ds_dish)
                if _draft_staples:
                    _suggest_directive += (
                        f"\n• CARRY FORWARD: The user's current draft already has staple: {', '.join(_draft_staples)}."
                        " Keep the same staple in BOTH options unless the user explicitly asks to change it."
                    )
            ctx += _suggest_directive
        elif precomputed_action == "ask" and not queue_target_date:
            ctx += (
                "\n\n=== ACTION DIRECTIVE (mandatory — do not override) ==="
                "\nThe user provided NO food preference at all."
                "\nMandatory action: use 'ask' with ONE focused question to gather a preference."
                "\n• Ask about cuisine style, ingredients, or dietary needs."
                "\n• Do NOT generate a plan or options yet."
            )
        elif precomputed_action == "add" and not queue_target_date:
            ctx += (
                "\n\n=== ACTION DIRECTIVE (mandatory — do not override) ==="
                "\nThe user explicitly named a specific dish to add."
                "\nMandatory action: use 'add' to add that exact dish."
            )

        ctx += "\n\n=== INSTRUCTIONS ==="
        ctx += (
            "\n1. Understand the user's intent and set 'action' to one of:"
            " add, modify, remove, update_ingredients, remove_ingredients, view, ask, recommend, suggest_options, confirm."
        )
        ctx += (
            "\n2. Set 'target_date' (YYYY-MM-DD) for the affected date,"
            " and 'meal_time' (breakfast/lunch/dinner)."
        )
        ctx += "\n3. Apply the change and output the relevant dates in 'meal_entries':"
        ctx += "\n   - For 'add': include ONLY the new date(s) the user mentioned. Do NOT echo back dates that already exist in the current plan."
        ctx += "\n   - For 'modify'/'remove'/'update_ingredients'/'remove_ingredients': include ONLY the date(s) being changed."
        ctx += "\n   - For 'view'/'confirm': mirror the current plan exactly (all dates)."
        ctx += "\n   - For unchanged dates in a modify/remove operation, you may omit them — they are preserved automatically."
        ctx += (
            f"\n   - Each dish MUST include ingredients as an array of objects."
            f" Each ingredient object MUST have 'name' (write in {_lang_name}), 'category' (from the list below, keep in English), and an optional 'quantity' (e.g. '200g', '2 pieces', '1 tbsp')."
            "\n     category values (keep these exact English codes): vegetable, protein, dairy, grain, spice, other."
            "\n   - Each dish SHOULD include a 'slot' field (keep in English) to indicate its role in the meal:"
            "\n     slot values: 'main' (主菜, e.g. meat/fish/tofu dish), 'side' (配菜, e.g. stir-fried vegetables),"
            "\n     'soup' (汤品, any soup/broth/congee), 'staple' (主食, e.g. rice/noodles/bread), 'other'."
        )
        ctx += (
            "\n   - If user removes an ENTIRE date (e.g. '去掉今天的计划'), omit that date from meal_entries entirely."
            "\n   - If user removes only a SPECIFIC meal_time (e.g. '去掉今天晚上', 'remove tonight's dinner'):"
            "\n     set meal_time to the removed slot (breakfast/lunch/dinner), and include the OTHER"
            "\n     remaining meal slots for that date in meal_entries. Leave meal_entries empty ONLY"
            "\n     if that meal_time was the only one for that date."
        )
        ctx += (
            "\n4. Write a brief, friendly message in 'user_message' (match user's language)."
            "\n   CRITICAL: NEVER mention 'JSON', 'data format', 'structured response', or any technical"
            " implementation detail in user_message. The message is shown directly to the user."
            "\n   If you need to mention cooking steps, write them as plain text — do NOT say"
            " 'I cannot provide this in JSON'."
        )
        ctx += "\n\nRULES:"
        ctx += "\n- NEVER invent meals for dates not mentioned unless user explicitly asks."
        ctx += (
            "\n- EXPLICIT DISH RULE: If the user explicitly names specific dish(es) they want"
            " (e.g. '吃个萝卜炖牛腩', '加个红烧肉', 'I want beef stew'):"
            "\n  1. Use action='add' and include ONLY those exact dishes — do NOT pad the meal with extra dishes."
            "\n  2. IGNORE diversity soft-avoids and hard-bans for those dishes — user's explicit choice overrides variety rules."
            "\n  3. NEVER refuse or redirect with 'you ate this recently' — honor the request directly."
        )
        ctx += "\n- For 'view', meal_entries should mirror the current plan exactly."
        ctx += "\n- For 'update_ingredients'/'remove_ingredients', keep the same meals but update dish ingredients."
        ctx += (
            "\n- For 'remove' of an ENTIRE date: that date MUST NOT appear in meal_entries."
            "\n- For 'remove' of a specific meal_time only: set meal_time= the removed slot,"
            "\n  include the remaining slots for that date in meal_entries (if any)."
        )
        if is_draft:
            ctx += (
                "\n- In DRAFT MODE: prefer acting on the user's intent over asking clarifying questions."
                " A loose dish reference (wrong name, wrong date) is NOT a reason to use 'ask' —"
                " make a sensible substitution and tell the user what you changed."
                "\n- REMOVE DISH IN DRAFT: If the user says they don't want a dish"
                " (e.g. '不要蔬菜', '去掉这道菜', '我不想要汤了', '删掉青菜', 'remove the soup', 'I don't want the vegetable dish'):"
                "\n  * Use action='modify' and remove that dish from the plan (keep all other dishes as-is)."
                "\n  * Do NOT replace it with another dish unless the user asks for a replacement."
                "\n  * Do NOT refuse because of meal blueprint defaults — user's explicit removal always wins."
                "\n  * Confirm in user_message what was removed."
            )
        ctx += "\n\n--- MULTI-TURN RECOMMENDATION FLOW (read carefully) ---"
        ctx += (
            "\n- Use 'ask' when the user wants a recommendation but has NOT yet told you their preferences"
            " (cuisine style, dietary restrictions, number of servings, specific dishes, etc.)."
            "\n  * Set meal_entries=[] — do NOT generate or save any plan yet."
            "\n  * In user_message, ask ONE focused, friendly question to learn their preference."
            "\n  * Example: 'What cuisine do you prefer — Chinese home-style, Japanese, or Western?' or 'Any ingredients you avoid?'"
            "\n  * NEVER jump straight to generating a full meal plan on the first request without context."
        )
        ctx += (
            "\n- Use 'suggest_options' whenever the user asks for meal suggestions AND has any preference"
            " context available — either in the CURRENT message or prior turns. Preference context includes:"
            "\n  * A food category or ingredient type (海鲜, 肉类, 蔬菜, etc.)"
            "\n  * A cuisine style (日式, 川菜, 西餐, etc.)"
            "\n  * A dietary constraint or occasion (清淡, 辛辣, 减脂, 家常, etc.)"
            "\n  * Any answer to a previous 'ask' turn."
            "\n  This presents 2 alternative meal plan styles so the user can choose — NO plan is saved yet."
            "\n  * Set meal_entries=[] (the primary plan slot is empty for suggest_options)."
            "\n  * In 'dish_options', provide EXACTLY 2 alternative plans (option_id '1' and '2')."
            "\n    Each option MUST have: option_id, label (e.g. '方案一：家常风味'), and meal_entries."
            "\n    CRITICAL — keep options COMPACT to avoid token overflow:"
            "\n      - Each option's meal_entries must list dish names only."
            "\n      - Set ingredients=[] for every dish in dish_options (ingredients are filled when user selects)."
            "\n      - Do NOT write out shopping lists or per-dish ingredient details in options."
            "\n  * In user_message, present the options clearly:"
            "\n    - List each option with its label and a one-line summary of the dishes."
            "\n    - Tell the user to reply with '选方案X' or 'I choose option X' to select."
            "\n    - Invite the user to request modifications (e.g. 'add more vegetables', 'make it simpler')."
            "\n  * PRIORITIZE dishes that use ingredients from the FOOD INVENTORY for ALL options."
            "\n  * Ensure variety across options (different cuisines/flavors/complexity levels)."
        )
        ctx += (
            "\n- Use 'add' (NOT 'recommend') when the user explicitly names a specific dish they want to add"
            " (e.g. '明天晚上加个萝卜炖牛腩', '帮我加上红烧肉', 'add beef stew for dinner')."
            " Just add that exact dish — do NOT pad the meal with extra dishes unless the user asks for a full plan."
        )
        ctx += (
            "\n- Use 'recommend' when:"
            "\n  (a) The user has SELECTED one of the suggested options (e.g. '选方案2', 'I choose option 1')."
            "    Use the EXACT meal_entries from that option as the draft."
            "\n  (b) The user has selected AND requested modifications: apply the modifications to the option's plan."
            "\n  NEVER use 'recommend' to skip the options step. The user must always see 'suggest_options' first"
            " so they can choose before a plan is drafted — even if they provided preferences in the first message."
            "\n  This generates a DRAFT — it is NOT saved to calendar yet."
            "\n  * Fill requested slots with suitable dishes based on stated preferences."
            "\n  * PRIORITIZE dishes that use ingredients from the FOOD INVENTORY above."
            "\n  * Prefer [USE SOON] items — help the user avoid food waste."
            "\n  * Set 'recommendation_reason' to briefly explain the choice."
            "\n  * Include realistic ingredients in every dish entry."
            "\n  * CRITICAL — user_message MUST include a formatted meal-by-meal summary of the entire proposed plan."
            "\n    Format each entry as one line: '📅 <date> <meal_time>: <dish1>, <dish2>, ...'"
            "\n    List ALL dates in chronological order, then add an invitation to refine."
            "\n    Example structure:"
            "\n      Here is your proposed plan:"
            "\n      📅 2026-02-24 dinner: Kung Pao Chicken, Stir-fried Broccoli"
            "\n      📅 2026-02-25 dinner: Braised Pork Ribs, Tomato Egg Drop Soup"
            "\n      ..."
            "\n      How does this look? Feel free to ask for any changes, or say 'confirm' to save."
        )
        ctx += (
            "\n  * CRITICAL — Name alignment: when a dish uses an ingredient from the FOOD INVENTORY,"
            " copy its name EXACTLY as listed above"
            " (e.g. if inventory lists 'Tomato', write 'Tomato' not 'tomatoes'; if inventory lists 'Egg', write 'Egg' not 'Eggs')."
        )
        if is_draft:
            ctx += (
                "\n- DRAFT MODE IS ACTIVE. ALL modification requests from the user target the DRAFT above."
                "\n  NEVER ask the user whether they mean the draft or previously-saved data — always assume the DRAFT."
                "\n- When the user references a dish or ingredient that does not exactly appear in the DRAFT on that date,"
                " interpret their intent flexibly:"
                "\n  * 'change the tofu on Feb 26 to a meat dish' — if Feb 26 has no tofu, find the most vegetarian-heavy"
                " dish on that date and replace it with the requested type of dish."
                "\n  * 'remove the spicy dish on Wednesday' — if there are multiple dishes, pick the most likely spicy one."
                "\n  * Use 'modify' and make a reasonable substitution; explain your interpretation in user_message."
                "\n  * Only use 'ask' if the user's intent is truly ambiguous (e.g. they name a date not in the draft at all)."
                "\n- Use 'confirm' when the user approves the draft — this includes EXPLICIT and IMPLICIT signals:"
                "\n  Explicit: 'ok', 'confirm', 'save it', 'yes', 'looks good', 'that works', '可以', '确认', '保存', '好的', '行', '没问题', 'sure', 'perfect', '就这个吧', '就这样', '这个可以'."
                "\n  Implicit: 'sounds good', 'not bad', '听起来不错', '挺好的', '就按这个来', '行吧', 'go ahead', 'let's do it', '就这么定了', '按这个做'."
                "\n  Compound: if the user confirms AND adds a new request (e.g. '可以，周三的牛排怎么做？'),"
                " STILL use 'confirm' — the system will handle the secondary request separately."
                "\n  * For 'confirm': set meal_entries=[] (the system uses the current draft plan)."
                "\n  * In user_message, tell the user their plan has been saved and briefly summarize."
            )
        else:
            ctx += (
                "\n- Use 'confirm' only when there is an active draft to save."
                " (Currently no draft — direct mutations like add/modify save immediately.)"
            )
        ctx += f"\n- Date references: today={today.strftime('%Y-%m-%d')}, "
        days_labels = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        refs = ", ".join(f"{d}={(next_monday + timedelta(days=i)).strftime('%Y-%m-%d')}" for i, d in enumerate(days_labels))
        ctx += refs
        return ctx

    def _inject_pending_options(self, ctx: str, state: Dict[str, Any]) -> str:
        """Append pending_options section to the context string when options exist."""
        pending_options = state.get("pending_options")
        if not pending_options:
            return ctx

        ctx += "\n\n=== PENDING MEAL PLAN OPTIONS (user has NOT selected yet) ==="
        ctx += "\nThe following options were presented to the user. They are now selecting or modifying:"
        for opt in pending_options:
            oid = opt.get("option_id", "?")
            label = opt.get("label", f"方案{oid}")
            slots = opt.get("meal_plan_slots") or {}
            ctx += f"\n\n[方案{oid}] {label}:"
            for date_str in sorted(slots.keys()):
                for mt in ("breakfast", "lunch", "dinner"):
                    dishes = (slots.get(date_str) or {}).get(mt)
                    if dishes:
                        dish_list = dishes if isinstance(dishes, list) else [dishes]
                        ctx += f"\n  {date_str} {mt}: {', '.join(dish_list)}"
        ctx += (
            "\n\nWhen the user selects an option (e.g. '选方案2', 'option 1', '我要第三个'):"
            "\n  - Use 'recommend' action."
            "\n  - Copy the selected option's dishes into meal_entries with ALL fields filled:"
            "\n    each dish MUST include 'name', 'slot', and a complete 'ingredients' array"
            "\n    (the original options may not have ingredients — you MUST generate them now)."
            "\n  - If the user also requests a modification, apply it to the selected option's plan."
            "\n  - Clear pending options (they are resolved once the user selects)."
        )
        return ctx

    # ------------------------------------------------------------------
    # Lightweight intent classifier: explicit-add vs recommendation request
    # ------------------------------------------------------------------

    # Keywords that strongly signal the user is explicitly naming a dish to add/modify
    _EXPLICIT_DISH_KEYWORDS = (
        "加", "加个", "加上", "加一个", "吃个", "吃一个", "想吃", "要吃",
        "做", "做个", "做一个", "换成", "换个", "改成",
        "add", "want", "have",
    )

    @staticmethod
    def _keyword_fallback_classify(query: str) -> Dict[str, Any]:
        """Last-resort keyword-based classifier used when the LLM call fails.

        Returns same shape as _classify_dish_intent but with intent only (no dish extraction).
        """
        q_lower = query.lower()
        for kw in PlanAheadPipeline._EXPLICIT_DISH_KEYWORDS:
            if kw in q_lower:
                return {"is_explicit": False, "dishes": [], "intent": "EXPLICIT_HINT"}
        return {"is_explicit": False, "dishes": [], "intent": "UNKNOWN"}

    async def _classify_dish_intent(
        self, query: str, history: List[Dict]
    ) -> Dict[str, Any]:
        """Classify the user's query intent and extract any explicitly named dishes.

        Returns a dict:
          {
            "is_explicit": bool,        # True if user named specific dish(es)
            "dishes": List[str],        # extracted dish names (may be empty)
            "intent": str,              # "EXPLICIT" | "RECOMMEND" | "UNKNOWN"
          }

        Uses responseMimeType=application/json to force clean JSON output.
        Falls back to keyword heuristic, then to UNKNOWN on error.
        """
        import json as _json
        import re as _re

        _system = (
            "You are a meal-planning intent classifier.\n"
            "Analyze the user's latest message and return a JSON object with exactly two keys:\n"
            '  "intent": "EXPLICIT" if the user names specific dish(es) they want to eat/add/change, '
            'or "RECOMMEND" if they ask the AI to suggest or plan meals without naming a dish.\n'
            '  "dishes": an array of the explicitly named dish names (empty array [] for RECOMMEND).\n'
            "CRITICAL: Food categories/preferences are NOT specific dishes.\n"
            "  海鲜, 肉, 蔬菜, 辣的, 清淡, 日式, 川菜, 快手菜 → RECOMMEND (not EXPLICIT).\n"
            "  Only return EXPLICIT when the user names a SPECIFIC dish (e.g. 红烧肉, 蒜蓉蒸虾).\n\n"
            "Examples:\n"
            '  Input: "加个萝卜炖牛腩"  → {"intent":"EXPLICIT","dishes":["萝卜炖牛腩"]}\n'
            '  Input: "明天晚饭及一个小笼包" → {"intent":"EXPLICIT","dishes":["小笼包"]}\n'
            '  Input: "换成照烧鸡腿"    → {"intent":"EXPLICIT","dishes":["照烧鸡腿"]}\n'
            '  Input: "帮我计划今晚吃什么" → {"intent":"RECOMMEND","dishes":[]}\n'
            '  Input: "给我推荐几个菜"  → {"intent":"RECOMMEND","dishes":[]}\n'
            '  Input: "我想吃个海鲜"    → {"intent":"RECOMMEND","dishes":[]}  // 海鲜 is a category\n'
            '  Input: "想吃辣的东西"    → {"intent":"RECOMMEND","dishes":[]}  // 辣 is a preference\n'
            '  Input: "来点清淡的"      → {"intent":"RECOMMEND","dishes":[]}  // 清淡 is a preference\n'
            '  Input: "想吃日式"        → {"intent":"RECOMMEND","dishes":[]}  // cuisine type is preference'
        )

        _history_turns = []
        for msg in (history or [])[-4:]:
            role = "user" if msg.get("role") == "user" else "model"
            _history_turns.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        _history_turns.append({"role": "user", "parts": [{"text": query}]})

        payload = {
            "contents": _history_turns,
            "systemInstruction": {"parts": [{"text": _system}]},
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 200,
                "responseMimeType": "application/json",
            },
        }
        _fallback = {"is_explicit": False, "dishes": [], "intent": "UNKNOWN"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.gemini_api_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()
            candidates = result.get("candidates") or []
            if candidates:
                raw = (
                    candidates[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                # Try to extract JSON even if LLM added markdown fences or extra text
                _json_match = _re.search(r"\{.*\}", raw, _re.DOTALL)
                raw_json = _json_match.group(0) if _json_match else raw
                parsed = _json.loads(raw_json)
                intent = str(parsed.get("intent", "UNKNOWN")).upper()
                dishes = [str(d) for d in (parsed.get("dishes") or []) if d]
                is_explicit = intent == "EXPLICIT" and bool(dishes)
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] dish-intent classifier: query=%r → intent=%s dishes=%s",
                    query[:60], intent, dishes,
                )
                return {"is_explicit": is_explicit, "dishes": dishes, "intent": intent}
        except Exception as _clf_err:
            logger.warning(
                "[PLAN_AHEAD_PIPELINE] dish-intent classifier failed, trying keyword fallback: %s",
                _clf_err,
            )
        # Keyword fallback: can detect EXPLICIT_HINT but cannot extract dish names
        return self._keyword_fallback_classify(query)

    # Thin wrapper used by the Fresh-session guard (uses only the bool result).
    # Delegates to ClassifyDishIntentSkill — prompt lives in the skill file.
    async def _classify_explicit_add(self, query: str, history: List[Dict]) -> bool:
        result = await ClassifyDishIntentSkill(self.gemini_api_url).execute(query, history)
        if result["intent"] == "EXPLICIT_HINT":
            return True
        return result["is_explicit"]

    # ------------------------------------------------------------------
    # Step 3: Single LLM call
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        system_context: str,
        history: List[Dict],
        user_input: str,
        max_attempts: int = 3,
        max_history_messages: int = 20,
    ) -> Optional[Dict[str, Any]]:
        """Single structured LLM call with up to `max_attempts` retries on transient failures.

        `max_history_messages` caps the number of history messages included in the request
        (default 20 = 10 user/model turn pairs) to prevent unbounded token growth in long
        sessions. The slice always starts on a user-role message so the contents array
        begins with the correct role for the Gemini API.
        """
        import asyncio

        # Truncate history to the most recent N messages, then ensure we start with a
        # user message (drop a leading model message if the slice lands mid-turn).
        _history = (history or [])[-max_history_messages:]
        if _history and _history[0].get("role") != "user":
            _history = _history[1:]
        if len(history or []) > max_history_messages:
            logger.debug(
                f"[PLAN_AHEAD_PIPELINE] _call_llm: history truncated "
                f"{len(history)} → {len(_history)} messages (max={max_history_messages})"
            )

        contents = []
        for msg in _history:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": user_input}]})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_context}]},
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
                "responseSchema": PLAN_AHEAD_RESPONSE_SCHEMA,
            },
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=90.0) as client:
                    resp = await client.post(
                        self.gemini_api_url,
                        headers={"Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    result = resp.json()

                if "error" in result:
                    logger.error(f"[PLAN_AHEAD_PIPELINE] Gemini API error: {result['error']}")
                    return None  # API-level errors are not transient; don't retry

                candidates = result.get("candidates") or []
                if not candidates:
                    logger.warning(
                        f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt}: no candidates — retrying"
                    )
                    last_error = ValueError("no candidates")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                candidate = candidates[0]
                finish_reason = candidate.get("finishReason", "")
                if finish_reason not in ("STOP", ""):
                    logger.warning(
                        f"[PLAN_AHEAD_PIPELINE] LLM: unexpected finishReason={finish_reason!r}"
                    )

                text = (
                    candidate.get("content", {}).get("parts", [{}])[0].get("text", "") or ""
                ).strip()
                if not text:
                    logger.warning(
                        f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt}: empty response — retrying"
                    )
                    last_error = ValueError("empty text")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                parsed = self.plan_ahead_agent.parse_structured_response(text)
                if parsed is None:
                    logger.warning(
                        f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt}: JSON parse failed — retrying"
                    )
                    last_error = ValueError("JSON parse failed")
                    await asyncio.sleep(1.0 * attempt)
                    continue

                if attempt > 1:
                    logger.info(f"[PLAN_AHEAD_PIPELINE] LLM succeeded on attempt {attempt}")
                return parsed

            except httpx.TimeoutException as e:
                logger.warning(
                    f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt} timed out: {e}",
                    exc_info=(attempt == max_attempts),
                )
                last_error = e
                if attempt < max_attempts:
                    await asyncio.sleep(3.0 * attempt)
            except Exception as e:
                logger.warning(
                    f"[PLAN_AHEAD_PIPELINE] LLM attempt {attempt} failed: {e}",
                    exc_info=(attempt == max_attempts),
                )
                last_error = e
                if attempt < max_attempts:
                    await asyncio.sleep(1.0 * attempt)

        logger.error(
            f"[PLAN_AHEAD_PIPELINE] LLM failed after {max_attempts} attempts. Last error: {last_error}"
        )
        return None

    # ------------------------------------------------------------------
    # Step 5: Persist
    # ------------------------------------------------------------------

    async def _delete_orphaned_schedules(
        self,
        owner_id: int,
        orphaned_dates: List[str],
        storage_client: Any,
    ) -> None:
        """Remove specific dates from schedules.

        If a schedule contains ONLY the date being removed, the entire schedule is
        deleted.  If a schedule contains multiple dates (the common case — all plan
        dates share a single schedule record), only that date is stripped from the
        schedule metadata and the record is updated in-place, leaving the remaining
        dates intact.
        """
        if not orphaned_dates:
            return
        try:
            schedules = await storage_client.get_user_schedules(owner_id)
            for date_str in orphaned_dates:
                for s in schedules:
                    if s.get("event_type") not in ("meal_plan_draft", "shopping_list"):
                        continue
                    mp, _sl, di, slots = storage_client._extract_meal_plan_from_schedule(s)
                    if date_str not in mp:
                        continue

                    remaining_mp = {k: v for k, v in mp.items() if k != date_str}
                    remaining_slots = {k: v for k, v in slots.items() if k != date_str}

                    if not remaining_mp:
                        # This was the only date in the schedule — delete entirely.
                        await storage_client.delete_schedule(s.get("id"), owner_id)
                        logger.info(
                            "[PLAN_AHEAD_PIPELINE] Deleted schedule id=%s (last date %s removed)",
                            s.get("id"), date_str,
                        )
                    else:
                        # Other dates remain — update the schedule without this date.
                        _rem_dishes: set = {
                            dish
                            for d_slots in remaining_slots.values()
                            for mt_dishes in d_slots.values()
                            for dish in (mt_dishes if isinstance(mt_dishes, list) else [])
                        }
                        remaining_di = {k: v for k, v in di.items() if k in _rem_dishes}
                        existing_dd = storage_client._extract_existing_dish_data(s)
                        remaining_dd = {k: v for k, v in existing_dd.items() if k in _rem_dishes}
                        remaining_shopping = compute_shopping_list(remaining_di)
                        new_metadata = storage_client._convert_to_feature_format(
                            remaining_mp,
                            remaining_shopping,
                            dish_ingredients=remaining_di,
                            meal_plan_slots=remaining_slots if remaining_slots else None,
                            existing_dish_data=remaining_dd if remaining_dd else None,
                        )
                        ok = await storage_client.update_schedule(
                            owner_id=owner_id,
                            schedule_id=s.get("id"),
                            metadata=new_metadata,
                        )
                        if ok:
                            logger.info(
                                "[PLAN_AHEAD_PIPELINE] Removed date %s from schedule id=%s, "
                                "%d date(s) remain",
                                date_str, s.get("id"), len(remaining_mp),
                            )
                        else:
                            logger.warning(
                                "[PLAN_AHEAD_PIPELINE] Failed to update schedule id=%s "
                                "after removing date %s",
                                s.get("id"), date_str,
                            )
                    # Do NOT break — per-meal queue mode can create multiple schedules
                    # for the same date (one per meal), so we must iterate all of them.
        except Exception as e:
            logger.warning("[PLAN_AHEAD_PIPELINE] Failed to remove orphaned date: %s", e)

    async def _delete_meal_time_from_schedules(
        self,
        owner_id: int,
        date_str: str,
        meal_time: str,
        exclude_schedule_id: Optional[int],
        storage_client: Any,
    ) -> None:
        """Remove a specific meal_time slot from all schedules on a given date.

        Used for partial-day removal (e.g. user removes only dinner but breakfast
        remains in the plan-ahead schedule).  For each matching schedule:
        - If removing the slot empties the entire date, remove the date entry.
        - If removing the date empties the entire schedule, delete the schedule.
        - Otherwise update the schedule in-place.

        ``exclude_schedule_id`` is the plan-ahead schedule that will be updated
        separately via persist_meal_plan; skip it here to avoid double-writes.
        """
        try:
            schedules = await storage_client.get_user_schedules(owner_id)
            for s in schedules:
                if s.get("event_type") not in ("meal_plan_draft", "shopping_list"):
                    continue
                if exclude_schedule_id and s.get("id") == exclude_schedule_id:
                    continue
                mp, _sl, di, slots = storage_client._extract_meal_plan_from_schedule(s)
                if date_str not in slots:
                    continue
                date_slots = slots[date_str]
                if meal_time not in date_slots:
                    continue

                # Remove the targeted meal_time from this date's slots.
                updated_date_slots = {mt: v for mt, v in date_slots.items() if mt != meal_time}
                if updated_date_slots:
                    remaining_slots = {**slots, date_str: updated_date_slots}
                    _all_dishes = [
                        d for mt_dishes in updated_date_slots.values()
                        for d in (mt_dishes if isinstance(mt_dishes, list) else [])
                    ]
                    remaining_mp = {**mp, date_str: " and ".join(_all_dishes)}
                else:
                    remaining_slots = {k: v for k, v in slots.items() if k != date_str}
                    remaining_mp = {k: v for k, v in mp.items() if k != date_str}

                if not remaining_mp:
                    await storage_client.delete_schedule(s.get("id"), owner_id)
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] Deleted schedule id=%s "
                        "(removed %s/%s, no dates left)",
                        s.get("id"), date_str, meal_time,
                    )
                else:
                    _rem_dishes: set = {
                        dish
                        for d_slots in remaining_slots.values()
                        for mt_dishes in d_slots.values()
                        for dish in (mt_dishes if isinstance(mt_dishes, list) else [])
                    }
                    remaining_di = {k: v for k, v in di.items() if k in _rem_dishes}
                    existing_dd = storage_client._extract_existing_dish_data(s)
                    remaining_dd = {k: v for k, v in existing_dd.items() if k in _rem_dishes}
                    remaining_shopping = compute_shopping_list(remaining_di)
                    new_metadata = storage_client._convert_to_feature_format(
                        remaining_mp,
                        remaining_shopping,
                        dish_ingredients=remaining_di,
                        meal_plan_slots=remaining_slots if remaining_slots else None,
                        existing_dish_data=remaining_dd if remaining_dd else None,
                    )
                    ok = await storage_client.update_schedule(
                        owner_id=owner_id,
                        schedule_id=s.get("id"),
                        metadata=new_metadata,
                    )
                    if ok:
                        logger.info(
                            "[PLAN_AHEAD_PIPELINE] Removed %s/%s from schedule id=%s",
                            date_str, meal_time, s.get("id"),
                        )
                    else:
                        logger.warning(
                            "[PLAN_AHEAD_PIPELINE] Failed to update schedule id=%s "
                            "after removing %s/%s",
                            s.get("id"), date_str, meal_time,
                        )
        except Exception as e:
            logger.warning("[PLAN_AHEAD_PIPELINE] _delete_meal_time_from_schedules failed: %s", e)

    async def _persist(
        self,
        owner_id: int,
        action: str,
        target_date: Optional[str],
        old_state: Dict[str, Any],
        new_meal_plan: Dict[str, str],
        new_meal_plan_slots: Dict[str, Any],
        dish_ingredients: Dict[str, List[str]],
        shopping_list: List[str],
        storage_client: Any,
        user_timezone: Optional[str],
        target_meal_time: Optional[str] = None,
    ) -> Optional[int]:
        """Persist changes to DB. Returns schedule_id or None."""
        if action == "remove" and target_date:
            if target_date in new_meal_plan:
                # Partial removal: only a specific meal_time was removed from this date;
                # remaining slots for the day are still in new_meal_plan.
                # Fall through to normal persist to update the plan-ahead schedule, and
                # also clean up any OTHER schedules that held the removed meal_time.
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] action=remove, target_date=%s still "
                    "present in new plan (partial removal of %s) — persisting updated schedule.",
                    target_date, target_meal_time or "?",
                )
                if target_meal_time:
                    existing_id = old_state.get("schedule_id")
                    await self._delete_meal_time_from_schedules(
                        owner_id, target_date, target_meal_time,
                        exclude_schedule_id=existing_id,
                        storage_client=storage_client,
                    )
            else:
                # Entire day was removed — delete the schedule for that date.
                await self._delete_orphaned_schedules(owner_id, [target_date], storage_client)
                return None

        # Find dates that were in the old plan but are absent from the new plan (e.g. move operation).
        # For 'add', the LLM correctly outputs ONLY the new dates — do NOT treat existing dates
        # as orphans; they should be preserved as-is.
        old_dates = set((old_state.get("meal_plan") or {}).keys())
        new_dates = set(new_meal_plan.keys())

        # HARDCODED SAFETY RULE: only delete a date if ALL conditions hold:
        #   1. It was in the old plan (i.e. it existed before this operation)
        #   2. It falls WITHIN the date range of the new plan [min_new_date, max_new_date]
        #      (dates outside that range were never "addressed" by this operation)
        #   3. It is NOT present in the new plan (i.e. it was intentionally removed)
        #   4. This is NOT an 'add' action (adds never delete)
        # This prevents "鸡蛋是给早上的" from deleting 2026-03-13 / 2026-03-14 just because
        # the LLM only output 2026-03-16.
        if action == "add" or not new_dates:
            orphaned_dates = []
        else:
            _new_min = min(new_dates)
            _new_max = max(new_dates)
            orphaned_dates = [
                d for d in (old_dates - new_dates)
                if _new_min <= d <= _new_max
            ]

        if orphaned_dates:
            logger.info(f"[PLAN_AHEAD_PIPELINE] Dates removed from plan: {orphaned_dates}")
            await self._delete_orphaned_schedules(owner_id, orphaned_dates, storage_client)

        # For 'add', only persist the newly added dates.
        # Existing dates already have their own DB schedules and must NOT be re-created here
        # (doing so would create duplicate schedules for the same date).
        # Orphan deletion is already disabled above (orphaned_dates = []), so old schedules
        # are preserved without any merging.
        if action == "add":
            logger.info(
                "[PLAN_AHEAD_PIPELINE] add: persisting only new dates (%d) → %s",
                len(new_meal_plan_slots), list(new_meal_plan_slots.keys()),
            )

        persist_meal_plan = new_meal_plan
        persist_slots = new_meal_plan_slots
        persist_ingr = dish_ingredients
        persist_shopping = shopping_list

        # For 'add': never pass existing_schedule_id — storage will do a per-date
        # lookup so each date lands in its own schedule, never merged into an old one.
        # For 'modify'/'confirm': pass existing_id as a hint so the right schedule
        # is updated in-place for the same date.
        existing_id = old_state.get("schedule_id") if action != "add" else None
        schedule_id = await self.plan_ahead_agent.persist_meal_plan(
            meal_plan=persist_meal_plan,
            shopping_list=persist_shopping,
            owner_id=owner_id,
            existing_schedule_id=existing_id,
            storage_client=storage_client,
            user_timezone=user_timezone,
            event_type="meal_plan_draft",
            dish_ingredients=persist_ingr,
            meal_plan_slots=persist_slots,
            is_append=(action == "add"),
        )
        return schedule_id

    # ------------------------------------------------------------------
    # Phase handlers  (each returns a result dict for an early exit, or
    # None to let execute() continue to the next phase)
    # ------------------------------------------------------------------

    async def _sync_state(
        self,
        owner_id: int,
        storage_client: Any,
        context: Optional[Dict],
    ) -> Dict[str, Any]:
        """Step 1 of execute(): sync in-memory state from DB, then apply any
        explicit context override.  Returns the fully-merged current_state."""
        from app.modules.plan_ahead_state import get_plan_state, update_plan_state

        old_state = await self.plan_ahead_agent.sync_meal_plan_from_database(
            owner_id=owner_id,
            storage_client=storage_client,
        )
        server_state = get_plan_state(owner_id)
        _in_queue_mode = bool(server_state.get("meal_planning_queue"))

        if server_state.get("is_draft", False) or _in_queue_mode:
            if old_state.get("schedule_id") and not server_state.get("schedule_id"):
                update_plan_state(owner_id=owner_id, schedule_id=old_state.get("schedule_id"))
            current_state = get_plan_state(owner_id)
            _skip_reason = "active draft" if server_state.get("is_draft") else "queue mode"
            logger.info(
                "[PLAN_AHEAD_PIPELINE] DB sync skipped (%s): %d meals in state.",
                _skip_reason, len(current_state.get("meal_plan", {})),
            )
        elif old_state.get("schedule_id"):
            db_mp = old_state.get("meal_plan") or {}
            db_slots = old_state.get("meal_plan_slots") or {}
            server_slots = server_state.get("meal_plan_slots") or {}
            merged_slots = {
                d: {**(db_slots.get(d) or {}), **(server_slots.get(d) or {})}
                for d in db_mp
            }
            db_di = old_state.get("dish_ingredients") or {}
            server_di = server_state.get("dish_ingredients") or {}
            merged_di: Dict[str, List[Any]] = {}
            for k in set(db_di) | set(server_di):
                by_name: Dict[str, Any] = {}
                for item in (db_di.get(k) or []) + (server_di.get(k) or []):
                    key = item.get("name") if isinstance(item, dict) else item
                    if key:
                        by_name[key] = item
                merged_di[k] = list(by_name.values())
            update_plan_state(
                owner_id=owner_id,
                meal_plan=db_mp,
                shopping_list=old_state.get("shopping_list", []),
                schedule_id=old_state.get("schedule_id"),
                meal_plan_slots=merged_slots,
                dish_ingredients=merged_di,
                merge=False,
            )
            current_state = get_plan_state(owner_id)
        else:
            if server_state.get("schedule_id") or server_state.get("meal_plan_slots"):
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] DB has no plan but in-memory has stale data "
                    "(schedule_id=%s, slots=%s) — clearing for user %d.",
                    server_state.get("schedule_id"),
                    list((server_state.get("meal_plan_slots") or {}).keys()),
                    owner_id,
                )
                update_plan_state(
                    owner_id=owner_id,
                    meal_plan={}, shopping_list=[], schedule_id=None,
                    meal_plan_slots={}, dish_ingredients={},
                    is_draft=False, last_pipeline_action=None, merge=False,
                )
            current_state = get_plan_state(owner_id)

        # Apply explicit UI context override (e.g. page-refresh context from frontend)
        if context and context.get("type") == "plan_ahead" and isinstance(context.get("data"), dict):
            cd = context["data"]
            if cd.get("meal_plan_slots"):
                current_state["meal_plan_slots"] = cd["meal_plan_slots"]
            if cd.get("dish_ingredients"):
                current_state["dish_ingredients"] = cd["dish_ingredients"]
            if not current_state.get("schedule_id") and cd.get("schedule_id"):
                current_state["schedule_id"] = cd["schedule_id"]

        # Return both: old_state is the raw DB snapshot (needed by execute() for
        # ghost-date filtering, echoed-date stripping, and draft-base snapshotting).
        return old_state, current_state

    async def _handle_date_confirmation(
        self,
        owner_id: int,
        user_input: str,
        current_state: Dict[str, Any],
        is_explicit_dish: bool,
        user_timezone: Optional[str],
        language: Optional[str],
        intent_result: Any,
    ) -> Optional[Dict[str, Any]]:
        """Phase 1b: handle the user's reply to an outstanding date-range confirmation.

        Returns a result dict if the turn is fully handled (early exit).
        Returns None when the confirmed queue has been activated and execution
        should fall through to the main LLM planning turn.
        """
        from app.modules.plan_ahead_state import get_plan_state, update_plan_state

        _pending_queue: List[str] = list(current_state.get("pending_planning_queue") or [])

        # Layer 0 guard: explicit dish clears any pending date-confirmation session.
        if is_explicit_dish and _pending_queue:
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Layer 0 short-circuit: clearing pending date "
                "confirmation queue (%d slot(s)) — user switched to explicit dish request.",
                len(_pending_queue),
            )
            update_plan_state(
                owner_id=owner_id,
                pending_planning_queue=None,
                last_pipeline_action=None,
                confirmation_retry_count=None,
            )
            return None  # fall through with cleared queue

        if not (_pending_queue and current_state.get("last_pipeline_action") == "ask_confirm_dates"):
            return None  # not in date-confirmation phase

        # Use skill — prompt lives in ClassifyDateConfirmationSkill.SKILL_PROMPT
        _now = self._now_in_timezone(user_timezone)
        _today_str_skill = _now.strftime("%Y-%m-%d")
        _clf = await ClassifyDateConfirmationSkill(self.gemini_api_url).execute(
            user_input, _pending_queue, _today_str_skill
        )
        _clf_intent = _clf["intent"]
        logger.info(
            "[PLAN_AHEAD_PIPELINE] Phase 1b classifier: intent=%r for %r",
            _clf_intent, user_input,
        )

        if _clf_intent == "confirmed":
            _planning_slots = list(_pending_queue)
            _pending_dates = list(dict.fromkeys(s.split("|")[0] for s in _planning_slots))
            update_plan_state(
                owner_id=owner_id,
                pending_planning_queue=None, last_pipeline_action=None,
                meal_planning_queue=_planning_slots, meal_planning_total=len(_planning_slots),
                meal_plan={}, meal_plan_slots={}, dish_ingredients={}, shopping_list=[],
                is_draft=False, confirmation_retry_count=None, merge=False,
            )
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Date range confirmed: %s → %s (%d days, %d meal slots). "
                "Entering per-meal queue mode.",
                _pending_dates[0], _pending_dates[-1], len(_pending_dates), len(_planning_slots),
            )
            return None  # fall through to queue-aware LLM planning

        if _clf_intent == "corrected":
            _corrected_dates = _clf.get("new_dates", [])
            _new_meal_times: Optional[List[str]] = _clf.get("new_meal_times") or None
            if not _corrected_dates:
                _corrected_dates = list(dict.fromkeys(
                    s.split("|")[0] for s in _pending_queue if "|" in s
                ))
            if _new_meal_times:
                _eff_meals: Optional[List[str]] = _new_meal_times
            else:
                _eff_meals = list(dict.fromkeys(
                    s.split("|")[1] for s in _pending_queue if "|" in s
                )) or None
            _corrected_slots = self._dates_to_slots(_corrected_dates, _eff_meals) if _corrected_dates else []

            if _corrected_slots and _corrected_slots != _pending_queue:
                update_plan_state(
                    owner_id=owner_id,
                    pending_planning_queue=_corrected_slots,
                    last_pipeline_action="ask_confirm_dates",
                    confirmation_retry_count=0,
                )
                _confirm_msg = self._build_date_confirm_message(_corrected_slots, language)
                self._log_pending_dates(_corrected_slots, current_state.get("meal_plan_slots"), "Date corrected by user")
            else:
                _retry = current_state.get("confirmation_retry_count", 0) + 1
                logger.info("[PLAN_AHEAD_PIPELINE] Phase 1b: no-op correction (retry=%d) for %r", _retry, user_input)
                if _retry >= 2:
                    update_plan_state(owner_id=owner_id, pending_planning_queue=None, last_pipeline_action=None, confirmation_retry_count=None)
                    _confirm_msg = (
                        "好像我没能理解您想修改的内容，让我们重新来一次吧！\n"
                        "您可以告诉我想规划哪几天、哪几餐，我来帮您安排。"
                    )
                    logger.info("[PLAN_AHEAD_PIPELINE] Phase 1b: 2 no-op corrections → degrading to fresh start.")
                else:
                    _pending_meals_str = "、".join(
                        {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(m, m)
                        for m in list(dict.fromkeys(s.split("|")[1] for s in _pending_queue if "|" in s))
                    ) or "三餐"
                    _pending_start_str = _pending_queue[0].split("|")[0] if _pending_queue else ""
                    _pending_end_str = _pending_queue[-1].split("|")[0] if _pending_queue else ""
                    _same_day = _pending_start_str == _pending_end_str
                    _date_hint = (
                        f"{_pending_start_str}（{_pending_meals_str}）"
                        if _same_day
                        else f"{_pending_start_str} 到 {_pending_end_str}（{_pending_meals_str}）"
                    )
                    _confirm_msg = (
                        f"我明白您想修改，但还不太确定您想改什么。\n"
                        f"当前方案是：{_date_hint}\n\n"
                        "是想改**日期**（比如：从明天开始 / 改成下周）？\n"
                        "还是想改**餐次**（比如：加上早餐 / 只要晚饭）？"
                    )
                    update_plan_state(owner_id=owner_id, confirmation_retry_count=_retry)

            _cur = get_plan_state(owner_id)
            return self._build_result(
                response_text=_confirm_msg,
                meal_plan=_cur.get("meal_plan", {}), meal_plan_slots=_cur.get("meal_plan_slots", {}),
                dish_ingredients=_cur.get("dish_ingredients", {}), shopping_list=_cur.get("shopping_list", []),
                schedule_id=_cur.get("schedule_id"), intent_result=intent_result,
            )

        # "unclear" — attempt date recovery via InitPlanningQueueSkill
        _recovery_result = await InitPlanningQueueSkill(self.gemini_api_url).execute(
            user_input, _now.strftime("%Y-%m-%d (%A)"), today_date=_now.date()
        )
        _recovery_queue = _recovery_result.get("slots", [])
        if _recovery_queue:
            update_plan_state(
                owner_id=owner_id,
                pending_planning_queue=_recovery_queue,
                last_pipeline_action="ask_confirm_dates",
                confirmation_retry_count=0,
            )
            _confirm_msg = self._build_date_confirm_message(_recovery_queue, language)
            self._log_pending_dates(_recovery_queue, None, "Phase 1b unclear → date recovery")
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Phase 1b: unclear but date recovery succeeded (%d slot(s)).",
                len(_recovery_queue),
            )
        else:
            _confirm_msg = self._build_date_confirm_message(_pending_queue, language)
            logger.info("[PLAN_AHEAD_PIPELINE] Phase 1b: unclear reply, re-asking.")
        _cur = get_plan_state(owner_id)
        return self._build_result(
            response_text=_confirm_msg,
            meal_plan=_cur.get("meal_plan", {}), meal_plan_slots=_cur.get("meal_plan_slots", {}),
            dish_ingredients=_cur.get("dish_ingredients", {}), shopping_list=_cur.get("shopping_list", []),
            schedule_id=_cur.get("schedule_id"), intent_result=intent_result,
        )

    async def _handle_ask_dates_resolution(
        self,
        owner_id: int,
        user_input: str,
        current_state: Dict[str, Any],
        is_currently_draft: bool,
        intent_result: Any,
    ) -> Optional[Dict[str, Any]]:
        """Phase 1a-pre: when pending_ask_dates are stored from a previous 'ask' turn,
        parse the meal type(s) from the user's reply and start the queue.

        Returns a result dict only if the meal type is still unclear (re-ask).
        Returns None on success (queue activated, fall through) or when not applicable.
        """
        from app.modules.plan_ahead_state import update_plan_state

        _saved_ask_dates: List[str] = list(current_state.get("pending_ask_dates") or [])
        _pending_queue = current_state.get("pending_planning_queue") or []

        if not (_saved_ask_dates and not _pending_queue and not is_currently_draft
                and current_state.get("last_pipeline_action") == "ask"):
            return None

        _meal_kw_map = {
            "breakfast": ["早餐", "早饭", "早上", "breakfast"],
            "lunch":     ["午餐", "午饭", "中饭", "中午", "lunch"],
            "dinner":    ["晚餐", "晚饭", "晚上", "dinner"],
        }
        _all_meals_kw = ["三餐", "全天", "全部", "all", "每餐", "都"]
        _q = (user_input or "").lower()
        if any(kw in _q for kw in _all_meals_kw):
            _resolved_meal_types = ["breakfast", "lunch", "dinner"]
        else:
            _resolved_meal_types = [mt for mt, kws in _meal_kw_map.items() if any(kw in _q for kw in kws)]

        if _resolved_meal_types:
            _resolved_queue = [
                f"{date}|{meal_type}"
                for date in _saved_ask_dates
                for meal_type in _resolved_meal_types
            ]
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Phase 1a-pre: resolved pending_ask_dates=%s + meal_types=%s → queue=%s",
                _saved_ask_dates, _resolved_meal_types, _resolved_queue,
            )
            update_plan_state(
                owner_id=owner_id,
                pending_ask_dates=None, pending_planning_queue=None, last_pipeline_action=None,
                meal_planning_queue=_resolved_queue, meal_planning_total=len(_resolved_queue),
                meal_plan={}, meal_plan_slots={}, dish_ingredients={}, shopping_list=[],
                is_draft=False, confirmation_retry_count=None, merge=False,
            )
            return None  # fall through — queue is now active

        # Meal type still unclear — re-ask
        _date_hint = "、".join(_saved_ask_dates)
        _re_ask_msg = f"请告诉我 {_date_hint} 您想安排哪一餐？（早餐 / 午餐 / 晚餐，或\"三餐都要\"）"
        logger.info("[PLAN_AHEAD_PIPELINE] Phase 1a-pre: meal type unclear for %s, re-asking.", _saved_ask_dates)
        update_plan_state(owner_id=owner_id, last_pipeline_action="ask")
        result = self._build_result(
            response_text=_re_ask_msg,
            meal_plan=current_state.get("meal_plan", {}), meal_plan_slots=current_state.get("meal_plan_slots", {}),
            dish_ingredients=current_state.get("dish_ingredients", {}), shopping_list=current_state.get("shopping_list", []),
            schedule_id=current_state.get("schedule_id"), is_draft=is_currently_draft, intent_result=intent_result,
        )
        result["action_data"]["pending_ask_dates"] = _saved_ask_dates
        result["action_data"]["ask_type"] = "meal_slot_selector"
        return result

    async def _handle_phase1a_queue_init(
        self,
        owner_id: int,
        user_input: str,
        current_state: Dict[str, Any],
        is_currently_draft: bool,
        is_explicit_dish: bool,
        dish_clf: Dict[str, Any],
        language: Optional[str],
        user_timezone: Optional[str],
        intent_result: Any,
        prerouted_action: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Phase 1a: detect fresh multi-slot planning intent and either enter queue
        mode directly (single slot) or ask for date-range confirmation (multiple slots).

        Returns a result dict if a confirmation question was emitted.
        Returns None when no queue was detected (fall through) or when the queue is
        already active (single-slot fast-path).
        """
        from app.modules.plan_ahead_state import update_plan_state

        # Skip if we are already in some active phase
        if (
            current_state.get("pending_planning_queue")
            or is_currently_draft
            or current_state.get("last_pipeline_action")
            or current_state.get("meal_planning_queue")
        ):
            return None

        if is_explicit_dish:
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Phase 1a short-circuit: explicit dish (%s) — "
                "skipping _init_planning_queue, falling through to main LLM.",
                dish_clf.get("dishes"),
            )
            return None

        # If the action has already been pre-classified as add/modify/remove,
        # the user is operating on a specific dish/slot — NOT starting a fresh
        # multi-meal planning session. Skip queue init to avoid misrouting.
        if prerouted_action in ("add", "modify", "remove"):
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Phase 1a short-circuit: prerouted action=%s — "
                "skipping InitPlanningQueueSkill.",
                prerouted_action,
            )
            return None

        # Use skill — prompt lives in InitPlanningQueueSkill.SKILL_PROMPT_TEMPLATE
        _pq_now = self._now_in_timezone(user_timezone)
        _pq_result = await InitPlanningQueueSkill(self.gemini_api_url).execute(
            user_input,
            _pq_now.strftime("%Y-%m-%d (%A)"),
            today_date=_pq_now.date(),
        )
        _new_queue = _pq_result.get("slots", [])
        if not _new_queue:
            return None

        if len(_new_queue) == 1:
            # Single explicit slot — bypass confirmation, activate queue immediately.
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Phase 1a: single slot %r — bypassing confirmation.",
                _new_queue[0],
            )
            update_plan_state(
                owner_id=owner_id,
                pending_planning_queue=None, last_pipeline_action=None,
                meal_planning_queue=_new_queue, meal_planning_total=1,
                meal_plan={}, meal_plan_slots={}, dish_ingredients={}, shopping_list=[],
                is_draft=False, confirmation_retry_count=None, merge=False,
            )
            return None  # fall through to LLM planning

        # Multiple slots — need confirmation before committing.
        update_plan_state(
            owner_id=owner_id,
            pending_planning_queue=_new_queue,
            last_pipeline_action="ask_confirm_dates",
        )
        _confirm_msg = self._build_date_confirm_message(_new_queue, language)
        self._log_pending_dates(_new_queue, current_state.get("meal_plan_slots"), "Multi-day planning detected")
        _cur = current_state  # state not yet changed for these fields
        return self._build_result(
            response_text=_confirm_msg,
            meal_plan=_cur.get("meal_plan", {}), meal_plan_slots=_cur.get("meal_plan_slots", {}),
            dish_ingredients=_cur.get("dish_ingredients", {}), shopping_list=_cur.get("shopping_list", []),
            schedule_id=_cur.get("schedule_id"), intent_result=intent_result,
        )

    def _handle_queue_advance(
        self,
        owner_id: int,
        user_input: str,
        current_state: Dict[str, Any],
    ) -> Optional[str]:
        """Phase Q: after a queue slot was presented (last_action=queue_day_complete),
        decide whether to advance the queue, pause for a save, or stay on the
        current slot (modification intent).

        Returns the target slot string ("YYYY-MM-DD|meal_type") for this turn,
        or None when not in queue mode.
        """
        from app.modules.plan_ahead_state import update_plan_state

        _meal_queue: List[str] = list(current_state.get("meal_planning_queue") or [])
        if not _meal_queue:
            return None

        if current_state.get("last_pipeline_action") == "queue_day_complete":
            _q_lower = (user_input or "").lower().strip()
            _has_mod_intent = any(
                kw in _q_lower
                for kw in ("改", "换", "修改", "不要", "不用", "重新", "换一个", "replace", "change", "modify")
            )
            _has_save_intent = not _has_mod_intent and any(
                kw in _q_lower
                for kw in ("保存", "存入", "存下", "save", "完成了", "结束", "不用继续", "不需要继续")
            )
            if _has_save_intent:
                update_plan_state(owner_id=owner_id, last_pipeline_action=None)
                _meal_queue = []  # local only — queue in state preserved
                logger.info("[PLAN_AHEAD_PIPELINE] Queue: save intent — pausing for confirm.")
            elif not _has_mod_intent:
                update_plan_state(owner_id=owner_id, last_pipeline_action=None)
                from app.modules.plan_ahead_state import get_plan_state as _gps
                _meal_queue = list(_gps(owner_id).get("meal_planning_queue") or [])
                _meal_total = current_state.get("meal_planning_total", 0)
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] Queue: advancing to next slot. Remaining: %d/%d.",
                    len(_meal_queue), _meal_total,
                )

        return _meal_queue[0] if _meal_queue else None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        owner_id: int,
        user_input: str,
        history: List[Dict],
        user_timezone: Optional[str],
        storage_client: Any,
        context: Optional[Dict] = None,
        intent_result: Any = None,
        cooking_level: Optional[str] = "beginner",
        language: Optional[str] = "zh",
        user_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the full PLAN_AHEAD pipeline and return a chat.py-compatible result dict.

        Orchestration (each phase either returns early or falls through):
          1. _sync_state          — DB → in-memory reconciliation
          2. Layer 0              — early dish-intent classification
          3. _handle_date_confirmation   — Phase 1b: confirm/correct/unclear
          4. _handle_ask_dates_resolution — Phase 1a-pre: resolve saved ask_dates
          5. _handle_phase1a_queue_init  — Phase 1a: detect fresh multi-slot intent
          6. _handle_queue_advance       — Phase Q: advance per-meal queue
          7. _build_context + _call_llm  — single structured LLM call
          8. Post-process + persist      — guardian, filter, save to DB
        """
        # ---- Step 1: Sync DB state ----
        # old_state = raw DB snapshot; used later for ghost-date filtering,
        # echoed-date stripping (action=add), and draft-base snapshotting.
        old_state, current_state = await self._sync_state(owner_id, storage_client, context)
        inventory_items = await self._fetch_inventory(owner_id)
        is_currently_draft = current_state.get("is_draft", False)

        # ---- Layer 0: LLM dish-intent early classification ----
        # Skill-based: prompt lives in ClassifyDishIntentSkill.SKILL_PROMPT (skills/plan_ahead/).
        _dish_clf = await ClassifyDishIntentSkill(self.gemini_api_url).execute(
            user_input, history or []
        )
        _is_explicit_dish = _dish_clf.get("is_explicit", False)
        if _is_explicit_dish:
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Layer 0: explicit dish detected (%s) — "
                "will bypass date confirmation flow if triggered.",
                _dish_clf.get("dishes"),
            )

        # ---- Layer 0b: Pre-route meal action (ClassifyMealActionSkill) ----
        # Decides ask / suggest_options / add / recommend BEFORE the main LLM context is built.
        # Core fix for "海鲜 → direct add" bug: the main LLM is now told the action upfront.
        _phase_str = get_phase(current_state).value
        _action_clf = await ClassifyMealActionSkill(self.gemini_api_url).execute(
            user_input, history or [],
            is_explicit_dish=_is_explicit_dish,
            pipeline_phase=_phase_str,
        )
        _prerouted_action: Optional[str] = _action_clf.get("action")
        logger.info(
            "[PLAN_AHEAD_PIPELINE] Layer 0b (ClassifyMealActionSkill): action=%s reason=%r",
            _prerouted_action, _action_clf.get("reason", ""),
        )

        # ---- Layer 0c: Extract & persist ingredients to Memory Layer ----
        # Runs only when the user may be referring to ingredients they have on hand.
        # Active context is union-merged so multi-turn conversations accumulate facts.
        _today_str = self._now_in_timezone(user_timezone).date().isoformat()
        _extracted = await ExtractIngredientsSkill(self.gemini_api_url).execute(
            user_input, today_date=_today_str,
        )
        _ext_ingredients: List[str] = _extracted.get("ingredients", [])
        _ext_target_date: Optional[str] = _extracted.get("target_date")
        _ext_meal_type: Optional[str] = _extracted.get("target_meal_type")
        if _ext_ingredients or _ext_target_date or _ext_meal_type:
            update_active_context(
                owner_id,
                add_ingredients=_ext_ingredients,
                target_date=_ext_target_date,
                target_meal_type=_ext_meal_type,
            )

        # Read the (now possibly updated) active context for context injection
        _active_ctx = get_active_context(owner_id)

        # ---- Phase 1b: Date confirmation ----
        if result := await self._handle_date_confirmation(
            owner_id, user_input, current_state, _is_explicit_dish,
            user_timezone, language, intent_result,
        ):
            return result
        current_state = get_plan_state(owner_id)
        is_currently_draft = current_state.get("is_draft", False)

        # ---- Phase 1a-pre: Resolve pending ask_dates ----
        if result := await self._handle_ask_dates_resolution(
            owner_id, user_input, current_state, is_currently_draft, intent_result,
        ):
            return result
        current_state = get_plan_state(owner_id)
        is_currently_draft = current_state.get("is_draft", False)

        # ---- Phase 1a: Detect fresh queue intent ----
        if result := await self._handle_phase1a_queue_init(
            owner_id, user_input, current_state, is_currently_draft,
            _is_explicit_dish, _dish_clf, language, user_timezone, intent_result,
            prerouted_action=_prerouted_action,
        ):
            return result
        current_state = get_plan_state(owner_id)
        is_currently_draft = current_state.get("is_draft", False)

        # ---- Phase Q: Per-meal queue advance ----
        _queue_target_slot: Optional[str] = self._handle_queue_advance(
            owner_id, user_input, current_state,
        )
        current_state = get_plan_state(owner_id)
        # Alias for backward-compat references further down
        _queue_target_date = _queue_target_slot
        # Read queue snapshot from state (used later for queue-mode progress tracking)
        _meal_queue: List[str] = list(current_state.get("meal_planning_queue") or [])
        _meal_total: int = current_state.get("meal_planning_total", 0)

        # ---- Step 2: Build context ----
        _scheduling_ctx = (context or {}).get("scheduling_context") if context else None
        system_context = self._build_context(
            current_state, user_timezone,
            inventory_items=inventory_items,
            is_draft=is_currently_draft,
            cooking_level=cooking_level,
            language=language,
            scheduling_context=_scheduling_ctx,
            user_profile=user_profile,
            queue_target_date=_queue_target_slot,
            precomputed_action=_prerouted_action,
            active_context=_active_ctx,
        )
        # Inject pending options so the LLM can reference them when user selects
        system_context = self._inject_pending_options(system_context, current_state)

        # ---- Step 3: Single LLM call ----
        parsed = await self._call_llm(system_context, history, user_input)
        if not parsed:
            fallback_msg = "Sorry, I was unable to process your request. Please try again."
            return self._build_result(
                response_text=fallback_msg,
                meal_plan=current_state.get("meal_plan", {}),
                meal_plan_slots=current_state.get("meal_plan_slots", {}),
                dish_ingredients=current_state.get("dish_ingredients", {}),
                shopping_list=current_state.get("shopping_list", []),
                schedule_id=current_state.get("schedule_id"),
                intent_result=intent_result,
            )

        user_message = parsed["user_message"]
        new_meal_plan = parsed["meal_plan"]
        new_meal_plan_slots = parsed["meal_plan_slots"]
        new_dish_ingredients = parsed["dish_ingredients"]
        new_dish_slots = parsed.get("dish_slots") or {}
        action = parsed["action"]
        target_date = parsed.get("target_date")

        # ---- Phase 3: Guardian validation (non-blocking) ----
        try:
            from app.services import guardian as _guardian
            from app.services import seed_library as _seed_lib_g
            _all_dish_names: List[str] = []
            for _date_slots in (new_meal_plan_slots or {}).values():
                for _mt_dishes in (_date_slots or {}).values():
                    if isinstance(_mt_dishes, list):
                        _all_dish_names.extend(_mt_dishes)
                    elif isinstance(_mt_dishes, str):
                        _all_dish_names.append(_mt_dishes)
            if _all_dish_names:
                _guardian_entries = [{"dish": n} for n in _all_dish_names]
                _guardian_issues = _guardian.validate_meal_entries(_guardian_entries)
                _in_seed_count = sum(1 for n in _all_dish_names if _seed_lib_g.lookup_dish(n))
                logger.info(
                    "[PLAN_AHEAD_PIPELINE][GUARDIAN] %d dish(es): %d from seed library, %d issue(s) detected",
                    len(_all_dish_names),
                    _in_seed_count,
                    len(_guardian_issues),
                )
        except Exception as _g_err:
            logger.warning("[PLAN_AHEAD_PIPELINE][GUARDIAN] validation failed (non-fatal): %s", _g_err)

        # ---- Guard: enforce ask-before-recommend/suggest_options on fresh sessions ----
        last_action = current_state.get("last_pipeline_action")

        def _build_ask_fallback(profile: Optional[Dict[str, Any]]) -> str:
            """Build a context-aware clarifying question using what's already known from the user profile."""
            _lang_map_local = {
                "zh": "zh", "en": "en", "ja": "ja", "ko": "ko",
            }
            _is_zh = (_lang_map_local.get(language or "zh", "zh") == "zh")

            # Extract what we already know from the profile
            _disliked: List[str] = (profile or {}).get("disliked_ingredients") or []
            _cw: Dict[str, int] = (profile or {}).get("cuisine_weights") or {}
            _top_cuisines = sorted(_cw.items(), key=lambda x: -x[1])[:2] if _cw else []

            # Build "already know" acknowledgement
            _known_parts: List[str] = []
            if _top_cuisines:
                _cuisine_str = "、".join(n for n, _ in _top_cuisines) if _is_zh else " and ".join(n for n, _ in _top_cuisines)
                _known_parts.append(
                    f"您偏好 {_cuisine_str} 菜系" if _is_zh else f"you prefer {_cuisine_str} cuisine"
                )
            if _disliked:
                _dis_str = "、".join(_disliked[:4]) if _is_zh else ", ".join(_disliked[:4])
                _known_parts.append(
                    f"不吃 {_dis_str}" if _is_zh else f"avoiding {_dis_str}"
                )

            if _known_parts and _is_zh:
                _known_line = "我已知道" + "，".join(_known_parts) + "。"
                return (
                    f"{_known_line}\n"
                    "想帮您推荐几套菜单方案 😊 请问您想规划哪天的饮食？（比如今天晚饭、下周几天等）"
                )
            elif _known_parts:
                _known_line = "I see that " + " and ".join(_known_parts) + "."
                return (
                    f"{_known_line}\n"
                    "I'd love to suggest a few meal plan options! Which day(s) are you planning for?"
                )
            elif _is_zh:
                return (
                    "想帮您推荐几套菜单方案 😊\n"
                    "请问您喜欢什么口味或菜系（比如家常菜、川菜、日料）？有没有不吃的食材或饮食要求？"
                    "以及想规划哪天的饮食？"
                )
            else:
                return (
                    "I'd love to suggest a few meal plan options!\n"
                    "What cuisine style do you prefer (e.g. Chinese home-style, Japanese, Western)? "
                    "Any ingredients to avoid? And which day(s) are you planning for?"
                )

        # Fresh-plan guard: intercept LLM "recommend/suggest_options" for genuinely new dates
        # when the user is NOT in the middle of an active draft session (suggest_options →
        # selection → modify flow).  This forces the proper option-selection UX.
        #
        # DRAFT-SESSION continuations — do NOT intercept:
        #   suggest_options → user picks option  (last_action="suggest_options")
        #   recommend draft → user modifies dish (last_action="recommend")
        #   modify draft    → user modifies more (last_action="modify")
        #
        # All other states (None, "add", "confirm", "ask", "view", …) are treated as
        # "fresh planning context" and MUST go through the guard.
        #
        # EXCEPTION: If the user explicitly names a specific dish
        # (e.g. "加个萝卜炖牛腩"), downgrade recommend → add (do NOT force ask).
        # Actions where an ongoing conversation is already in progress — do NOT intercept.
        # "ask": user answered a clarification question, let the next step through.
        _DRAFT_SESSION_ACTIONS = {"suggest_options", "recommend", "modify", "ask"}
        # Only intercept "recommend" — suggest_options is ALWAYS valid and must never be blocked.
        # Also bypass in queue mode: the queue context already scopes the LLM to one day.
        _pending_ask_dates: list = []  # dates captured by Fresh-plan guard for next-turn resolution
        if action == "recommend" and last_action not in _DRAFT_SESSION_ACTIONS and not _queue_target_date:
            _new_dates = set(new_meal_plan_slots.keys()) if new_meal_plan_slots else set()
            _old_dates = set((old_state.get("meal_plan_slots") or {}).keys())
            # Only bother classifying if there are new meal entries and genuinely new dates
            _candidate_for_add = (
                action == "recommend"
                and bool(new_meal_plan_slots)
                and _new_dates.isdisjoint(_old_dates)
                and not current_state.get("pending_options")
            )
            _is_explicit_add = False
            if _candidate_for_add:
                _is_explicit_add = await self._classify_explicit_add(
                    query=user_input or "", history=history or []
                )
            if _is_explicit_add:
                action = "add"
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] Fresh session: classifier=EXPLICIT_ADD, intercepted recommend "
                    "with new date(s) %s → downgraded to add.",
                    sorted(_new_dates),
                )
            else:
                _orig_action = action
                action = "ask"
                # If the LLM already identified specific dates, give a targeted nudge
                # ("tell me which meals for 2026-03-18") instead of the generic
                # "which day do you want to plan?" fallback.
                if _new_dates:
                    _pending_ask_dates = sorted(_new_dates)
                    _date_hint = "、".join(_pending_ask_dates)
                    user_message = (
                        f"好的，我来为您规划 {_date_hint} 的饮食！\n"
                        "请选择您想安排的餐次（可多选）：早餐 / 午餐 / 晚餐"
                    )
                else:
                    user_message = _build_ask_fallback(user_profile)
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] Fresh-plan guard: classifier=RECOMMEND (or non-candidate), "
                    "intercepted %s (last_action=%s) → forced ask. pending_ask_dates=%s",
                    _orig_action, last_action, _pending_ask_dates,
                )
        # Also catch: LLM returned 'ask' action but put meal data in user_message.
        # Detectable by 📅 emoji or a meal_entries payload on a fresh session.
        elif action == "ask" and last_action not in _DRAFT_SESSION_ACTIONS and (
            "📅" in user_message or new_meal_plan
        ):
            user_message = _build_ask_fallback(user_profile)
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Fresh session: ask with embedded meal data — replaced with preferences question."
            )

        # ---- Step 4: Early exits for non-plan actions ----

        # 'ask': AI is gathering preferences — no plan change, no persist.
        if action == "ask":
            # Guard: if the AI has already asked once (last_action == "ask") and the user's message
            # looks like an explicit dish request, the LLM is likely stuck because of diversity rules.
            # In this case, override to a helpful message that acknowledges the user's intent.
            if last_action == "ask" and not new_meal_plan_slots:
                _q = (user_input or "").strip()
                _explicit_add_keywords = ["加", "吃", "想要", "来个", "来一个", "做个", "加个", "add", "want"]
                if any(kw in _q for kw in _explicit_add_keywords):
                    user_message = (
                        "抱歉，您提到的菜品刚好在近期吃过，系统建议多样化饮食。"
                        "不过如果您坚持想吃，可以直接说「就要这个，帮我加上」，我会按您的意愿添加。"
                        "或者告诉我想要哪天哪餐，我来给您推荐几个新方案。"
                    )
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] action=ask (second consecutive): LLM stuck on diversity rules, "
                        "replaced with override-hint message."
                    )
            # Save pending dates so the next turn can resolve "只计划晚饭" without losing context.
            # If _pending_ask_dates is empty (no dates captured), explicitly clear the field.
            update_plan_state(
                owner_id=owner_id,
                last_pipeline_action="ask",
                pending_ask_dates=_pending_ask_dates if _pending_ask_dates else None,
            )
            logger.info(
                "[PLAN_AHEAD_PIPELINE] action=ask: returning clarification question, skipping persist. "
                "saved pending_ask_dates=%s", _pending_ask_dates,
            )
            _ask_result = self._build_result(
                response_text=user_message,
                meal_plan=current_state.get("meal_plan", {}),
                meal_plan_slots=current_state.get("meal_plan_slots", {}),
                dish_ingredients=current_state.get("dish_ingredients", {}),
                shopping_list=current_state.get("shopping_list", []),
                schedule_id=current_state.get("schedule_id"),
                is_draft=is_currently_draft,
                intent_result=intent_result,
            )
            # Expose pending dates in action_data so the frontend can render an interactive
            # meal-slot selector (date × meal-type grid) instead of relying on text only.
            if _pending_ask_dates:
                _ask_result["action_data"]["pending_ask_dates"] = _pending_ask_dates
                _ask_result["action_data"]["ask_type"] = "meal_slot_selector"
            return _ask_result

        # 'suggest_options': AI presents multiple plan choices — store options, no draft yet.
        if action == "suggest_options":
            dish_options = parsed.get("dish_options") or []
            if not dish_options:
                # LLM returned suggest_options but no dish_options — fall back to ask
                logger.warning(
                    "[PLAN_AHEAD_PIPELINE] suggest_options with empty dish_options — falling back to ask."
                )
                update_plan_state(owner_id=owner_id, last_pipeline_action="ask")
                if "?" not in user_message and "？" not in user_message:
                    user_message = (
                        "请告诉我您的饮食偏好（比如口味、菜系、饮食禁忌），我来为您推荐几套方案供选择。"
                    )
                return self._build_result(
                    response_text=user_message,
                    meal_plan=current_state.get("meal_plan", {}),
                    meal_plan_slots=current_state.get("meal_plan_slots", {}),
                    dish_ingredients=current_state.get("dish_ingredients", {}),
                    shopping_list=current_state.get("shopping_list", []),
                    schedule_id=current_state.get("schedule_id"),
                    is_draft=False,
                    intent_result=intent_result,
                )

            update_plan_state(
                owner_id=owner_id,
                last_pipeline_action="suggest_options",
                pending_options=dish_options,
            )
            logger.info(
                "[PLAN_AHEAD_PIPELINE] suggest_options: %d options stored, awaiting user selection.",
                len(dish_options),
            )
            return self._build_result(
                response_text=user_message,
                meal_plan=current_state.get("meal_plan", {}),
                meal_plan_slots=current_state.get("meal_plan_slots", {}),
                dish_ingredients=current_state.get("dish_ingredients", {}),
                shopping_list=current_state.get("shopping_list", []),
                schedule_id=current_state.get("schedule_id"),
                is_draft=False,
                intent_result=intent_result,
                dish_options=dish_options,
            )

        # After user selects an option: clear pending_options so they don't linger.
        if action == "recommend" and current_state.get("pending_options"):
            # Build a whitelist of (date, meal_type) pairs covered by the options.
            # This prevents the LLM from "echoing back" existing DB meals (e.g. yesterday's
            # lunch/breakfast) that appear in its context but were not part of the new plan.
            pending = current_state.get("pending_options") or []
            option_pairs: set = set()
            for opt in pending:
                for date, meals in (opt.get("meal_plan_slots") or {}).items():
                    for meal_type in meals.keys():
                        option_pairs.add((date, meal_type))

            if option_pairs:
                # Filter new_meal_plan_slots to only include whitelisted (date, meal_type) pairs
                filtered_slots: dict = {}
                for date, meals in new_meal_plan_slots.items():
                    kept_meals = {
                        mt: dishes
                        for mt, dishes in meals.items()
                        if (date, mt) in option_pairs
                    }
                    if kept_meals:
                        filtered_slots[date] = kept_meals

                removed_pairs = {
                    (d, mt)
                    for d, meals in new_meal_plan_slots.items()
                    for mt in meals
                    if (d, mt) not in option_pairs
                }
                if removed_pairs:
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] recommend after suggest_options: "
                        "stripped %d echoed-back DB (date, meal_type) pair(s): %s",
                        len(removed_pairs),
                        sorted(removed_pairs),
                    )

                new_meal_plan_slots = filtered_slots
                # Rebuild flat meal_plan from filtered slots
                new_meal_plan = {
                    date: [dish for dishes in meals.values() for dish in dishes]
                    for date, meals in new_meal_plan_slots.items()
                }
                # Re-filter dish_ingredients and dish_slots to only kept dishes
                kept_dishes: set = set()
                for meals in new_meal_plan_slots.values():
                    for dish_list in meals.values():
                        kept_dishes.update(dish_list)
                new_dish_ingredients = {d: v for d, v in new_dish_ingredients.items() if d in kept_dishes}
                new_dish_slots = {d: v for d, v in new_dish_slots.items() if d in kept_dishes}

            update_plan_state(owner_id=owner_id, pending_options=None)
            logger.info("[PLAN_AHEAD_PIPELINE] recommend after suggest_options: clearing pending_options.")

        # 'confirm': user approved the draft — persist whatever is in current in-memory state.
        if action == "confirm":
            draft_plan = current_state.get("meal_plan", {})
            draft_slots = current_state.get("meal_plan_slots", {})
            draft_di = current_state.get("dish_ingredients", {})

            # ----- Ghost-date filter (Issue 5) -----
            # If the user deleted a date from the Web UI while this draft was active, that date
            # may still be in the draft.  Filter it out to prevent "resurrection".
            # Logic: dates that were in DB when the draft was created (base_db_dates) but are
            # now absent from DB (old_state["meal_plan"]) were explicitly deleted by the user.
            base_db_dates: set = current_state.get("draft_base_db_dates") or set()
            current_db_dates: set = set((old_state.get("meal_plan") or {}).keys())
            user_deleted_dates = base_db_dates - current_db_dates
            if user_deleted_dates:
                logger.info(
                    f"[PLAN_AHEAD_PIPELINE] confirm: filtering {len(user_deleted_dates)} "
                    f"user-deleted date(s) from draft: {sorted(user_deleted_dates)}"
                )
                draft_plan = {d: v for d, v in draft_plan.items() if d not in user_deleted_dates}
                draft_slots = {d: v for d, v in draft_slots.items() if d not in user_deleted_dates}
                draft_di = {
                    dish: ings for dish, ings in draft_di.items()
                    if not any(
                        dish in (draft_slots.get(d) or {}).get(mt, [])  # type: ignore[operator]
                        for d in user_deleted_dates
                        for mt in ("breakfast", "lunch", "dinner", "snack")
                    )
                }

            draft_sl = compute_shopping_list(draft_di)

            if not draft_plan and not draft_slots:
                return self._build_result(
                    response_text=user_message or "No active draft plan found. Please request a meal recommendation first.",
                    meal_plan={}, meal_plan_slots={}, dish_ingredients={}, shopping_list=[],
                    schedule_id=current_state.get("schedule_id"),
                    is_draft=False,
                    intent_result=intent_result,
                )

            new_sid = await self._persist(
                owner_id=owner_id,
                action="add",
                target_date=None,
                old_state=current_state,
                new_meal_plan=draft_plan,
                new_meal_plan_slots=draft_slots,
                dish_ingredients=draft_di,
                shopping_list=draft_sl,
                storage_client=storage_client,
                user_timezone=user_timezone,
            )
            schedule_id = new_sid or current_state.get("schedule_id")
            update_plan_state(
                owner_id=owner_id,
                meal_plan=draft_plan,
                shopping_list=draft_sl,
                schedule_id=schedule_id,
                meal_plan_slots=draft_slots,
                dish_ingredients=draft_di,
                is_draft=False,
                last_pipeline_action=None,  # reset session after confirm
                draft_rejected_dishes=None,  # clear rejected list on confirm
                merge=False,
            )
            logger.info(f"[PLAN_AHEAD_PIPELINE] confirm: draft persisted, schedule_id={schedule_id}")

            # If the meal_planning_queue still has remaining slots (mid-queue checkpoint
            # save), set last_pipeline_action so Phase Q resumes on the next turn, and
            # append a hint so the user knows to reply 继续.
            _remaining_queue: List[str] = list(current_state.get("meal_planning_queue") or [])
            if _remaining_queue:
                # Reset draft so the next queue slot starts accumulating from scratch.
                # Without this, previously-confirmed meals stay in state and get merged
                # into the next slot's draft, causing already-saved meals to be re-sent
                # on every subsequent confirm.
                update_plan_state(
                    owner_id=owner_id,
                    meal_plan={},
                    meal_plan_slots={},
                    dish_ingredients={},
                    shopping_list=[],
                    is_draft=False,
                    last_pipeline_action="queue_day_complete",
                    merge=False,
                )
                _next_slot = _remaining_queue[0]
                if "|" in _next_slot:
                    _ns_date, _ns_meal = _next_slot.split("|", 1)
                    _ns_meal_zh = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(_ns_meal, _ns_meal)
                    _day_abbrev_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
                    from datetime import date as _ns_date_cls
                    _ns_abbr = _day_abbrev_map.get(_ns_date_cls.fromisoformat(_ns_date).weekday(), "")
                    _resume_hint = (
                        f"\n\n还有 {len(_remaining_queue)} 餐待规划。"
                        f"下一餐：{_ns_date}（{_ns_abbr}）{_ns_meal_zh}，回复「继续」→"
                    )
                else:
                    _resume_hint = f"\n\n还有 {len(_remaining_queue)} 餐待规划，回复「继续」→"
                # Strip any LLM-generated queue-hint line to prevent duplication.
                # The LLM can see the queue context and sometimes adds its own
                # "还有 N 餐待规划" line, which would duplicate the one we append.
                import re as _re
                user_message = _re.sub(r'\n*还有\s*\d+\s*餐待规划[^\n]*', '', user_message).rstrip()
                user_message = user_message + _resume_hint
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] confirm: %d queue slot(s) remain — draft reset, queue resumes next turn.",
                    len(_remaining_queue),
                )

            # Phase 2: update recent_dishes after confirmed commit
            if user_profile is not None and new_sid:
                try:
                    from app.services.diversity_engine import extract_dishes_for_history
                    _new_dish_entries = extract_dishes_for_history(draft_slots, dish_ingredients=draft_di)
                    if _new_dish_entries:
                        _dish_names_log = [e["dish"] for e in _new_dish_entries[:5]]
                        logger.info(
                            f"[PLAN_AHEAD_PIPELINE] confirm: writing {len(_new_dish_entries)} dishes "
                            f"to recent_dishes for user {owner_id}: {_dish_names_log}"
                            + (" ..." if len(_new_dish_entries) > 5 else "")
                        )
                        import asyncio
                        asyncio.ensure_future(
                            storage_client.update_user_recent_dishes(
                                owner_id=owner_id,
                                new_entries=_new_dish_entries,
                                existing_recent_dishes=user_profile.get("recent_dishes") or [],
                            )
                        )
                    else:
                        logger.info(f"[PLAN_AHEAD_PIPELINE] confirm: no new dish entries to write to recent_dishes")
                except Exception as _rd_err:
                    logger.warning(f"[PLAN_AHEAD_PIPELINE] recent_dishes update failed (non-fatal): {_rd_err}")

            return self._build_result(
                response_text=user_message,
                meal_plan=draft_plan,
                meal_plan_slots=draft_slots,
                dish_ingredients=draft_di,
                shopping_list=draft_sl,
                schedule_id=schedule_id,
                is_draft=False,
                intent_result=intent_result,
            )

        # ---- Step 4b: Compute shopping list ----
        shopping_list = compute_shopping_list(new_dish_ingredients)

        # ---- Step 4c: Recommend — cross-reference with inventory ----
        if action == "recommend" and inventory_items:
            inv_names_lower = {item["product_name"].lower() for item in inventory_items}
            already_have: List[str] = []
            need_to_buy: List[str] = []
            for ingredient in shopping_list:
                ing_name = ingredient["name"] if isinstance(ingredient, dict) else ingredient
                if ing_name.lower() in inv_names_lower:
                    already_have.append(ing_name)
                else:
                    need_to_buy.append(ingredient)
            shopping_list = need_to_buy
            if already_have:
                inv_highlight = "、".join(already_have)
                user_message = (
                    user_message
                    + f"\n\nInventory used: {inv_highlight}. These have been removed from the shopping list automatically."
                )
                logger.info(
                    f"[PLAN_AHEAD_PIPELINE] Recommend: {len(already_have)} ingredients "
                    f"covered by inventory, {len(need_to_buy)} still needed."
                )

        # ---- Step 4c: For explicit single-dish add/modify, strip LLM auto-completed extra dishes ----
        # When the user explicitly names specific dish(es) (e.g. "加个萝卜炖牛腩"),
        # the LLM may add extra dishes (sides, staples) that were not requested.
        # We detect this via a lightweight classifier and keep only the requested dishes.
        #
        # Two-tier detection:
        #   Tier 1 (LLM): _classify_dish_intent returns is_explicit=True + dish list → use it
        #   Tier 2 (query-substring): LLM failed / returned EXPLICIT_HINT with empty dishes →
        #       check which of the LLM's proposed dishes actually appear in the user's query.
        #       A dish the user never typed is by definition an AI hallucination; strip it.
        if action in ("add", "modify") and new_meal_plan_slots:
            _dish_intent = await ClassifyDishIntentSkill(self.gemini_api_url).execute(
                user_input or "", history or []
            )

            # Tier-2 fallback: LLM classifier returned EXPLICIT_HINT (keyword match,
            # no dish extraction) or any other state where dishes==[].
            # Derive dish list by checking which proposed dishes appear in the query.
            if not _dish_intent["dishes"] and (
                _dish_intent.get("intent") == "EXPLICIT_HINT"
                or _dish_intent["is_explicit"]
            ):
                _query_clean = (user_input or "").lower().replace(" ", "")
                _all_proposed: list = [
                    d
                    for _meals in new_meal_plan_slots.values()
                    for _mt_dishes in _meals.values()
                    for d in _mt_dishes
                ]
                _in_query = [
                    d for d in _all_proposed
                    if d.lower().replace(" ", "") in _query_clean
                ]
                if _in_query:
                    _dish_intent = {
                        "is_explicit": True,
                        "dishes": _in_query,
                        "intent": "QUERY_EXTRACTED",
                    }
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] Tier-2 dish extraction: found %s in query",
                        _in_query,
                    )

            if _dish_intent["is_explicit"] and _dish_intent["dishes"]:
                _requested = _dish_intent["dishes"]

                # Safety net: if NONE of the requested (named) dishes appear in the
                # new plan output, the user named them as the SOURCE of a replacement
                # (e.g. "把X换成另一道类似的菜"), not as a dish to ADD.
                # The main LLM already generated the correct replacement — skip filtering.
                _all_new_dish_names: set = {
                    d.lower().replace(" ", "")
                    for _meals in new_meal_plan_slots.values()
                    for _mt_dishes in _meals.values()
                    for d in _mt_dishes
                }
                _any_requested_in_output = any(
                    _req.lower().replace(" ", "") in _d
                    or _d in _req.lower().replace(" ", "")
                    for _req in _requested
                    for _d in _all_new_dish_names
                )
                if not _any_requested_in_output:
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] explicit-dish filter SKIPPED: "
                        "named dishes %s absent from new plan — they were the SOURCE of replacement.",
                        _requested,
                    )
                else:
                    # Collect dishes that were already in the draft BEFORE this turn.
                    # In draft mode, the LLM returns the full modified draft (not just the
                    # newly-added dish), so we must not strip pre-existing draft content.
                    # Example: draft has [番茄炖排骨, 蚝油生菜]; user says "加个米饭，不要生菜"
                    # → LLM correctly returns [番茄炖排骨, 米饭]; filter must NOT strip 番茄炖排骨.
                    _pre_draft_dishes: set = set()
                    if is_currently_draft:
                        for _pd_meals in (current_state.get("meal_plan_slots") or {}).values():
                            for _pd_dishes in _pd_meals.values():
                                _pre_draft_dishes.update(_pd_dishes)

                    _explicit_slots: dict = {}
                    _removed_extras: list = []
                    for _date, _meals in new_meal_plan_slots.items():
                        _kept_meals_ex = {}
                        for _mt, _dishes in _meals.items():
                            # Keep a dish if it:
                            #   (a) fuzzy-matches any explicitly requested dish name, OR
                            #   (b) was already present in the draft before this turn
                            _kept: list = []
                            _removed: list = []
                            for _d in _dishes:
                                _d_lower = _d.lower().replace(" ", "")
                                _match = any(
                                    _req.lower().replace(" ", "") in _d_lower
                                    or _d_lower in _req.lower().replace(" ", "")
                                    for _req in _requested
                                ) or _d in _pre_draft_dishes
                                if _match:
                                    _kept.append(_d)
                                else:
                                    _removed.append(_d)
                            if _kept:
                                _kept_meals_ex[_mt] = _kept
                            _removed_extras.extend(_removed)
                        if _kept_meals_ex:
                            _explicit_slots[_date] = _kept_meals_ex
                    if _removed_extras:
                        logger.info(
                            "[PLAN_AHEAD_PIPELINE] explicit-dish filter: kept %s, stripped extra: %s",
                            _requested, _removed_extras,
                        )
                        new_meal_plan_slots = _explicit_slots
                        new_meal_plan = {
                            d: [dish for mt_dishes in meals.values() for dish in mt_dishes]
                            for d, meals in new_meal_plan_slots.items()
                        }
                        _kept_dishes_ex: set = {
                            dish for meals in new_meal_plan_slots.values()
                            for mt_dishes in meals.values() for dish in mt_dishes
                        }
                        new_dish_ingredients = {k: v for k, v in new_dish_ingredients.items() if k in _kept_dishes_ex}
                        new_dish_slots = {k: v for k, v in new_dish_slots.items() if k in _kept_dishes_ex}
                        shopping_list = compute_shopping_list(new_dish_ingredients)

                        # Rebuild the response message to match what was actually saved.
                        _meal_type_labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
                        _lines: list = []
                        for _d in sorted(new_meal_plan_slots):
                            for _mt, _ds in new_meal_plan_slots[_d].items():
                                _mt_label = _meal_type_labels.get(_mt, _mt)
                                _dish_str = "、".join(_ds)
                                _lines.append(f"📅 {_d} {_mt_label}: {_dish_str}")
                        if _lines:
                            _verb = "已为您" + ("添加" if action == "add" else "调整为")
                            user_message = _verb + "\n" + "\n".join(_lines)
                        logger.info(
                            "[PLAN_AHEAD_PIPELINE] explicit-dish filter: rebuilt response message to match filtered plan."
                        )

        # ---- Step 4d: For 'add', strip echoed-back confirmed dates ----
        # The LLM may include existing confirmed dates in its output (copied from context).
        # For 'add', we only want truly new (date, meal_type) pairs — remove anything that
        # already exists in the confirmed plan with identical content.
        if action == "add" and not is_currently_draft:
            _old_slots = old_state.get("meal_plan_slots") or {}
            _add_filtered_slots: dict = {}
            _add_echoed_pairs: list = []
            for _date, _meals in new_meal_plan_slots.items():
                _old_date_meals = _old_slots.get(_date, {})
                _kept_meals = {}
                for _mt, _dishes in _meals.items():
                    _old_dishes = set(_old_date_meals.get(_mt, []))
                    _new_set = set(_dishes)
                    if _mt not in _old_date_meals or _new_set != _old_dishes:
                        _kept_meals[_mt] = _dishes
                    else:
                        _add_echoed_pairs.append((_date, _mt))
                if _kept_meals:
                    _add_filtered_slots[_date] = _kept_meals
            if _add_echoed_pairs:
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] add: stripped %d echoed-back confirmed (date, meal_type) pair(s): %s",
                    len(_add_echoed_pairs), sorted(_add_echoed_pairs),
                )
                new_meal_plan_slots = _add_filtered_slots
                new_meal_plan = {
                    d: [dish for mt_dishes in meals.values() for dish in mt_dishes]
                    for d, meals in new_meal_plan_slots.items()
                }
                _kept_add_dishes: set = {
                    dish for meals in new_meal_plan_slots.values()
                    for mt_dishes in meals.values() for dish in mt_dishes
                }
                new_dish_ingredients = {d: v for d, v in new_dish_ingredients.items() if d in _kept_add_dishes}
                new_dish_slots = {d: v for d, v in new_dish_slots.items() if d in _kept_add_dishes}
                # Recompute shopping list after filtering
                shopping_list = compute_shopping_list(new_dish_ingredients)

        # ---- Step 5: Draft-mode check ----
        # recommend always creates a draft.
        # Mutations (modify/add/remove/…) while a draft is active stay in draft — don't persist.
        _mutation_actions = {"modify", "add", "remove", "update_ingredients", "remove_ingredients"}
        is_draft_operation = (action == "recommend") or (
            is_currently_draft and action in _mutation_actions
        )

        if is_draft_operation:
            # When creating a brand-new recommend draft, snapshot which dates are currently
            # in DB.  This lets confirm() detect dates the user later deletes via the Web UI.
            base_db_dates_kwarg: Dict[str, Any] = {}
            if action == "recommend" and not is_currently_draft:
                base_db_dates_kwarg["draft_base_db_dates"] = set(old_state.get("meal_plan", {}).keys())

            # Compute which dishes were replaced/removed in this operation so we can track
            # them as "rejected" and avoid re-suggesting them in subsequent turns.
            # IMPORTANT: always scope to only the dates that are actually being modified/replaced.
            # Using all dates in state would wrongly mark dishes from OTHER dates (e.g. existing DB
            # meals) as rejected just because they're absent from the new single-day plan.
            if _queue_target_slot:
                _slot_date_only = _queue_target_slot.split("|")[0] if "|" in _queue_target_slot else _queue_target_slot
                _prev_for_tracking = {_slot_date_only: (current_state.get("meal_plan_slots") or {}).get(_slot_date_only, {})}
            else:
                # Scope to dates actually present in the new plan — never bleed in unrelated dates.
                _new_plan_dates = set(new_meal_plan_slots.keys())
                _prev_for_tracking = {
                    d: v for d, v in (current_state.get("meal_plan_slots") or {}).items()
                    if d in _new_plan_dates
                }
            _prev_dishes: set = set()
            for _pdate, _pmeals in _prev_for_tracking.items():
                for _pmt, _pdishes in _pmeals.items():
                    _prev_dishes.update(_pdishes)
            _new_dishes: set = set()
            for _ndate, _nmeals in new_meal_plan_slots.items():
                for _nmt, _ndishes in _nmeals.items():
                    _new_dishes.update(_ndishes)
            _newly_rejected = _prev_dishes - _new_dishes
            # Preserve existing rejected dishes across turns (union-merge in update_plan_state)
            _existing_rejected: set = current_state.get("draft_rejected_dishes") or set()

            # Queue mode: use merge=True so previously-planned days accumulate in state.
            # Normal mode: use merge=False to replace stale draft data cleanly.
            _draft_merge = bool(_queue_target_date)

            update_plan_state(
                owner_id=owner_id,
                meal_plan=new_meal_plan,
                shopping_list=shopping_list,
                schedule_id=current_state.get("schedule_id"),
                meal_plan_slots=new_meal_plan_slots,
                dish_ingredients=new_dish_ingredients,
                is_draft=True,
                last_pipeline_action=action,
                draft_rejected_dishes=_existing_rejected | _newly_rejected,
                merge=_draft_merge,
                **base_db_dates_kwarg,
            )
            if _newly_rejected:
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] Tracking %d newly rejected dish(es): %s",
                    len(_newly_rejected), sorted(_newly_rejected),
                )

            # ---- Queue mode: pop the planned meal slot and append progress info ----
            _day_abbrev = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
            _meal_zh_label = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
            if _queue_target_slot and action == "recommend":
                _remaining_queue = _meal_queue[1:]
                _slot_num_done = _meal_total - len(_meal_queue) + 1
                # Parse current slot for display
                if "|" in _queue_target_slot:
                    _cur_date_str, _cur_meal = _queue_target_slot.split("|", 1)
                    _cur_meal_zh = _meal_zh_label.get(_cur_meal, _cur_meal)
                else:
                    _cur_date_str, _cur_meal_zh = _queue_target_slot, ""

                # Replace LLM's user_message with a single-slot summary so other
                # already-planned meals for the same date don't bleed into the response.
                _slot_dishes = (new_meal_plan_slots or {}).get(_cur_date_str, {}).get(_cur_meal, [])
                if _slot_dishes:
                    _dish_list = "、".join(_slot_dishes)
                    user_message = f"好的，已为您规划 {_cur_date_str} {_cur_meal_zh}：{_dish_list}。"
                if _remaining_queue:
                    update_plan_state(
                        owner_id=owner_id,
                        meal_planning_queue=_remaining_queue,
                        last_pipeline_action="queue_day_complete",
                    )
                    from datetime import date as _qdcls
                    _next_slot = _remaining_queue[0]
                    if "|" in _next_slot:
                        _next_date_str, _next_meal = _next_slot.split("|", 1)
                        _next_meal_zh = _meal_zh_label.get(_next_meal, _next_meal)
                        _next_abbr = _day_abbrev.get(_qdcls.fromisoformat(_next_date_str).weekday(), "")
                        _next_label = f"{_next_date_str}（{_next_abbr}）{_next_meal_zh}"
                    else:
                        _next_label = _next_slot
                    user_message = (
                        user_message
                        + f"\n\n---\n✅ 第 {_slot_num_done}/{_meal_total} 餐 — {_cur_date_str} {_cur_meal_zh}已确认。"
                        f"\n下一餐：{_next_label}，回复「继续」→"
                    )
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] Queue: slot %d/%d planned (%s), %d slot(s) remaining.",
                        _slot_num_done, _meal_total, _queue_target_slot, len(_remaining_queue),
                    )
                else:
                    update_plan_state(
                        owner_id=owner_id,
                        meal_planning_queue=[],
                        meal_planning_total=0,
                        last_pipeline_action=None,
                    )
                    user_message = (
                        user_message
                        + f"\n\n🎉 全部 {_meal_total} 餐规划已完成！回复「确认」将计划保存到日历。"
                    )
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] Queue: all %d meal slots planned. Queue complete.",
                        _meal_total,
                    )

            # Re-read state to get the full accumulated meal plan for the response
            _final_state = get_plan_state(owner_id) if _queue_target_date else None
            _resp_meal_plan = (_final_state or {}).get("meal_plan", new_meal_plan) if _queue_target_date else new_meal_plan
            _resp_slots = (_final_state or {}).get("meal_plan_slots", new_meal_plan_slots) if _queue_target_date else new_meal_plan_slots
            _resp_di = (_final_state or {}).get("dish_ingredients", new_dish_ingredients) if _queue_target_date else new_dish_ingredients
            _resp_sl = compute_shopping_list(_resp_di) if _queue_target_date else shopping_list

            logger.info(
                f"[PLAN_AHEAD_PIPELINE] action={action} (draft): memory updated, DB persist deferred. "
                f"dates={list(_resp_meal_plan.keys())}"
            )
            return self._build_result(
                response_text=user_message,
                meal_plan=_resp_meal_plan,
                meal_plan_slots=_resp_slots,
                dish_ingredients=_resp_di,
                shopping_list=_resp_sl,
                schedule_id=current_state.get("schedule_id"),
                is_draft=True,
                intent_result=intent_result,
                dish_slots=new_dish_slots,
            )

        # ---- Step 6: Persist (non-draft direct operations) ----
        schedule_id = current_state.get("schedule_id")
        target_meal_time = parsed.get("meal_time")  # None means remove entire day
        if action != "view":
            # For 'remove': the LLM returns action=remove + target_date but empty
            # meal_entries.  Rebuild new_meal_plan as the surviving plan:
            #   - If target_meal_time is set AND the date has other slots → only remove
            #     that specific meal_time, keeping the rest of the day intact.
            #   - Otherwise → remove the entire date.
            if action == "remove" and target_date and target_date not in new_meal_plan:
                _old_mp = current_state.get("meal_plan") or {}
                _old_slots = current_state.get("meal_plan_slots") or {}
                _old_di = current_state.get("dish_ingredients") or {}

                _old_date_slots = _old_slots.get(target_date, {})
                _remaining_mt = (
                    {mt: v for mt, v in _old_date_slots.items() if mt != target_meal_time and v}
                    if target_meal_time else {}
                )

                if target_meal_time and _remaining_mt:
                    # Partial removal: keep the date with its remaining meal slots.
                    new_meal_plan_slots = {**_old_slots, target_date: _remaining_mt}
                    _remaining_dishes: List[str] = []
                    for _mt in ("breakfast", "lunch", "dinner", "snack"):
                        _remaining_dishes.extend(_remaining_mt.get(_mt) or [])
                    new_meal_plan = {**_old_mp, target_date: " and ".join(_remaining_dishes)}
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] action=remove partial: kept %s with %s "
                        "(removed meal_time=%s)",
                        target_date, list(_remaining_mt.keys()), target_meal_time,
                    )
                else:
                    # Remove the entire date.
                    new_meal_plan = {k: v for k, v in _old_mp.items() if k != target_date}
                    new_meal_plan_slots = {k: v for k, v in _old_slots.items() if k != target_date}

                _rem_dishes: set = {
                    dish
                    for meals in new_meal_plan_slots.values()
                    for mt_dishes in meals.values()
                    for dish in (mt_dishes if isinstance(mt_dishes, list) else [])
                }
                new_dish_ingredients = {k: v for k, v in _old_di.items() if k in _rem_dishes}
                shopping_list = compute_shopping_list(new_dish_ingredients)

            new_sid = await self._persist(
                owner_id=owner_id,
                action=action,
                target_date=target_date,
                old_state=current_state,
                new_meal_plan=new_meal_plan,
                new_meal_plan_slots=new_meal_plan_slots,
                dish_ingredients=new_dish_ingredients,
                shopping_list=shopping_list,
                storage_client=storage_client,
                user_timezone=user_timezone,
                target_meal_time=target_meal_time,
            )
            if new_sid:
                schedule_id = new_sid

            # Phase 2: update recent_dishes after direct persist (add / modify)
            if user_profile is not None and new_sid and action in ("add", "modify"):
                try:
                    from app.services.diversity_engine import extract_dishes_for_history
                    _new_dish_entries = extract_dishes_for_history(new_meal_plan_slots, dish_ingredients=new_dish_ingredients)
                    if _new_dish_entries:
                        import asyncio
                        asyncio.ensure_future(
                            storage_client.update_user_recent_dishes(
                                owner_id=owner_id,
                                new_entries=_new_dish_entries,
                                existing_recent_dishes=user_profile.get("recent_dishes") or [],
                            )
                        )
                except Exception as _rd_err:
                    logger.warning(f"[PLAN_AHEAD_PIPELINE] recent_dishes update failed (non-fatal): {_rd_err}")

        update_plan_state(
            owner_id=owner_id,
            meal_plan=new_meal_plan,
            shopping_list=shopping_list,
            schedule_id=schedule_id,
            meal_plan_slots=new_meal_plan_slots,
            dish_ingredients=new_dish_ingredients,
            is_draft=False,
            last_pipeline_action=action,
            merge=False,
        )

        logger.info(
            f"[PLAN_AHEAD_PIPELINE] Completed: action={action}, "
            f"dates={list(new_meal_plan.keys())}, schedule_id={schedule_id}"
        )
        return self._build_result(
            response_text=user_message,
            meal_plan=new_meal_plan,
            meal_plan_slots=new_meal_plan_slots,
            dish_ingredients=new_dish_ingredients,
            shopping_list=shopping_list,
            schedule_id=schedule_id,
            is_draft=False,
            intent_result=intent_result,
        )

    @staticmethod
    def _build_result(
        response_text: str,
        meal_plan: Dict,
        meal_plan_slots: Dict,
        dish_ingredients: Dict,
        shopping_list: List,
        schedule_id: Optional[int],
        intent_result: Any,
        is_draft: bool = False,
        dish_options: Optional[List] = None,
        dish_slots: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Build chat.py-compatible response dict."""
        try:
            from app.modules.intent_classifier import Intent
            intent_val = intent_result.intent if intent_result else Intent.PLAN_AHEAD
            confidence = intent_result.confidence if intent_result else 0.95
            reasoning = intent_result.reasoning if intent_result else ""
        except Exception:
            intent_val = "PLAN_AHEAD"
            confidence = 0.95
            reasoning = ""

        action_str = "SUGGEST_OPTIONS" if dish_options else "PLAN_AHEAD"
        action_data: Dict[str, Any] = {
            "meal_plan": meal_plan,
            "shopping_list": shopping_list,
            "meal_plan_slots": meal_plan_slots,
            "dish_ingredients": dish_ingredients,
            "schedule_id": schedule_id,
            "is_draft": is_draft,
        }
        if dish_options:
            action_data["dish_options"] = dish_options
        if dish_slots:
            action_data["dish_slots"] = dish_slots

        return {
            "response": response_text,
            "intent": intent_val,
            "confidence": confidence,
            "reasoning": reasoning,
            "action": action_str,
            "action_data": action_data,
        }
