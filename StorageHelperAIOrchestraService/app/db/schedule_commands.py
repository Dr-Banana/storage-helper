"""
Schedule DB Commands — hardcoded HTTP calls to DataStorageService.

Not routed through LLM decisions. The agent calls these directly
once it has determined the correct operation.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_MEAL_TIME_TO_HOUR = {"breakfast": 8, "lunch": 12, "dinner": 18}


def _base_url() -> str:
    url = settings.STORAGE_SERVICE_URL.rstrip("/")
    for suffix in ("/internal", "/api/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def _scheduled_time(date: str, meal_type: str) -> str:
    """'2026-06-11' + 'dinner'  →  '2026-06-11T18:00:00'"""
    hour = _MEAL_TIME_TO_HOUR.get(meal_type, 12)
    return f"{date}T{hour:02d}:00:00"


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
        "event_type": "meal",
        "scheduled_time": _scheduled_time(date, meal_type),
        "metadata": {
            "meal_type": meal_type,
            "dishes": dishes,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_base_url()}/internal/schedule",
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
    dishes: List[Dict[str, Any]],
    auth_token: str,
) -> Optional[Dict[str, Any]]:
    """Update dishes and steps on an existing schedule record."""
    payload = {
        "metadata": {"dishes": dishes},
        "title": f"Updated — {', '.join(d['name'] for d in dishes)}",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{_base_url()}/internal/schedule/{schedule_id}",
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
                f"{_base_url()}/internal/schedule/{schedule_id}",
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
                f"{_base_url()}/internal/schedule/range",
                headers={"Authorization": f"Bearer {auth_token}"},
                params={"start_time": start, "end_time": end},
            )
            resp.raise_for_status()
            records = resp.json()

        for r in records:
            meta = r.get("metadata") or {}
            if meta.get("meal_type") == meal_type:
                return r
        return None
    except Exception as exc:
        logger.error("[schedule_commands] fetch_existing failed: %s", exc)
        return None
