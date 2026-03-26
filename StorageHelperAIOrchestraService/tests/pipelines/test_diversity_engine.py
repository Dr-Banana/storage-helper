"""Quick smoke tests for DiversityEngine (Phase 2)."""
from datetime import date
from app.services.diversity_engine import (
    compute_diversity_directive,
    extract_dishes_for_history,
    merge_and_prune_recent_dishes,
    recency_penalty,
    HARD_BAN_DAYS,
    SOFT_AVOID_DAYS,
    WINDOW_DAYS,
)

TODAY = date(2026, 3, 11)


def test_recency_penalty_values():
    assert recency_penalty(0) == 1.0
    assert recency_penalty(7) == 0.5
    assert recency_penalty(14) == 0.0
    assert recency_penalty(20) == 0.0


def test_hard_ban_recent_dish():
    recent = [{"dish": "Tomato Egg Stir-fry", "date": "2026-03-10"}]  # 1 day ago
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "HARD BAN" in directive
    assert "Tomato Egg Stir-fry" in directive


def test_soft_avoid_5days():
    recent = [{"dish": "Kung Pao Chicken", "date": "2026-03-06"}]  # 5 days ago
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "SOFT AVOID" in directive
    assert "Kung Pao Chicken" in directive


def test_old_dish_cleared():
    recent = [{"dish": "Red Braised Pork", "date": "2026-02-20"}]  # >14 days ago
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "HARD BAN" not in directive
    assert "SOFT AVOID" not in directive
    assert "No recent dish history" in directive


def test_cuisine_variety_target():
    cw = {"Chinese": 70, "Western": 20, "Japanese": 10}
    directive = compute_diversity_directive([], cw, today=TODAY)
    assert "WEEKLY VARIETY TARGET" in directive
    assert "Chinese" in directive


def test_cuisine_weight_zero_skipped():
    cw = {"Chinese": 100, "Western": 0}
    directive = compute_diversity_directive([], cw, today=TODAY)
    assert "Chinese" in directive
    assert "Western" not in directive


def test_extract_dishes_from_slots():
    slots = {
        "2026-03-11": {"dinner": ["Mapo Tofu", "Stir-fried Broccoli"]},
        "2026-03-12": {"lunch": ["Tomato Egg Noodles"], "dinner": ["Red Braised Pork"]},
    }
    entries = extract_dishes_for_history(slots, today=TODAY)
    names = [e["dish"] for e in entries]
    assert "Mapo Tofu" in names
    assert "Stir-fried Broccoli" in names
    assert "Tomato Egg Noodles" in names
    assert "Red Braised Pork" in names


def test_extract_dishes_prunes_old_dates():
    slots = {
        "2026-02-01": {"dinner": ["Old Dish"]},   # >14 days ago → pruned
        "2026-03-10": {"dinner": ["Fresh Dish"]},
    }
    entries = extract_dishes_for_history(slots, today=TODAY)
    names = [e["dish"] for e in entries]
    assert "Old Dish" not in names
    assert "Fresh Dish" in names


def test_merge_and_prune_removes_old():
    old_history = [
        {"dish": "Expired Dish", "date": "2026-02-01"},   # >14 days → pruned
        {"dish": "Recent Dish", "date": "2026-03-05"},    # 6 days → kept
    ]
    new_entries = [{"dish": "New Dish", "date": "2026-03-11"}]
    merged = merge_and_prune_recent_dishes(old_history, new_entries, today=TODAY)
    names = [e["dish"] for e in merged]
    assert "Expired Dish" not in names
    assert "Recent Dish" in names
    assert "New Dish" in names


def test_merge_and_prune_deduplicates():
    existing = [{"dish": "Tomato Egg Stir-fry", "date": "2026-03-10"}]
    new_entries = [{"dish": "Tomato Egg Stir-fry", "date": "2026-03-10"}]  # duplicate
    merged = merge_and_prune_recent_dishes(existing, new_entries, today=TODAY)
    assert len([e for e in merged if e["dish"] == "Tomato Egg Stir-fry"]) == 1


def test_directive_with_empty_recent_dishes():
    directive = compute_diversity_directive([], today=TODAY)
    assert "No recent dish history" in directive


