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
            parsed = json.loads(raw.strip())
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
            parsed = json.loads(raw.strip())
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
    Handles all PLAN_AHEAD logic including:
    - Syncing meal plan state from database
    - Extracting modification intent from user input
    - Applying modifications programmatically
    - Generating context messages for LLM
    - Parsing PLAN_JSON from LLM responses
    - Filtering deleted dates
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
        Sync meal plan state from database to ensure we have the latest data.
        Returns synced state with meal_plan, shopping_list, schedule_id.

        NOTE: All schedule *fetch* operations are temporarily disabled while
        the schedule service is being redesigned. This method now returns an
        empty state without calling storage_client.get_user_schedules.
        """
        logger.info(
            f"[SCHEDULING AGENT] PlanAheadAgent: Meal plan sync from database is disabled for user {owner_id}"
        )
        return {"meal_plan": {}, "shopping_list": [], "schedule_id": None}

    async def extract_modification_intent(
        self,
        user_input: str,
        history: Optional[List[Dict[str, str]]],
        current_meal_plan: Dict[str, str],
        user_timezone: Optional[str] = None,
        gemini_api_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Use LLM to extract structured modification intent from user input.
        Returns: { "operation": "remove"|"modify"|"add"|"none", "date": "YYYY-MM-DD", "meal": "...", "append": bool }
        """
        api_url = gemini_api_url or self.gemini_api_url
        if not api_url:
            logger.warning("[SCHEDULING AGENT] PlanAheadAgent: No Gemini API URL provided for modification intent extraction")
            return None

        now = self._now_in_timezone(user_timezone)
        today = now.date()
        days_ahead = (7 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_monday = today + timedelta(days=days_ahead)
        week_dates = {d: (next_monday + timedelta(days=i)).strftime("%Y-%m-%d") for i, d in enumerate(
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        )}
        date_ref = json.dumps(week_dates)
        
        # Build date ref from current plan if available (more accurate)
        plan_dates_ref = ""
        if current_meal_plan:
            weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            for dt_str in sorted(current_meal_plan.keys()):
                try:
                    y, m, d = map(int, dt_str.split("-"))
                    wd = weekdays[datetime(y, m, d).date().weekday()]
                    plan_dates_ref += f"{dt_str} ({wd}), "
                except (ValueError, IndexError):
                    pass
            if plan_dates_ref:
                date_ref = f"Dates in current plan: {plan_dates_ref.rstrip(', ')}. Fallback: {date_ref}"
        
        history_blob = ""
        if history and len(history) >= 2:
            recent = history[-4:]
            history_blob = "\nRecent conversation:\n"
            for msg in recent:
                role = "User" if msg.get("role") == "user" else "Assistant"
                history_blob += f"{role}: {msg.get('content', '')[:250]}\n"

        prompt = f"""You are extracting meal plan modification intent. Current date: {today.strftime('%Y-%m-%d')}.
Date reference (weekday -> YYYY-MM-DD): {date_ref}
Current plan: {json.dumps(current_meal_plan, ensure_ascii=False)}
{history_blob}
User's latest input: "{user_input}"

Determine the modification intent. You can return EITHER:
- A single operation (old format): {{"operation": "...", "date": "...", "meal": "...", "append": bool}}
- Multiple operations (new format): {{"operations": [{{"operation": "...", "date": "...", "meal": "..."}}, ...]}}

For single operation:
- operation: "remove" | "modify" | "add" | "none"
- date: "YYYY-MM-DD" (required if operation is remove/modify/add; use date reference)
- meal: string (required only for modify/add; the meal name)
- append: boolean (true if user wants to ADD ANOTHER dish to existing date; false to replace)

For multiple operations (use when user wants to correct/move a plan):
- operations: array of operation objects, each with operation, date, meal (if needed), append (if needed)
- Execute operations in order: remove operations first, then add/modify

CRITICAL RULES:
1. If user says "time should be [new_date] not [old_date]" or "change from [old_date] to [new_date]" -> return multiple operations: [{{"operation":"remove","date":"old_date"}}, {{"operation":"add","date":"new_date","meal":"[meal from old_date]"}}]
2. If user says "and [another dish]" or "also [dish]" or "add another [dish]" -> append=true, operation="add"
3. If user says "[day] will be [dish]" -> append=false, operation="modify" (replace)
4. If user says "[dish] on [day]" without context -> append=false, operation="add"
5. Infer the date from conversation context if not explicitly mentioned
6. When correcting dates, preserve the meal name from the old date

Examples:
- "remove tuesday" -> {{"operation":"remove","date":"2026-02-10","meal":null,"append":false}}
- "change thursday to pizza" -> {{"operation":"modify","date":"2026-02-12","meal":"Pizza","append":false}}
- "add pasta on friday" -> {{"operation":"add","date":"2026-02-13","meal":"Pasta","append":false}}
- "time should be feb 15th not 22nd" (current plan has Indian food on 2026-02-22) -> {{"operations":[{{"operation":"remove","date":"2026-02-22"}},{{"operation":"add","date":"2026-02-15","meal":"Indian food"}}]}}
- "change from monday to tuesday" (current plan has Pizza on Monday) -> {{"operations":[{{"operation":"remove","date":"2026-02-09"}},{{"operation":"add","date":"2026-02-10","meal":"Pizza"}}]}}
- "what's my plan" -> {{"operation":"none","date":null,"meal":null,"append":false}}

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
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not text:
                return None
            parsed = json.loads(text)
            
            # Check for multiple operations format
            if "operations" in parsed and isinstance(parsed.get("operations"), list):
                operations = parsed.get("operations", [])
                if operations:
                    # Validate all operations have required fields
                    valid_ops = []
                    for op in operations:
                        op_type = op.get("operation")
                        if op_type and op_type != "none":
                            date_val = op.get("date")
                            if date_val:
                                valid_ops.append(op)
                    if valid_ops:
                        logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Extracted multiple modification intents: {len(valid_ops)} operations")
                        return {"operations": valid_ops}
                return None
            
            # Single operation format (backward compatible)
            op = parsed.get("operation")
            if op and op != "none":
                date_val = parsed.get("date")
                if date_val:
                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Extracted modification intent: {parsed}")
                    return parsed
            return None
        except Exception as e:
            logger.warning(f"[SCHEDULING AGENT] PlanAheadAgent: Extract modification intent failed: {e}")
            return None

    def apply_plan_modification(
        self,
        meal_plan: Dict[str, str],
        shopping_list: List[str],
        intent: Dict[str, Any],
    ) -> Dict[str, str]:
        """Apply modification intent to meal_plan. Returns new meal_plan dict.
        
        Supports both single operation and multiple operations:
        - Single: {"operation": "add", "date": "...", "meal": "..."}
        - Multiple: {"operations": [{"operation": "remove", "date": "..."}, {"operation": "add", "date": "...", "meal": "..."}]}
        """
        plan = dict(meal_plan)
        
        # Handle multiple operations
        if "operations" in intent and isinstance(intent.get("operations"), list):
            operations = intent.get("operations", [])
            # Execute remove operations first, then add/modify
            sorted_ops = sorted(operations, key=lambda x: 0 if x.get("operation") == "remove" else 1)
            
            for op_intent in sorted_ops:
                op = op_intent.get("operation")
                date = op_intent.get("date")
                meal = op_intent.get("meal")
                append = op_intent.get("append", False)
                
                if op == "remove" and date:
                    plan.pop(date, None)
                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Removed date {date} from meal plan (multi-op)")
                elif op == "modify" and date and meal:
                    if date in plan and append:
                        existing = plan[date]
                        plan[date] = f"{existing} and {meal}"
                    else:
                        plan[date] = meal
                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Modified date {date} to '{meal}' (multi-op)")
                elif op == "add" and date and meal:
                    if date in plan:
                        existing = plan[date]
                        plan[date] = f"{existing} and {meal}"
                    else:
                        plan[date] = meal
                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Added meal '{meal}' to date {date} (multi-op)")
            
            return plan
        
        # Single operation (backward compatible)
        op = intent.get("operation")
        date = intent.get("date")
        meal = intent.get("meal")
        append = intent.get("append", False)
        
        if op == "remove" and date:
            plan.pop(date, None)
            logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Removed date {date} from meal plan")
        elif op == "modify" and date and meal:
            if date in plan and append:
                existing = plan[date]
                plan[date] = f"{existing} and {meal}"
            else:
                plan[date] = meal
            logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Modified date {date} to '{meal}'")
        elif op == "add" and date and meal:
            if date in plan:
                existing = plan[date]
                plan[date] = f"{existing} and {meal}"
            else:
                plan[date] = meal
            logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Added meal '{meal}' to date {date}")
        
        return plan

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

    def generate_plan_ahead_context(
        self,
        meal_plan: Dict[str, str],
        shopping_list: List[str],
        user_timezone: Optional[str] = None,
    ) -> str:
        """
        Generate context message for PLAN_AHEAD mode.
        """
        now = self._now_in_timezone(user_timezone)
        today = now.date()
        days_ahead = (7 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_monday = today + timedelta(days=days_ahead)
        next_sunday = next_monday + timedelta(days=6)
        
        context_msg = f"\n\nTODAY'S DATE: {today.strftime('%Y-%m-%d (%A)')}"
        context_msg += f"\nNEXT WEEK: Monday {next_monday.strftime('%Y-%m-%d')} to Sunday {next_sunday.strftime('%Y-%m-%d')}"
        context_msg += "\n\n=== PLAN_AHEAD MODE ==="
        context_msg += "\nYour task: Help the user plan meals for the coming week."
        context_msg += "\n\nCRITICAL REQUIREMENT - PLAN_JSON OUTPUT:"
        context_msg += "\nYou MUST end EVERY response with a PLAN_JSON line showing the COMPLETE final plan."
        context_msg += "\nFormat: PLAN_JSON: {\"meal_plan\": {\"YYYY-MM-DD\": \"meal name\", ...}, \"shopping_list\": [\"item1\", ...]}"
        context_msg += "\nExample: PLAN_JSON: {\"meal_plan\": {\"2025-02-10\": \"Stir Fry\", \"2025-02-12\": \"Tacos\"}, \"shopping_list\": [\"beef\", \"rice\"]}"
        context_msg += "\n\nWhen to save: When user says 'save' or 'add to schedule', add a line: SAVE_TO_SCHEDULE"
        
        separator_line = "=" * 60
        if meal_plan or shopping_list:
            context_msg += f"\n\n{separator_line}"
            context_msg += f"\nCURRENT PLAN STATE (SYNCED FROM DATABASE - THIS IS THE ONLY TRUTH):"
            context_msg += f"\nmeal_plan={json.dumps(meal_plan, ensure_ascii=False)}"
            context_msg += f"\nshopping_list={json.dumps(shopping_list, ensure_ascii=False)}"
            context_msg += f"\n{separator_line}"
            context_msg += "\n\nCRITICAL RULES FOR PLAN_JSON OUTPUT:"
            context_msg += "\n\nRULE 0 (MOST IMPORTANT): IGNORE ALL MEAL PLANS FROM CHAT HISTORY!"
            context_msg += "\n   - Chat history may contain outdated plans from previous conversations."
            context_msg += "\n   - The user may have manually deleted/edited days in the Schedule UI."
            context_msg += "\n   - ONLY trust the CURRENT PLAN STATE shown above (synced from database)."
            context_msg += "\n   - If a date is missing from CURRENT PLAN STATE, it was deleted - DO NOT mention or add it back!"
            context_msg += "\n\n1. START with the EXACT content from CURRENT PLAN STATE above."
            context_msg += "\n2. ONLY modify what the user explicitly asked to change in THIS message."
            context_msg += "\n3. If user says 'remove [day]', DELETE only that date from the plan."
            context_msg += "\n4. If user says 'change [day] to [meal]', UPDATE only that date."
            context_msg += "\n5. If user says 'add [meal] on [day]', ADD that date."
            context_msg += "\n6. NEVER re-add dates that are missing from CURRENT PLAN STATE."
            context_msg += "\n7. NEVER invent, modify, or hallucinate meals for dates the user didn't mention."
            context_msg += "\n8. PRESERVE the exact meal names from CURRENT PLAN STATE for all unchanged dates."
        else:
            context_msg += f"\n\n{separator_line}"
            context_msg += "\nCURRENT PLAN STATE: Empty (no existing plan)"
            context_msg += f"\n{separator_line}"
            context_msg += "\n\nThe user may have deleted all previous plans or this is a fresh start."
            context_msg += "\nONLY add dates/meals that the user explicitly requests in this message."
            context_msg += "\nDO NOT try to 'restore' or 'remember' plans from chat history!"
        
        return context_msg

    def parse_plan_json(self, response_text: str) -> Optional[Dict[str, Any]]:
        """
        Parse PLAN_JSON from LLM response text.
        Returns parsed plan dict or None if not found/invalid.
        """
        plan_json_marker = "PLAN_JSON:"
        
        if plan_json_marker not in response_text:
            return None
        
        try:
            idx = response_text.find(plan_json_marker)
            json_str = response_text[idx + len(plan_json_marker):].strip()
            # Trim to first complete JSON object
            brace = json_str.find("{")
            if brace >= 0:
                depth = 0
                end = brace
                for i, c in enumerate(json_str[brace:], start=brace):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                json_str = json_str[brace:end + 1]
            parsed = json.loads(json_str)
            if isinstance(parsed.get("meal_plan"), dict) or isinstance(parsed.get("shopping_list"), list):
                logger.info("[SCHEDULING AGENT] PlanAheadAgent: Parsed PLAN_JSON from response")
                return parsed
        except (json.JSONDecodeError, ValueError) as parse_err:
            logger.warning(f"[SCHEDULING AGENT] PlanAheadAgent: Could not parse PLAN_JSON: {parse_err}")
        
        return None

    def try_detect_removal_from_response(
        self,
        response_text: str,
        user_input: str,
        current_meal_plan: Dict[str, str],
        current_shopping_list: List[str],
        user_timezone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Programmatic fallback: when AI says "Tuesday has been removed" but no PLAN_JSON,
        detect the removed day and apply it. Handles confirmation flow (user: "Yes").
        """
        if not current_meal_plan:
            return None
        text = (response_text + " " + user_input).lower()
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        removed_day = None
        if "removed" not in text and "remove" not in text and "now open" not in text:
            return None
        idx_key = text.find("removed") if "removed" in text else (text.find("remove") if "remove" in text else text.find("now open"))
        # Find weekday closest to "removed"
        best_day, best_dist = None, 999
        for day in weekdays:
            if day not in text:
                continue
            idx_day = text.find(day)
            dist = abs(idx_day - idx_key)
            if dist < best_dist:
                best_dist, best_day = dist, day
        removed_day = best_day
        if not removed_day:
            return None
        day_idx = weekdays.index(removed_day)
        date_to_remove = None
        for dt_str in current_meal_plan:
            try:
                y, m, d = map(int, dt_str.split("-"))
                dt = datetime(y, m, d).date()
                if dt.weekday() == day_idx:
                    date_to_remove = dt_str
                    break
            except (ValueError, IndexError):
                continue
        if not date_to_remove:
            return None
        new_meal_plan = {k: v for k, v in current_meal_plan.items() if k != date_to_remove}
        logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Programmatic removal detected - removed {date_to_remove} ({removed_day})")
        return {"meal_plan": new_meal_plan, "shopping_list": current_shopping_list}

    async def extract_plan_from_text(
        self,
        response_text: str,
        user_input: str,
        history: Optional[List[Dict[str, str]]],
        user_timezone: Optional[str] = None,
        current_meal_plan: Optional[Dict[str, str]] = None,
        current_shopping_list: Optional[List[str]] = None,
        gemini_api_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract meal_plan and shopping_list from LLM response text using another LLM call.
        Used as fallback when PLAN_JSON marker is not found.
        """
        api_url = gemini_api_url or self.gemini_api_url
        if not api_url:
            logger.warning("[SCHEDULING AGENT] PlanAheadAgent: No Gemini API URL provided for plan extraction")
            return None

        now = self._now_in_timezone(user_timezone)
        today = now.date()
        days_ahead = (7 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_monday = today + timedelta(days=days_ahead)
        week_dates = {
            "Monday": next_monday,
            "Tuesday": next_monday + timedelta(days=1),
            "Wednesday": next_monday + timedelta(days=2),
            "Thursday": next_monday + timedelta(days=3),
            "Friday": next_monday + timedelta(days=4),
            "Saturday": next_monday + timedelta(days=5),
            "Sunday": next_monday + timedelta(days=6),
        }
        date_ref = ", ".join([f"{day}={date.strftime('%Y-%m-%d')}" for day, date in week_dates.items()])
        
        current_state = ""
        if current_meal_plan or current_shopping_list:
            current_state = f"\nCURRENT PLAN STATE (before user's modification):\n"
            current_state += f"meal_plan={json.dumps(current_meal_plan or {}, ensure_ascii=False)}\n"
            current_state += f"shopping_list={json.dumps(current_shopping_list or [], ensure_ascii=False)}\n"
        
        history_blob = ""
        if history and len(history) >= 2:
            recent = history[-4:]
            history_blob = "\nRECENT CONVERSATION (for context):\n"
            for msg in recent:
                role = "User" if msg.get("role") == "user" else "Assistant"
                history_blob += f"{role}: {msg.get('content', '')[:300]}\n"
        
        extract_prompt = f"""Extract the FINAL meal plan state after applying the user's modification.

IMPORTANT: CURRENT PLAN STATE is synced from database and is the ONLY source of truth.
DO NOT restore dates from chat history if they are missing from CURRENT PLAN STATE!

{history_blob}
User's latest input: {user_input}
{current_state}
Assistant's response:
{response_text}

Today is {today.strftime('%Y-%m-%d (%A)')}. Next week dates: {date_ref}

TASK: What is the FINAL meal_plan after applying the change?

CRITICAL RULES (in priority order):
0. IGNORE all meal plan data from chat history! Only trust CURRENT PLAN STATE!
1. If user said "remove [day]" OR user confirmed (Yes/Yeah/Confirm) and assistant says "[day] has been removed" -> EXCLUDE that date
2. If assistant's response explicitly states a day was removed -> EXCLUDE that date from meal_plan
3. If user says "change [day] to [meal]" -> update that date
4. If user says "add [meal] on [day]" -> ADD that date
5. For all OTHER dates -> COPY ONLY from CURRENT PLAN STATE (if date is not there, don't add it!)
6. Convert day names (Monday, Tuesday, etc.) to YYYY-MM-DD using: {date_ref}
7. If a date is missing from CURRENT PLAN STATE, it means user deleted it - DO NOT add it back

Return ONLY a JSON object: {{"meal_plan": {{"YYYY-MM-DD": "meal", ...}}, "shopping_list": [...]}}

JSON:"""
        
        try:
            payload = {
                "contents": [{"parts": [{"text": extract_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.0,
                },
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(api_url, headers={'Content-Type': 'application/json'}, json=payload)
                response.raise_for_status()
                result = response.json()
                
                json_string = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text')
                if json_string:
                    parsed = json.loads(json_string)
                    if isinstance(parsed.get("meal_plan"), dict) or isinstance(parsed.get("shopping_list"), list):
                        logger.info("[SCHEDULING AGENT] PlanAheadAgent: Extracted plan from text using LLM")
                        return parsed
        except Exception as e:
            logger.warning(f"[SCHEDULING AGENT] PlanAheadAgent: Failed to extract plan from text: {e}")
        
        return None

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
    ) -> Optional[int]:
        """
        Persist meal plan to schedule. Returns schedule_id if successful, None otherwise.
        If meal plan is empty and there's an existing schedule, deletes it.
        """
        try:
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
            "purpose": "Handles all PLAN_AHEAD logic including meal plan sync, modification, and persistence",
            "capabilities": [
                "Sync meal plan state from database",
                "Extract modification intent from user input",
                "Apply modifications programmatically",
                "Generate context messages for LLM",
                "Parse PLAN_JSON from LLM responses",
                "Filter deleted dates to prevent resurrection",
                "Persist meal plans to schedule",
                "Handle SAVE_TO_SCHEDULE finalization",
            ],
        }