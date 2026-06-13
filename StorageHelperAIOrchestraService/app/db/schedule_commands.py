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


def _scheduled_time(date: str, meal_type: str) -> str:
    """'2026-06-11' + 'dinner'  →  '2026-06-11T18:00:00'"""
    hour = _MEAL_TIME_TO_HOUR.get(meal_type, 12)
    return f"{date}T{hour:02d}:00:00"


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
) -> Optional[Dict[str, Any]]:
    """
    Create a schedule record with the dish list and cooking steps.

    dishes format:
      [{"name": str, "ingredients": [...], "steps": [...]}]
    """
    payload = {
        "title": f"{meal_type.capitalize()} — {', '.join(d['name'] for d in dishes)}",
        "event_type": "meal_plan",
        "scheduled_time": _scheduled_time(date, meal_type),
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
            logger.info("[schedule_commands] saved plan id=%s", result.get("id"))
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


async def fetch_existing(
    date: str,
    meal_type: str,
    auth_token: str,
) -> Optional[Dict[str, Any]]:
    """
    Look up whether a schedule already exists for the given date and meal type.
    Returns the first matching record, or None.
    """
    start = f"{date}T00:00:00"
    end = f"{date}T23:59:59"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_base_url()}/api/schedule/range",
                headers={"Authorization": f"Bearer {auth_token}"},
                params={"start_time": start, "end_time": end},
            )
            resp.raise_for_status()
            records = resp.json()

        for r in records:
            # meal_type is stored inside features[].plans[].meals[].mealTime
            try:
                features = (r.get("metadata") or {}).get("features") or []
                for feat in features:
                    if feat.get("type") == "meal_plan":
                        for plan in feat.get("plans") or []:
                            for meal in plan.get("meals") or []:
                                if meal.get("mealTime") == meal_type:
                                    return r
            except Exception:
                pass
        return None
    except Exception as exc:
        logger.error("[schedule_commands] fetch_existing failed: %s", exc)
        return None
