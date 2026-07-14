"""
Schedule DB Commands — hardcoded HTTP calls to DataStorageService.

Not routed through LLM decisions. The agent calls these directly
once it has determined the correct operation.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_MEAL_TIME_TO_HOUR = {"breakfast": 8, "lunch": 12, "dinner": 18}


def _base_url() -> str:
    from urllib.parse import urlparse
    p = urlparse(settings.STORAGE_SERVICE_URL)
    return f"{p.scheme}://{p.netloc}"


def _scheduled_time(date: str, meal_type: str, user_timezone: Optional[str] = None) -> str:
    """'2026-06-11' + 'dinner' + 'Asia/Shanghai' → '2026-06-11T18:00:00+08:00'"""
    from zoneinfo import ZoneInfo
    hour = _MEAL_TIME_TO_HOUR.get(meal_type, 12)
    try:
        tz = ZoneInfo(user_timezone) if user_timezone else timezone.utc
    except Exception:
        tz = timezone.utc
    from datetime import datetime as _dt
    local_dt = _dt(int(date[:4]), int(date[5:7]), int(date[8:10]), hour, 0, 0, tzinfo=tz)
    return local_dt.isoformat()


def _build_metadata(date: str, meal_type: str, dishes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build metadata in the MealPlanFeature format the frontend expects."""
    now = datetime.now(timezone.utc).isoformat()
    dish_objects = [
        {
            "id": f"dish_{uuid.uuid4().hex[:8]}",
            "name": d["name"],
            "ingredients": [
                {"name": ing["name"], "quantity": ing.get("quantity", "")}
                for ing in d.get("ingredients", [])
            ],
            "cookingSteps": d.get("steps", []),
        }
        for d in dishes
    ]
    return {
        "features": [
            {
                "type": "meal_plan",
                "id": f"mp_{uuid.uuid4().hex[:8]}",
                "created_at": now,
                "updated_at": now,
                "plans": [
                    {
                        "date": date,
                        "meals": [
                            {
                                "id": f"meal_{uuid.uuid4().hex[:8]}",
                                "mealTime": meal_type,
                                "dishes": dish_objects,
                            }
                        ],
                    }
                ],
            }
        ]
    }


