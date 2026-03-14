"""
Guardian — Phase 3 Validation Layer

Post-processes the LLM's meal plan to detect and correct impossible
cuisine / sub-style combinations.  Uses the seed library for name-based
lookups; falls back to the VALID_CUISINE_MAP Enum for dishes not in the seed.

Public API
----------
validate_meal_entries(entries)  → List[GuardianIssue]
    Returns a list of detected issues (warning level), does NOT mutate input.

correct_meal_entries(entries)   → List[dict], List[GuardianIssue]
    Returns corrected entries + the list of issues that were fixed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.seed_library import lookup_dish

logger = logging.getLogger(__name__)

# ─── valid cuisine taxonomy ───────────────────────────────────────────────────

class CuisineL1(str, Enum):
    CHINESE  = "Chinese"
    JAPANESE = "Japanese"
    KOREAN   = "Korean"
    WESTERN  = "Western"
    OTHER    = "Other"


VALID_CUISINE_MAP: Dict[str, Set[str]] = {
    CuisineL1.CHINESE:  {
        "Sichuan", "Cantonese", "Northeast", "Shanghai", "Hunan",
        "Home-style", "Huaiyang", "Beijing", "Fujian", "Zhejiang", "Hakka",
    },
    CuisineL1.JAPANESE: {
        "Washoku", "Ramen", "Yoshoku", "Street Food", "Izakaya", "Sushi",
    },
    CuisineL1.KOREAN:   {
        "Bansang", "Street Food", "BBQ", "Jjigae",
    },
    CuisineL1.WESTERN:  {
        "Italian", "American", "Mexican", "French", "Mediterranean",
        "German", "Spanish", "Greek",
    },
    CuisineL1.OTHER:    set(),  # anything allowed
}

# Absolute cross-cuisine violations: (dish_keyword_lower, forbidden_cuisine_l1)
FORBIDDEN_CROSS: List[Tuple[str, str]] = [
    ("taco",    "Chinese"),  ("tacos",      "Chinese"),
    ("taco",    "Japanese"), ("tacos",      "Japanese"),
    ("taco",    "Korean"),
    ("sushi",   "Chinese"),  ("sushi",      "Korean"),
    ("kimchi",  "Japanese"), ("kimchi",     "Chinese"),
    ("pasta",   "Chinese"),  ("pasta",      "Korean"),
    ("pizza",   "Chinese"),  ("pizza",      "Korean"),
    ("ramen",   "Chinese"),  ("ramen",      "Korean"),
    ("dumpling","Japanese"), ("dumpling",   "Western"),
    ("burger",  "Chinese"),  ("burger",     "Japanese"),  ("burger", "Korean"),
]


# ─── data types ──────────────────────────────────────────────────────────────

@dataclass
class GuardianIssue:
    dish_name: str
    issue_type: str          # "FORBIDDEN_CROSS" | "INVALID_SUBCUISINE" | "SEED_MISMATCH"
    detail: str
    suggested_cuisine_l1: Optional[str] = None
    suggested_cuisine_l2: Optional[str] = None


# ─── internal helpers ─────────────────────────────────────────────────────────

def _check_forbidden_cross(dish_name: str, cuisine_l1: str) -> Optional[GuardianIssue]:
    name_lower = dish_name.lower()
    for keyword, forbidden in FORBIDDEN_CROSS:
        if keyword in name_lower and cuisine_l1 == forbidden:
            return GuardianIssue(
                dish_name=dish_name,
                issue_type="FORBIDDEN_CROSS",
                detail=f"'{keyword}' dish placed under '{forbidden}' cuisine — impossible combination",
            )
    return None


def _check_subcuisine(dish_name: str, cuisine_l1: str, cuisine_l2: str) -> Optional[GuardianIssue]:
    allowed = VALID_CUISINE_MAP.get(cuisine_l1)
    if allowed is None:
        return None  # unknown cuisine_l1 — skip
    if not allowed:
        return None  # Other — no restriction
    if cuisine_l2 and cuisine_l2 not in allowed:
        return GuardianIssue(
            dish_name=dish_name,
            issue_type="INVALID_SUBCUISINE",
            detail=f"'{cuisine_l2}' is not a recognised sub-style of '{cuisine_l1}'",
        )
    return None


def _check_seed_mismatch(dish_name: str, cuisine_l1: str, cuisine_l2: str) -> Optional[GuardianIssue]:
    """
    If the dish exists in the seed library and the LLM explicitly provided
    cuisine tags, verify that those tags match the seed's authoritative values.

    When cuisine_l1 is absent/empty (the normal case — the LLM response schema
    does not include cuisine tags), this check is skipped to avoid false positives.
    """
    if not cuisine_l1:
        return None
    seed = lookup_dish(dish_name)
    if seed is None:
        return None
    if seed["cuisine_l1"] != cuisine_l1:
        return GuardianIssue(
            dish_name=dish_name,
            issue_type="SEED_MISMATCH",
            detail=(
                f"Seed library says '{dish_name}' is {seed['cuisine_l1']}·{seed['cuisine_l2']}, "
                f"but LLM tagged it as {cuisine_l1}·{cuisine_l2}"
            ),
            suggested_cuisine_l1=seed["cuisine_l1"],
            suggested_cuisine_l2=seed["cuisine_l2"],
        )
    return None


# ─── public API ──────────────────────────────────────────────────────────────

def validate_meal_entries(entries: List[Dict[str, Any]]) -> List[GuardianIssue]:
    """
    Inspect a list of meal entries for cuisine tagging issues.

    Each entry is expected to have at least:
      - "dish"        (str)
      - "cuisine_l1"  (str, optional)
      - "cuisine_l2"  (str, optional)

    Returns a list of GuardianIssue objects (empty = all clear).
    """
    issues: List[GuardianIssue] = []
    for entry in entries:
        dish_name  = entry.get("dish", "") or entry.get("name", "") or entry.get("name_zh", "")
        cuisine_l1 = entry.get("cuisine_l1", "")
        cuisine_l2 = entry.get("cuisine_l2", "")

        if not dish_name:
            continue

        issue = _check_forbidden_cross(dish_name, cuisine_l1)
        if issue:
            issues.append(issue)
            continue  # forbidden cross is definitive — skip further checks

        if cuisine_l2:
            issue = _check_subcuisine(dish_name, cuisine_l1, cuisine_l2)
            if issue:
                issues.append(issue)

        issue = _check_seed_mismatch(dish_name, cuisine_l1, cuisine_l2)
        if issue:
            issues.append(issue)

    if issues:
        for iss in issues:
            logger.warning(
                "[GUARDIAN] %s — %s: %s",
                iss.issue_type, iss.dish_name, iss.detail,
            )
    else:
        logger.debug("[GUARDIAN] All %d entries passed validation", len(entries))

    return issues


def correct_meal_entries(
    entries: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[GuardianIssue]]:
    """
    Auto-correct entries where the seed library provides authoritative tags.
    Only SEED_MISMATCH issues with a suggested correction are fixed in-place.
    FORBIDDEN_CROSS and INVALID_SUBCUISINE are logged but not mutated
    (those require human / LLM re-generation).

    Returns (corrected_entries, issues_found).
    """
    issues = validate_meal_entries(entries)
    corrected = [dict(e) for e in entries]

    for issue in issues:
        if issue.issue_type == "SEED_MISMATCH" and issue.suggested_cuisine_l1:
            for entry in corrected:
                dish_name = entry.get("dish", "") or entry.get("name", "") or entry.get("name_zh", "")
                if dish_name == issue.dish_name:
                    entry["cuisine_l1"] = issue.suggested_cuisine_l1
                    if issue.suggested_cuisine_l2:
                        entry["cuisine_l2"] = issue.suggested_cuisine_l2
                    logger.info(
                        "[GUARDIAN] Auto-corrected '%s' → %s·%s",
                        dish_name,
                        issue.suggested_cuisine_l1,
                        issue.suggested_cuisine_l2 or "?",
                    )

    return corrected, issues
