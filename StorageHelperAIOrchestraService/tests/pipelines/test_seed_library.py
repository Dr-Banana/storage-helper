# -*- coding: utf-8 -*-
"""Unit tests for SeedLibrary algorithms.

HowToCookService is mocked with a small, controlled catalog so tests are
independent of live MCP connections and seed_dishes.json content.
The tests verify the selection/filtering/formatting LOGIC, not data content.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import date, timedelta

from app.services.seed_library import (
    build_seed_context,
    lookup_dish,
    select_candidates,
    invalidate_cache,
    DEFAULT_CANDIDATE_COUNT,
)

# ── Controlled catalog ────────────────────────────────────────────────────────
# Covers: multi-cuisine, Soup category, ingredient filtering, name lookup.

_MOCK_CATALOG = [
    # Chinese — Meat
    {"id": "mock_cn_001", "name_zh": "宫保鸡丁", "name_en": "Kung Pao Chicken",
     "cuisine_l1": "Chinese", "cuisine_l2": "Sichuan",
     "flavor_profile": ["spicy", "savory"], "main_ingredients": ["鸡肉", "花生", "辣椒"],
     "category": "Meat", "servings_base": 2},
    {"id": "mock_cn_002", "name_zh": "红烧肉", "name_en": "Red Braised Pork",
     "cuisine_l1": "Chinese", "cuisine_l2": "Home Cooking",
     "flavor_profile": ["rich", "savory"], "main_ingredients": ["五花肉", "生抽"],
     "category": "Meat", "servings_base": 2},
    # Chinese — Vegetable
    {"id": "mock_cn_003", "name_zh": "番茄炒蛋", "name_en": "Tomato and Egg Stir-fry",
     "cuisine_l1": "Chinese", "cuisine_l2": "Home Cooking",
     "flavor_profile": ["savory"], "main_ingredients": ["番茄", "鸡蛋"],
     "category": "Vegetable", "servings_base": 2},
    # Chinese — Soup
    {"id": "mock_cn_004", "name_zh": "紫菜蛋花汤", "name_en": "Seaweed Egg Soup",
     "cuisine_l1": "Chinese", "cuisine_l2": "Soups",
     "flavor_profile": ["light"], "main_ingredients": ["紫菜", "鸡蛋"],
     "category": "Soup", "servings_base": 2},
    # Western — needed for multi-cuisine and ingredient-filter tests
    {"id": "mock_we_001", "name_zh": "牛肉塔可", "name_en": "Beef Tacos",
     "cuisine_l1": "Western", "cuisine_l2": "Mexican",
     "flavor_profile": ["savory"], "main_ingredients": ["beef", "cilantro", "tortilla"],
     "category": "Meat", "servings_base": 2},
    {"id": "mock_we_002", "name_zh": "意大利面", "name_en": "Spaghetti",
     "cuisine_l1": "Western", "cuisine_l2": "Italian",
     "flavor_profile": ["savory"], "main_ingredients": ["spaghetti", "tomato"],
     "category": "Staple", "servings_base": 2},
    # Japanese
    {"id": "mock_jp_001", "name_zh": "照烧鸡腿", "name_en": "Teriyaki Chicken",
     "cuisine_l1": "Japanese", "cuisine_l2": "Traditional",
     "flavor_profile": ["sweet", "savory"], "main_ingredients": ["chicken", "soy sauce"],
     "category": "Meat", "servings_base": 2},
]


@pytest.fixture(autouse=True)
def mock_howtocook_catalog():
    """Inject controlled catalog and reset seed cache before/after each test."""
    mock_svc = MagicMock()
    mock_svc.get_dish_catalog.return_value = list(_MOCK_CATALOG)
    mock_svc.lookup_dish.side_effect = lambda name: next(
        (d for d in _MOCK_CATALOG
         if name in (d["name_zh"], d["name_en"])
         or name.lower() in d["name_zh"].lower()
         or d["name_zh"].lower() in name.lower()),
        None,
    )
    with patch("app.services.howtocook_service.get_howtocook_service", return_value=mock_svc):
        invalidate_cache()
        yield
    invalidate_cache()


# ─── lookup_dish ──────────────────────────────────────────────────────────────

def test_lookup_by_chinese_name():
    dish = lookup_dish("宫保鸡丁")
    assert dish is not None
    assert dish["id"] == "mock_cn_001"
    assert dish["cuisine_l1"] == "Chinese"


def test_lookup_by_english_name():
    dish = lookup_dish("Kung Pao Chicken")
    assert dish is not None
    assert dish["name_zh"] == "宫保鸡丁"


def test_lookup_unknown_returns_none():
    assert lookup_dish("NonExistentDish XYZ") is None


def test_catalog_entries_have_required_fields():
    from app.services.seed_library import _load
    dishes = _load()
    assert len(dishes) == len(_MOCK_CATALOG)
    for d in dishes:
        for field in ("id", "name_zh", "name_en", "cuisine_l1", "cuisine_l2",
                      "flavor_profile", "main_ingredients", "category", "servings_base"):
            assert field in d, f"Dish {d.get('id')} missing field '{field}'"


# ─── select_candidates — filtering ───────────────────────────────────────────

def test_select_returns_up_to_n_candidates():
    candidates = select_candidates(n=4)
    assert len(candidates) <= 4


def test_select_default_count():
    candidates = select_candidates()
    assert len(candidates) <= DEFAULT_CANDIDATE_COUNT


def test_disliked_ingredient_filtered_out():
    candidates = select_candidates(disliked_ingredients=["cilantro"])
    names = [d["name_en"] for d in candidates]
    assert "Beef Tacos" not in names


def test_hard_banned_dish_excluded():
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    recent = [{"dish": "宫保鸡丁", "date": yesterday}]
    candidates = select_candidates(recent_dishes=recent)
    names = [d["name_zh"] for d in candidates]
    assert "宫保鸡丁" not in names


def test_soft_avoided_dish_not_crashed():
    five_days_ago = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
    recent = [{"dish": "宫保鸡丁", "date": five_days_ago}]
    candidates = select_candidates(recent_dishes=recent, n=DEFAULT_CANDIDATE_COUNT)
    assert isinstance(candidates, list)


def test_empty_pool_returns_list():
    # Block every ingredient in the mock catalog
    overwhelming_dislikes = [
        "鸡肉", "五花肉", "番茄", "紫菜", "beef", "spaghetti", "chicken",
    ]
    candidates = select_candidates(disliked_ingredients=overwhelming_dislikes)
    assert isinstance(candidates, list)


# ─── select_candidates — cuisine diversity ────────────────────────────────────

def test_candidates_contain_multiple_cuisines():
    """Mock catalog has Chinese + Western + Japanese — at least 2 should appear."""
    candidates = select_candidates(n=6)
    cuisines = {d["cuisine_l1"] for d in candidates}
    assert len(cuisines) >= 2, f"Expected diverse cuisines, got {cuisines}"


def test_cuisine_weights_param_no_crash():
    """cuisine_weights is kept for API compat — must not raise."""
    candidates = select_candidates(cuisine_weights={"Chinese": 90, "Western": 5}, n=5)
    assert isinstance(candidates, list)


# ─── select_candidates — soup guarantee ──────────────────────────────────────

def test_soup_guaranteed_when_available():
    candidates = select_candidates(n=DEFAULT_CANDIDATE_COUNT)
    categories = [d["category"] for d in candidates]
    assert "Soup" in categories


# ─── build_seed_context ───────────────────────────────────────────────────────

def test_build_seed_context_contains_header():
    candidates = select_candidates(n=3)
    ctx = build_seed_context(candidates)
    assert "SEED DISH REFERENCE" in ctx


def test_build_seed_context_lists_all_candidates():
    candidates = select_candidates(n=3)
    ctx = build_seed_context(candidates)
    for d in candidates:
        assert d["name_zh"] in ctx
        assert d["name_en"] in ctx


def test_build_seed_context_shows_cuisine_tags():
    candidates = select_candidates(n=3)
    ctx = build_seed_context(candidates)
    assert "·" in ctx


def test_build_seed_context_includes_footer_instruction():
    candidates = select_candidates(n=3)
    ctx = build_seed_context(candidates)
    assert "You MAY suggest dishes not on this list" in ctx


def test_build_seed_context_empty_input():
    assert build_seed_context([]) == ""
