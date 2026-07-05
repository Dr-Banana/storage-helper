import httpx
import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime

# Import location data handler from pipeline_storage
from app.storage.pipeline_storage import LocationDataHandler, LLM_LOCATION_FORMAT, DB_LOCATION_FORMAT, _get_storage_base_url
# Import settings for API configuration
from app.core.config import settings

logger = logging.getLogger(__name__)

# Import category configuration from centralized module
from app.core.category_config import (
    is_allowed_category_type,
    get_category_suggestion,
    get_category_keywords,
    get_category_metadata_fields,
    ALLOWED_CATEGORY_TYPES,
    CATEGORY_LOCATION_KEYWORDS,
)

# Import metadata extractor
from app.modules.metadata_extractor import extract_metadata


class RecommendationGenerator:
    """
    Recommendation generator class that uses Gemini API to generate structured storage recommendations.
    """

    # Default storage paths (no longer used for categories/locations, kept for potential future use)
    STORAGE_DIR = Path(__file__).parent.parent.parent / "tmp" / "Storage"

    # Define the mandatory structured output schema for the recommendation
    RECOMMENDATION_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            "category_code": {
                "type": "STRING",
                "description": "The code of the document category that best matches this document. MUST be one of the canonical category codes provided."
            },
            "location_id": {
                "type": "INTEGER",
                "description": "The ID of the best suggested storage location from the provided locations list. MUST be one of the location IDs provided."
            },
            "location_name": {
                "type": "STRING",
                "description": "The name of the suggested location (for reference, should match one of the provided locations)."
            },
            "suggested_tags": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "3 to 5 relevant keywords/tags for indexing and searching."
            },
            "recommendation_reason": {
                "type": "STRING",
                "description": "A brief, one-sentence explanation for the recommendation."
            },
            "storage_suggestion": {
                "type": "STRING",
                "enum": ["Fridge", "Freezer", "Pantry", "Other"],
                "description": "The general type of storage recommended for this item."
            }
        },
        "required": ["category_code", "location_id", "location_name", "suggested_tags", "recommendation_reason", "storage_suggestion"]
    }

    # Define the System Instruction to guide the LLM's persona
    SYSTEM_PROMPT = (
        "You are an expert Kitchen Inventory Agent. Your task is to analyze the "
        "provided text and classify it into one of the kitchen categories.\n\n"
        "IMPORTANT RULES:\n"
        "1. You MUST select a category_code from the provided kitchen categories.\n"
        "2. If the text is a shopping receipt with multiple items, you MUST select 'Receipt' as the category_code.\n"
        "3. For a single item (e.g., a photo of a milk carton), select the most specific category like 'Dairy'.\n"
        "Respond only with a JSON object that strictly adheres to the provided schema."
    )

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize the RecommendationGenerator.

        :param model_name: Optional Gemini model name. Defaults to settings.GEMINI_LLM_MODEL.
        :param api_key: Optional API key. Defaults to settings.GEMINI_LLM_API_KEY.
        """
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"

    def load_document_categories(self, user_id: int) -> List[Dict[str, Any]]:
        """Load document categories from API for a specific user."""
        try:
            # Extract base URL from STORAGE_SERVICE_URL
            base_url = _get_storage_base_url()
            if not base_url:
                return []
            
            # Call API to get categories
            url = f"{base_url}/api/users/{user_id}/categories"
            logger.debug(f"Loading document categories from API: {url}")
            
            # Use sync httpx client for this sync method
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url)
                response.raise_for_status()
                result = response.json()
                categories = result.get("categories", [])
                logger.info(f"Loaded {len(categories)} document categories for user {user_id}")
                return categories
                
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to DataStorageService at {base_url}. Service may be down or URL incorrect.")
            logger.debug(f"Full error: {e}")
            return []
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"User {user_id} not found or has no categories yet")
            else:
                logger.error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
            return []
        except Exception as e:
            logger.error(f"Error loading document categories from API: {e}")
            return []

    def load_locations(self, user_id: int) -> List[Dict[str, Any]]:
        """Load storage locations from API for a specific user."""
        try:
            # Extract base URL from STORAGE_SERVICE_URL
            base_url = _get_storage_base_url()
            if not base_url:
                return []
            
            # Call API to get locations
            url = f"{base_url}/api/users/{user_id}/locations"
            logger.debug(f"Loading storage locations from API: {url}")
            
            # Use sync httpx client for this sync method
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url)
                response.raise_for_status()
                result = response.json()
                locations = result.get("locations", [])
                logger.info(f"Loaded {len(locations)} storage locations for user {user_id}")
                return locations
                
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to DataStorageService at {base_url}. Service may be down or URL incorrect.")
            logger.debug(f"Full error: {e}")
            return []
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"User {user_id} not found or has no locations yet")
            else:
                logger.error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
            return []
        except Exception as e:
            logger.error(f"Error loading storage locations from API: {e}")
            return []

    def load_location_mappings(self) -> List[Dict[str, Any]]:
        """Load category-to-location mappings. Returns empty list (index.json support removed)."""
        # index.json support has been removed - always return empty list
        return []

    def ensure_location_mapping(self, category_id: Optional[int], location_id: Optional[int], priority: int = 8) -> None:
        """
        Ensure a mapping between category and location exists (no-op, index.json support removed).

        :param category_id: Category ID to map
        :param location_id: Location ID to assign
        :param priority: Priority score for the mapping
        """
        # index.json support has been removed - this method is now a no-op
        # Location mappings are no longer persisted to local files
        pass

    @staticmethod
    def get_used_location_ids(mappings: List[Dict[str, Any]]) -> set:
        """Return a set of location IDs that are already assigned in mappings."""
        if not mappings:
            return set()
        return {
            mapping.get("location_id")
            for mapping in mappings
            if mapping.get("location_id") is not None
        }

    def get_preferred_location_for_category(self, category_id: Optional[int]) -> Optional[int]:
        """
        Get the preferred location ID for the given category based on stored mappings.

        :param category_id: ID of the category
        :return: Preferred location_id or None if not found
        """
        if category_id is None:
            return None

        mappings = self.load_location_mappings()
        if not mappings:
            return None

        # Filter mappings for this category where is_allowed is True
        category_mappings = [
            m for m in mappings
            if m.get("category_id") == category_id and m.get("is_allowed", True)
        ]

        if not category_mappings:
            return None

        # Choose the mapping with highest priority (default priority 0)
        category_mappings.sort(key=lambda m: m.get("priority", 0), reverse=True)
        return category_mappings[0].get("location_id")

    def score_location_for_category(self, category_code: str, location: Dict[str, Any]) -> int:
        """Score how suitable a location is for the given category code using keyword overlap."""
        keywords = get_category_keywords(category_code) or []
        if not keywords:
            return 0

        text = f"{location.get('name', '')} {location.get('description', '')}".lower()
        score = 0
        for keyword in keywords:
            if keyword.lower() in text:
                score += 1
        return score

    def find_best_unused_location(self, category_code: str, locations: List[Dict[str, Any]], used_location_ids: set) -> Optional[int]:
        """Return the ID of the best unused location for the category if available."""
        unused_locations = [loc for loc in locations if loc.get("id") not in used_location_ids]
        if not unused_locations:
            return None

        best_location = max(
            unused_locations,
            key=lambda loc: self.score_location_for_category(category_code, loc),
            default=None
        )
        if best_location and self.score_location_for_category(category_code, best_location) > 0:
            return best_location.get("id")

        # If no keyword match, just take the first unused slot
        return unused_locations[0].get("id") if unused_locations else None

    def find_best_location_any(self, category_code: str, locations: List[Dict[str, Any]]) -> Optional[int]:
        """Find the best location (used or unused) for the category."""
        if not locations:
            return None
        best_location = max(
            locations,
            key=lambda loc: self.score_location_for_category(category_code, loc),
            default=None
        )
        if best_location and self.score_location_for_category(category_code, best_location) > 0:
            return best_location.get("id")
        return locations[0].get("id")

    def _ensure_user_exists(self, user_id: int, base_url: str) -> bool:
        """
        Ensure user exists in DataStorageService, create if not exists.
        
        Note: This method attempts to create a user, but the user ID in the database
        is auto-generated. We cannot directly create a user with a specific ID.
        This is a limitation - we can only check if user exists and log a warning.
        """
        try:
            # Try to get user first
            url = f"{base_url}/api/users/{user_id}"
            auth_token = f"user_{user_id}"
            headers = {
                "Authorization": f"Bearer {auth_token}",
                "Content-Type": "application/json"
            }
            
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=headers)
                if response.status_code == 200:
                    logger.debug(f"User {user_id} already exists")
                    return True
                
                # User doesn't exist
                if response.status_code == 404:
                    logger.warning(f"User {user_id} not found in DataStorageService database")
                    logger.warning(f"This usually means the user was created in a different database (e.g., local vs Supabase)")
                    logger.warning(f"Please ensure you're using the correct database for preprod mode (Supabase)")
                    # Note: We cannot create a user with a specific ID - IDs are auto-generated
                    # The user needs to be created through Google OAuth login first
                    return False
                
                return False
        except Exception as e:
            logger.error(f"Error checking if user exists: {e}")
            return False

    def save_category_to_api(self, user_id: int, code: str, name: str, description: str, classification: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Save a new category to API for a specific user."""
        try:
            # Extract base URL from STORAGE_SERVICE_URL
            base_url = _get_storage_base_url()
            if not base_url:
                return None
            
            # Ensure user exists before creating category
            if not self._ensure_user_exists(user_id, base_url):
                logger.error(f"Cannot create category for user {user_id}: user does not exist and could not be created")
                return None
            
            # Call API to create category
            url = f"{base_url}/api/users/{user_id}/categories"
            payload = {
                "code": code,
                "name": name,
                "description": description,
                "classification": classification
            }
            
            logger.debug(f"Saving category to API: {url}, payload: {payload}")
            
            # Prepare auth headers
            auth_token = f"user_{user_id}"
            headers = {
                "Authorization": f"Bearer {auth_token}",
                "Content-Type": "application/json"
            }
            
            # Use sync httpx client for this sync method
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                logger.info(f"Successfully saved category '{code}' ({name}) for user {user_id}")
                # API returns empty dict on success, but we return the payload as the category info
                return {
                    "code": code,
                    "name": name,
                    "description": description,
                    "classification": classification
                }
                
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to DataStorageService at {base_url}. Service may be down or URL incorrect.")
            logger.debug(f"Full error: {e}")
            return None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                error_text = e.response.text[:200]
                logger.warning(f"Category '{code}' may already exist for user {user_id}: {error_text}")
                # Try to get existing category
                categories = self.load_document_categories(user_id)
                for cat in categories:
                    if cat.get("code", "").upper() == code.upper():
                        return cat
            elif e.response.status_code == 404:
                # User not found - try to create user and retry once
                error_text = e.response.text[:200]
                logger.warning(f"User {user_id} not found when creating category: {error_text}")
                if self._ensure_user_exists(user_id, base_url):
                    # Retry once after creating user
                    logger.info(f"Retrying category creation for user {user_id} after user creation")
                    try:
                        with httpx.Client(timeout=10.0) as client:
                            response = client.post(url, json=payload, headers=headers)
                            response.raise_for_status()
                            logger.info(f"Successfully saved category '{code}' ({name}) for user {user_id} after user creation")
                            return {
                                "code": code,
                                "name": name,
                                "description": description,
                                "classification": classification
                            }
                    except Exception as retry_e:
                        logger.error(f"Failed to create category after user creation: {retry_e}")
            else:
                logger.error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"Error saving category to API: {e}")
            return None

    def add_new_category(self, user_id: int, name: str, description: str, code: Optional[str] = None, classification: Optional[str] = None, existing_categories: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Add a new category via API for a specific user.

        :param user_id: User ID
        :param name: Category name
        :param description: Category description
        :param code: Category code (auto-generated if not provided)
        :param classification: Optional classification (e.g., virtual/physical)
        :param existing_categories: Optional pre-loaded categories list to avoid redundant API calls
        :return: The new category dict
        """
        # Use provided categories if available, otherwise load from API
        if existing_categories is not None:
            categories = existing_categories
        else:
            categories = self.load_document_categories(user_id)

        # Generate code if not provided (use first 3 uppercase letters of name)
        if not code or not is_allowed_category_type(code):
            # Fallback code generation logic

            # 1. Try to use the first available canonical allowed code not already in existing categories
            existing_codes = {cat.get("code", "").upper() for cat in categories}
            allowed_codes_set = {c.upper() for c in ALLOWED_CATEGORY_TYPES}

            available_code = next(
                (c for c in allowed_codes_set if c not in existing_codes),
                None
            )

            if available_code:
                code = available_code
            else:
                # 2. If all canonical codes are used, try to use a 3-letter code from the name
                code = name.upper().replace(" ", "")[:3]
                original_code = code
                counter = 1
                while code in existing_codes:
                    code = f"{original_code}{counter}"
                    counter += 1

        # Save via API
        new_category = self.save_category_to_api(user_id, code, name, description, classification)
        if new_category:
            # Reload to get the full category with ID from database
            categories = self.load_document_categories(user_id)
            for cat in categories:
                if cat.get("code", "").upper() == code.upper():
                    logger.info(f"Added new category: {code} ({name}) with ID {cat.get('id')}")
                    return cat
            
            # If not found after reload, return what we have
            logger.warning(f"Category saved but not found after reload. Returning basic info.")
            return {
                "code": code,
                "name": name,
                "description": description,
                "classification": classification
            }
        else:
            raise Exception(f"Failed to save new category '{code}' to API for user {user_id}")

    def ensure_category_exists(self, user_id: int, code: str, name: str, description: str, classification: Optional[str] = None, existing_categories: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Ensure a category with the given code exists for a specific user via API.
        If it does not exist, create it using the provided metadata.

        :param user_id: User ID
        :param code: Category code to ensure
        :param name: Category display name
        :param description: Category description text
        :param classification: Optional classification (e.g., virtual/physical)
        :param existing_categories: Optional pre-loaded categories list to avoid redundant API calls
        :return: Dictionary representing the ensured category
        """
        # Use provided categories if available, otherwise load from API
        if existing_categories is not None:
            categories = existing_categories
        else:
            categories = self.load_document_categories(user_id)
        
        for category in categories:
            if category.get("code", "").upper() == code.upper():
                return category

        # Category doesn't exist, create it
        new_category = self.add_new_category(user_id=user_id, name=name, description=description, code=code, classification=classification, existing_categories=categories)
        
        # add_new_category already reloads categories, so we can get it from there
        # But we need to reload once more to ensure we have the latest
        categories = self.load_document_categories(user_id)
        for category in categories:
            if category.get("code", "").upper() == code.upper():
                return category
        
        # Fallback: return what we have
        return new_category

    async def generate(
        self,
        document_text: str,
        owner_id: int,
        existing_locations: Optional[Dict[int, Any]] = None
    ) -> Dict[str, Any]:
        """
        Uses the Gemini API to generate structured storage recommendations based on document text,
        classifying the document into a canonical category from category_config.py.

        :param document_text: The cleaned or raw text extracted from the document.
        :param owner_id: The ID of the owner.
        :param existing_locations: Raw DB format ({id: [metadata]}) or LLM format ({id: {name:..., description:...}}).
        :return: Dictionary containing the structured recommendation with category_id and category_code, or error info.
        """

        # Format canonical categories for LLM context (from category_config.py)
        allowed_codes = ALLOWED_CATEGORY_TYPES
        if not allowed_codes:
            logger.error("No allowed category types found in configuration. Cannot generate recommendation.")
            return {"status": "llm_error", "error": "No allowed category types configured."}

        categories_context = "\n--- CANONICAL DOCUMENT CATEGORIES ---\n"
        categories_context += "You MUST select one of these EXACT category codes (case-sensitive):\n"
        for code in allowed_codes:
            suggestion = get_category_suggestion(code)
            if suggestion:
                name = suggestion.get('name', code)
                description = suggestion.get('description', f"Category: {code}")
                categories_context += f"  '{code}' - {name}: {description}\n"
            else:
                categories_context += f"  '{code}' - Category: {code}\n"
        categories_context += "---------------------------------\n"

        categories_context += "\nCRITICAL RULES:\n"
        categories_context += "1. You MUST use one of the EXACT category codes listed above (e.g., 'TAX', 'MED', 'REC').\n"
        categories_context += "2. DO NOT use descriptive names like 'RECIPE' or 'MEDICAL' - use the short codes like 'REC' or 'MED' instead.\n"
        categories_context += "3. Select the category code that best matches the document content.\n\n"

        # Load locations from file if not provided
        locations = []
        location_lookup = {}
        location_mappings = self.load_location_mappings()
        used_location_ids = self.get_used_location_ids(location_mappings)
        if existing_locations:
            # Use provided locations (convert format if needed)
            first_location_metadata = next(iter(existing_locations.values()), None)
            if isinstance(first_location_metadata, list):
                logger.info("Existing locations detected in raw DB format. Converting using handler.")
                formatted_locations = LocationDataHandler.format_db_locations_for_llm(existing_locations)
                # Convert to list format for consistency
                locations = [
                    {
                        "id": int(loc_id),
                        "name": metadata.get('name', f"Location {loc_id}"),
                        "description": metadata.get('description', 'No description available.')
                    }
                    for loc_id, metadata in formatted_locations.items()
                ]
            else:
                # Already in LLM format, convert to list
                locations = [
                    {
                        "id": int(loc_id),
                        "name": metadata.get('name', f"Location {loc_id}"),
                        "description": metadata.get('description', 'No description available.')
                    }
                    for loc_id, metadata in existing_locations.items()
                ]
        else:
            # Load from API
            locations = self.load_locations(owner_id)
            if not locations:
                logger.warning(f"No locations found for user {owner_id}. Location recommendation will not be available.")

        # Format locations for LLM context
        location_context = ""
        if locations:
            location_context = "\n\n--- EXISTING STORAGE LOCATIONS ---\n"
            location_context += "You MUST select one of these location IDs for suggested_location_id:\n"
            for loc in locations:
                loc_id = loc.get("id")
                name = loc.get("name", f"Location {loc_id}")
                description = loc.get("description", 'No description available.')
                location_lookup[loc_id] = loc
                location_context += f"ID {loc_id}: {name}\n  Description: {description}\n\n"
            location_context += "---------------------------------\n"
            location_context += "IMPORTANT: You MUST return the exact location ID (suggested_location_id) from the list above.\n\n"

        # Construct the complete user query
        user_query = (
            f"Analyze the following document text and:\n"
            f"1. Classify it into one of the canonical document categories.\n"
            f"2. Recommend the best storage location from the provided locations list.\n\n"
            f"{categories_context}\n"
            f"{location_context}"
            f"DOCUMENT TEXT:\n{document_text}\n---\n"
            f"IMPORTANT INSTRUCTIONS:\n"
            f"- category_code: MUST be one of the EXACT codes listed in the canonical categories above.\n"
        )

        payload = {
            "contents": [{"parts": [{"text": user_query}]}],
            "systemInstruction": {"parts": [{"text": self.SYSTEM_PROMPT}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": self.RECOMMENDATION_SCHEMA,
                "temperature": 0.1  # Keep the recommendations factual and consistent
            }
        }

        headers = {'Content-Type': 'application/json', 'x-goog-api-key': self.api_key}

        # Implementing exponential backoff for robustness
        max_retries = 3
        delay = 1

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(self.api_url, headers=headers, json=payload)
                    response.raise_for_status()

                    result = response.json()

                    # Extract and parse the JSON string from the response
                    json_string = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text')

                    if not json_string:
                        logger.error("Gemini response was missing text content.")
                        raise ValueError("Gemini API response format invalid: Missing text content.")

                    parsed_json = json.loads(json_string)

                    # Extract and validate category code from LLM response
                    final_category_code = parsed_json.get("category_code", "").upper().strip()

                    # Validate that the category code is allowed
                    if not final_category_code or not is_allowed_category_type(final_category_code):
                        logger.error(f"AI returned invalid category_code '{final_category_code}'. Must be one of: {ALLOWED_CATEGORY_TYPES}")
                        raise ValueError(f"Invalid category code: {final_category_code}")

                    # Get category suggestion from config
                    suggestion = get_category_suggestion(final_category_code)
                    category_name = suggestion.get("name", final_category_code)
                    category_description = suggestion.get("description", f"Category for documents classified as {final_category_code}")

                    # Ensure the category exists for this user (create if needed)
                    category = self.ensure_category_exists(
                        user_id=owner_id,
                        code=final_category_code,
                        name=category_name,
                        description=category_description
                    )

                    final_category_id = category.get("id")
                    if not final_category_id:
                        logger.error(f"Failed to get category_id for code '{final_category_code}'")
                        raise ValueError(f"Category '{final_category_code}' does not have an ID")

                    parsed_json["category_id"] = final_category_id
                    parsed_json["category_code"] = final_category_code

                    # 🎯 Hardcoded assembly: Get fields from config and call MetadataExtractor
                    # 1. Get the list of fields we want to extract for this category
                    target_fields = get_category_metadata_fields(final_category_code)
                    logger.info(f"Targeting metadata fields for {final_category_code}: {target_fields}")

                    # 2. Call specialized extractor with explicit field list to restrict AI
                    extracted_metadata = extract_metadata(
                        text=document_text,
                        category_code=final_category_code,
                        fields=target_fields,
                        llm_metadata={}
                    )
                    # logger.debug(f"Extracted metadata for {final_category_code}: {extracted_metadata}")
                    
                    # 3. Match locations for individual items if it's a receipt.
                    # Two-level routing mirrors the backend _resolve_location logic:
                    #   Level 1 — tag match: sub_category vs location [Tags: ...] / content_analysis
                    #   Level 2 — name match: storage_suggestion substring vs location name
                    if final_category_code.upper() in ["RECEIPT", "REC"] and "items" in extracted_metadata and locations:
                        import re as _re

                        def _parse_tags_from_desc(desc: str) -> list:
                            m = _re.search(r'\[Tags:\s*([^\]]+)\]', desc or '', _re.IGNORECASE)
                            return [t.strip().lower() for t in m.group(1).split(',')] if m else []

                        def _parse_excl_from_desc(desc: str) -> set:
                            m = _re.search(r'\[Excl:\s*([^\]]+)\]', desc or '', _re.IGNORECASE)
                            return {t.strip().lower() for t in m.group(1).split(',')} if m else set()

                        # Pre-build storage-type buckets for Level 3 fallback.
                        # Classify each location as fridge, freezer, or pantry based on
                        # its name keywords. Used when Level 1+2 produce no match.
                        _FRIDGE_KW = {"fridge", "refrigerator", "冰箱"}
                        _FREEZER_KW = {"freezer", "freeze", "冷冻", "冷柜"}
                        _fridge_locs, _freezer_locs, _pantry_locs = [], [], []
                        for _l in locations:
                            _n = _l.get("name", "").lower()
                            if any(k in _n for k in _FRIDGE_KW):
                                _fridge_locs.append(_l)
                            elif any(k in _n for k in _FREEZER_KW):
                                _freezer_locs.append(_l)
                            else:
                                _pantry_locs.append(_l)

                        def _resolve_item_loc(sub_cat: str, storage_hint: str):
                            # Level 1: tag match against [Tags: ...] (manual) then content_analysis (learned)
                            if sub_cat:
                                tag_target = sub_cat.strip().lower()
                                manual_hits, learned_hits = [], []
                                for loc in locations:
                                    desc = loc.get("description", "")
                                    if tag_target in _parse_excl_from_desc(desc):
                                        continue
                                    if tag_target in _parse_tags_from_desc(desc):
                                        manual_hits.append(loc)
                                        continue
                                    learned = [t.lower() for t in (loc.get("content_analysis") or {}).get("top_sub_tags", [])]
                                    if tag_target in learned:
                                        learned_hits.append(loc)
                                candidates = manual_hits or learned_hits
                                if candidates:
                                    if len(candidates) > 1 and storage_hint:
                                        hint = storage_hint.lower()
                                        for c in candidates:
                                            n = c.get("name", "").lower()
                                            if hint in n or n in hint:
                                                return c
                                    return candidates[0]
                            # Level 2: name substring match on storage_suggestion
                            if storage_hint:
                                hint = storage_hint.lower()
                                for loc in locations:
                                    n = loc.get("name", "").lower()
                                    if hint in n or n in hint:
                                        return loc
                            # Level 3: storage-type bucket fallback — always pick an existing location
                            # rather than leaving the item unassigned.
                            if storage_hint:
                                hint_upper = storage_hint.upper()
                                if "FRIDGE" in hint_upper:
                                    bucket = _fridge_locs or locations
                                elif "FREEZE" in hint_upper:
                                    bucket = _freezer_locs or locations
                                else:
                                    # Pantry / Other → prefer non-fridge/non-freezer locations
                                    bucket = _pantry_locs or locations
                                if bucket:
                                    return bucket[0]
                            return locations[0] if locations else None

                        for item in extracted_metadata["items"]:
                            matched = _resolve_item_loc(
                                item.get("sub_category", "") or "",
                                item.get("storage_suggestion", "") or ""
                            )
                            if matched:
                                item["location_id"] = matched.get("id")
                                item["location_name"] = matched.get("name")
                                logger.info(
                                    f"Matched item '{item.get('product_name')}' "
                                    f"(sub_category={item.get('sub_category')}, "
                                    f"storage={item.get('storage_suggestion')}) "
                                    f"-> '{matched.get('name')}'"
                                )
                            else:
                                logger.warning(
                                    f"No location found for '{item.get('product_name')}' "
                                    f"(sub_category={item.get('sub_category')}, "
                                    f"storage={item.get('storage_suggestion')}) — no locations available"
                                )

                    # 4. Assemble results
                    parsed_json["metadata"] = extracted_metadata

                    assigned_location_id = None
                    assigned_location_name = None
                    if locations:
                        valid_location_ids = {loc.get("id") for loc in locations}

                        preferred_location_id = self.get_preferred_location_for_category(final_category_id)
                        if preferred_location_id and preferred_location_id in valid_location_ids:
                            assigned_location_id = preferred_location_id
                        else:
                            assigned_location_id = self.find_best_unused_location(final_category_code, locations,
                                                                                  used_location_ids)
                            if assigned_location_id:
                                used_location_ids.add(assigned_location_id)

                            if not assigned_location_id:
                                assigned_location_id = self.find_best_location_any(final_category_code, locations)

                        if assigned_location_id and assigned_location_id in valid_location_ids:
                            matched_location = location_lookup.get(assigned_location_id)
                            assigned_location_name = matched_location.get("name",
                                                                          f"Location {assigned_location_id}") if matched_location else f"Location {assigned_location_id}"
                        else:
                            assigned_location_id = locations[0].get("id")
                            assigned_location_name = locations[0].get("name", f"Location {assigned_location_id}")

                        parsed_json["location_id"] = assigned_location_id
                        parsed_json["location_name"] = assigned_location_name

                        if final_category_id and assigned_location_id and not preferred_location_id:
                            self.ensure_location_mapping(final_category_id, assigned_location_id)
                    else:
                        # If no locations available, set to None explicitly
                        parsed_json["location_id"] = None
                        parsed_json["location_name"] = None
                        logger.warning("No locations available. Location recommendation set to None.")

                    return {
                        "status": "llm_success",
                        "recommendation": parsed_json
                    }

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error on attempt {attempt + 1}: {e.response.status_code} - {e.response.text}")
            except Exception as e:
                logger.error(f"Error on attempt {attempt + 1}: {e}")

                # Exponential backoff
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    logger.error("Max retries reached. Failed to generate recommendation.")
                    return {
                        "status": "llm_error",
                        "error": "Failed to generate recommendation after multiple retries."
                    }

        return {"status": "llm_error", "error": "Failed to generate recommendation due to an unknown issue."}

# Default instance for backward compatibility
_default_generator = RecommendationGenerator()


# Backward compatibility functions
async def generate_recommendation(
    document_text: str,
    owner_id: int,
    existing_locations: Optional[Dict[int, Any]] = None
) -> Dict[str, Any]:
    """
    Backward compatibility wrapper for generate_recommendation function.
    Uses the default RecommendationGenerator instance.
    
    :param document_text: The cleaned or raw text extracted from the document.
    :param owner_id: The ID of the owner.
    :param existing_locations: Raw DB format ({id: [metadata]}) or LLM format ({id: {name:..., description:...}}).
    :return: Dictionary containing the structured recommendation with category_id and category_code, or error info.
    """
    return await _default_generator.generate(document_text, owner_id, existing_locations)
