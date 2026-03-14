# -*- coding: utf-8 -*-
"""Unit tests for Guardian validation layer (Phase 3)."""
import pytest
from app.services.guardian import (
    GuardianIssue,
    correct_meal_entries,
    validate_meal_entries,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _entry(dish: str, cuisine_l1: str = "", cuisine_l2: str = "") -> dict:
    return {"dish": dish, "cuisine_l1": cuisine_l1, "cuisine_l2": cuisine_l2}


# ─── clean entries — no issues ───────────────────────────────────────────────

def test_clean_entries_pass():
    entries = [
        _entry("宫保鸡丁", "Chinese", "Sichuan"),
        _entry("照烧鸡腿排", "Japanese", "Washoku"),
        _entry("培根蛋面", "Western", "Italian"),
    ]
    issues = validate_meal_entries(entries)
    assert issues == []


def test_unknown_dish_without_tags_passes():
    # Dishes not in seed library with no tags — nothing to flag
    issues = validate_meal_entries([_entry("家常炒饭")])
    assert issues == []


def test_empty_entries_passes():
    assert validate_meal_entries([]) == []


# ─── FORBIDDEN_CROSS ─────────────────────────────────────────────────────────

def test_taco_under_chinese_flagged():
    issues = validate_meal_entries([_entry("Beef Tacos", "Chinese", "Sichuan")])
    assert len(issues) == 1
    assert issues[0].issue_type == "FORBIDDEN_CROSS"
    assert issues[0].dish_name == "Beef Tacos"


def test_taco_under_western_not_flagged():
    issues = validate_meal_entries([_entry("Beef Tacos", "Western", "Mexican")])
    assert issues == []


def test_sushi_under_chinese_flagged():
    issues = validate_meal_entries([_entry("Salmon Sushi", "Chinese", "Cantonese")])
    assert len(issues) == 1
    assert issues[0].issue_type == "FORBIDDEN_CROSS"


def test_kimchi_under_japanese_flagged():
    issues = validate_meal_entries([_entry("Kimchi", "Japanese", "Washoku")])
    assert len(issues) == 1
    assert issues[0].issue_type == "FORBIDDEN_CROSS"


def test_pasta_under_korean_flagged():
    issues = validate_meal_entries([_entry("Spaghetti Pasta", "Korean", "Bansang")])
    assert len(issues) == 1
    assert issues[0].issue_type == "FORBIDDEN_CROSS"


def test_burger_under_japanese_flagged():
    issues = validate_meal_entries([_entry("Classic Burger", "Japanese", "Washoku")])
    assert len(issues) == 1
    assert issues[0].issue_type == "FORBIDDEN_CROSS"


# ─── INVALID_SUBCUISINE ──────────────────────────────────────────────────────

def test_invalid_subcuisine_under_chinese():
    # "Mexican" is not a valid sub-style of "Chinese"
    issues = validate_meal_entries([_entry("红烧肉", "Chinese", "Mexican")])
    types = [i.issue_type for i in issues]
    assert "INVALID_SUBCUISINE" in types


def test_invalid_subcuisine_under_japanese():
    # "Italian" is not a valid sub-style of "Japanese"
    issues = validate_meal_entries([_entry("拉面", "Japanese", "Italian")])
    types = [i.issue_type for i in issues]
    assert "INVALID_SUBCUISINE" in types


def test_valid_subcuisine_passes():
    valid = [
        _entry("菜A", "Chinese", "Sichuan"),
        _entry("菜B", "Japanese", "Ramen"),
        _entry("菜C", "Korean", "Street Food"),
        _entry("菜D", "Western", "French"),
    ]
    issues = validate_meal_entries(valid)
    assert issues == []


def test_other_cuisine_allows_any_substyle():
    # "Other" has no sub-style restriction
    issues = validate_meal_entries([_entry("印度咖喱", "Other", "North Indian")])
    assert issues == []


# ─── SEED_MISMATCH ───────────────────────────────────────────────────────────

def test_seed_mismatch_detected_when_wrong_l1_provided():
    # 宫保鸡丁 is Chinese·Sichuan — tagging it as Japanese is a mismatch
    issues = validate_meal_entries([_entry("宫保鸡丁", "Japanese", "Washoku")])
    types = [i.issue_type for i in issues]
    assert "SEED_MISMATCH" in types
    mismatch = next(i for i in issues if i.issue_type == "SEED_MISMATCH")
    assert mismatch.suggested_cuisine_l1 == "Chinese"
    assert mismatch.suggested_cuisine_l2 == "Sichuan"


def test_seed_mismatch_skipped_when_no_tags():
    # LLM response normally has no cuisine tags — must NOT produce false positives
    issues = validate_meal_entries([_entry("宫保鸡丁")])
    assert issues == []


def test_seed_mismatch_skipped_for_correct_tags():
    issues = validate_meal_entries([_entry("麻婆豆腐", "Chinese", "Sichuan")])
    assert issues == []


def test_non_seed_dish_with_valid_tags_no_mismatch():
    # "家常豆腐" is not in seed library — no mismatch to detect
    issues = validate_meal_entries([_entry("家常豆腐", "Chinese", "Home-style")])
    assert issues == []


# ─── correct_meal_entries ────────────────────────────────────────────────────

def test_correct_entries_fixes_seed_mismatch():
    wrong = [{"dish": "宫保鸡丁", "cuisine_l1": "Japanese", "cuisine_l2": "Washoku"}]
    corrected, issues = correct_meal_entries(wrong)
    assert corrected[0]["cuisine_l1"] == "Chinese"
    assert corrected[0]["cuisine_l2"] == "Sichuan"
    assert len(issues) >= 1


def test_correct_entries_does_not_mutate_original():
    original = [{"dish": "宫保鸡丁", "cuisine_l1": "Japanese", "cuisine_l2": "Washoku"}]
    correct_meal_entries(original)
    # original must be unchanged
    assert original[0]["cuisine_l1"] == "Japanese"


def test_correct_entries_no_change_for_clean_input():
    clean = [{"dish": "照烧鸡腿排", "cuisine_l1": "Japanese", "cuisine_l2": "Washoku"}]
    corrected, issues = correct_meal_entries(clean)
    assert corrected[0]["cuisine_l1"] == "Japanese"
    assert issues == []


def test_correct_entries_forbidden_cross_logged_but_not_mutated():
    # FORBIDDEN_CROSS issues are logged but not auto-corrected
    bad = [{"dish": "Beef Tacos", "cuisine_l1": "Chinese", "cuisine_l2": "Sichuan"}]
    corrected, issues = correct_meal_entries(bad)
    assert corrected[0]["cuisine_l1"] == "Chinese"  # unchanged — no auto-correction
    cross_issues = [i for i in issues if i.issue_type == "FORBIDDEN_CROSS"]
    assert len(cross_issues) == 1
