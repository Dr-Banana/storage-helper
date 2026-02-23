"""
Scheduling Agent module (OOD): schedule lookup, mutation, and plan-ahead agents.

- TimeRange, ScheduleSessionContext: value objects for time range and session.
- ScheduleRangeDecider: fetch intent + time range via LLM.
- SchedulingResponseGenerator: build context, fetch/filter schedules by range.
- ScheduleMutationIntent, ScheduleMutationParser, ScheduleMutationApplier: add/delete/modify schedule by user semantics (OOD).
- PlanAheadAgent: meal plan sync, modification, persistence.
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional, List, Tuple, Dict, Any, Literal
import logging
import json
import os
import re
import httpx

# --- NEW IMPORTS FOR DATABASE WRITE ---
try:
    from app.services.schedule import ScheduleService
    from app.schemas.schedule import ScheduleCreate
    # 假设你的数据库会话生成器在这里，请根据实际项目结构调整
    from app.core.database import SessionLocal 
    DB_SERVICE_AVAILABLE = True
except ImportError:
    DB_SERVICE_AVAILABLE = False
    # 如果找不到 SessionLocal，可以在这里定义一个占位符或报错
    SessionLocal = None

# Import plan_ahead_state functions for PlanAheadAgent
try:
    from app.modules.plan_ahead_state import get_plan_state, update_plan_state
    PLAN_AHEAD_STATE_AVAILABLE = True
except ImportError:
    PLAN_AHEAD_STATE_AVAILABLE = False
    logger.warning("[SCHEDULING AGENT] plan_ahead_state module not available")

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Structured output schema for PLAN_AHEAD (replaces free-text PLAN_JSON)
# ---------------------------------------------------------------------------

PLAN_AHEAD_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add", "modify", "remove", "update_ingredients", "remove_ingredients", "view", "ask", "recommend", "confirm"],
        },
        "target_date": {"type": "string"},
        "meal_time": {"type": "string", "enum": ["breakfast", "lunch", "dinner"]},
        "recommendation_reason": {"type": "string", "nullable": True},
        "user_message": {"type": "string"},
        "meal_entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "meal_time": {"type": "string", "enum": ["breakfast", "lunch", "dinner"]},
                    "dishes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "ingredients": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name":     {"type": "string"},
                                            "category": {
                                                "type": "string",
                                                "enum": ["vegetable", "protein", "dairy", "grain", "spice", "other"],
                                            },
                                            "quantity": {"type": "string"},
                                        },
                                        "required": ["name", "category"],
                                    },
                                },
                            },
                            "required": ["name", "ingredients"],
                        },
                    },
                },
                "required": ["date", "meal_time", "dishes"],
            },
        },
    },
    "required": ["action", "user_message", "meal_entries"],
}


def compute_shopping_list(dish_ingredients: Dict[str, List[Any]]) -> List[Dict[str, str]]:
    """
    Compute deduplicated shopping list from dish_ingredients map.
    Supports both new dict format {"name":..,"category":..,"quantity":..}
    and legacy plain-string format for backward compatibility.
    Returns a list of dicts sorted by name.
    """
    seen: Dict[str, Dict[str, str]] = {}
    for ings in (dish_ingredients or {}).values():
        for ing in (ings or []):
            if isinstance(ing, dict):
                name = (ing.get("name") or "").strip()
                if not name:
                    continue
                if name not in seen:
                    seen[name] = {
                        "name": name,
                        "category": ing.get("category") or "other",
                        "quantity": ing.get("quantity") or "",
                    }
            elif isinstance(ing, str) and ing.strip():
                name = ing.strip()
                if name not in seen:
                    seen[name] = {"name": name, "category": "other", "quantity": ""}
    return sorted(seen.values(), key=lambda x: x["name"])


# ---------------------------------------------------------------------------
# Context and data structures
# ---------------------------------------------------------------------------


@dataclass
class TimeRange:
    """Structured time range for requesting the user's saved schedules (start/end)."""

    start: datetime
    end: datetime

    def to_date_range(self) -> Tuple[date, date]:
        """Return (start_date, end_date) for date-based filtering."""
        return self.start.date(), self.end.date()


@dataclass
class ScheduleSessionContext:
    """
    Holds the current query and prior chat history for use by sub-agents.
    """

    current_query: str
    history: List[dict] = field(default_factory=list)
    user_timezone: Optional[str] = None

    def get_recent_turns(self, n: int = 10) -> List[dict]:
        """Last n conversation turns (one user or assistant message per turn)."""
        return self.history[-n:] if self.history else []

    def get_user_messages_only(self, max_chars: int = 2000) -> str:
        """
        Extract only user messages from history (excluding agent/assistant messages).
        Includes current query as the most recent user message.
        
        Returns:
            Concatenated string of user messages, with current query last.
        """
        user_messages = []
        
        # Extract user messages from history
        for turn in self.get_recent_turns():
            role = turn.get("role", "").lower()
            # Check if this is a user message (not agent/assistant/model)
            if role in ("user", "human") and role not in ("assistant", "agent", "model", "system"):
                content = turn.get("content", "") or turn.get("message", "") or ""
                if isinstance(content, str) and content.strip():
                    user_messages.append(content.strip())
        
        # Add current query as the most recent user message
        if self.current_query and self.current_query.strip():
            user_messages.append(self.current_query.strip())
        
        text = " ".join(user_messages)
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text

    def full_text_for_decider(self, max_chars: int = 2000) -> str:
        """Concatenate recent history + current_query for the decider to infer intent."""
        parts = []
        for turn in self.get_recent_turns():
            role = turn.get("role", "")
            content = turn.get("content", "") or turn.get("message", "") or ""
            if isinstance(content, str) and content.strip():
                parts.append(f"{role}: {content.strip()}")
        parts.append(f"user: {self.current_query.strip()}")
        text = "\n".join(parts)
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text


# ---------------------------------------------------------------------------
# Schedule range decider: fetch intent + time range via LLM only
# ---------------------------------------------------------------------------


