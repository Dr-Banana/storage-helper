"""
Category Configuration for Kitchen Agent.
This file now serves as a facade for the configurations defined in app/schema/kitchen.py.
"""
from typing import Dict, List, Set, Optional
from app.schema.kitchen import (
    ALLOWED_CATEGORY_TYPES,
    CATEGORY_LOCATION_KEYWORDS,
    COMMON_KITCHEN_FIELDS,
    CATEGORY_METADATA_FIELDS,
    COMMON_CATEGORY_SUGGESTIONS,
    SECURE_CATEGORIES,
    FREQUENT_ACCESS_CATEGORIES
)

# ============================================================================
# Helper Functions (Maintaining Backward Compatibility)
# ============================================================================

def get_category_metadata_fields(category_code: str) -> List[str]:
    """Get the list of metadata fields relevant for a specific category."""
    return CATEGORY_METADATA_FIELDS.get(category_code, COMMON_KITCHEN_FIELDS)

def get_category_keywords(category_code: str) -> List[str]:
    """Get location keywords for a category code."""
    return CATEGORY_LOCATION_KEYWORDS.get(category_code, [])

def is_allowed_category_type(category_code: str) -> bool:
    """Check if a category code is in the allowed list (case-insensitive)."""
    allowed_upper = [c.upper() for c in ALLOWED_CATEGORY_TYPES]
    return category_code.upper() in allowed_upper

def get_category_suggestion(category_code: str) -> Dict[str, str]:
    """Get suggested name and description for a category code."""
    if category_code in COMMON_CATEGORY_SUGGESTIONS:
        return COMMON_CATEGORY_SUGGESTIONS[category_code]
    
    # Fallback to case-insensitive match
    for code, suggestion in COMMON_CATEGORY_SUGGESTIONS.items():
        if code.upper() == category_code.upper():
            return suggestion
    return {}

def get_all_category_codes() -> List[str]:
    """Get all defined category codes."""
    return ALLOWED_CATEGORY_TYPES

def is_secure_category(category_code: str) -> bool:
    """Check if a category requires secure storage."""
    return category_code.upper() in {c.upper() for c in SECURE_CATEGORIES}

def is_frequent_access_category(category_code: str) -> bool:
    """Check if a category is frequently accessed."""
    return category_code.upper() in {c.upper() for c in FREQUENT_ACCESS_CATEGORIES}

def add_category_keywords(category_code: str, keywords: List[str]) -> None:
    """Add or update keywords for a category (Runtime only)."""
    CATEGORY_LOCATION_KEYWORDS[category_code] = keywords

def add_secure_category(category_code: str) -> None:
    """Add a category to the secure list (Runtime only)."""
    SECURE_CATEGORIES.add(category_code)

def add_frequent_access_category(category_code: str) -> None:
    """Add a category to the frequent access list (Runtime only)."""
    FREQUENT_ACCESS_CATEGORIES.add(category_code)
