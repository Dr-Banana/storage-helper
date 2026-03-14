# -*- coding: utf-8 -*-
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
    recent = [{"dish": "西红柿炒蛋", "date": "2026-03-10"}]  # 1 day ago
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "HARD BAN" in directive
    assert "西红柿炒蛋" in directive


def test_soft_avoid_5days():
    recent = [{"dish": "宫保鸡丁", "date": "2026-03-06"}]  # 5 days ago
    directive = compute_diversity_directive(recent, today=TODAY)
    assert "SOFT AVOID" in directive
    assert "宫保鸡丁" in directive


def test_old_dish_cleared():
    recent = [{"dish": "红烧肉", "date": "2026-02-20"}]  # >14 days ago
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
        "2026-03-11": {"dinner": ["麻婆豆腐", "清炒西兰花"]},
        "2026-03-12": {"lunch": ["西红柿鸡蛋面"], "dinner": ["红烧肉"]},
    }
    entries = extract_dishes_for_history(slots, today=TODAY)
    names = [e["dish"] for e in entries]
    assert "麻婆豆腐" in names
    assert "清炒西兰花" in names
    assert "西红柿鸡蛋面" in names
    assert "红烧肉" in names


def test_extract_dishes_prunes_old_dates():
    slots = {
        "2026-02-01": {"dinner": ["过期菜"]},  # >14 days ago
        "2026-03-10": {"dinner": ["新鲜菜"]},
    }
    entries = extract_dishes_for_history(slots, today=TODAY)
    names = [e["dish"] for e in entries]
    assert "过期菜" not in names
    assert "新鲜菜" in names


def test_merge_and_prune_removes_old():
    old_history = [
        {"dish": "旧菜", "date": "2026-02-01"},   # >14 days → pruned
        {"dish": "近期菜", "date": "2026-03-05"},  # 6 days → kept
    ]
    new_entries = [{"dish": "新菜", "date": "2026-03-11"}]
    merged = merge_and_prune_recent_dishes(old_history, new_entries, today=TODAY)
    names = [e["dish"] for e in merged]
    assert "旧菜" not in names
    assert "近期菜" in names
    assert "新菜" in names


def test_merge_and_prune_deduplicates():
    existing = [{"dish": "西红柿炒蛋", "date": "2026-03-10"}]
    new_entries = [{"dish": "西红柿炒蛋", "date": "2026-03-10"}]  # duplicate
    merged = merge_and_prune_recent_dishes(existing, new_entries, today=TODAY)
    assert len([e for e in merged if e["dish"] == "西红柿炒蛋"]) == 1


def test_directive_with_empty_recent_dishes():
    directive = compute_diversity_directive([], today=TODAY)
    assert "No recent dish history" in directive


def test_directive_with_none_recent_dishes():
    directive = compute_diversity_directive(None, today=TODAY)
    assert "No recent dish history" in directive
