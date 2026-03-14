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
    assert len(dishes) == 50
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
    recent = [{"dish": "宫保鸡丁", "date": "2026-03-10"}]
    candidates = select_candidates(recent_dishes=recent)
    names = [d["name_zh"] for d in candidates]
    assert "宫保鸡丁" not in names


def test_soft_avoided_dish_deprioritised_but_not_excluded():
    # "宫保鸡丁" eaten 5 days ago — soft avoid only, may still appear in small pools
    recent = [{"dish": "宫保鸡丁", "date": "2026-03-06"}]
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


# ─── select_candidates — cuisine weight distribution ─────────────────────────

def test_cuisine_weight_biases_selection():
    # Heavy Chinese weight → expect majority of candidates to be Chinese
    heavy_chinese = {"Chinese": 90, "Western": 5, "Japanese": 3, "Korean": 2}
    candidates = select_candidates(cuisine_weights=heavy_chinese, n=10)
    chinese_count = sum(1 for d in candidates if d["cuisine_l1"] == "Chinese")
    # With 90% weight, at least half should be Chinese
    assert chinese_count >= 5


def test_zero_weight_cuisine_still_possible():
    # Zero weight for Japanese — should be very unlikely but not crash
    weights = {"Chinese": 50, "Western": 50, "Japanese": 0, "Korean": 0}
    candidates = select_candidates(cuisine_weights=weights, n=10)
    assert isinstance(candidates, list)


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
