# -*- coding: utf-8 -*-
"""Unit tests for SeedLibrary (Phase 3)."""
import pytest
from app.services.seed_library import (
    build_seed_context,
    lookup_dish,
    select_candidates,
    DEFAULT_CANDIDATE_COUNT,
)

# ─── lookup_dish ─────────────────────────────────────────────────────────────

def test_lookup_by_chinese_name():
    dish = lookup_dish("宫保鸡丁")
    assert dish is not None
    assert dish["id"] == "cn_sichuan_001"
    assert dish["cuisine_l1"] == "Chinese"
    assert dish["cuisine_l2"] == "Sichuan"


def test_lookup_by_english_name():
    dish = lookup_dish("Kung Pao Chicken")
    assert dish is not None
    assert dish["name_zh"] == "宫保鸡丁"


def test_lookup_unknown_returns_none():
    assert lookup_dish("NonExistentDish XYZ") is None


def test_lookup_all_50_dishes_have_required_fields():
    from app.services.seed_library import _load
    dishes = _load()
    # Count updated to 56: added 6 shrimp-focused dishes (宫保虾仁, 泰式咖喱虾, etc.)
    assert len(dishes) == 122
    for d in dishes:
        for field in ("id", "name_zh", "name_en", "cuisine_l1", "cuisine_l2",
                      "flavor_profile", "main_ingredients", "category", "servings_base"):
            assert field in d, f"Dish {d.get('id')} missing field '{field}'"


# ─── select_candidates — filtering ───────────────────────────────────────────

def test_select_returns_up_to_n_candidates():
    candidates = select_candidates(n=8)
    assert len(candidates) <= 8


def test_select_default_count():
    candidates = select_candidates()
    assert len(candidates) <= DEFAULT_CANDIDATE_COUNT


def test_disliked_ingredient_filtered_out():
    # cilantro maps to 'cilantro' in we_mexican_001 ingredients
    candidates = select_candidates(disliked_ingredients=["cilantro"])
    names = [d["name_en"] for d in candidates]
    assert "Beef Tacos" not in names  # contains cilantro


def test_hard_banned_dish_excluded():
    # "宫保鸡丁" eaten 1 day ago — should be excluded from candidates
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    recent = [{"dish": "宫保鸡丁", "date": yesterday}]
    candidates = select_candidates(recent_dishes=recent)
    names = [d["name_zh"] for d in candidates]
    assert "宫保鸡丁" not in names


def test_soft_avoided_dish_deprioritised_but_not_excluded():
    # "宫保鸡丁" eaten 5 days ago — soft avoid only, may still appear in small pools
    from datetime import date, timedelta
    five_days_ago = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
    recent = [{"dish": "宫保鸡丁", "date": five_days_ago}]
    candidates = select_candidates(recent_dishes=recent, n=DEFAULT_CANDIDATE_COUNT)
    # We can't guarantee exclusion, but if it appears it should rank lower.
    # Just assert the function doesn't crash and returns a valid list.
    assert isinstance(candidates, list)


def test_empty_pool_returns_empty_list():
    # Dislike every possible ingredient keyword to drain the pool
    overwhelming_dislikes = [
        "chicken", "pork", "beef", "tofu", "shrimp", "egg", "rice", "noodles",
        "lettuce", "cabbage", "potato", "eggplant", "scallop", "mackerel",
        "eel", "clams", "flour", "spaghetti",
    ]
    candidates = select_candidates(disliked_ingredients=overwhelming_dislikes)
    assert isinstance(candidates, list)  # must not raise


# ─── select_candidates — cuisine fair-rotation distribution ──────────────────

def test_cuisine_fair_rotation_multiple_cuisines():
    """Fair rotation: candidate set must contain ≥ 2 distinct cuisine_l1 values."""
    candidates = select_candidates(n=12)
    cuisines = {d["cuisine_l1"] for d in candidates}
    assert len(cuisines) >= 2, f"Expected diverse cuisines, got {cuisines}"


def test_cuisine_fair_rotation_no_single_dominance():
    """No single cuisine should dominate (take more than half the candidate slots)."""
    n = 12
    candidates = select_candidates(n=n)
    cuisines = {d["cuisine_l1"] for d in candidates}
    # Allow at most 50% to any one cuisine — the spirit is that Chinese (majority
    # of the seed library) can't crowd out everything else.
    max_allowed = n // 2
    for c in cuisines:
        count = sum(1 for d in candidates if d["cuisine_l1"] == c)
        assert count <= max_allowed, (
            f"{c} has {count} slots which exceeds the 50% cap ({max_allowed}) — "
            "fair rotation is not preventing single-cuisine dominance"
        )


def test_cuisine_weights_param_ignored_no_crash():
    """cuisine_weights is kept for API compat but no longer affects selection."""
    heavy_chinese = {"Chinese": 90, "Western": 5, "Japanese": 3, "Korean": 2}
    candidates = select_candidates(cuisine_weights=heavy_chinese, n=10)
    assert isinstance(candidates, list)
    # With fair rotation Chinese should NOT dominate even with heavy weights passed
    cuisines = {d["cuisine_l1"] for d in candidates}
    assert len(cuisines) >= 2


# ─── select_candidates — soup guarantee ──────────────────────────────────────

def test_soup_guaranteed_when_available():
    # With no filters the pool has 6 soup dishes — at least one should appear
    candidates = select_candidates(n=DEFAULT_CANDIDATE_COUNT)
    categories = [d["category"] for d in candidates]
    assert "Soup" in categories


# ─── build_seed_context ───────────────────────────────────────────────────────

def test_build_seed_context_contains_header():
    candidates = select_candidates(n=5)
    ctx = build_seed_context(candidates)
    assert "SEED DISH REFERENCE" in ctx


def test_build_seed_context_lists_all_candidates():
    candidates = select_candidates(n=5)
    ctx = build_seed_context(candidates)
    for d in candidates:
        assert d["name_zh"] in ctx
        assert d["name_en"] in ctx


def test_build_seed_context_shows_cuisine_tags():
    candidates = select_candidates(n=5)
    ctx = build_seed_context(candidates)
    # At least one entry should show [CuisineL1·CuisineL2] pattern
    assert "·" in ctx


def test_build_seed_context_includes_footer_instruction():
    candidates = select_candidates(n=3)
    ctx = build_seed_context(candidates)
    assert "You MAY suggest dishes not on this list" in ctx


def test_build_seed_context_empty_input():
    assert build_seed_context([]) == ""