def extract_dishes_from_record(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract the dish list from a saved schedule record's metadata."""
    try:
        features = (record.get("metadata") or {}).get("features") or []
        for feature in features:
            if feature.get("type") == "meal_plan":
                for plan in feature.get("plans") or []:
                    for meal in plan.get("meals") or []:
                        dishes = []
                        for d in meal.get("dishes") or []:
                            dishes.append({
                                "name": d.get("name", ""),
                                "ingredients": [
                                    {"name": i["name"], "quantity": i.get("quantity", "")}
                                    for i in d.get("ingredients") or []
                                ],
                                "steps": d.get("cookingSteps") or [],
                            })
                        return dishes
    except Exception:
        pass
    return []


async def save_plan(
    date: str,
    meal_type: str,
    dishes: List[Dict[str, Any]],
    auth_token: str,
    user_timezone: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Create a schedule record with the dish list and cooking steps.

    dishes format:
      [{"name": str, "ingredients": [...], "steps": [...]}]
    """
    payload = {
        "title": f"{meal_type.capitalize()} — {', '.join(d['name'] for d in dishes)}",
        "event_type": "meal_plan",
        "scheduled_time": _scheduled_time(date, meal_type, user_timezone),
        "metadata": _build_metadata(date, meal_type, dishes),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_base_url()}/api/schedule",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {auth_token}",
                },
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info("[schedule_commands] saved plan id=%s scheduled_time=%s", result.get("id"), payload.get("scheduled_time"))
            return result
    except Exception as exc:
        logger.error("[schedule_commands] save_plan failed: %s", exc)
        return None


async def update_plan(
    schedule_id: int,
    date: str,
    meal_type: str,
    dishes: List[Dict[str, Any]],
    auth_token: str,
    user_timezone: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update dishes and steps on an existing schedule record."""
    payload = {
        "title": f"{meal_type.capitalize()} — {', '.join(d['name'] for d in dishes)}",
        "event_type": "meal_plan",
        "metadata": _build_metadata(date, meal_type, dishes),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{_base_url()}/api/schedule/{schedule_id}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {auth_token}",
                },
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("[schedule_commands] update_plan id=%s failed: %s", schedule_id, exc)
        return None


async def delete_plan(
    schedule_id: int,
    auth_token: str,
) -> bool:
    """Delete a schedule record."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"{_base_url()}/api/schedule/{schedule_id}",
                headers={"Authorization": f"Bearer {auth_token}"},
            )
            resp.raise_for_status()
            logger.info("[schedule_commands] deleted plan id=%s", schedule_id)
            return True
    except Exception as exc:
        logger.error("[schedule_commands] delete_plan id=%s failed: %s", schedule_id, exc)
        return False


async def fetch_upcoming(
    auth_token: str,
    from_date: str,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """
    Fetch all meal_plan records from from_date for the next `days` days.
    Returns a list of (date, meal_type, dish_names) summaries.
    """
    from datetime import date as _date, timedelta
    start = f"{from_date}T00:00:00"
    end_d = (_date.fromisoformat(from_date) + timedelta(days=days)).isoformat()
    end = f"{end_d}T23:59:59"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_base_url()}/api/schedule/range",
                headers={"Authorization": f"Bearer {auth_token}"},
                params={"start_time": start, "end_time": end},
            )
            resp.raise_for_status()
            records = resp.json()

        summaries = []
        for r in records:
            try:
                features = (r.get("metadata") or {}).get("features") or []
                for feat in features:
                    if feat.get("type") != "meal_plan":
                        continue
                    for plan in feat.get("plans") or []:
                        plan_date = plan.get("date", "")
                        for meal in plan.get("meals") or []:
                            meal_type = meal.get("mealTime", "")
                            dish_names = [d["name"] for d in meal.get("dishes") or []]
                            if dish_names:
                                summaries.append({
                                    "date": plan_date,
                                    "meal_type": meal_type,
                                    "dishes": dish_names,
                                })
            except Exception:
                pass
        summaries.sort(key=lambda x: (x["date"], x["meal_type"]))
        logger.info("[fetch_upcoming] from=%s days=%s → %d record(s)", from_date, days, len(summaries))
        return summaries
    except Exception as exc:
        logger.error("[schedule_commands] fetch_upcoming failed: %s", exc)
        return []


async def fetch_dishes_with_ingredients(
    auth_token: str,
    from_date: str,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """
    Fetch meal_plan records in [from_date, from_date+days) and return a flat list
    of dishes with their ingredients — used to build a shopping list for pricing:
      [{"date","meal_type","name","ingredients":[{"name","quantity"}]}]
    """
    from datetime import date as _date, timedelta
    start = f"{from_date}T00:00:00"
    end_d = (_date.fromisoformat(from_date) + timedelta(days=days)).isoformat()
    end = f"{end_d}T23:59:59"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_base_url()}/api/schedule/range",
                headers={"Authorization": f"Bearer {auth_token}"},
                params={"start_time": start, "end_time": end},
            )
            resp.raise_for_status()
            records = resp.json()

        out: List[Dict[str, Any]] = []
        for r in records:
            features = (r.get("metadata") or {}).get("features") or []
            for feat in features:
                if feat.get("type") != "meal_plan":
                    continue
                for plan in feat.get("plans") or []:
                    plan_date = plan.get("date", "")
                    for meal in plan.get("meals") or []:
                        meal_type = meal.get("mealTime", "")
                        for d in meal.get("dishes") or []:
                            out.append({
                                "date": plan_date,
                                "meal_type": meal_type,
                                "name": d.get("name", ""),
                                "ingredients": [
                                    {"name": i.get("name", ""), "quantity": i.get("quantity", "")}
                                    for i in d.get("ingredients") or []
                                ],
                            })
        logger.info("[fetch_dishes_with_ingredients] from=%s days=%s → %d dish(es)", from_date, days, len(out))
        return out
    except Exception as exc:
        logger.error("[schedule_commands] fetch_dishes_with_ingredients failed: %s", exc)
        return []


async def fetch_existing(
    date: str,
    meal_type: str,
    auth_token: str,
) -> Optional[Dict[str, Any]]:
    """
    Look up whether a schedule already exists for the given date and meal type.
    Returns the first matching record, or None.

    The query window is widened by one day on each side: a meal's stored
    `scheduled_time` is timezone-aware, so for negative-UTC-offset users an
    18:00 dinner lands on the *next* UTC day (e.g. 18:00-07:00 → 01:00Z next
    day). A naive same-day window would miss it and cause a duplicate save.
    Correctness is preserved by the exact `plan.date == date` filter below.
    """
    from datetime import date as _date, timedelta
    d = _date.fromisoformat(date)
    start = f"{(d - timedelta(days=1)).isoformat()}T00:00:00"
    end = f"{(d + timedelta(days=1)).isoformat()}T23:59:59"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_base_url()}/api/schedule/range",
                headers={"Authorization": f"Bearer {auth_token}"},
                params={"start_time": start, "end_time": end},
            )
            resp.raise_for_status()
            records = resp.json()

        logger.info(
            "[fetch_existing] GET %s/api/schedule/range params=%s → %d record(s) raw=%s",
            _base_url(), {"start_time": start, "end_time": end}, len(records),
            str(records)[:300],
        )
        for r in records:
            # meal_type is stored inside features[].plans[].meals[].mealTime
            try:
                features = (r.get("metadata") or {}).get("features") or []
                for feat in features:
                    if feat.get("type") == "meal_plan":
                        for plan in feat.get("plans") or []:
                            if plan.get("date") != date:
                                continue
                            for meal in plan.get("meals") or []:
                                if meal.get("mealTime") == meal_type:
                                    logger.info("[fetch_existing] matched record id=%s", r.get("id"))
                                    return r
            except Exception:
                pass
        logger.info("[fetch_existing] no match found for date=%s meal_type=%s", date, meal_type)
        return None
    except Exception as exc:
        logger.error("[schedule_commands] fetch_existing failed: %s", exc)
        return None
