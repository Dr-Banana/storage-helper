from typing import Dict, List, Set

# ============================================================================
# Category-Location Keyword Mappings
# ============================================================================
# Maps category codes to keywords that should match location descriptions
CATEGORY_LOCATION_KEYWORDS: Dict[str, List[str]] = {
    "TAX": ["tax", "financial", "desk", "office", "filing", "archive"],
    "VISA": ["immigration", "passport", "visa", "safe", "important", "legal"],
    "MED": ["medical", "medicine", "health", "prescription", "bathroom", "kitchen"],
    "INS": ["insurance", "medical", "health", "kitchen", "cabinet"],
    "EDU": ["education", "diploma", "transcript", "certificate", "school", "archive"],
    "LEG": ["legal", "contract", "agreement", "law", "safe", "important"],
    "REC": ["receipt", "purchase", "expense", "transaction", "financial"],
    "BANK": ["bank", "banking", "account", "statement", "financial", "safe"],
    "UTIL": ["utility", "bill", "electricity", "water", "gas", "internet"],
    "WORK": ["work", "employment", "contract", "job", "office", "filing"],
    "MISC": ["unknown", "misc", "other", "unreadable", "uncategorized", "illegible"],
}

# ============================================================================
# Category Security and Access Requirements
# ============================================================================
# Categories that require secure storage (should go to secure locations like safes)
SECURE_CATEGORIES: Set[str] = {
    "TAX",       # Tax documents are sensitive
    "VISA",      # Immigration documents are critical
    "LEG",       # Legal documents are important
    "BANK",      # Banking documents are sensitive
}

# Categories that are frequently accessed (should go to accessible locations)
FREQUENT_ACCESS_CATEGORIES: Set[str] = {
    "MED",       # Medical documents are frequently needed
    "INS",       # Insurance documents are often accessed
    "REC",       # Receipts are frequently referenced
    "UTIL",      # Utility bills are regularly checked
}

# ============================================================================
# Allowed Category Types (for NEW_CATEGORY creation)
# ============================================================================
# This list defines what types of categories are allowed when creating NEW_CATEGORY
# This helps limit the scope and maintain consistency
ALLOWED_CATEGORY_TYPES: List[str] = [
    # Financial
    "TAX", "BANK", "REC", "UTIL", "INS",
    # Personal
    "VISA", "MED", "EDU",
    # Professional
    "LEG", "WORK",
    # Miscellaneous / fallback
    "MISC",
    # Add more as needed
]

# Common category suggestions for AI when creating NEW_CATEGORY
COMMON_CATEGORY_SUGGESTIONS: Dict[str, Dict[str, str]] = {
    "LEG": {
        "name": "Legal Documents",
        "description": "Legal documents including contracts, agreements, and legal papers"
    },
    "REC": {
        "name": "Receipts",
        "description": "Various receipts for purchases, expenses, and transactions"
    },
    "BANK": {
        "name": "Banking Documents",
        "description": "Bank statements, account information, and financial records"
    },
    "UTIL": {
        "name": "Utility Bills",
        "description": "Utility bills including electricity, water, gas, and internet bills"
    },
    "WORK": {
        "name": "Work Documents",
        "description": "Work-related documents including employment contracts and work records"
    },
    "EDU": {
        "name": "Education Documents",
        "description": "Education-related documents including diplomas, transcripts, and certificates"
    },
    "MISC": {
        "name": "Miscellaneous Documents",
        "description": "Documents that cannot be classified, unreadable scans, or uncategorized paperwork"
    },
}

# ============================================================================
# Helper Functions
# ============================================================================

def get_category_keywords(category_code: str) -> List[str]:
    """
    Get location keywords for a category code.
    
    :param category_code: Category code (e.g., "TAX", "MED")
    :return: List of keywords that should match location descriptions
    """
    return CATEGORY_LOCATION_KEYWORDS.get(category_code.upper(), [])


def is_secure_category(category_code: str) -> bool:
    """
    Check if a category requires secure storage.
    
    :param category_code: Category code
    :return: True if category requires secure storage
    """
    return category_code.upper() in SECURE_CATEGORIES


def is_frequent_access_category(category_code: str) -> bool:
    """
    Check if a category is frequently accessed.
    
    :param category_code: Category code
    :return: True if category is frequently accessed
    """
    return category_code.upper() in FREQUENT_ACCESS_CATEGORIES


def get_all_category_codes() -> List[str]:
    """
    Get all defined category codes.
    
    :return: List of all category codes
    """
    return list(CATEGORY_LOCATION_KEYWORDS.keys())


def is_allowed_category_type(category_code: str) -> bool:
    """
    Check if a category code is in the allowed list.
    This helps validate NEW_CATEGORY creation.
    
    :param category_code: Category code to check
    :return: True if category code is allowed
    """
    return category_code.upper() in ALLOWED_CATEGORY_TYPES


def get_category_suggestion(category_code: str) -> Dict[str, str]:
    """
    Get suggested name and description for a category code.
    Useful when creating NEW_CATEGORY.
    
    :param category_code: Category code
    :return: Dictionary with 'name' and 'description', or empty dict if not found
    """
    return COMMON_CATEGORY_SUGGESTIONS.get(category_code.upper(), {})


def add_category_keywords(category_code: str, keywords: List[str]) -> None:
    """
    Add or update keywords for a category.
    This allows dynamic updates to category configurations.
    
    :param category_code: Category code
    :param keywords: List of keywords
    """
    CATEGORY_LOCATION_KEYWORDS[category_code.upper()] = keywords


def add_secure_category(category_code: str) -> None:
    """
    Add a category to the secure categories list.
    
    :param category_code: Category code
    """
    SECURE_CATEGORIES.add(category_code.upper())


def add_frequent_access_category(category_code: str) -> None:
    """
    Add a category to the frequent access categories list.
    
    :param category_code: Category code
    """
    FREQUENT_ACCESS_CATEGORIES.add(category_code.upper())