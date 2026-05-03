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
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from app.agents.scheduling_agent import (
    PLAN_AHEAD_RESPONSE_SCHEMA,
    PlanAheadAgent,
    compute_shopping_list,
)
from app.modules.active_context import get_active_context, update_active_context
from app.modules.plan_ahead_state import PipelinePhase, _UNSET, get_phase, get_plan_state, update_plan_state
from app.skills.plan_ahead import (
    ClassifyDateConfirmationSkill,
    ClassifyDishIntentSkill,
    ClassifyMealActionSkill,
    ExtractIngredientsSkill,
    GenerateMealPlanSkill,
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

    @staticmethod
    def _extract_session_avoidance_preferences(user_input: str) -> Dict[str, List[str]]:
        """
        Extract short-lived "don't want" preferences from current user turn.

        These are session-level constraints (not permanent profile edits), e.g.:
          - "最近不想吃韩餐"
          - "先不要番茄炒蛋"
          - "别推荐香菜"
        """
        text = (user_input or "").strip()
        if not text:
            return {"avoid_dishes": [], "avoid_ingredients": [], "avoid_cuisines": []}

        avoid_dishes: List[str] = []
        avoid_ingredients: List[str] = []
        avoid_cuisines: List[str] = []

        def _add_unique(bucket: List[str], value: str) -> None:
            v = value.strip(" ，,。！？!?.;；:：\"'“”‘’")
            if not v:
                return
            if v.lower() not in {x.lower() for x in bucket}:
                bucket.append(v)

        # Cuisine-level dislike/avoid
        cuisine_patterns = {
            "韩餐": [r"(?:不爱吃|不喜欢|不想吃|不要|别(?:给我)?推荐).{0,3}(韩餐|韩式|韩国菜)"],
            "日料": [r"(?:不爱吃|不喜欢|不想吃|不要|别(?:给我)?推荐).{0,3}(日料|日式|日本菜)"],
            "川菜": [r"(?:不爱吃|不喜欢|不想吃|不要|别(?:给我)?推荐).{0,3}(川菜)"],
            "粤菜": [r"(?:不爱吃|不喜欢|不想吃|不要|别(?:给我)?推荐).{0,3}(粤菜)"],
            "西餐": [r"(?:不爱吃|不喜欢|不想吃|不要|别(?:给我)?推荐).{0,3}(西餐)"],
        }
        cuisine_alias_to_canonical = {
            "韩餐": "韩餐", "韩式": "韩餐", "韩国菜": "韩餐",
            "日料": "日料", "日式": "日料", "日本菜": "日料",
            "川菜": "川菜",
            "粤菜": "粤菜",
            "西餐": "西餐",
        }
        for canonical, patterns in cuisine_patterns.items():
            if any(re.search(pat, text, flags=re.IGNORECASE) for pat in patterns):
                _add_unique(avoid_cuisines, canonical)

        # Dish/ingredient-level avoidance: capture phrase tail after negation trigger
        for m in re.finditer(
            r"(?:最近|这几天|今天|今晚|明天|先)?\s*(?:不想吃|不爱吃|不喜欢|不要|别(?:给我)?推荐)\s*([^\n，,。！？!?.]{1,16})",
            text,
            flags=re.IGNORECASE,
        ):
            phrase = (m.group(1) or "").strip()
            if not phrase:
                continue
            # Cuisine aliases must stay in avoid_cuisines, not avoid_dishes.
            # This prevents duplicated/conflicting constraints like "Avoid dishes: 韩餐".
            canonical_cuisine = cuisine_alias_to_canonical.get(phrase)
            if canonical_cuisine:
                _add_unique(avoid_cuisines, canonical_cuisine)
                continue
            # Skip broad generic nouns
            if phrase in {"这个", "这个菜", "那道", "这道", "吃的"}:
                continue
            # Prefer ingredient bucket for common single ingredients
            if len(phrase) <= 4 and phrase in {"香菜", "葱", "蒜", "姜", "辣椒", "洋葱", "番茄", "土豆", "茄子"}:
                _add_unique(avoid_ingredients, phrase)
            else:
                _add_unique(avoid_dishes, phrase)

        return {
            "avoid_dishes": avoid_dishes,
            "avoid_ingredients": avoid_ingredients,
            "avoid_cuisines": avoid_cuisines,
        }

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

    @staticmethod
    def _augment_ask_confirm_result(
        result: Dict[str, Any],
        slots: List[str],
    ) -> Dict[str, Any]:
        """Inject pending_slots + ask_type into action_data so the frontend
        can render an interactive meal-selector card instead of relying on text."""
        parsed: List[Dict[str, str]] = []
        for s in slots:
            parts = s.split("|", 1)
            if len(parts) == 2:
                parsed.append({"date": parts[0], "meal_type": parts[1]})
        result["action_data"]["ask_type"] = "date_meal_confirm"
        result["action_data"]["pending_slots"] = parsed
        return result

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
        _day_en = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
        _meal_zh = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
        _meal_en = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner"}
        is_zh = (language or "en") == "zh"

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

        _day_map = _day_zh if is_zh else _day_en

        def _fmt(date_str: str):
            try:
                _dt = _d.fromisoformat(date_str)
                _short = f"{_dt.month}月{_dt.day}日" if is_zh else date_str
                return _short, _day_map.get(_dt.weekday(), "")
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
            meals_en = " and ".join(_meal_en.get(m, m.capitalize()) for m in unique_meals)
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

        # Cancel check — must come before confirm so "取消" doesn't partially match confirm
        _cancel_kw = ("取消", "不用了", "不用规划", "算了", "不想了", "cancel", "never mind", "forget it")
        if any(kw in _q.lower() for kw in _cancel_kw):
            return {"intent": "cancel", "new_dates": [], "new_meal_times": None}

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

                # If corrected but no dates extracted, use ScheduleRangeDecider
                # (same LLM date parser used by the chat pipeline hydration).
                if not new_dates and _new_meal_times is None:
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] corrected but empty dates — "
                        "delegating to ScheduleRangeDecider for %r",
                        user_input,
                    )
                    from app.agents.scheduling_agent import ScheduleRangeDecider
                    _time_range = await ScheduleRangeDecider()._parse_range_llm(
                        user_input, user_timezone, self.gemini_api_url
                    )
                    if _time_range:
                        from datetime import date as _d
                        s, e = _time_range.to_date_range()
                        new_dates = [
                            (s + timedelta(days=i)).strftime("%Y-%m-%d")
                            for i in range((e - s).days + 1)
                        ]

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

        # Use a semantic schema so the LLM expresses *meaning*, not absolute dates.
        # Python handles all date arithmetic — this makes the prompt language-agnostic.
        system_prompt = (
            f"Today is {today_str}. The current meal plan proposal covers {current_start} to {current_end}.\n"
            "The user wants to change the proposed dates or meals. Extract their intent.\n\n"
            "Return JSON with these fields (omit fields that don't apply):\n\n"
            "  // Option A — user named specific dates or day anchors\n"
            "  new_start: 'YYYY-MM-DD'   // first day of the new period\n"
            "  new_end:   'YYYY-MM-DD'   // last day  (>= new_start)\n\n"
            "  // Option B — user expressed a duration ('3 days', '三天', 'a week', etc.)\n"
            "  duration_days:     <integer>  // total number of days in the new period\n"
            "  start_offset_days: 0 or 1    // 0 = starts TODAY, 1 = starts TOMORROW\n"
            "    (use 1 for 'next/upcoming/后面/接下来/以后/from tomorrow' expressions)\n"
            "    (use 0 for 'today/today onward/今天' expressions)\n\n"
            "  // Meal scope (only if the user changes which meals to plan)\n"
            "  new_meal_times: ['breakfast','lunch','dinner'] subset | null\n"
            "    (null = all three meals)\n\n"
            "Choose Option A OR Option B — do not mix them.\n"
            "Return JSON only — no explanation, no markdown."
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": f'User said: "{user_input}"'}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 150,
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

            from datetime import date as _d

            ref_today = _d.fromisoformat(today_str[:10])
            new_dates: List[str] = []

            # Option B — duration-based (language-agnostic)
            _duration = extracted.get("duration_days")
            _offset = extracted.get("start_offset_days") or 0
            if _duration and int(_duration) > 0:
                s = ref_today + timedelta(days=int(_offset))
                e = s + timedelta(days=int(_duration) - 1)
                new_dates = [
                    (s + timedelta(days=i)).strftime("%Y-%m-%d")
                    for i in range((e - s).days + 1)
                ]
            else:
                # Option A — explicit dates
                new_start = (extracted.get("new_start") or "")[:10]
                new_end = (extracted.get("new_end") or "")[:10]
                if new_start and new_end:
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
    # Steps 2+3: delegated to GenerateMealPlanSkill
    # (context building, seed-library RAG, diversity directives, LLM call
    #  with retry — all prompt engineering now lives in the Skill file)
    #
    # _build_context, _inject_pending_options, and _call_llm below are
    # delegation shims so that existing tests remain valid without changes.
    # The pipeline's run() path calls GenerateMealPlanSkill.execute() which
    # bundles all three in a single, clean Skill invocation.
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
        is_escalation: bool = False,
    ) -> str:
        """Delegation shim → GenerateMealPlanSkill._build_system_context."""
        return GenerateMealPlanSkill(self.gemini_api_url)._build_system_context(
            state=state,
            user_timezone=user_timezone,
            inventory_items=inventory_items,
            is_draft=is_draft,
            cooking_level=cooking_level,
            language=language,
            scheduling_context=scheduling_context,
            user_profile=user_profile,
            queue_target_date=queue_target_date,
            precomputed_action=precomputed_action,
            active_context=active_context,
            is_escalation=is_escalation,
        )

    def _inject_pending_options(self, ctx: str, state: Dict[str, Any]) -> str:
        """Delegation shim → GenerateMealPlanSkill._inject_pending_options."""
        return GenerateMealPlanSkill(self.gemini_api_url)._inject_pending_options(ctx, state)

    @staticmethod
    def _keyword_fallback_classify(query: str) -> Dict[str, Any]:
        """Delegation shim → ClassifyDishIntentSkill._keyword_fallback.

        Kept here so existing tests that call pipeline._keyword_fallback_classify()
        continue to work without changes.
        """
        from app.skills.plan_ahead.classify_dish_intent import ClassifyDishIntentSkill
        return ClassifyDishIntentSkill._keyword_fallback(query)

    async def _classify_dish_intent(
        self, query: str, history: List[Dict]
    ) -> Dict[str, Any]:
        """Delegation shim → ClassifyDishIntentSkill.execute.

        Prompt and logic live in app/skills/plan_ahead/classify_dish_intent.py.
        Kept here so existing tests that call pipeline._classify_dish_intent()
        or mock it continue to work without changes.
        """
        return await ClassifyDishIntentSkill(self.gemini_api_url).execute(query, history)

    # Thin wrapper used by the Fresh-session guard (uses only the bool result).
    # Delegates to ClassifyDishIntentSkill — prompt lives in the skill file.
    async def _classify_explicit_add(self, query: str, history: List[Dict]) -> bool:
        result = await ClassifyDishIntentSkill(self.gemini_api_url).execute(query, history)
        if result["intent"] == "EXPLICIT_HINT":
            return True
        return result["is_explicit"]

    # ------------------------------------------------------------------
    # Step 3: delegated to GenerateMealPlanSkill._generate_with_retry
    # (The pipeline's run() path calls GenerateMealPlanSkill.execute() which
    #  bundles context-build + LLM-call in one clean Skill invocation.)
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        system_context: str,
        history: List[Dict],
        user_input: str,
        max_attempts: int = 3,
        max_history_messages: int = 20,
        refresh_count: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Delegation shim to GenerateMealPlanSkill._generate_with_retry."""
        return await GenerateMealPlanSkill(self.gemini_api_url)._generate_with_retry(
            system_context=system_context,
            history=history or [],
            user_input=user_input,
            max_attempts=max_attempts,
            max_history_messages=max_history_messages,
            refresh_count=refresh_count,
        )

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
        extra_existing_dish_data: Optional[Dict[str, Any]] = None,
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
            extra_existing_dish_data=extra_existing_dish_data,
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
        context: Optional[Dict] = None,
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

        # ── Fast-path: frontend submitted confirmed slots directly (button click) ──────
        _direct_slots: List[str] = [
            s for s in list((context or {}).get("confirmed_slots") or [])
            if isinstance(s, str) and "|" in s
        ]
        if _direct_slots:
            update_plan_state(
                owner_id=owner_id,
                pending_planning_queue=None,
                last_pipeline_action=None,
                meal_planning_queue=_direct_slots,
                meal_planning_total=len(_direct_slots),
                meal_plan={}, meal_plan_slots={}, dish_ingredients={}, shopping_list=[],
                is_draft=False, confirmation_retry_count=None, merge=False,
            )
            logger.info(
                "[PLAN_AHEAD_PIPELINE] Phase 1b fast-path: %d slot(s) confirmed directly via frontend button.",
                len(_direct_slots),
            )
            return None  # fall through to queue-aware LLM planning

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

        if _clf_intent == "cancel":
            update_plan_state(
                owner_id=owner_id,
                pending_planning_queue=None,
                last_pipeline_action=None,
                confirmation_retry_count=None,
            )
            logger.info("[PLAN_AHEAD_PIPELINE] Phase 1b: user cancelled meal planning.")
            _cur = get_plan_state(owner_id)
            _result = self._build_result(
                response_text="",
                meal_plan=_cur.get("meal_plan", {}), meal_plan_slots=_cur.get("meal_plan_slots", {}),
                dish_ingredients=_cur.get("dish_ingredients", {}), shopping_list=_cur.get("shopping_list", []),
                schedule_id=_cur.get("schedule_id"), intent_result=intent_result,
            )
            _result["tool_result"] = "The user cancelled the meal planning session. Acknowledge the cancellation naturally and let them know they can ask again anytime."
            return _result

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
            if not _corrected_dates and _new_meal_times is None:
                from app.agents.scheduling_agent import ScheduleRangeDecider
                _time_range = await ScheduleRangeDecider()._parse_range_llm(
                    user_input, user_timezone, self.gemini_api_url
                )
                if _time_range:
                    _s, _e = _time_range.to_date_range()
                    _corrected_dates = [
                        (_s + timedelta(days=i)).strftime("%Y-%m-%d")
                        for i in range((_e - _s).days + 1)
                    ]
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] Phase 1b: ScheduleRangeDecider extracted corrected dates: %s",
                        _corrected_dates,
                    )
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
                    logger.info("[PLAN_AHEAD_PIPELINE] Phase 1b: 2 no-op corrections → degrading to fresh start.")
                    _cur = get_plan_state(owner_id)
                    _result = self._build_result(
                        response_text="",
                        meal_plan=_cur.get("meal_plan", {}), meal_plan_slots=_cur.get("meal_plan_slots", {}),
                        dish_ingredients=_cur.get("dish_ingredients", {}), shopping_list=_cur.get("shopping_list", []),
                        schedule_id=_cur.get("schedule_id"), intent_result=intent_result,
                    )
                    _result["tool_result"] = (
                        "The user tried to correct the planned dates twice but the intent was unclear. "
                        "The planning session has been reset. Ask the user to start over by telling you "
                        "which days and meals they'd like to plan."
                    )
                    return _result
                else:
                    _pending_start_str = _pending_queue[0].split("|")[0] if _pending_queue else ""
                    _pending_end_str = _pending_queue[-1].split("|")[0] if _pending_queue else ""
                    _pending_meals = list(dict.fromkeys(s.split("|")[1] for s in _pending_queue if "|" in s))
                    _date_hint = (
                        f"{_pending_start_str} ({', '.join(_pending_meals) or 'all meals'})"
                        if _pending_start_str == _pending_end_str
                        else f"{_pending_start_str} to {_pending_end_str} ({', '.join(_pending_meals) or 'all meals'})"
                    )
                    _confirm_msg = self._build_date_confirm_message(_corrected_slots or _pending_queue, language)
                    update_plan_state(owner_id=owner_id, confirmation_retry_count=_retry)

            _cur = get_plan_state(owner_id)
            _result = self._build_result(
                response_text=_confirm_msg,
                meal_plan=_cur.get("meal_plan", {}), meal_plan_slots=_cur.get("meal_plan_slots", {}),
                dish_ingredients=_cur.get("dish_ingredients", {}), shopping_list=_cur.get("shopping_list", []),
                schedule_id=_cur.get("schedule_id"), intent_result=intent_result,
            )
            _slots_for_card = _cur.get("pending_planning_queue") or _pending_queue
            return self._augment_ask_confirm_result(_result, _slots_for_card)

        # "unclear" — attempt date recovery via InitPlanningQueueSkill
        _unclear_retry = current_state.get("confirmation_retry_count", 0) + 1
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
        elif _unclear_retry >= 2:
            update_plan_state(
                owner_id=owner_id,
                pending_planning_queue=None, last_pipeline_action=None, confirmation_retry_count=None,
            )
            logger.info("[PLAN_AHEAD_PIPELINE] Phase 1b: 2 unclear retries → clearing pending, falling through.")
            return None
        else:
            update_plan_state(owner_id=owner_id, confirmation_retry_count=_unclear_retry)
            _confirm_msg = self._build_date_confirm_message(_pending_queue, language)
            logger.info("[PLAN_AHEAD_PIPELINE] Phase 1b: unclear reply (retry=%d), re-asking.", _unclear_retry)
        _cur = get_plan_state(owner_id)
        _result = self._build_result(
            response_text=_confirm_msg,
            meal_plan=_cur.get("meal_plan", {}), meal_plan_slots=_cur.get("meal_plan_slots", {}),
            dish_ingredients=_cur.get("dish_ingredients", {}), shopping_list=_cur.get("shopping_list", []),
            schedule_id=_cur.get("schedule_id"), intent_result=intent_result,
        )
        _slots_for_card = _recovery_queue if _recovery_queue else _pending_queue
        return self._augment_ask_confirm_result(_result, _slots_for_card)

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

        # Skip if we are already in some active phase.
        # Only "suggest_options" (pending option selection) and "ask" (pending clarification)
        # count as blocking active states. Completed actions like "modify"/"recommend"/"confirm"
        # must not block a fresh planning request.
        _ACTIVE_BLOCKING_ACTIONS = {"suggest_options", "ask"}
        if (
            current_state.get("pending_planning_queue")
            or is_currently_draft
            or current_state.get("last_pipeline_action") in _ACTIVE_BLOCKING_ACTIONS
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
        _result = self._build_result(
            response_text=_confirm_msg,
            meal_plan=_cur.get("meal_plan", {}), meal_plan_slots=_cur.get("meal_plan_slots", {}),
            dish_ingredients=_cur.get("dish_ingredients", {}), shopping_list=_cur.get("shopping_list", []),
            schedule_id=_cur.get("schedule_id"), intent_result=intent_result,
        )
        return self._augment_ask_confirm_result(_result, _new_queue)

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
        # ---- Free-tier limit check (new sessions only) ----
        _pre_state = get_plan_state(owner_id)
        _is_new_session = get_phase(_pre_state) == PipelinePhase.IDLE
        if _is_new_session and storage_client is not None:
            _sub = await storage_client.get_user_subscription(owner_id)
            if not _sub.get("is_active", False):
                _limits = await storage_client.check_usage_limits(owner_id)
                if not _limits.get("allowed", True):
                    _reason = _limits.get("reason", "daily_session_limit")
                    if _reason == "daily_token_limit":
                        _tokens_used = _limits.get("tokens_used", 0)
                        _tokens_limit = _limits.get("tokens_limit", 20000)
                        _reason_msg = (
                            f"You've reached today's AI usage limit "
                            f"({_tokens_used:,} / {_tokens_limit:,} tokens used). "
                            "Your limit resets at midnight — come back tomorrow to plan more meals!"
                        )
                    else:
                        _sessions_used = _limits.get("sessions_used", 0)
                        _sessions_limit = _limits.get("sessions_limit", 2)
                        _reason_msg = (
                            f"You've reached today's meal planning limit "
                            f"({_sessions_used} / {_sessions_limit} sessions used). "
                            "Your limit resets at midnight — come back tomorrow to plan more meals!"
                        )
                    return {
                        "response": _reason_msg,
                        "intent": "PLAN_AHEAD",
                        "confidence": 1.0,
                        "reasoning": f"Free tier limit reached: {_reason}",
                        "action": "limit_exceeded",
                        "action_data": {"limit_info": _limits},
                        "error_code": "LIMIT_EXCEEDED",
                        "error_detail": _reason,
                        "limit_info": _limits,
                    }
            # Track estimated token usage in background (non-blocking)
            import asyncio as _asyncio
            _asyncio.ensure_future(storage_client.add_token_usage(owner_id, 5000))

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
        # Decides ask / suggest_options / add / recommend / modify BEFORE the main LLM
        # context is built.  Passing is_draft lets the skill detect swap signals in draft
        # mode and pre-route to "modify" instead of the incorrect "add" path.
        _phase_str = get_phase(current_state).value
        _action_clf = await ClassifyMealActionSkill(self.gemini_api_url).execute(
            user_input, history or [],
            is_explicit_dish=_is_explicit_dish,
            pipeline_phase=_phase_str,
            is_draft=current_state.get("is_draft", False),
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
        _avoid = self._extract_session_avoidance_preferences(user_input)
        if _ext_ingredients or _ext_target_date or _ext_meal_type:
            update_active_context(
                owner_id,
                add_ingredients=_ext_ingredients,
                target_date=_ext_target_date,
                target_meal_type=_ext_meal_type,
            )
        if _avoid["avoid_dishes"] or _avoid["avoid_ingredients"] or _avoid["avoid_cuisines"]:
            update_active_context(
                owner_id,
                add_avoid_dishes=_avoid["avoid_dishes"],
                add_avoid_ingredients=_avoid["avoid_ingredients"],
                add_avoid_cuisines=_avoid["avoid_cuisines"],
            )

        # Read the (now possibly updated) active context for context injection
        _active_ctx = get_active_context(owner_id)

        # ---- Phase 1b: Date confirmation ----
        if result := await self._handle_date_confirmation(
            owner_id, user_input, current_state, _is_explicit_dish,
            user_timezone, language, intent_result, context=context,
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

        # ---- Layer 0d: Detect "再换一批" refresh signal ----
        # When the user asks for a completely new set of options (re-roll) two or
        # more consecutive times we escalate temperature to increase creativity.
        # refresh_count accumulates within the session; it is reset when the user
        # confirms a plan (action=confirm) or starts a brand-new planning session.
        _refresh_keywords = (
            "再换一批", "再换几个", "换一批", "换一换", "换点别的",
            "再来一组", "再推荐几个", "换个方向", "重新推荐", "全换掉",
            "another set", "different options", "refresh", "try again",
            "再来几个", "给我换", "全部换",
        )
        _is_refresh_request = any(kw in user_input.lower() for kw in _refresh_keywords)
        _current_refresh_count: int = current_state.get("refresh_count", 0)
        if _is_refresh_request and current_state.get("pending_options"):
            _current_refresh_count += 1
            update_plan_state(owner_id, refresh_count=_current_refresh_count)
            current_state = get_plan_state(owner_id)
            logger.info(
                "[PLAN_AHEAD_PIPELINE] refresh_count=%d — escalating temperature for user %d",
                _current_refresh_count, owner_id,
            )

        # Determine whether the escalation prompt should activate.
        # ALL THREE conditions must hold simultaneously:
        #   1. The CURRENT TURN is itself a refresh request — without this gate,
        #      a stale refresh_count of 2 persisted from earlier turns would
        #      mistakenly activate escalation on selection turns ("选方案2") or
        #      draft-edit turns, because pending_options is not cleared until
        #      AFTER the LLM call in the recommend handler.
        #   2. ≥2 consecutive refresh requests in this session.
        #   3. There are pending options — confirms we are in a re-roll turn,
        #      not in an unrelated planning phase.
        _is_escalation = (
            _is_refresh_request
            and _current_refresh_count >= 2
            and bool(current_state.get("pending_options"))
        )

        # ---- Step 2: Build context (via GenerateMealPlanSkill delegation shim) ----
        # All prompt engineering lives in GenerateMealPlanSkill._build_system_context.
        # The shim methods allow existing tests to mock at the pipeline level.
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
            is_escalation=_is_escalation,
        )
        system_context = self._inject_pending_options(system_context, current_state)

        # ---- Step 3: Single structured LLM call (via GenerateMealPlanSkill delegation shim) ----
        parsed = await self._call_llm(
            system_context, history, user_input,
            refresh_count=_current_refresh_count,
        )
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
        source_date = parsed.get("source_date")
        source_meal_time = parsed.get("source_meal_time")

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
            _is_zh = (_lang_map_local.get(language or "en", "zh") == "zh")

            # Extract what we already know from the profile
            _disliked: List[str] = (profile or {}).get("disliked_ingredients") or []

            # Build "already know" acknowledgement
            _known_parts: List[str] = []
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

            # Clear pending_options and reset refresh_count together.
            # refresh_count is reset here (not just on confirm) to prevent
            # _is_escalation from remaining True on every subsequent draft-edit
            # turn after the user has already selected a plan.
            update_plan_state(owner_id=owner_id, pending_options=None, refresh_count=0)
            logger.info("[PLAN_AHEAD_PIPELINE] recommend after suggest_options: clearing pending_options, resetting refresh_count.")

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
                action="confirm",
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
                refresh_count=0,  # reset refresh escalation counter after confirm
                merge=False,
            )
            logger.info(f"[PLAN_AHEAD_PIPELINE] confirm: draft persisted, schedule_id={schedule_id}")
            if storage_client is not None:
                try:
                    await storage_client.increment_meal_plan_session(owner_id)
                except Exception as _e:
                    logger.warning(f"Failed to increment session count: {_e}")

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
                _ns_is_zh = (language or "en") == "zh"
                _next_slot = _remaining_queue[0]
                if "|" in _next_slot:
                    _ns_date, _ns_meal = _next_slot.split("|", 1)
                    _ns_meal_label = (
                        {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(_ns_meal, _ns_meal)
                        if _ns_is_zh else _ns_meal
                    )
                    _day_abbrev_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
                    from datetime import date as _ns_date_cls
                    _ns_abbr = _day_abbrev_map.get(_ns_date_cls.fromisoformat(_ns_date).weekday(), "")
                    _resume_hint = (
                        f"\n\n还有 {len(_remaining_queue)} 餐待规划。"
                        f"下一餐：{_ns_date}（{_ns_abbr}）{_ns_meal_label}，回复「继续」→"
                        if _ns_is_zh else
                        f"\n\n{len(_remaining_queue)} meal(s) left to plan. "
                        f"Next: {_ns_date} ({_ns_abbr}) {_ns_meal_label} — reply \"continue\" →"
                    )
                else:
                    _resume_hint = (
                        f"\n\n还有 {len(_remaining_queue)} 餐待规划，回复「继续」→"
                        if _ns_is_zh else
                        f"\n\n{len(_remaining_queue)} meal(s) left to plan — reply \"continue\" →"
                    )
                # Strip any LLM-generated queue-hint line to prevent duplication.
                import re as _re
                user_message = _re.sub(r'\n*还有\s*\d+\s*餐待规划[^\n]*', '', user_message).rstrip()
                user_message = _re.sub(r'\n*\d+\s*meal\(s\) left to plan[^\n]*', '', user_message).rstrip()
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

            # Background auto-generation of cooking steps for all confirmed dishes.
            if schedule_id and draft_slots:
                _all_confirmed_dishes = list({
                    dish
                    for meals in draft_slots.values()
                    for mt_dishes in meals.values()
                    for dish in (mt_dishes if isinstance(mt_dishes, list) else [mt_dishes])
                    if dish
                })
                if _all_confirmed_dishes:
                    import asyncio as _asyncio
                    from app.agents.cooking_steps_agent import CookingStepsAgent as _CSA
                    _batch_ctx = {
                        "type": "plan_ahead",
                        "data": {
                            "schedule_id": schedule_id,
                            "meal_plan_slots": draft_slots,
                            "dish_ingredients": draft_di,
                        },
                    }
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] confirm: scheduling background step generation for "
                        "%d dish(es) in schedule_id=%s: %s",
                        len(_all_confirmed_dishes), schedule_id, _all_confirmed_dishes,
                    )
                    _asyncio.ensure_future(
                        _CSA().execute_batch(
                            dish_names=_all_confirmed_dishes,
                            owner_id=owner_id,
                            context=_batch_ctx,
                            schedule_id=schedule_id,
                            cooking_level=cooking_level,
                            language=language,
                            skip_if_exists=True,
                        )
                    )

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
                    # Collect dishes that were already in the plan BEFORE this turn.
                    # In draft mode, the LLM returns the full modified draft (not just the
                    # newly-added dish), so we must not strip pre-existing draft content.
                    # Example: draft has [番茄炖排骨, 蚝油生菜]; user says "加个米饭，不要生菜"
                    # → LLM correctly returns [番茄炖排骨, 米饭]; filter must NOT strip 番茄炖排骨.
                    #
                    # For non-draft MODIFY (dish swap), the same logic applies: the LLM
                    # correctly returns the full updated slot (sibling dishes carried over +
                    # the replacement dish).  Without this guard the filter would strip the
                    # siblings because they are not in _requested.
                    # Example: DB has [清炖牛骨汤, 蒜蓉炒蒜苔, 杂粮饭]; user says "换成蒜苔炒鸡蛋"
                    # → LLM returns [清炖牛骨汤, 蒜苔炒鸡蛋, 杂粮饭].
                    # → Guard preserves 清炖牛骨汤 and 杂粮饭; only strips truly new LLM extras.
                    _pre_draft_dishes: set = set()
                    if is_currently_draft:
                        for _pd_meals in (current_state.get("meal_plan_slots") or {}).values():
                            for _pd_dishes in _pd_meals.values():
                                _pre_draft_dishes.update(_pd_dishes)
                    elif action == "modify":
                        # Non-draft modify: protect dishes already in the persisted DB slot.
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

        # ---- Step 4e: Restore full dish list for update_ingredients / remove_ingredients ----
        # The LLM only returns the dish whose ingredients changed (e.g. "辣椒炒肉"),
        # but persist uses is_append=False which PUTs and OVERWRITES the whole slot.
        # Without this fix, sibling dishes (e.g. 麻婆豆腐, 米饭) are silently wiped.
        # Solution: replace the LLM's partial dish list with the full existing dish list
        # from DB state, and merge the updated dish_ingredients on top.
        if action in ("update_ingredients", "remove_ingredients") and new_meal_plan_slots:
            _ui_existing_slots = current_state.get("meal_plan_slots") or {}
            _ui_existing_ingr = current_state.get("dish_ingredients") or {}
            _ui_fixed_slots: dict = {}
            for _ui_date, _ui_mt_map in new_meal_plan_slots.items():
                _ui_existing_date = _ui_existing_slots.get(_ui_date) or {}
                _ui_fixed_mt: dict = {}
                for _ui_mt, _ui_llm_dishes in _ui_mt_map.items():
                    _ui_existing_dishes = _ui_existing_date.get(_ui_mt)
                    # Prefer existing dish list; fall back to LLM output if slot is new
                    _ui_fixed_mt[_ui_mt] = (
                        list(_ui_existing_dishes)
                        if _ui_existing_dishes
                        else list(_ui_llm_dishes)
                    )
                _ui_fixed_slots[_ui_date] = _ui_fixed_mt
            new_meal_plan_slots = _ui_fixed_slots
            new_meal_plan = {
                d: ", ".join(
                    dish
                    for mt_dishes in meals.values()
                    for dish in (mt_dishes if isinstance(mt_dishes, list) else [mt_dishes])
                )
                for d, meals in _ui_fixed_slots.items()
            }
            # Merge LLM's ingredient updates into the full existing ingredients dict
            _ingr_merged = dict(_ui_existing_ingr)
            _ingr_merged.update(new_dish_ingredients)
            new_dish_ingredients = _ingr_merged
            shopping_list = compute_shopping_list(new_dish_ingredients)
            logger.info(
                "[PLAN_AHEAD_PIPELINE] %s: restored full dish list from existing state "
                "(sibling-dish preservation). dates=%s",
                action, list(_ui_fixed_slots.keys()),
            )

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
            _meal_en_label = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner"}
            _is_zh_q = (language or "en") == "zh"
            if _queue_target_slot and action == "recommend":
                _remaining_queue = _meal_queue[1:]
                _slot_num_done = _meal_total - len(_meal_queue) + 1
                # Parse current slot for display
                if "|" in _queue_target_slot:
                    _cur_date_str, _cur_meal = _queue_target_slot.split("|", 1)
                    _cur_meal_label = (_meal_zh_label.get(_cur_meal, _cur_meal) if _is_zh_q else _meal_en_label.get(_cur_meal, _cur_meal.capitalize()))
                else:
                    _cur_date_str, _cur_meal, _cur_meal_label = _queue_target_slot, "", ""

                # Replace LLM's user_message with a single-slot summary so other
                # already-planned meals for the same date don't bleed into the response.
                _slot_dishes = (new_meal_plan_slots or {}).get(_cur_date_str, {}).get(_cur_meal, [])
                if _slot_dishes:
                    _sep = "、" if _is_zh_q else ", "
                    _dish_list = _sep.join(_slot_dishes)
                    user_message = (
                        f"好的，已为您规划 {_cur_date_str} {_cur_meal_label}：{_dish_list}。"
                        if _is_zh_q else
                        f"Here's the plan for {_cur_date_str} {_cur_meal_label}: {_dish_list}."
                    )
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
                        _next_meal_label = (_meal_zh_label.get(_next_meal, _next_meal) if _is_zh_q else _meal_en_label.get(_next_meal, _next_meal.capitalize()))
                        _next_abbr = _day_abbrev.get(_qdcls.fromisoformat(_next_date_str).weekday(), "")
                        _next_label = (
                            f"{_next_date_str}（{_next_abbr}）{_next_meal_label}"
                            if _is_zh_q else
                            f"{_next_date_str} ({_next_abbr}) {_next_meal_label}"
                        )
                    else:
                        _next_label = _next_slot
                    _progress_line = (
                        f"✅ 第 {_slot_num_done}/{_meal_total} 餐 — {_cur_date_str} {_cur_meal_label}已确认。"
                        f"\n下一餐：{_next_label}，回复「继续」→"
                        if _is_zh_q else
                        f"✅ Meal {_slot_num_done}/{_meal_total} — {_cur_date_str} {_cur_meal_label} confirmed."
                        f"\nNext: {_next_label} — reply \"continue\" →"
                    )
                    user_message = user_message + "\n\n---\n" + _progress_line
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
                    user_message = user_message + (
                        f"\n\n🎉 全部 {_meal_total} 餐规划已完成！回复「确认」将计划保存到日历。"
                        if _is_zh_q else
                        f"\n\n🎉 All {_meal_total} meals planned! Reply \"confirm\" to save the plan to your calendar."
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

            # For move operations: carry cooking steps from source to destination so
            # CookingStepsAgent doesn't waste tokens regenerating already-saved steps.
            # Must be fetched BEFORE _persist(), while the source schedule still exists.
            _source_dish_data: Dict[str, Any] = {}
            if source_date and source_meal_time and action == "modify":
                _mv_schedules = await storage_client.get_user_schedules(owner_id)
                for _mv_s in _mv_schedules:
                    _, _, _, _mv_slots = storage_client._extract_meal_plan_from_schedule(_mv_s)
                    if source_date in _mv_slots and source_meal_time in (_mv_slots.get(source_date) or {}):
                        _source_dish_data = storage_client._extract_existing_dish_data(_mv_s)
                        break

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
                extra_existing_dish_data=_source_dish_data or None,
            )
            if new_sid:
                schedule_id = new_sid

            # ---- Step 6b: Move operation — delete source slot ----
            # When the LLM sets source_date, the user wants to MOVE a meal.
            # The destination was just persisted above; now delete the origin slot.
            if source_date and action == "modify":
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] move: deleting source slot %s %s",
                    source_date, source_meal_time or "(all)",
                )
                _src_existing_slots = (current_state.get("meal_plan_slots") or {})
                _src_date_slots = _src_existing_slots.get(source_date, {})
                _src_remaining_mt = (
                    {mt: v for mt, v in _src_date_slots.items()
                     if mt != source_meal_time and v}
                    if source_meal_time else {}
                )
                if source_meal_time and _src_remaining_mt:
                    # Only one meal_time removed — update the source date in DB.
                    _src_old_mp = current_state.get("meal_plan") or {}
                    _src_remaining_dishes: List[str] = [
                        d for mt_dishes in _src_remaining_mt.values() for d in mt_dishes
                    ]
                    _src_new_mp = {**_src_old_mp, source_date: ", ".join(_src_remaining_dishes)}
                    _src_new_slots = {**_src_existing_slots, source_date: _src_remaining_mt}
                    _src_di = current_state.get("dish_ingredients") or {}
                    _src_kept_dishes = {d for mt_dishes in _src_remaining_mt.values() for d in mt_dishes}
                    _src_new_di = {k: v for k, v in _src_di.items() if k in _src_kept_dishes}
                    await self.plan_ahead_agent.persist_meal_plan(
                        meal_plan=_src_new_mp,
                        shopping_list=compute_shopping_list(_src_new_di),
                        owner_id=owner_id,
                        existing_schedule_id=current_state.get("schedule_id"),
                        storage_client=storage_client,
                        user_timezone=user_timezone,
                        event_type="meal_plan_draft",
                        dish_ingredients=_src_new_di,
                        meal_plan_slots=_src_new_slots,
                        is_append=False,
                    )
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] move: source %s updated — removed %s, kept %s",
                        source_date, source_meal_time, list(_src_remaining_mt.keys()),
                    )
                else:
                    # Entire source date is now empty — delete its schedule.
                    await self._delete_orphaned_schedules(
                        owner_id, [source_date], storage_client
                    )
                    logger.info(
                        "[PLAN_AHEAD_PIPELINE] move: source date %s deleted (no remaining slots)",
                        source_date,
                    )

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

        # Background auto-generation of cooking steps for all newly added dishes.
        # Runs fire-and-forget so it never delays the chat response.
        if action in ("add", "modify", "generate") and schedule_id and new_meal_plan_slots:
            _all_dishes = list({
                dish
                for meals in new_meal_plan_slots.values()
                for mt_dishes in meals.values()
                for dish in (mt_dishes if isinstance(mt_dishes, list) else [mt_dishes])
                if dish
            })
            if _all_dishes:
                import asyncio as _asyncio
                from app.agents.cooking_steps_agent import CookingStepsAgent as _CSA
                _batch_ctx = {
                    "type": "plan_ahead",
                    "data": {
                        "schedule_id": schedule_id,
                        "meal_plan_slots": new_meal_plan_slots,
                        "dish_ingredients": new_dish_ingredients,
                    },
                }
                _asyncio.ensure_future(
                    _CSA().execute_batch(
                        dish_names=_all_dishes,
                        owner_id=owner_id,
                        context=_batch_ctx,
                        schedule_id=schedule_id,
                        cooking_level=cooking_level,
                        language=language,
                        skip_if_exists=True,
                    )
                )
                logger.info(
                    "[PLAN_AHEAD_PIPELINE] Triggered background step generation for %d dish(es): %s",
                    len(_all_dishes), _all_dishes,
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
