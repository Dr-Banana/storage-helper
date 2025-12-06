"""
Result assembler for combining search results with document and location information.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging
import json
from pathlib import Path

from app.storage.local_storage import get_document
from app.integrations.storage_client import get_location_info
from app.api.schemas import LocationInfo

logger = logging.getLogger(__name__)

# Path to locations.json
STORAGE_DIR = Path(__file__).parent.parent.parent / "tmp" / "Storage"
LOCATIONS_FILE = STORAGE_DIR / "locations.json"


def load_locations() -> Dict[int, Dict[str, Any]]:
    """
    Load locations from locations.json file.
    
    :return: Dictionary mapping location_id to location data
    """
    try:
        if not LOCATIONS_FILE.exists():
            logger.warning(f"Locations file not found: {LOCATIONS_FILE}")
            return {}
        
        with open(LOCATIONS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        locations = data.get("locations", [])
        # Create a dictionary mapping location_id to location data
        location_dict = {}
        for loc in locations:
            loc_id = loc.get("id")
            if loc_id is not None:
                location_dict[loc_id] = loc
        
        return location_dict
    except Exception as e:
        logger.error(f"Error loading locations: {e}", exc_info=True)
        return {}


class ResultAssembler:
    """
    Assembles search results by combining document data with location information.
    Results are ranked by similarity score and formatted according to API schema.
    """
    
    @staticmethod
    async def assemble(
        search_results: List[Dict[str, Any]],
        include_location: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Assemble search results by enriching with document and location data.
        Results are already sorted by score from search_engine.
        
        :param search_results: List of search results with document_id and score (sorted by score)
        :param include_location: Whether to include location information
        :return: List of assembled search result items matching SearchResultItem schema
        """
        assembled_results = []
        
        # Load locations once if needed (optimization: avoid loading multiple times)
        locations_cache = None
        if include_location:
            locations_cache = load_locations()
        
        for result in search_results:
            doc_id = result.get("document_id")
            score = result.get("score", 0.0)
            
            if not doc_id:
                continue
            
            # Get full document data
            doc = get_document(str(doc_id), include_embedding=False)
            if not doc:
                logger.warning(f"Document not found: {doc_id}")
                continue
            
            # Extract text for title and snippet
            extracted_text = doc.get("extracted_text", "")
            
            # Get file type and path
            file_type = doc.get("file_type", "image")
            file_path = doc.get("file_path") or doc.get("image_path") or doc.get("pdf_path") or doc.get("source", "")
            
            # Build result item matching SearchResultItem schema
            # Use original UUID string as document_id (not converted to int)
            result_item = {
                "document_id": str(doc_id),  # Keep original UUID string
                "score": score,
                "title": extracted_text[:100].strip() if extracted_text else None,
                "snippet": extracted_text[:300].strip() if extracted_text else None,
                "preview_image_url": file_path,  # Use file path as preview URL
                "file_type": file_type,  # Include file type
                "created_at": None,
            }
            
            # Parse created_at if available
            created_at_str = doc.get("created_at")
            if created_at_str:
                try:
                    result_item["created_at"] = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                except Exception as e:
                    logger.debug(f"Could not parse created_at: {e}")
            
            # Add location information if requested
            if include_location:
                # Try to get location from recommendation data
                recommendation = doc.get("recommendation_data")
                if recommendation:
                    # Get location_id and location_name from recommendation_data
                    location_id = recommendation.get("location_id") or recommendation.get("suggested_location_id")
                    location_name = recommendation.get("location_name") or recommendation.get("suggested_location_name")
                    
                    if location_id and locations_cache:
                        # Get location data from cache
                        location_data = locations_cache.get(location_id)
                        
                        if location_data:
                            # Use full location data from locations.json
                            result_item["location"] = LocationInfo(
                                id=location_id,
                                name=location_data.get("name", location_name or "Unknown Location"),
                                description=location_data.get("description"),
                                photo_url=location_data.get("photo_url")
                            )
                        elif location_name:
                            # Fallback: use location_name from recommendation if location not found in locations.json
                            result_item["location"] = LocationInfo(
                                id=location_id,
                                name=location_name,
                                description=None,
                                photo_url=None
                            )
                        else:
                            result_item["location"] = None
                    elif location_id and location_name:
                        # Fallback if locations cache is not available
                        result_item["location"] = LocationInfo(
                            id=location_id,
                            name=location_name,
                            description=None,
                            photo_url=None
                        )
                    else:
                        result_item["location"] = None
                else:
                    result_item["location"] = None
            
            assembled_results.append(result_item)
        
        # Results are already sorted by score (descending) from search_engine
        # No need to sort again
        
        logger.info(f"Assembled {len(assembled_results)} search results")
        return assembled_results


# Default instance
_default_assembler = ResultAssembler()


async def assemble_search_results(
    search_results: List[Dict[str, Any]],
    include_location: bool = True
) -> List[Dict[str, Any]]:
    """
    Backward compatibility wrapper for result assembly.
    Uses the default ResultAssembler instance.
    
    :param search_results: List of search results with document_id and score
    :param include_location: Whether to include location information
    :return: List of assembled search result items
    """
    return await _default_assembler.assemble(search_results, include_location=include_location)