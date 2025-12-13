import httpx
import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime

# Import location data handler from pipeline_storage
from app.storage.pipeline_storage import LocationDataHandler, LLM_LOCATION_FORMAT, DB_LOCATION_FORMAT
# Import settings for API configuration
from app.core.config import settings

logger = logging.getLogger(__name__)

# Import category configuration from centralized module
from app.core.category_config import (
    get_all_category_codes,
    is_allowed_category_type,
    get_category_suggestion,
    get_category_keywords,
    COMMON_CATEGORY_SUGGESTIONS,
    ALLOWED_CATEGORY_TYPES,
    CATEGORY_LOCATION_KEYWORDS,
)


class RecommendationGenerator:
    """
    Recommendation generator class that uses Gemini API to generate structured storage recommendations.
    """

    # Default storage paths
    STORAGE_DIR = Path(__file__).parent.parent.parent / "tmp" / "Storage"
    LOCATIONS_FILE = STORAGE_DIR / "locations.json"

    # Define the mandatory structured output schema for the recommendation
    RECOMMENDATION_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            "category_code": {
                "type": "STRING",
                "description": "The code of the document category that best matches this document. MUST be one of the existing category codes provided, or 'NEW_CATEGORY' if none match."
            },
            "suggested_location_id": {
                "type": "INTEGER",
                "description": "The ID of the best suggested storage location from the provided locations list. MUST be one of the location IDs provided."
            },
            "suggested_location_name": {
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
            "new_category_name": {
                "type": "STRING",
                "description": "Required only if category_code is 'NEW_CATEGORY'. The name for the new category (e.g., 'Legal Documents', 'Work Contracts')."
            },
            "new_category_code": {
                "type": "STRING",
                "description": "Required only if category_code is 'NEW_CATEGORY'. A short code for the new category (e.g., 'LEG', 'WORK', 'REC'). Should be 2-4 uppercase letters."
            },
            "new_category_description": {
                "type": "STRING",
                "description": "Required only if category_code is 'NEW_CATEGORY'. A description of what documents belong to this category."
            }
        },
        "required": ["category_code", "suggested_location_id", "suggested_location_name", "suggested_tags", "recommendation_reason"]
    }

    # Define the System Instruction to guide the LLM's persona
    SYSTEM_PROMPT = (
        "You are a world-class smart home storage assistant. Your task is to analyze the "
        "provided document text and classify it into one of the existing document categories, "
        "and recommend the best storage location from the provided locations list.\n\n"
        "IMPORTANT RULES:\n"
        "1. You MUST select a category_code from the provided list of existing categories.\n"
        "2. Only use 'NEW_CATEGORY' as category_code if the document truly does not fit any existing category.\n"
        "3. If using 'NEW_CATEGORY', you MUST provide new_category_name, new_category_code, and new_category_description.\n"
        "4. You MUST select a suggested_location_id from the provided locations list. The ID must match exactly one of the location IDs provided.\n"
        "5. The suggested_location_name should match the name of the location with the selected ID.\n"
        "6. Consider the document category and location descriptions to make the best match.\n"
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
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

    def load_document_categories(self, user_id: int) -> List[Dict[str, Any]]:
        """Load document categories from API for a specific user."""
        try:
            # Extract base URL from STORAGE_SERVICE_URL
            # e.g., "http://localhost:8000" from "http://localhost:8000/internal"
            base_url = settings.STORAGE_SERVICE_URL.replace("/internal", "").rstrip("/")
            
            # Validate base URL
            if not base_url or not base_url.startswith(("http://", "https://")):
                logger.error(f"Invalid STORAGE_SERVICE_URL configuration: {settings.STORAGE_SERVICE_URL}")
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

    def load_locations(self) -> List[Dict[str, Any]]:
        """Load storage locations from JSON file."""
        try:
            if not self.LOCATIONS_FILE.exists():
                logger.warning(f"Locations file not found: {self.LOCATIONS_FILE}")
                return []
            with open(self.LOCATIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("locations", [])
        except Exception as e:
            logger.error(f"Error loading locations: {e}")
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

    def save_category_to_api(self, user_id: int, code: str, name: str, description: str, classification: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Save a new category to API for a specific user."""
        try:
            # Extract base URL from STORAGE_SERVICE_URL
            base_url = settings.STORAGE_SERVICE_URL.replace("/internal", "").rstrip("/")
            
            # Validate base URL
            if not base_url or not base_url.startswith(("http://", "https://")):
                logger.error(f"Invalid STORAGE_SERVICE_URL configuration: {settings.STORAGE_SERVICE_URL}")
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
            
            # Use sync httpx client for this sync method
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload)
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
        classifying the document into an existing category or creating a new one if needed.

        :param document_text: The cleaned or raw text extracted from the document.
        :param owner_id: The ID of the owner.
        :param existing_locations: Raw DB format ({id: [metadata]}) or LLM format ({id: {name:..., description:...}}).
        :return: Dictionary containing the structured recommendation with category_id and category_code, or error info.
        """

        # Load existing document categories (cache for reuse later)
        categories = self.load_document_categories(owner_id)
        if not categories:
            logger.warning(f"No document categories found for user {owner_id}. The system will create new categories as needed.")

        # Build existing_codes lookup for reuse
        existing_codes = {cat.get("code", "").upper(): cat for cat in categories}

        # Format categories for LLM context
        categories_context = "\n--- EXISTING DOCUMENT CATEGORIES (现有文档分类) ---\n"
        categories_context += "You MUST select one of these EXACT category codes (case-sensitive):\n"
        for cat in categories:
            cat_id = cat.get("id")
            code = cat.get("code", "")
            name = cat.get("name", "")
            description = cat.get("description", "")
            categories_context += f"  '{code}' - {name}: {description}\n"
        categories_context += "---------------------------------\n"

        # Add allowed category types context for NEW_CATEGORY
        allowed_codes = get_all_category_codes()
        if allowed_codes:
            categories_context += f"\n--- CANONICAL CATEGORY CODES (规范分类代码) ---\n"
            categories_context += "If you need to create a NEW_CATEGORY, you MUST use one of these EXACT codes:\n"
            for code in allowed_codes:
                suggestion = get_category_suggestion(code)
                if suggestion:
                    categories_context += f"  '{code}' - {suggestion.get('name', '')}: {suggestion.get('description', '')}\n"
            categories_context += "---------------------------------\n"

        categories_context += "\nCRITICAL RULES:\n"
        categories_context += "1. You MUST use one of the EXACT category codes listed in the EXISTING or CANONICAL lists (e.g., 'TAX', 'MED', 'REC').\n"
        categories_context += "2. DO NOT use descriptive names like 'RECIPE' or 'MEDICAL' - use the short codes like 'REC' or 'MED' instead.\n"
        categories_context += "3. Only use 'NEW_CATEGORY' if the document truly does not fit ANY existing category, and use a code from the CANONICAL list for new_category_code.\n\n"

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
            # Load from file
            locations = self.load_locations()
            if not locations:
                logger.warning("No locations found. Location recommendation will not be available.")

        # Format locations for LLM context
        location_context = ""
        if locations:
            location_context = "\n\n--- EXISTING STORAGE LOCATIONS (现有存储位置) ---\n"
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
            f"1. Classify it into one of the existing document categories or 'NEW_CATEGORY'.\n"
            f"2. Recommend the best storage location from the provided locations list.\n\n"
            f"{categories_context}\n"
            f"{location_context}"
            f"DOCUMENT TEXT (文档内容):\n{document_text}\n---\n"
            f"IMPORTANT INSTRUCTIONS:\n"
            f"- category_code: MUST be one of the EXACT codes listed in the EXISTING or CANONICAL lists. If none match, use 'NEW_CATEGORY'.\n"
            f"- suggested_location_id: The exact ID number from the locations list above\n"
            f"- suggested_location_name: The name of the location matching the selected ID\n"
            f"If using 'NEW_CATEGORY', you MUST provide:\n"
            f"  - new_category_code: Must be from the CANONICAL CATEGORY CODES list\n"
            f"  - new_category_name: Full name for the category\n"
            f"  - new_category_description: Description of what this category contains"
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

        headers = {'Content-Type': 'application/json'}

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

                    # Reuse cached categories and existing_codes (no need to reload)
                    # If a new category was created, we'll reload categories later if needed
                    # existing_codes was already built above from the initial categories load

                    final_category_id = None
                    final_category_code = parsed_json.get("category_code", "").upper().strip()
                    is_new_category = False

                    if final_category_code == "NEW_CATEGORY":
                        new_name = parsed_json.get("new_category_name", "")
                        new_code = parsed_json.get("new_category_code", "").upper().strip()
                        new_description = parsed_json.get("new_category_description", "")

                        if not new_code or not is_allowed_category_type(new_code):
                            logger.warning(
                                f"AI returned invalid new_category_code '{new_code}'. Using first available canonical code.")
                            new_code = ALLOWED_CATEGORY_TYPES[0] if ALLOWED_CATEGORY_TYPES else "TAX"

                        if not new_name or not new_description:
                            logger.error(
                                "NEW_CATEGORY specified but missing name or description. Using canonical suggestion.")
                            suggestion = get_category_suggestion(new_code)
                            new_name = suggestion.get("name", new_code)
                            new_description = suggestion.get("description",
                                                             f"Category for documents classified as {new_code}")

                        new_category = self.ensure_category_exists(user_id=owner_id, code=new_code, name=new_name,
                                                                   description=new_description, existing_categories=categories)
                        # Reload categories after creating new one to update cache
                        categories = self.load_document_categories(owner_id)
                        existing_codes = {cat.get("code", "").upper(): cat for cat in categories}
                        final_category_code = new_category["code"]
                        final_category_id = new_category["id"]
                        logger.info(
                            f"Ensured category exists: {final_category_code} ({new_name}) with ID {final_category_id}")
                        is_new_category = True

                    elif final_category_code in existing_codes:
                        final_category_id = existing_codes[final_category_code]["id"]

                    elif is_allowed_category_type(final_category_code):
                        logger.info(
                            f"Canonical code '{final_category_code}' does not exist. Creating new category using suggestion.")
                        suggestion = get_category_suggestion(final_category_code)
                        new_name = suggestion.get("name", final_category_code)
                        new_description = suggestion.get("description",
                                                         f"Category for documents classified as {final_category_code}")
                        new_category = self.ensure_category_exists(user_id=owner_id, code=final_category_code, name=new_name,
                                                                   description=new_description, existing_categories=categories)
                        # Reload categories after creating new one to update cache
                        categories = self.load_document_categories(owner_id)
                        existing_codes = {cat.get("code", "").upper(): cat for cat in categories}
                        final_category_code = new_category["code"]
                        final_category_id = new_category["id"]
                        is_new_category = True

                    else:
                        logger.error(
                            f"Final category code '{final_category_code}' is neither NEW_CATEGORY nor an existing/canonical code.")
                        raise ValueError(f"Failed to resolve category code: {final_category_code}")

                    parsed_json["category_id"] = final_category_id
                    parsed_json["category_code"] = final_category_code

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

                        parsed_json["suggested_location_id"] = assigned_location_id
                        parsed_json["suggested_location_name"] = assigned_location_name
                        # Also set location_id and location_name for compatibility with router.py and ingestion.py
                        parsed_json["location_id"] = assigned_location_id
                        parsed_json["location_name"] = assigned_location_name

                        if final_category_id and assigned_location_id and (
                                is_new_category or not preferred_location_id):
                            self.ensure_location_mapping(final_category_id, assigned_location_id)
                    else:
                        # If no locations available, set to None explicitly
                        parsed_json["suggested_location_id"] = None
                        parsed_json["suggested_location_name"] = None
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
