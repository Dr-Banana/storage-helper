"""
AI Quality Test Suite – shared fixtures, schemas, and helpers.

Three-layer architecture:
  Layer 1 – Structural  (test_structural.py)  : JSON Schema validation
  Layer 2 – Assertions  (test_assertions.py)  : keyword / step-count / cosine-similarity
  Layer 3 – Semantic    (test_semantic.py)    : LLM-as-Judge (requires live API; skip by default)
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pytest

# ─────────────────────────────────────────────────────────────────────────────
# JSON Schemas
# ─────────────────────────────────────────────────────────────────────────────

#: Schema for CookingStepsAgent.execute() response
COOKING_STEPS_SINGLE_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["action", "message", "data"],
    "additionalProperties": True,
    "properties": {
        "action": {"type": "string", "minLength": 1},
        "message": {"type": "string"},
        "data": {
            "type": "object",
            "required": ["dish_name", "cooking_steps", "saved"],
            "properties": {
                "dish_name": {"type": "string", "minLength": 1},
                "cooking_steps": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                },
                "schedule_id": {"type": ["integer", "null"]},
                "date": {"type": ["string", "null"]},
                "meal_time": {
                    "type": ["string", "null"],
                    "enum": ["breakfast", "lunch", "dinner", None],
                },
                "saved": {"type": "boolean"},
            },
        },
    },
}

#: Schema for CookingStepsAgent.execute_batch() response
COOKING_STEPS_BATCH_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "required": ["dish_name", "cooking_steps", "saved"],
        "properties": {
            "dish_name": {"type": "string", "minLength": 1},
            "cooking_steps": {
                "type": "array",
                "items": {"type": "string"},
            },
            "saved": {"type": "boolean"},
            "schedule_id": {"type": ["integer", "null"]},
            "date": {"type": ["string", "null"]},
            "meal_time": {"type": ["string", "null"]},
            "error": {"type": "string"},
        },
    },
}

#: Schema for IntentClassificationResult (serialised as dict)
INTENT_CLASSIFICATION_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["intent", "confidence", "reasoning"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "SEARCH",
                "UPDATE",
                "PLAN_EAT_OUT",
                "PLAN_AHEAD",
                "COOKING_STEPS",
                "RECIPE_QA",
                "GENERAL",
            ],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string", "minLength": 1},
        "compound_intents": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "array",
                    "items": {"type": "string"},
                },
            ]
        },
        "extracted_items": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "array",
                    "items": {"type": "string"},
                },
            ]
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Golden Dataset  (20 representative user inputs)
# ─────────────────────────────────────────────────────────────────────────────

#: 10 cooking-steps golden cases  ─  {dish, query, min_steps, max_steps,
#:                                      required_keywords, required_units}
GOLDEN_COOKING_CASES: List[Dict[str, Any]] = [
    {
        "dish": "Kung Pao Chicken",
        "query": "How do I make Kung Pao Chicken?",
        "min_steps": 5,
        "max_steps": 14,
        "required_keywords": ["chicken", "peanut", "chili"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup", "min"],
    },
    {
        "dish": "Fish-Fragrant Pork Shreds",
        "query": "How to cook Fish-Fragrant Pork Shreds?",
        "min_steps": 5,
        "max_steps": 14,
        "required_keywords": ["pork", "bean paste", "mushroom"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup", "min"],
    },
    {
        "dish": "Tomato and Egg Stir-Fry",
        "query": "How do I make Tomato and Egg Stir-Fry?",
        "min_steps": 4,
        "max_steps": 10,
        "required_keywords": ["tomato", "egg"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup"],
    },
    {
        "dish": "Twice-Cooked Pork",
        "query": "How to make Twice-Cooked Pork?",
        "min_steps": 5,
        "max_steps": 14,
        "required_keywords": ["pork belly", "bean paste"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup"],
    },
    {
        "dish": "Garlic Pork",
        "query": "How do I make Garlic Pork slices?",
        "min_steps": 5,
        "max_steps": 14,
        "required_keywords": ["pork belly", "garlic"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup"],
    },
    {
        "dish": "Mapo Tofu",
        "query": "How to cook Mapo Tofu?",
        "min_steps": 5,
        "max_steps": 14,
        "required_keywords": ["tofu", "bean paste"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup"],
    },
    {
        "dish": "Red Braised Pork Belly",
        "query": "How do I make Red Braised Pork Belly?",
        "min_steps": 6,
        "max_steps": 15,
        "required_keywords": ["pork belly", "soy sauce"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup"],
    },
    {
        "dish": "Oyster Sauce Lettuce",
        "query": "How to cook Oyster Sauce Lettuce?",
        "min_steps": 3,
        "max_steps": 8,
        "required_keywords": ["lettuce", "oyster sauce"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup"],
    },
    {
        "dish": "Steamed Fish",
        "query": "How do I make Steamed Fish?",
        "min_steps": 5,
        "max_steps": 12,
        "required_keywords": ["fish", "ginger"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup"],
    },
    {
        "dish": "Frozen Dumplings",
        "query": "How to cook Frozen Dumplings?",
        "min_steps": 3,
        "max_steps": 8,
        "required_keywords": ["dumpling", "water"],
        "required_units": ["g", "tbsp", "tsp", "ml", "oz", "cup", "min"],
    },
]

#: 10 intent classification golden cases  ─  {input, expected_intent, description}
GOLDEN_INTENT_CASES: List[Dict[str, Any]] = [
    {
        "input": "How do I make Kung Pao Chicken?",
        "expected_intent": "COOKING_STEPS",
        "description": "Direct recipe query → COOKING_STEPS",
    },
    {
        "input": "Add Kung Pao Chicken for tomorrow evening",
        "expected_intent": "PLAN_AHEAD",
        "description": "Add dish to meal plan → PLAN_AHEAD",
    },
    {
        "input": "What is currently in my fridge?",
        "expected_intent": "SEARCH",
        "description": "Inventory check → SEARCH",
    },
    {
        "input": "What is the sauce ratio for Garlic Pork?",
        "expected_intent": "RECIPE_QA",
        "description": "Follow-up detail question about a dish → RECIPE_QA",
    },
    {
        "input": "I want to eat out tonight, any good restaurants nearby?",
        "expected_intent": "PLAN_EAT_OUT",
        "description": "Dining out plan → PLAN_EAT_OUT",
    },
    {
        "input": "Add Kung Pao Chicken and Fish-Fragrant Pork for tonight with cooking steps",
        "expected_intent": "PLAN_AHEAD",
        "description": "Compound intent: add dishes + steps, primary = PLAN_AHEAD",
    },
    {
        "input": "Save the steps",
        "expected_intent": "COOKING_STEPS",
        "description": "Save steps trigger → COOKING_STEPS",
    },
    {
        "input": "Replace the current plan with this method",
        "expected_intent": "COOKING_STEPS",
        "description": "Replace steps trigger → COOKING_STEPS",
    },
    {
        "input": "How should I plan my meals for next week?",
        "expected_intent": "PLAN_AHEAD",
        "description": "Next week meal planning → PLAN_AHEAD",
    },
    {
        "input": "Store the recipe",
        "expected_intent": "COOKING_STEPS",
        "description": "Explicit save recipe → COOKING_STEPS",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Shared Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def normalize_recipe_text(text: str) -> str:
    """
    Strip step-numbering artifacts and normalise punctuation so that
    ``"1. Marinate chicken..."`` and ``"Marinate chicken..."`` compare fairly.

    Removes: leading digits + dots/parentheses (e.g. "1.", "Step 2:"),
    full-width / ASCII punctuation, and extra whitespace.
    """
    import re
    # Remove step numbers like "1.", "Step 2:", "(3)"
    text = re.sub(r"(?:^|\n)\s*(?:step\s*)?\d+[\.\)）、:：]\s*", " ", text, flags=re.IGNORECASE)
    # Strip remaining punctuation that adds noise to n-grams
    text = re.sub(r"[，。！？、；：""''《》【】\[\]()（）\-–—,.!?;:'\"\s]+", "", text)
    return text.lower()


def char_ngram_vector(text: str, n: int = 2) -> Counter:
    """Return character n-gram frequency counter for *text*.

    Default n=2 (bigrams) works well for both Chinese and English recipe text:
    - Same dish, different wording: higher than cross-dish
    - Use relative comparison (same > cross) as the primary assertion.
    """
    text = text.lower().replace(" ", "")
    return Counter(text[i : i + n] for i in range(len(text) - n + 1))


def cosine_similarity(text_a: str, text_b: str, n: int = 2) -> float:
    """
    Compute cosine similarity between two texts using character n-gram vectors.

    Default n=2 (bigrams). For recipe comparison:
    * Same-dish paraphrases should score higher than cross-dish comparisons.
    * Use ``normalize_recipe_text()`` before comparing texts that contain
      step-numbering to avoid false-low scores.

    Clamps output to [0, 1] to absorb floating-point rounding errors.
    """
    vec_a = char_ngram_vector(text_a, n)
    vec_b = char_ngram_vector(text_b, n)

    common = set(vec_a.keys()) & set(vec_b.keys())
    if not common:
        return 0.0

    dot = sum(vec_a[k] * vec_b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    # Clamp to [0, 1] to absorb floating-point rounding errors (e.g. 1.0000000000000002)
    return min(1.0, dot / (norm_a * norm_b))


def has_measurement_units(steps: List[str], threshold: int = 1) -> bool:
    """
    Return True if at least *threshold* steps contain an explicit measurement unit.

    Recognises both cooking-specific and SI / imperial abbreviations.
    """
    units = (
        # Weight / volume
        "kg", "oz", "lb", "ml", "litre", "liter",
        # "g" and "h" are omitted intentionally: they are single characters
        # that produce false positives inside common English words
        # (e.g. "g" in "egg", "h" in "the"/"chicken").
        # Use "gram" or explicit multi-char forms instead.
        "gram", "grams",
        # Spoon / cup measures
        "tbsp", "tsp", "cup",
        "tablespoon", "tablespoons", "teaspoon", "teaspoons",
        # Time
        "min", "minute", "minutes", "hour", "hours", "second", "seconds",
        # Temperature
        "°C", "℃", "°F",
        # Chinese units kept for backward compatibility
        "克", "千克", "毫升", "升",
        "汤匙", "茶匙", "勺", "大勺", "小勺",
        "分钟", "小时",
    )
    count = sum(
        1 for step in steps if any(u in step for u in units)
    )
    return count >= threshold


def steps_have_action_verbs(steps: List[str]) -> bool:
    """
    Return True if every step contains at least one cooking action verb.

    Checks a representative set of English and Chinese cooking verbs.
    """
    verbs = (
        # English verbs
        "heat", "add", "mix", "stir", "cook", "cut", "pour", "place",
        "slice", "chop", "dice", "fry", "sauté", "saute", "simmer",
        "boil", "toss", "season", "remove", "return", "marinate",
        "combine", "whisk", "beat", "drain", "serve", "coat", "rub",
        # Chinese verbs (kept for backward compatibility)
        "将", "把", "加", "放", "切", "炒", "煮", "焯", "腌", "搅",
        "混", "拌", "烧", "蒸", "烤", "炸", "盛", "撒", "倒", "取",
    )
    for step in steps:
        step_lower = step.lower()
        if not any(v.lower() in step_lower for v in verbs):
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Reusable Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def valid_cooking_steps_output() -> Dict[str, Any]:
    """A well-formed CookingStepsAgent.execute() response."""
    return {
        "action": "COOKING_STEPS",
        "message": "Generated cooking steps for Kung Pao Chicken",
        "data": {
            "dish_name": "Kung Pao Chicken",
            "cooking_steps": [
                "Cut 200g chicken breast into 1 cm cubes; marinate with 1 tbsp soy sauce and 1 tsp cornstarch for 15 minutes.",
                "Cut dried chilies into sections; toast peanuts in a dry pan until golden and set aside.",
                "Make sauce: combine 2 tbsp soy sauce, 1 tbsp rice vinegar, 1 tsp sugar, and 1 tsp cornstarch.",
                "Heat 2 tbsp oil in a wok; stir-fry dried chilies and Sichuan peppercorns until fragrant.",
                "Add marinated chicken; stir-fry over high heat until cooked through, about 2 minutes.",
                "Pour in sauce and toss to coat evenly.",
                "Add peanuts and scallions; stir briefly and serve.",
            ],
            "schedule_id": 42,
            "date": "2026-03-06",
            "meal_time": "dinner",
            "saved": True,
        },
    }


@pytest.fixture
def valid_batch_output() -> List[Dict[str, Any]]:
    """A well-formed CookingStepsAgent.execute_batch() response."""
    return [
        {
            "dish_name": "Kung Pao Chicken",
            "cooking_steps": [
                "Cube chicken breast and marinate with 1 tbsp soy sauce.",
                "Make sauce: combine 2 tbsp soy sauce with 1 tbsp vinegar.",
                "Stir-fry chilies and peppercorns; add chicken and cook through.",
                "Pour in sauce; add peanuts and serve.",
            ],
            "saved": True,
            "schedule_id": 42,
            "date": "2026-03-06",
            "meal_time": "dinner",
        },
        {
            "dish_name": "Fish-Fragrant Pork Shreds",
            "cooking_steps": [
                "Slice pork loin into thin strips; marinate with 1 tsp cornstarch for 10 minutes.",
                "Stir-fry bean paste until red oil appears; add ginger and garlic.",
                "Add pork strips and stir-fry until cooked; toss in wood ear mushrooms and bamboo shoots.",
                "Pour in fish-fragrant sauce; toss and serve.",
            ],
            "saved": True,
            "schedule_id": 42,
            "date": "2026-03-06",
            "meal_time": "dinner",
        },
    ]


@pytest.fixture
def valid_intent_output() -> Dict[str, Any]:
    """A well-formed IntentClassificationResult serialised as dict."""
    return {
        "intent": "COOKING_STEPS",
        "confidence": 0.95,
        "reasoning": "User asked how to make a dish.",
        "compound_intents": None,
        "extracted_items": ["Kung Pao Chicken"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Quantity-Entity Helpers  (used by Layer 2 assertion and structural tests)
# ─────────────────────────────────────────────────────────────────────────────

# Canonical unit aliases – maps every surface form to a single token
_UNIT_ALIASES: Dict[str, str] = {
    "tablespoon": "tbsp", "tablespoons": "tbsp",
    "teaspoon": "tsp",    "teaspoons": "tsp",
    "cup": "cup",         "cups": "cup",
    "gram": "g",          "grams": "g", "g": "g",
    "kilogram": "kg",     "kilograms": "kg",
    "ml": "ml",           "milliliter": "ml", "millilitre": "ml",
    "milliliters": "ml",  "millilitres": "ml",
    "liter": "l",         "litre": "l", "liters": "l", "litres": "l",
    "ounce": "oz",        "ounces": "oz", "oz": "oz",
    "pound": "lb",        "pounds": "lb", "lb": "lb",
    "minute": "min",      "minutes": "min", "min": "min",
    "hour": "hr",         "hours": "hr",
    "second": "sec",      "seconds": "sec",
}

# Plausible (min, max) cooking bounds per canonical unit.
# Keys MUST be lowercase because _normalise_unit returns raw.lower().
_SANE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "g":    (0.1,  2_000),   # 2 kg per ingredient; > 2 kg is unusual in home cooking
    "kg":   (0.001, 2),
    "ml":   (0.5,  2_000),
    "l":    (0.001, 3),      # lowercase "l" — _normalise_unit lowercases all aliases
    "tbsp": (0.25,  30),
    "tsp":  (0.125, 30),
    "cup":  (0.125, 12),
    "oz":   (0.1,   64),
    "lb":   (0.1,   10),
    "min":  (1,    240),     # up to 4 h
    "hr":   (0.25,  12),
    "sec":  (5,    120),
}

# Regex: matches  "2 tbsp", "0.5 tsp", "1/2 cup", "200 grams" etc.
_QTY_PATTERN = re.compile(
    r'(\d+(?:[./]\d+)?)\s*'
    r'(tbsp|tablespoons?|tsp|teaspoons?|cups?|ml|litres?|liters?|oz|lbs?|'
    r'grams?|kg|minutes?|min|hours?|seconds?|°[CF]|℃)',
    re.IGNORECASE,
)


def _normalise_unit(raw: str) -> str:
    """Return the canonical alias for a raw unit string."""
    return _UNIT_ALIASES.get(raw.lower(), raw.lower())


def _parse_qty(qty_str: str) -> float:
    """Parse '1/2' or '0.5' → float."""
    if "/" in qty_str:
        num, den = qty_str.split("/", 1)
        return float(num) / float(den)
    return float(qty_str)


def extract_quantity_units(text: str) -> List[Tuple[str, str]]:
    """
    Extract (quantity_str, canonical_unit) pairs from *text*.

    Example::

        extract_quantity_units("Add 2 tbsp soy sauce and 0.5 tsp salt.")
        # → [("2", "tbsp"), ("0.5", "tsp")]
    """
    return [
        (m.group(1), _normalise_unit(m.group(2)))
        for m in _QTY_PATTERN.finditer(text)
    ]


def jaccard_qty_similarity(text_a: str, text_b: str) -> float:
    """
    Compute Jaccard similarity between the quantity-unit **sets** of two texts.

    Each unique (qty, unit) pair is treated as an element.  Returns 1.0 if
    both texts contain no quantities (no penalty for quantity-free text).

    Example::

        jaccard_qty_similarity(
            "Add 2 tbsp soy sauce and 1 tsp sugar.",
            "Mix 2 tbsp soy sauce with 1 tsp sugar and 1 tbsp vinegar.",
        )
        # → 2/3 ≈ 0.667  ({"2 tbsp", "1 tsp"} in common; "1 tbsp" only in B)
    """
    set_a: Set[Tuple[str, str]] = set(extract_quantity_units(text_a))
    set_b: Set[Tuple[str, str]] = set(extract_quantity_units(text_b))
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    intersection = set_a & set_b
    return len(intersection) / len(union)


def is_quantity_sane(qty_str: str, unit: str) -> bool:
    """
    Return True if the numeric *qty_str* is within plausible cooking bounds
    for the given *unit* (after canonicalisation).

    Catches obvious hallucinations such as '500g salt' or '10 litres soy sauce'.

    Returns True (safe) when the unit is unrecognised or the value is
    unparseable, so unrecognised units never cause false failures.
    """
    canon = _normalise_unit(unit)
    bounds = _SANE_BOUNDS.get(canon)
    if bounds is None:
        return True  # unknown unit → pass through
    try:
        qty = _parse_qty(qty_str)
    except (ValueError, ZeroDivisionError):
        return True  # unparseable → pass through
    lo, hi = bounds
    return lo <= qty <= hi
