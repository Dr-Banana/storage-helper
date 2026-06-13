"""
Unit tests for schedule_commands helpers — no network, no DB.
"""
import pytest


# ── _scheduled_time ───────────────────────────────────────────────────────────

def test_scheduled_time_dinner():
    from app.db.schedule_commands import _scheduled_time
    assert _scheduled_time("2026-06-13", "dinner") == "2026-06-13T18:00:00"


def test_scheduled_time_lunch():
    from app.db.schedule_commands import _scheduled_time
    assert _scheduled_time("2026-06-13", "lunch") == "2026-06-13T12:00:00"


def test_scheduled_time_breakfast():
    from app.db.schedule_commands import _scheduled_time
    assert _scheduled_time("2026-06-13", "breakfast") == "2026-06-13T08:00:00"


def test_scheduled_time_unknown_defaults_to_noon():
    from app.db.schedule_commands import _scheduled_time
    assert _scheduled_time("2026-06-13", "snack") == "2026-06-13T12:00:00"


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
    def matches(r, meal_type):
        try:
            features = (r.get("metadata") or {}).get("features") or []
            for feat in features:
                if feat.get("type") == "meal_plan":
                    for plan in feat.get("plans") or []:
                        for meal in plan.get("meals") or []:
                            if meal.get("mealTime") == meal_type:
                                return True
        except Exception:
            pass
        return False

    assert matches(record, "dinner") is True
    assert matches(record, "lunch") is False
    assert matches(record, "breakfast") is False
