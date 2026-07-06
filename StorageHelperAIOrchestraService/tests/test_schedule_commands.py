"""
Unit tests for schedule_commands helpers — no network, no DB.
"""
import pytest


# ── _scheduled_time ───────────────────────────────────────────────────────────

def test_scheduled_time_dinner():
    from app.db.schedule_commands import _scheduled_time
    # No timezone → UTC offset (+00:00)
    assert _scheduled_time("2026-06-13", "dinner") == "2026-06-13T18:00:00+00:00"


def test_scheduled_time_lunch():
    from app.db.schedule_commands import _scheduled_time
    assert _scheduled_time("2026-06-13", "lunch") == "2026-06-13T12:00:00+00:00"


def test_scheduled_time_breakfast():
    from app.db.schedule_commands import _scheduled_time
    assert _scheduled_time("2026-06-13", "breakfast") == "2026-06-13T08:00:00+00:00"


def test_scheduled_time_unknown_defaults_to_noon():
    from app.db.schedule_commands import _scheduled_time
    assert _scheduled_time("2026-06-13", "snack") == "2026-06-13T12:00:00+00:00"


def test_scheduled_time_with_timezone():
    from app.db.schedule_commands import _scheduled_time
    result = _scheduled_time("2026-06-13", "dinner", user_timezone="Asia/Shanghai")
    assert result == "2026-06-13T18:00:00+08:00"


# ── _build_metadata ───────────────────────────────────────────────────────────

def test_build_metadata_structure():
    from app.db.schedule_commands import _build_metadata
    dishes = [
        {
            "name": "Pork Ribs",
            "ingredients": [{"name": "pork ribs", "quantity": "500g"}],
            "steps": ["Step 1", "Step 2"],
        }
    ]
    meta = _build_metadata("2026-06-13", "dinner", dishes)
    features = meta["features"]
    assert len(features) == 1
    feat = features[0]
    assert feat["type"] == "meal_plan"
    assert len(feat["plans"]) == 1
    plan = feat["plans"][0]
    assert plan["date"] == "2026-06-13"
    meal = plan["meals"][0]
    assert meal["mealTime"] == "dinner"
    dish = meal["dishes"][0]
    assert dish["name"] == "Pork Ribs"
    assert dish["ingredients"] == [{"name": "pork ribs", "quantity": "500g"}]
    assert dish["cookingSteps"] == ["Step 1", "Step 2"]


def test_build_metadata_dish_ids_are_unique():
    from app.db.schedule_commands import _build_metadata
    dishes = [
        {"name": "A", "ingredients": [], "steps": []},
        {"name": "B", "ingredients": [], "steps": []},
    ]
    meta = _build_metadata("2026-06-13", "dinner", dishes)
    dish_ids = [d["id"] for d in meta["features"][0]["plans"][0]["meals"][0]["dishes"]]
    assert len(set(dish_ids)) == 2


# ── extract_dishes_from_record ────────────────────────────────────────────────

def test_extract_dishes_from_record_happy_path():
    from app.db.schedule_commands import extract_dishes_from_record, _build_metadata
    dishes = [
        {
            "name": "Braised Pork Ribs",
            "ingredients": [{"name": "pork ribs", "quantity": "500g"}],
            "steps": ["Marinate", "Braise"],
        }
    ]
    record = {"metadata": _build_metadata("2026-06-13", "dinner", dishes)}
    extracted = extract_dishes_from_record(record)
    assert len(extracted) == 1
    assert extracted[0]["name"] == "Braised Pork Ribs"
    assert extracted[0]["steps"] == ["Marinate", "Braise"]


def test_extract_dishes_from_record_empty_metadata():
    from app.db.schedule_commands import extract_dishes_from_record
    assert extract_dishes_from_record({}) == []
    assert extract_dishes_from_record({"metadata": None}) == []
    assert extract_dishes_from_record({"metadata": {}}) == []


def test_extract_dishes_from_record_multiple_dishes():
    from app.db.schedule_commands import extract_dishes_from_record, _build_metadata
    dishes = [
        {"name": "Dish A", "ingredients": [], "steps": ["s1"]},
        {"name": "Dish B", "ingredients": [], "steps": ["s2"]},
    ]
    record = {"metadata": _build_metadata("2026-06-13", "dinner", dishes)}
    extracted = extract_dishes_from_record(record)
    assert {d["name"] for d in extracted} == {"Dish A", "Dish B"}


# ── fetch_existing matching logic (unit-level, no HTTP) ──────────────────────