def test_directive_with_none_recent_dishes():
    directive = compute_diversity_directive(None, today=TODAY)
    assert "No recent dish history" in directive


# ── Ingredient-level soft avoid tests ────────────────────────────────────────

def test_extract_dishes_attaches_ingredients_str():
    """String ingredient values are stored as-is."""
    slots = {"2026-03-10": {"dinner": ["Tomato Braised Pork Ribs"]}}
    dish_ingredients = {"Tomato Braised Pork Ribs": ["tomato", "pork ribs", "ginger"]}
    entries = extract_dishes_for_history(slots, dish_ingredients=dish_ingredients, today=TODAY)
    assert entries[0]["dish"] == "Tomato Braised Pork Ribs"
    assert entries[0]["ingredients"] == ["tomato", "pork ribs", "ginger"]


def test_extract_dishes_attaches_ingredients_dict():
    """Dict ingredient values (from dish_ingredients state) are normalised to name strings."""
    slots = {"2026-03-10": {"dinner": ["Kung Pao Chicken"]}}
    dish_ingredients = {
        "Kung Pao Chicken": [{"name": "chicken", "qty": "200g"}, {"name": "peanut", "qty": "50g"}]
    }
    entries = extract_dishes_for_history(slots, dish_ingredients=dish_ingredients, today=TODAY)
    assert entries[0]["ingredients"] == ["chicken", "peanut"]


def test_extract_dishes_no_ingredients_when_not_provided():
    """Without dish_ingredients, entries have no 'ingredients' key."""
    slots = {"2026-03-10": {"dinner": ["Mapo Tofu"]}}
    entries = extract_dishes_for_history(slots, today=TODAY)
    assert "ingredients" not in entries[0]


def test_ingredient_soft_avoid_in_directive():
    """Stored ingredients from hard-banned dishes appear in INGREDIENT SOFT AVOID."""
    recent = [
        {
            "dish": "Tomato Braised Pork Ribs",
            "date": "2026-03-10",  # 1 day ago → hard ban
            "ingredients": ["tomato", "pork ribs", "ginger"],
        },
        {
            "dish": "Italian Tomato Bolognese",
            "date": "2026-03-09",  # 2 days ago → hard ban
            "ingredients": ["tomato", "minced pork", "pasta"],
        },
    ]
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "INGREDIENT SOFT AVOID" in directive
    assert "tomato" in directive
    assert "pork ribs" in directive
    # "tomato" should appear exactly once in the INGREDIENT SOFT AVOID line (deduped),
    # even though two banned dishes both list it as an ingredient.
    ingredient_line = next(l for l in directive.splitlines() if "INGREDIENT SOFT AVOID" in l)
    assert ingredient_line.count("tomato") == 1


def test_ingredient_soft_avoid_not_injected_for_soft_only():
    """INGREDIENT SOFT AVOID should NOT appear for soft-avoided dishes (only hard-banned)."""
    recent = [
        {
            "dish": "Tomato Egg Stir-fry",
            "date": "2026-03-05",  # 6 days ago → soft avoid only
            "ingredients": ["tomato", "egg"],
        },
    ]
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "SOFT AVOID" in directive
    assert "INGREDIENT SOFT AVOID" not in directive


def test_ingredient_soft_avoid_absent_when_no_ingredients_stored():
    """When hard-banned entries carry no ingredients, INGREDIENT SOFT AVOID is omitted."""
    recent = [{"dish": "Red Braised Pork", "date": "2026-03-10"}]  # no "ingredients" key
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "HARD BAN" in directive
    assert "INGREDIENT SOFT AVOID" not in directive


def test_ingredient_soft_avoid_language_agnostic():
    """Works with any language: Japanese dish names and ingredient lists."""
    recent = [
        {
            "dish": "トマト煮込みビーフ",  # tomato braised beef
            "date": "2026-03-10",
            "ingredients": ["トマト", "牛肉", "玉ねぎ"],
        },
    ]
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "INGREDIENT SOFT AVOID" in directive
    assert "トマト" in directive
    assert "牛肉" in directive