class ScheduleRangeDecider:
    """
    Decides whether to fetch schedules and parses time range using LLM only.
    Single responsibility: intent detection + time range parsing (LLM).
    """

    INTENT_FETCH = [
        "日程", "安排", "计划", "行程", "日历", "schedule", "plans", "agenda",
        "有什么", "看看", "查", "查看", "显示", "列出", "list", "show", "check",
        "下周", "next week", "本周", "这周", "this week", "今天", "明天", "后天",
        "today", "tomorrow", "周一", "周二", "周三", "周四", "周五", "周六", "周日",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "week", "我的安排", "我的日程", "我的计划",
    ]
    INTENT_NO_FETCH = ["你好", "嗨", "hi", "hello", "在吗", "谢谢", "thanks", "再见", "bye"]

    async def decide(
        self, context: ScheduleSessionContext, api_url: Optional[str] = None
    ) -> Optional[TimeRange]:
        """
        Decide if we should fetch schedules; if yes, parse time range via LLM.
        Requires api_url for time parsing. On LLM failure, returns default 7-day range.
        """
        text = context.full_text_for_decider().lower()
        query = (context.current_query or "").strip().lower()

        for no_k in self.INTENT_NO_FETCH:
            if query == no_k or query == no_k.strip():
                return None
        if len(query) <= 6 and not any(k in text for k in self.INTENT_FETCH):
            return None
        if not any(kw in text for kw in self.INTENT_FETCH):
            logger.info(f"[SCHEDULING AGENT] No fetch intent: {text[:100]}")
            return None

        logger.info(
            f"[SCHEDULING AGENT] Parsing time range (LLM): query={query[:80]}, history_len={len(context.history)}"
        )
        time_range: Optional[TimeRange] = None
        if api_url:
            time_range = await self._parse_range_llm(
                context.current_query or "",
                context.user_timezone,
                api_url,
            )
        if time_range:
            logger.info("[SCHEDULING AGENT] Time range (LLM): %s ~ %s", time_range.start, time_range.end)
        else:
            logger.info("[SCHEDULING AGENT] Time range fallback: default 7 days")
            time_range = self._default_range(context.user_timezone)
        return time_range

    async def _parse_range_llm(
        self, query: str, timezone: Optional[str], api_url: str
    ) -> Optional[TimeRange]:
        """Parse user time phrase to start/end via LLM. Input: query + current time."""
        now = self._now_tz(timezone)
        now_str = now.strftime("%Y-%m-%d %H:%M %Z") if now.tzinfo else now.strftime("%Y-%m-%d %H:%M UTC")
        tz_label = timezone or "UTC"
        prompt = f"""You are a time-range parser. Given the user's time phrase and current time, output the requested period.

Current time (user timezone): {now_str} (timezone: {tz_label})
User's time phrase: "{query}"

Rules: Return start and end. "This week" = Mon–Sun of current week. "Next week" = Mon–Sun of next week. "First week of March" = week containing March 1. "Today" = that day (start 00:00, end next day 00:00). Output ONLY valid JSON: {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}} (start inclusive, end exclusive).
Example for "next week" when today 2026-02-11: {{"start": "2026-02-16", "end": "2026-02-23"}}

JSON:"""
        try:
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(api_url, headers={"Content-Type": "application/json"}, json=payload)
                resp.raise_for_status()
                data = resp.json()
            raw = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not raw or not raw.strip():
                return None
            try:
                parsed = json.loads(raw.strip())
            except json.JSONDecodeError:
                return None
            if not parsed:
                return None
            start_s, end_s = parsed.get("start"), parsed.get("end")
            if not start_s or not end_s:
                return None
            start_dt = datetime.strptime(start_s[:10], "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = datetime.strptime(end_s[:10], "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
            return TimeRange(start=start_dt, end=end_dt)
        except Exception as e:
            logger.warning(f"[SCHEDULING AGENT] LLM time parse failed: {e}")
            return None

    def _now_tz(self, timezone: Optional[str] = None) -> datetime:
        try:
            from zoneinfo import ZoneInfo
            if timezone:
                return datetime.now(ZoneInfo(timezone))
        except Exception as e:
            logger.warning(f"[SCHEDULING AGENT] Timezone {timezone}: {e}")
        return datetime.utcnow()

    def _default_range(self, timezone: Optional[str] = None) -> TimeRange:
        """Default: 7 days from today 00:00 in user timezone."""
        now = self._now_tz(timezone)
        start = datetime.combine(now.date(), datetime.min.time())
        return TimeRange(start=start, end=start + timedelta(days=7))


# ---------------------------------------------------------------------------
# Schedule context builder: format context, fetch and filter schedules
# ---------------------------------------------------------------------------


class SchedulingResponseGenerator:
    """
    Builds schedule context for LLM: decides range (via ScheduleRangeDecider),
    fetches/filters schedules, formats context message.
    """

    def __init__(self):
        self._range_decider = ScheduleRangeDecider()
        logger.info("[SCHEDULING AGENT] SchedulingResponseGenerator initialized")

    async def generate_context(
        self,
        context: ScheduleSessionContext,
        schedules: Optional[List[Dict[str, Any]]] = None,
        time_range: Optional[TimeRange] = None,
        api_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate response context for schedule queries.
        
        Args:
            context: Session context with current query and history.
            schedules: Optional list of schedule dictionaries. If None, will decide
                      whether to fetch schedules based on context.
            time_range: Optional pre-computed time range. If provided, skips decide() call
                       to avoid redundant computation.
            api_url: Optional Gemini API URL for LLM-based time parsing. When provided,
                    time range is inferred via LLM (user phrase + current time) first.
        
        Returns:
            Dictionary containing:
            - should_fetch: bool - whether schedules should be fetched
            - time_range: Optional[TimeRange] - time range to fetch if should_fetch is True
            - context_message: str - formatted context message for LLM
            - schedules_data: Optional[List[Dict]] - schedule data if available
        """
        logger.info(
            f"[SCHEDULING AGENT] SchedulingResponseGenerator.generate_context called for query: {context.current_query[:50]}, "
            f"has_precomputed_time_range={time_range is not None}, has_schedules={schedules is not None}"
        )

        # Step 1: Decide if we need to fetch schedules (only if time_range not provided)
        if time_range is None:
            logger.info("[SCHEDULING AGENT] Calling ScheduleRangeDecider.decide()")
            time_range = await self._range_decider.decide(context, api_url=api_url)
        else:
            logger.info(
                f"[SCHEDULING AGENT] Using pre-computed time_range: {time_range.start} ~ {time_range.end}"
            )
        
        should_fetch = time_range is not None

        logger.info(
            f"[SCHEDULING AGENT] Decision: should_fetch={should_fetch}, "
            f"time_range={time_range.start if time_range else None} ~ {time_range.end if time_range else None}"
        )

        # Step 2: Format context message
        context_message = self._format_context_message(
            context, schedules, time_range, should_fetch
        )

        result = {
            "should_fetch": should_fetch,
            "time_range": time_range,
            "context_message": context_message,
            "schedules_data": schedules,
        }

        logger.info(
            f"[SCHEDULING AGENT] SchedulingResponseGenerator result: should_fetch={should_fetch}, "
            f"schedules_count={len(schedules) if schedules else 0}"
        )

        return result

    def _filter_schedule_content_by_date_range(
        self,
        schedules: List[Dict[str, Any]],
        time_range: TimeRange,
    ) -> List[Dict[str, Any]]:
        """
        Filter date-keyed content (e.g. meal_plan) inside schedules so only dates
        within time_range are included. Prevents "next week" query from showing this week's plan.
        """
        start_date = time_range.start.date()
        end_date = time_range.end.date()
        result = []
        for s in schedules:
            s = dict(s)
            meta = (s.get("metadata") or {}).copy()
            # Old format: metadata.meal_plan = { "2026-02-10": "..." }
            meal_plan = meta.get("meal_plan") or {}
            if isinstance(meal_plan, dict):
                filtered_mp = {
                    k: v for k, v in meal_plan.items()
                    if self._date_str_in_range(k, start_date, end_date)
                }
                meta["meal_plan"] = filtered_mp
            # New format: metadata.features with meal_plan inside
            features = meta.get("features") or {}
            if isinstance(features, dict):
                fmp = features.get("meal_plan") or {}
                if isinstance(fmp, dict):
                    filtered_fmp = {
                        k: v for k, v in fmp.items()
                        if self._date_str_in_range(k, start_date, end_date)
                    }
                    meta["features"] = {**features, "meal_plan": filtered_fmp}
            s["metadata"] = meta
            result.append(s)
        return result

    @staticmethod
    def _date_str_in_range(date_str: str, start_date: date, end_date: date) -> bool:
        """True if date_str (YYYY-MM-DD) is in [start_date, end_date)."""
        try:
            y, m, d = map(int, date_str.split("-")[:3])
            dte = date(y, m, d)
            return start_date <= dte < end_date
        except (ValueError, TypeError):
            return True

    def _format_context_message(
        self,
        context: ScheduleSessionContext,
        schedules: Optional[List[Dict[str, Any]]],
        time_range: Optional[TimeRange],
        should_fetch: bool,
    ) -> str:
        """Format context message for LLM based on schedule data."""
        parts = []

        if should_fetch:
            if time_range:
                parts.append(
                    f"\n=== SCHEDULE QUERY ==="
                )
                parts.append(
                    f"User is asking about their schedule for: {time_range.start.strftime('%Y-%m-%d %H:%M')} to {time_range.end.strftime('%Y-%m-%d %H:%M')}"
                )

            if schedules:
                # Only show schedule content (e.g. meal_plan dates) that fall in the requested range
                schedules_to_show = self._filter_schedule_content_by_date_range(schedules, time_range) if time_range else schedules
                parts.append(f"\nFound {len(schedules_to_show)} schedule(s) in this time range:")
                parts.append("\nSCHEDULE DATA:")
                parts.append(json.dumps(schedules_to_show, indent=2, ensure_ascii=False, default=str))
                parts.append(
                    "\nCRITICAL: Base your response ONLY on the schedule data above. "
                    "If the list is empty, explicitly state that the user has no schedules in this time range."
                )
            else:
                parts.append(
                    "\nSCHEDULE DATA: No schedules found in the requested time range."
                )
                parts.append(
                    "CRITICAL: Tell the user they have no schedules in this time period."
                )
        else:
            # No fetch needed - user is not asking about schedules
            parts.append(
                "\n=== SCHEDULE CONTEXT ==="
            )
            parts.append(
                "User query does not require fetching schedule data."
            )

        return "\n".join(parts)

    async def fetch_schedules_in_range(
        self,
        owner_id: int,
        time_range: TimeRange,
        storage_client: Any,
    ) -> List[Dict[str, Any]]:
        """
        Fetch user's schedules from database and filter by time range.
        
        Args:
            owner_id: User/owner id.
            time_range: Time range (start, end) to filter schedules.
            storage_client: PipelineStorage (or similar) with get_user_schedules(owner_id).
        
        Returns:
            List of schedule dicts that overlap with the given time range.
        """
        logger.info(
            f"[SCHEDULING AGENT] fetch_schedules_in_range: owner_id={owner_id}, "
            f"range={time_range.start} ~ {time_range.end}"
        )
        try:
            all_schedules = await storage_client.get_user_schedules(owner_id)
        except Exception as e:
            logger.warning(f"[SCHEDULING AGENT] get_user_schedules failed: {e}")
            return []
        start_date = time_range.start.date()
        end_date = time_range.end.date()
        filtered = []
        for s in all_schedules:
            # Prefer plan implementation dates (meal_plan date keys) over scheduled_time/title
            plan_dates = self._get_plan_implementation_dates(s)
            if plan_dates:
                # Include if any plan date falls in [start_date, end_date)
                if any(
                    start_date <= d < end_date
                    for d in plan_dates
                ):
                    filtered.append(s)
                    continue
            # No plan dates: use scheduled_time / end_time
            sched_time = s.get("scheduled_time")
            end_time = s.get("end_time") or sched_time
            if not sched_time:
                continue
            if isinstance(sched_time, str):
                try:
                    sched_time = datetime.fromisoformat(sched_time.replace("Z", "+00:00"))
                except Exception as e:
                    logger.warning(f"[SCHEDULING AGENT] Failed to parse scheduled_time: {sched_time}, error: {e}")
                    continue
            if isinstance(end_time, str):
                try:
                    end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                except Exception as e:
                    logger.warning(f"[SCHEDULING AGENT] Failed to parse end_time: {end_time}, error: {e}")
                    end_time = sched_time
            if not isinstance(sched_time, datetime) or not isinstance(end_time, datetime):
                continue
            if (
                (time_range.start <= sched_time <= time_range.end)
                or (time_range.start <= end_time <= time_range.end)
                or (sched_time <= time_range.start and end_time >= time_range.end)
            ):
                filtered.append(s)
        logger.info(f"[SCHEDULING AGENT] fetch_schedules_in_range: got {len(filtered)} schedule(s) in range")
        return filtered

    def _get_plan_implementation_dates(self, schedule: Dict[str, Any]) -> List[date]:
        """
        Get the actual implementation dates of a plan from metadata (meal_plan date keys).
        Returns a list of date objects; empty if not a plan-with-dates schedule.
        """
        out: List[date] = []
        meta = schedule.get("metadata") or {}
        if not isinstance(meta, dict):
            return out
        # Old format: metadata.meal_plan = { "2026-02-17": "..." }
        meal_plans_to_check: List[Any] = [meta.get("meal_plan")]
        features = meta.get("features")
        if isinstance(features, dict):
            meal_plans_to_check.append(features.get("meal_plan"))
        for mp in meal_plans_to_check:
            if not isinstance(mp, dict):
                continue
            for k in mp:
                try:
                    y, m, d = map(int, str(k).split("-")[:3])
                    out.append(date(y, m, d))
                except (ValueError, TypeError):
                    continue
        return out

    def get_agent_capabilities(self) -> Dict[str, Any]:
        """
        Return information about the capabilities of scheduling agents.
        Used for logging and debugging.
        """
        return {
            "agents": [
                {"name": "ScheduleRangeDecider", "purpose": "Fetch intent + time range via LLM", "output": "Optional[TimeRange]"},
                {"name": "SchedulingResponseGenerator", "purpose": "Build schedule context, fetch/filter by range", "output": "Dict"},
                {"name": "ScheduleMutationParser", "purpose": "Parse add/delete/modify intent from user semantics via LLM", "output": "Optional[ScheduleMutationIntent]"},
                {"name": "ScheduleMutationApplier", "purpose": "Apply add/delete/modify to storage", "output": "Dict"},
                {"name": "ScheduleMutationHandler", "purpose": "Facade: parse + apply schedule mutation", "output": "Dict"},
            ],
            "capabilities": [
                "Detect schedule intent from query/history",
                "Parse time range via LLM (user phrase + current time)",
                "Fetch schedules by range (plan dates or scheduled_time)",
                "Format and filter schedule data for LLM context",
                "Add/delete/modify specific schedule by user semantics (LLM intent + storage apply)",
            ],
        }


# ---------------------------------------------------------------------------
# Schedule mutation (add / delete / modify) by user semantics — OOD
# ---------------------------------------------------------------------------


@dataclass
class ScheduleMutationIntent:
    """
    Value object: user intent to add, delete, or modify a schedule.
    """
    operation: Literal["add", "delete", "modify"]
    schedule_id: Optional[int] = None  # for delete/modify: which schedule
    title: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    description: Optional[str] = None
    event_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ScheduleMutationParser:
    """
    Parses user query + current schedules into ScheduleMutationIntent via LLM.
    Single responsibility: semantic extraction (add/delete/modify which schedule).
    """

    def __init__(self, api_url: Optional[str] = None):
        self._api_url = api_url

    async def parse(
        self,
        context: ScheduleSessionContext,
        schedules: List[Dict[str, Any]],
        api_url: Optional[str] = None,
    ) -> Optional[ScheduleMutationIntent]:
        """
        Extract add/delete/modify intent from user query.
        When schedules are provided, LLM can resolve "delete the one on Monday" to schedule_id.
        """
        url = api_url or self._api_url
        if not url:
            logger.warning("[SCHEDULING AGENT] ScheduleMutationParser: no api_url")
            return None
        now = self._now_tz(context.user_timezone)
        now_str = now.strftime("%Y-%m-%d %H:%M") + (f" ({context.user_timezone})" if context.user_timezone else " UTC")
        schedules_summary = self._summarize_schedules(schedules)
        prompt = f"""You are a schedule intent parser. Given the user message and current schedules, output ONE action: add, delete, or modify.

Current time: {now_str}

User message: "{context.current_query}"

Current schedules (id, title, scheduled_time):
{schedules_summary}

Rules:
- "delete ... on Monday" / "remove the meeting on Feb 20" -> operation "delete" and set schedule_id to the matching schedule's id.
- "add a meeting on Feb 20" / "create ..." -> operation "add", set title, scheduled_time (YYYY-MM-DDTHH:MM:SS), optionally end_time, description, event_type.
- "change the title of ..." / "update ..." / "reschedule ..." -> operation "modify", set schedule_id and only the fields to change (title, scheduled_time, end_time, description).
- If no clear add/delete/modify intent, output operation "none" and no other fields.

Output ONLY valid JSON. Use null for missing fields. Format:
{{"operation": "add"|"delete"|"modify"|"none", "schedule_id": null or int, "title": null or string, "scheduled_time": null or "YYYY-MM-DDTHH:MM:SS", "end_time": null or "YYYY-MM-DDTHH:MM:SS", "description": null or string, "event_type": null or string}}

JSON:"""
        try:
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
            }
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(url, headers={"Content-Type": "application/json"}, json=payload)
                resp.raise_for_status()
                data = resp.json()
            raw = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not raw or not raw.strip():
                return None
            try:
                parsed = json.loads(raw.strip())
            except json.JSONDecodeError:
                return None
            if not parsed:
                return None
            op = (parsed.get("operation") or "").lower()
            if op == "none" or op not in ("add", "delete", "modify"):
                return None
            schedule_id = parsed.get("schedule_id")
            if schedule_id is not None:
                schedule_id = int(schedule_id)
            scheduled_time = None
            if parsed.get("scheduled_time"):
                try:
                    scheduled_time = datetime.fromisoformat(parsed["scheduled_time"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            end_time = None
            if parsed.get("end_time"):
                try:
                    end_time = datetime.fromisoformat(parsed["end_time"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            return ScheduleMutationIntent(
                operation=op,
                schedule_id=schedule_id,
                title=parsed.get("title"),
                scheduled_time=scheduled_time,
                end_time=end_time,
                description=parsed.get("description"),
                event_type=parsed.get("event_type"),
                metadata=parsed.get("metadata"),
            )
        except Exception as e:
            logger.warning(f"[SCHEDULING AGENT] ScheduleMutationParser parse failed: {e}")
            return None

    @staticmethod
    def _summarize_schedules(schedules: List[Dict[str, Any]], max_entries: int = 30) -> str:
        lines = []
        for s in schedules[:max_entries]:
            sid = s.get("id")
            title = (s.get("title") or "")[:40]
            st = s.get("scheduled_time") or ""
            if isinstance(st, str) and len(st) > 19:
                st = st[:19]
            lines.append(f"  id={sid}, title={title!r}, scheduled_time={st}")
        return "\n".join(lines) if lines else "  (none)"

    def _now_tz(self, timezone: Optional[str] = None) -> datetime:
        try:
            from zoneinfo import ZoneInfo
            if timezone:
                return datetime.now(ZoneInfo(timezone))
        except Exception:
            pass
        return datetime.utcnow()


class ScheduleMutationApplier:
    """
    Applies ScheduleMutationIntent via storage: create_schedule, update_schedule, delete_schedule.
    Single responsibility: persistence of add/delete/modify.
    """

    async def apply(
        self,
        intent: ScheduleMutationIntent,
        owner_id: int,
        storage_client: Any,
    ) -> Dict[str, Any]:
        """
        Apply intent to storage. Returns { "ok": bool, "schedule_id": int or None, "error": str or None }.
        """
        if intent.operation == "delete":
            return await self._apply_delete(intent, owner_id, storage_client)
        if intent.operation == "add":
            return await self._apply_add(intent, owner_id, storage_client)
        if intent.operation == "modify":
            return await self._apply_modify(intent, owner_id, storage_client)
        return {"ok": False, "schedule_id": None, "error": "unknown operation"}

    async def _apply_delete(
        self, intent: ScheduleMutationIntent, owner_id: int, storage_client: Any
    ) -> Dict[str, Any]:
        if intent.schedule_id is None:
            return {"ok": False, "schedule_id": None, "error": "delete requires schedule_id"}
        ok = await storage_client.delete_schedule(
            schedule_id=intent.schedule_id,
            owner_id=owner_id,
        )
        logger.info("[SCHEDULING AGENT] ScheduleMutationApplier: delete id=%s ok=%s", intent.schedule_id, ok)
        return {"ok": ok, "schedule_id": intent.schedule_id, "error": None if ok else "delete failed"}

    async def _apply_add(
        self, intent: ScheduleMutationIntent, owner_id: int, storage_client: Any
    ) -> Dict[str, Any]:
        """
        Updated: Apply ADD using ScheduleService and ScheduleCreate directly, 
        bypassing potentially broken storage_client wrapper.
        """
        title = (intent.title or "").strip() or "Untitled"
        scheduled_time = intent.scheduled_time
        if not scheduled_time:
            scheduled_time = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Determine end_time (default to 1 hour after start if not provided)
        end_time = intent.end_time
        if not end_time and scheduled_time:
            end_time = scheduled_time + timedelta(hours=1)
            
        logger.info(f"[SCHEDULING AGENT] ScheduleMutationApplier: Adding schedule via Service - title={title!r}, time={scheduled_time}")

        # Check if database services are imported correctly
        if not DB_SERVICE_AVAILABLE or not SessionLocal:
             logger.error("[SCHEDULING AGENT] DB Service or SessionLocal not available for direct write.")
             return {"ok": False, "schedule_id": None, "error": "Database service unavailable"}

        try:
            # 1. Create the Pydantic schema object required by ScheduleService
            schedule_data = ScheduleCreate(
                title=title,
                event_type=intent.event_type or "event",
                description=intent.description,
                scheduled_time=scheduled_time,
                end_time=end_time,
                location=None, # Defaulting as it's not in intent
                priority="medium", # Defaulting
                metadata=intent.metadata or {}
            )

            # 2. Synchronous DB operation logic
            # Since this is an async method, we ideally shouldn't block, 
            # but for this specific fix requested, we run the sync DB call here.
            
            sid = None
            db = SessionLocal()
            try:
                # Call the static method provided in prompt
                new_schedule = ScheduleService.create_schedule(
                    db=db, 
                    user_id=owner_id, 
                    schedule_data=schedule_data
                )
                if new_schedule:
                    sid = new_schedule.id
            finally:
                db.close()

            if sid:
                logger.info(f"[SCHEDULING AGENT] ScheduleMutationApplier: Successfully created schedule id={sid}")
                return {"ok": True, "schedule_id": sid, "error": None}
            else:
                logger.error("[SCHEDULING AGENT] ScheduleMutationApplier: ScheduleService returned None")
                return {"ok": False, "schedule_id": None, "error": "Service returned no schedule"}

        except Exception as e:
            logger.error(f"[SCHEDULING AGENT] ScheduleMutationApplier add failed: {e}", exc_info=True)
            return {"ok": False, "schedule_id": None, "error": str(e)}

    async def _apply_modify(
        self, intent: ScheduleMutationIntent, owner_id: int, storage_client: Any
    ) -> Dict[str, Any]:
        if intent.schedule_id is None:
            return {"ok": False, "schedule_id": None, "error": "modify requires schedule_id"}
        payload: Dict[str, Any] = {}
        if intent.title is not None:
            payload["title"] = intent.title
        if intent.scheduled_time is not None:
            payload["scheduled_time"] = intent.scheduled_time
        if intent.end_time is not None:
            payload["end_time"] = intent.end_time
        if intent.description is not None:
            payload["description"] = intent.description
        if intent.event_type is not None:
            payload["event_type"] = intent.event_type
        if intent.metadata is not None:
            payload["metadata"] = intent.metadata
        if not payload:
            return {"ok": True, "schedule_id": intent.schedule_id, "error": None}
        ok = await storage_client.update_schedule(
            owner_id=owner_id,
            schedule_id=intent.schedule_id,
            **payload,
        )
        logger.info("[SCHEDULING AGENT] ScheduleMutationApplier: modify id=%s ok=%s", intent.schedule_id, ok)
        return {"ok": ok, "schedule_id": intent.schedule_id, "error": None if ok else "update failed"}


class ScheduleMutationHandler:
    """
    Facade: parse user semantics into ScheduleMutationIntent, then apply via storage.
    Use when the pipeline has determined the user wants to add/delete/modify a schedule.
    """

    def __init__(self, api_url: Optional[str] = None):
        self._parser = ScheduleMutationParser(api_url=api_url)
        self._applier = ScheduleMutationApplier()
        logger.info("[SCHEDULING AGENT] ScheduleMutationHandler initialized")

    async def handle(
        self,
        context: ScheduleSessionContext,
        owner_id: int,
        storage_client: Any,
        api_url: Optional[str] = None,
        schedules: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Parse user query into intent; if intent is add/delete/modify, apply and return result.
        Returns { "handled": bool, "intent": ScheduleMutationIntent or None, "result": apply result dict or None }.
        """
        schedules = schedules or []
        intent = await self._parser.parse(context, schedules, api_url=api_url or self._parser._api_url)
        if intent is None:
            return {"handled": False, "intent": None, "result": None}
        result = await self._applier.apply(intent, owner_id, storage_client)
        return {"handled": True, "intent": intent, "result": result}


# ---------------------------------------------------------------------------
# Sub-agent: Plan Ahead (meal planning) agent
# ---------------------------------------------------------------------------


class PlanAheadAgent:
    """
    Handles PLAN_AHEAD logic:
    - Syncing meal plan state from database
    - Structured JSON response parsing (PLAN_AHEAD_RESPONSE_SCHEMA)
    - Programmatic shopping list computation
    - Persisting meal plans to schedule
    """

    def __init__(self, gemini_api_url: Optional[str] = None):
        """
        Initialize PlanAheadAgent.
        
        Args:
            gemini_api_url: Optional Gemini API URL for LLM calls. If None, will need to be provided per call.
        """
        self.gemini_api_url = gemini_api_url
        logger.info("[SCHEDULING AGENT] PlanAheadAgent initialized")

    def _extract_from_feature_format(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract simple meal plan and shopping list from Feature format.
        
        Input (Feature format):
            {
                "features": [
                    {
                        "type": "meal_plan",
                        "plans": [
                            {
                                "date": "2026-02-10",
                                "meals": [
                                    {
                                        "mealTime": "dinner",
                                        "dishes": [
                                            {
                                                "name": "Pasta",
                                                "ingredients": [{"name": "pasta"}, {"name": "tomato"}]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        
        Output (simple format for in-memory state):
            {
                "meal_plan": {"2026-02-10": "Pasta"},
                "shopping_list": ["pasta", "tomato"]
            }
        """
        meal_plan = {}
        shopping_list_set = set()
        
        features = metadata.get("features", [])
        for feature in features:
            if feature.get("type") == "meal_plan":
                plans = feature.get("plans", [])
                for day_plan in plans:
                    date = day_plan.get("date")
                    meals = day_plan.get("meals", [])
                    
                    # Collect all dish names for this date
                    dish_names = []
                    for meal in meals:
                        for dish in meal.get("dishes", []):
                            dish_name = dish.get("name", "").strip()
                            if dish_name:
                                dish_names.append(dish_name)
                            
                            # Collect ingredients
                            for ingredient in dish.get("ingredients", []):
                                ing_name = ingredient.get("name", "").strip()
                                if ing_name:
                                    shopping_list_set.add(ing_name)
                    
                    # Combine dish names into single meal text
                    if dish_names:
                        meal_plan[date] = ", ".join(dish_names)
        
        return {
            "meal_plan": meal_plan,
            "shopping_list": sorted(list(shopping_list_set))
        }

    def _now_in_timezone(self, user_timezone: Optional[str] = None) -> datetime:
        """Return current datetime in user's timezone, or UTC if not provided."""
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            tz = None
        if tz:
            return datetime.now(tz)
        return datetime.utcnow()

    async def sync_meal_plan_from_database(
        self,
        owner_id: int,
        storage_client: Any,  # PipelineStorage instance
    ) -> Dict[str, Any]:
        """
        Sync meal plan state from database so chat and calendar use the same data.
        Returns synced state with meal_plan, shopping_list, schedule_id.
        """
        try:
            schedules = await storage_client.get_user_schedules(owner_id)
        except Exception as e:
            logger.warning(f"[SCHEDULING AGENT] PlanAheadAgent: get_user_schedules failed for user {owner_id}: {e}")
            return {"meal_plan": {}, "shopping_list": [], "schedule_id": None, "meal_plan_slots": {}, "dish_ingredients": {}}
        merged_meal_plan: Dict[str, str] = {}
        merged_shopping_list: List[str] = []
        merged_slots: Dict[str, Dict[str, str]] = {}
        merged_dish_ingredients: Dict[str, List[str]] = {}
        first_meal_plan_schedule_id: Optional[int] = None
        for s in schedules:
            if s.get("event_type") not in ("meal_plan_draft", "shopping_list"):
                continue
            mp, sl, di, slots = storage_client._extract_meal_plan_from_schedule(s)
            if mp:
                merged_meal_plan.update(mp)
                if first_meal_plan_schedule_id is None:
                    first_meal_plan_schedule_id = s.get("id")
            if sl:
                merged_shopping_list = list(set(merged_shopping_list + sl))
            for d, slot_dict in (slots or {}).items():
                merged_slots[d] = {**merged_slots.get(d, {}), **slot_dict}
            for dish_name, ing_list in (di or {}).items():
                merged_dish_ingredients[dish_name] = list(set(merged_dish_ingredients.get(dish_name, []) + (ing_list or [])))
        result = {
            "meal_plan": merged_meal_plan,
            "shopping_list": merged_shopping_list,
            "schedule_id": first_meal_plan_schedule_id,
            "meal_plan_slots": merged_slots,
            "dish_ingredients": merged_dish_ingredients,
        }
        logger.info(
            f"[SCHEDULING AGENT] PlanAheadAgent: Synced from DB for user {owner_id}: "
            f"{len(merged_meal_plan)} meals, {len(merged_shopping_list)} ingredients, schedule_id={first_meal_plan_schedule_id}"
        )
        return result

    def apply_plan_modification(
        self,
        meal_plan: Dict[str, str],
        shopping_list: List[str],
        intent: Dict[str, Any],
        meal_plan_slots: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, List[str]]]]:
        """Apply modification intent. Returns (new_meal_plan, new_meal_plan_slots).
        meal_plan_slots: date -> { breakfast?, lunch?, dinner? } where each value is List[str].
        Accepts legacy str values and migrates them to List[str] automatically.
        """
        # Normalise to List[str] format
        slots: Dict[str, Dict[str, List[str]]] = {}
        if meal_plan_slots:
            for d, s in meal_plan_slots.items():
                slots[d] = {}
                for mt, val in (s or {}).items():
                    if isinstance(val, list):
                        slots[d][mt] = list(val)
                    elif isinstance(val, str) and val.strip():
                        # Treat the whole string as a single dish name to avoid splitting
                        # dish names that contain " and " (e.g. "Fish and Chips").
                        slots[d][mt] = [val.strip()]
        for d, text in meal_plan.items():
            if d not in slots:
                slots[d] = {}
            if not slots[d] and text:
                # Legacy flat string from DB: may be ", "-joined (sync path) or
                # " and "-joined (parse_structured_response path).
                # Split by comma first; fall back to whole string to avoid breaking
                # dish names that contain " and ".
                if "," in text:
                    parts = [p.strip() for p in text.split(",") if p.strip()]
                else:
                    parts = [text.strip()]
                slots[d]["dinner"] = parts

        def apply_one(op: str, date: Optional[str], meal: Optional[str], append: bool, meal_time: str) -> None:
            if not date:
                return
            if date not in slots:
                slots[date] = {}
            mt = meal_time or "dinner"
            if op == "remove":
                if meal and mt:
                    # Remove only this dish; support partial match (e.g. "汉堡" removes "蘑菇汉堡")
                    current: List[str] = list(slots.get(date, {}).get(mt) or [])
                    meal_lower = meal.strip().lower()
                    remaining = [p for p in current if not (p.strip().lower() == meal_lower or meal_lower in p.strip().lower())]
                    if remaining:
                        slots[date][mt] = remaining
                    else:
                        slots[date].pop(mt, None)
                    if not slots.get(date):
                        slots.pop(date, None)
                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Removed '{meal}' from {date} {mt}")
                else:
                    slots.pop(date, None)
                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Removed date {date} from meal plan")
            elif op in ("modify", "add") and meal:
                # The meal value from LLM-structured intent is a single dish name;
                # do NOT split by " and " — "Fish and Chips" is one dish.
                meal_parts = [meal.strip()] if meal.strip() else []
                if not meal_parts:
                    meal_parts = [meal.strip()]
                if append and mt in slots.get(date, {}):
                    slots[date][mt] = list(slots[date][mt]) + meal_parts
                else:
                    slots[date][mt] = meal_parts
                logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: {op} date {date} {mt} to {meal_parts}")

        # Multi-op
        if "operations" in intent and isinstance(intent.get("operations"), list):
            sorted_ops = sorted(intent["operations"], key=lambda x: 0 if x.get("operation") == "remove" else 1)
            for op_intent in sorted_ops:
                apply_one(
                    op_intent.get("operation", ""),
                    op_intent.get("date"),
                    op_intent.get("meal"),
                    op_intent.get("append", False),
                    op_intent.get("meal_time") or "dinner",
                )
        else:
            op = intent.get("operation")
            date = intent.get("date")
            meal = intent.get("meal")
            apply_one(op, date, meal, intent.get("append", False), intent.get("meal_time") or "dinner")

        # Build flat meal_plan from slots (backward-compat legacy string for storage layer).
        # meal_plan_slots is the authoritative structure; meal_plan is derived only.
        plan: Dict[str, str] = {}
        for d, s in slots.items():
            all_dishes: List[str] = []
            for mt in ("breakfast", "lunch", "dinner", "snack"):
                all_dishes.extend(s.get(mt) or [])
            if all_dishes:
                plan[d] = " and ".join(all_dishes)
        return plan, slots

    def execute_operation(
        self,
        op_type: str,
        mod_intent: Dict[str, Any],
        meal_plan: Dict[str, str],
        meal_plan_slots: Dict[str, Any],
        shopping_list: List[str],
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, List[str]]]]:
        """
        Apply op_type (add/modify/remove/multi) to meal_plan via apply_plan_modification.
        Returns (meal_plan, meal_plan_slots) with List[str] slot values. VIEW/unknown returns inputs unchanged.
        """
        if op_type in ("add", "modify", "remove", "multi"):
            logger.info(f"[SCHEDULING AGENT] execute_operation: op_type={op_type}, date={mod_intent.get('date')}, meal={mod_intent.get('meal')}")
            return self.apply_plan_modification(meal_plan, shopping_list, mod_intent, meal_plan_slots=meal_plan_slots)
        return meal_plan, meal_plan_slots

    async def create_schedule_for_add(
        self,
        owner_id: int,
        date: str,
        meal: str,
        user_timezone: Optional[str] = None,
    ) -> Optional[int]:
        """
        Create a new schedule directly when add operation is performed.
        This is called when we know what schedule to add and want to create it immediately.
        
        Args:
            owner_id: User ID
            date: Date string in YYYY-MM-DD format
            meal: Meal description
            user_timezone: Optional user timezone
            
        Returns:
            Schedule ID if successful, None otherwise
        """
        if not DB_SERVICE_AVAILABLE or not SessionLocal:
            logger.warning("[SCHEDULING AGENT] PlanAheadAgent: DB Service not available for direct schedule creation")
            return None
        
        try:
            # Parse date string to datetime
            try:
                date_obj = datetime.strptime(date, '%Y-%m-%d').date()
            except ValueError:
                logger.error(f"[SCHEDULING AGENT] PlanAheadAgent: Invalid date format: {date}")
                return None
            
            # Convert date to datetime (set to start of day in user timezone or UTC)
            if user_timezone:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(user_timezone)
                scheduled_time = datetime.combine(date_obj, datetime.min.time(), tzinfo=tz)
                # Convert to UTC and remove timezone info for database storage
                scheduled_time = scheduled_time.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
            else:
                scheduled_time = datetime.combine(date_obj, datetime.min.time())
            
            # End time defaults to end of day
            end_time = datetime.combine(date_obj, datetime.max.time())
            if user_timezone:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(user_timezone)
                end_time = datetime.combine(date_obj, datetime.max.time(), tzinfo=tz)
                # Convert to UTC and remove timezone info for database storage
                end_time = end_time.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
            
            # Create meal plan dict with single date
            meal_plan = {date: meal}
            
            # Create schedule data
            schedule_data = ScheduleCreate(
                title=f"Meal Plan - {date}",
                event_type="meal_plan_draft",
                description=f"Meal plan for {date}: {meal}",
                scheduled_time=scheduled_time,
                end_time=end_time,
                location=None,
                priority=0,
                metadata={
                    "meal_plan": meal_plan,
                    "shopping_list": []
                }
            )
            
            # Create schedule using ScheduleService
            db = SessionLocal()
            try:
                new_schedule = ScheduleService.create_schedule(
                    db=db,
                    user_id=owner_id,
                    schedule_data=schedule_data
                )
                if new_schedule:
                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Successfully created schedule id={new_schedule.id} for date {date}")
                    return new_schedule.id
                else:
                    logger.error("[SCHEDULING AGENT] PlanAheadAgent: ScheduleService returned None")
                    return None
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"[SCHEDULING AGENT] PlanAheadAgent: Failed to create schedule for add: {e}", exc_info=True)
            return None

    def parse_structured_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """
        Parse Gemini structured JSON response (PLAN_AHEAD_RESPONSE_SCHEMA).

        Converts meal_entries → meal_plan (flat str, backward compat),
        meal_plan_slots (List[str] per slot), dish_ingredients, and
        computes shopping_list programmatically.

        Returns None if parsing fails.
        """
        try:
            data = json.loads(response_text.strip())
        except json.JSONDecodeError:
            snippet = response_text.strip()[:300].replace("\n", "\\n")
            logger.warning(
                f"[SCHEDULING AGENT] parse_structured_response: JSON decode failed. "
                f"Response snippet: {snippet!r}"
            )
            return None

        meal_entries = data.get("meal_entries")
        if not isinstance(meal_entries, list):
            logger.warning("[SCHEDULING AGENT] parse_structured_response: meal_entries missing or not a list")
            return None

        # Build meal_plan_slots: date -> meal_time -> List[str]
        meal_plan_slots: Dict[str, Dict[str, List[str]]] = {}
        dish_ingredients: Dict[str, List[Dict[str, str]]] = {}

        for entry in meal_entries:
            date_str = (entry.get("date") or "").strip()
            meal_time = (entry.get("meal_time") or "dinner").strip()
            dishes = entry.get("dishes") or []
            if not date_str:
                continue
            if date_str not in meal_plan_slots:
                meal_plan_slots[date_str] = {}
            dish_names: List[str] = []
            for dish in dishes:
                name = (dish.get("name") or "").strip()
                if not name:
                    continue
                dish_names.append(name)
                # Accept new dict format [{"name":..,"category":..,"quantity":..}]
                # and legacy string format ["pasta"] for backward compatibility.
                raw_ings = dish.get("ingredients") or []
                ings: List[Dict[str, str]] = []
                for i in raw_ings:
                    if isinstance(i, dict):
                        ing_name = (i.get("name") or "").strip()
                        if ing_name:
                            ings.append({
                                "name":     ing_name,
                                "category": i.get("category") or "other",
                                "quantity": i.get("quantity") or "",
                            })
                    elif isinstance(i, str) and i.strip():
                        ings.append({"name": i.strip(), "category": "other", "quantity": ""})
                if ings:
                    dish_ingredients[name] = ings
            if dish_names:
                meal_plan_slots[date_str][meal_time] = dish_names

        # Flat meal_plan: date -> "Dish A and Dish B" (kept for backward compat with storage layer)
        meal_plan: Dict[str, str] = {}
        for date_str, slots in meal_plan_slots.items():
            all_dishes: List[str] = []
            for mt in ("breakfast", "lunch", "dinner", "snack"):
                all_dishes.extend(slots.get(mt) or [])
            if all_dishes:
                meal_plan[date_str] = " and ".join(all_dishes)

        shopping_list = compute_shopping_list(dish_ingredients)
        logger.info(
            f"[SCHEDULING AGENT] parse_structured_response: {len(meal_plan)} dates, "
            f"{len(dish_ingredients)} dishes with ingredients"
        )
        return {
            "action": (data.get("action") or "view").strip(),
            "target_date": (data.get("target_date") or "").strip() or None,
            "meal_time": (data.get("meal_time") or "dinner").strip(),
            "user_message": (data.get("user_message") or "").strip(),
            "meal_plan": meal_plan,
            "meal_plan_slots": meal_plan_slots,
            "dish_ingredients": dish_ingredients,
            "shopping_list": shopping_list,
        }


    async def filter_deleted_dates(
        self,
        meal_plan_final: Dict[str, str],
        mod_intent: Optional[Dict[str, Any]],
        storage_client: Any,  # PipelineStorage instance
        owner_id: int,
    ) -> Dict[str, str]:
        """
        Filter out dates that were deleted from database (prevent AI from resurrecting them).
        Returns filtered meal_plan dict.
        """
        # NOTE: Schedule fetch is temporarily disabled during redesign.
        # We no longer call storage_client.get_user_schedules here and simply
        # return the provided meal_plan_final as-is.
        logger.info(
            f"[SCHEDULING AGENT] PlanAheadAgent: filter_deleted_dates disabled for user {owner_id}; "
            "returning meal_plan without additional DB checks"
        )
        return meal_plan_final

    async def persist_meal_plan(
        self,
        meal_plan: Dict[str, str],
        shopping_list: List[str],
        owner_id: int,
        existing_schedule_id: Optional[int],
        storage_client: Any,  # PipelineStorage instance
        user_timezone: Optional[str] = None,
        event_type: str = "meal_plan_draft",
        dish_ingredients: Optional[Dict[str, List[str]]] = None,
        meal_plan_slots: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Optional[int]:
        """
        Persist meal plan to schedule. Returns schedule_id if successful, None otherwise.
        If meal plan is empty and there's an existing schedule, deletes it.
        dish_ingredients: optional map dish_name -> list of ingredient names for per-dish display.
        meal_plan_slots: optional date -> { breakfast?, lunch?, dinner? } so lunch/breakfast are stored correctly.
        """
        try:
            logger.info(f"[BACKEND] persist_meal_plan entry: existing_schedule_id={existing_schedule_id}, meal_plan_keys={list(meal_plan.keys()) if meal_plan else []}, has_meal_plan_slots={bool(meal_plan_slots)}")
            # If meal plan is empty and there's an existing schedule, delete it
            if not meal_plan and not shopping_list and existing_schedule_id:
                logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Meal plan is empty, deleting schedule id={existing_schedule_id}")
                await storage_client.delete_schedule(existing_schedule_id, owner_id)
                return None

            # If there's content, save/update the schedule
            if meal_plan or shopping_list:
                schedule_id = await storage_client.create_or_update_meal_plan_schedule(
                    owner_id=owner_id,
                    meal_plan=meal_plan,
                    shopping_list=shopping_list,
                    existing_schedule_id=existing_schedule_id,
                    event_type=event_type,
                    user_timezone=user_timezone,
                    dish_ingredients=dish_ingredients,
                    meal_plan_slots=meal_plan_slots,
                )
                if schedule_id:
                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Meal plan persisted to schedule id={schedule_id}")
                    return schedule_id
                else:
                    logger.warning("[SCHEDULING AGENT] PlanAheadAgent: Failed to persist meal plan to schedule")
                    return None
            
            return None
        except Exception as e:
            logger.error(f"[SCHEDULING AGENT] PlanAheadAgent: Failed to persist meal plan: {e}", exc_info=True)
            return None

    def get_agent_capabilities(self) -> Dict[str, Any]:
        """Return information about PlanAheadAgent capabilities."""
        return {
            "name": "PlanAheadAgent",
            "purpose": "Handles PLAN_AHEAD logic including meal plan sync, modification, and persistence",
            "capabilities": [
                "Sync meal plan state from database",
                "Apply modifications programmatically (List[str] slot format)",
                "Parse structured JSON response (PLAN_AHEAD_RESPONSE_SCHEMA)",
                "Filter deleted dates to prevent resurrection",
                "Persist meal plans to schedule",
            ],
        }