def test_fetch_existing_meal_type_matching():
    """Verify the mealTime matching logic used inside fetch_existing."""
    from app.db.schedule_commands import _build_metadata

    # Build a record as if it came from the DB
    record = {"id": 42, "metadata": _build_metadata("2026-06-13", "dinner", [])}

    # Replicate the matching logic from fetch_existing
    def matches(r, date, meal_type):
        try:
            features = (r.get("metadata") or {}).get("features") or []
            for feat in features:
                if feat.get("type") == "meal_plan":
                    for plan in feat.get("plans") or []:
                        if plan.get("date") != date:
                            continue
                        for meal in plan.get("meals") or []:
                            if meal.get("mealTime") == meal_type:
                                return True
        except Exception:
            pass
        return False

    assert matches(record, "2026-06-13", "dinner") is True
    assert matches(record, "2026-06-13", "lunch") is False
    assert matches(record, "2026-06-14", "dinner") is False  # wrong date


# ── fetch_existing: timezone/date regression ──────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_existing_rejects_record_with_mismatched_metadata_date():
    """
    Regression: a record with scheduled_time=2026-06-14T01:00:00 (UTC) but
    metadata plan.date=2026-06-13 must NOT be returned for a 2026-06-14 query.

    Root cause: the DB stores UTC times, so a June 13 18:00 CST dinner appears
    in the June 14 naive range query. The fix is to filter by metadata plan.date.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.db.schedule_commands import _build_metadata, fetch_existing

    june13_record = {
        "id": 37,
        "scheduled_time": "2026-06-14T01:00:00",  # UTC — looks like June 14 in naive range
        "metadata": _build_metadata("2026-06-13", "dinner", [
            {"name": "Curry Beef", "ingredients": [], "steps": []}
        ]),
    }
    june14_record = {
        "id": 38,
        "scheduled_time": "2026-06-14T10:00:00",
        "metadata": _build_metadata("2026-06-14", "dinner", [
            {"name": "Zucchini Egg Soup", "ingredients": [], "steps": []}
        ]),
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=[june13_record, june14_record])

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_existing("2026-06-14", "dinner", "token")

    # Must return June 14's record, not the June 13 record that leaked via UTC offset
    assert result is not None
    assert result["id"] == 38


@pytest.mark.asyncio
async def test_fetch_existing_returns_none_when_only_wrong_date_record_present():
    """
    If the only matching mealTime record has the wrong metadata date,
    fetch_existing must return None — not a false positive.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.db.schedule_commands import _build_metadata, fetch_existing

    june13_record = {
        "id": 37,
        "scheduled_time": "2026-06-14T01:00:00",
        "metadata": _build_metadata("2026-06-13", "dinner", [
            {"name": "Curry Beef", "ingredients": [], "steps": []}
        ]),
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=[june13_record])

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_existing("2026-06-14", "dinner", "token")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_existing_queries_widened_window():
    """
    Regression (prod duplicate saves, ids 109/110): an 18:00 dinner for a
    -07:00 user is stored as next-day 01:00 UTC, so a naive same-day range
    query missed the record and phase 2 created a duplicate. fetch_existing
    must query one day on each side; exact matching is done by plan.date.
    Also covers month boundaries via date arithmetic.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.db.schedule_commands import fetch_existing

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=[])

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await fetch_existing("2026-07-06", "dinner", "token")
    params = mock_client.get.call_args.kwargs["params"]
    assert params["start_time"] == "2026-07-05T00:00:00"
    assert params["end_time"] == "2026-07-07T23:59:59"

    with patch("httpx.AsyncClient", return_value=mock_client):
        await fetch_existing("2026-08-01", "dinner", "token")
    params = mock_client.get.call_args.kwargs["params"]
    assert params["start_time"] == "2026-07-31T00:00:00", "Window must cross month boundaries"
    assert params["end_time"] == "2026-08-02T23:59:59"


@pytest.mark.asyncio
async def test_fetch_existing_finds_utc_shifted_record_in_widened_window():
    """
    The prod failure record: metadata date=2026-07-06, scheduled_time stored
    as 2026-07-07T01:00:00 UTC. With the widened window the backend returns
    it and fetch_existing must match it for date=2026-07-06.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.db.schedule_commands import _build_metadata, fetch_existing

    utc_shifted = {
        "id": 109,
        "scheduled_time": "2026-07-07T01:00:00",  # 2026-07-06 18:00 at -07:00
        "metadata": _build_metadata("2026-07-06", "dinner", [
            {"name": "Braised Beef Brisket", "ingredients": [], "steps": []}
        ]),
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=[utc_shifted])

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_existing("2026-07-06", "dinner", "token")

    assert result is not None
    assert result["id"] == 109
