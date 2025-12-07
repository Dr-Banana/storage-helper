"""
Pipeline Storage Handler
Handles storage of all AI Orchestration pipeline output results including:
- Ingestion pipeline results (OCR, vision, recommendations, embeddings)
- Search pipeline results
- Document metadata and files
- Error documents
- Location data

All data is saved and retrieved via remote API.
"""
from typing import Dict, Any, Optional, List, TYPE_CHECKING, Union
from pathlib import Path
import logging
import uuid
from datetime import datetime
import json
import httpx
import aiofiles
from io import BytesIO

from app.core.config import settings

# Use TYPE_CHECKING to avoid circular import
if TYPE_CHECKING:
    from app.api.schemas import FeedbackRequest

logger = logging.getLogger(__name__)

# Initialize HTTPX client for Storage Service API
_storage_client = httpx.AsyncClient(base_url=settings.STORAGE_SERVICE_URL)

# Type aliases for location data formats
DB_LOCATION_FORMAT = Dict[int, List[Any]]  # Input format: {location_id: [name, description, ...]}
LLM_LOCATION_FORMAT = Dict[int, Dict[str, Any]]  # Output format: {location_id: {"name": str, "description": str}}


class LocationDataHandler:
    """
    Handler class for location data format conversion.
    Provides methods to convert between database format (input) and LLM-friendly format (output).
    """
    
    @staticmethod
    def format_db_locations_for_llm(db_locations: DB_LOCATION_FORMAT) -> LLM_LOCATION_FORMAT:
        """
        Convert raw database location data (location_id mapped to metadata list)
        to structured dictionary format expected by LLM recommendation module.
        
        Assumptions:
        1. Location name is the first element of metadata list (index 0).
        2. Location description is the second element of metadata list (index 1).
        
        Uses safe default values if data is missing. Long descriptions are truncated
        to keep LLM context concise.
        
        :param db_locations: Raw location data from database (input format).
        :return: Formatted dictionary suitable for LLM context (output format).
        """
        formatted_data: LLM_LOCATION_FORMAT = {}
        
        for loc_id, metadata_list in db_locations.items():
            # Ensure metadata_list is a list and not empty
            if not isinstance(metadata_list, list) or not metadata_list:
                logger.warning(f"Metadata for location ID {loc_id} is missing or not a list. Skipping.")
                continue

            # Extract name and description based on assumed indices (0 and 1)
            name = str(metadata_list[0]) if len(metadata_list) > 0 else f"Unknown Location {loc_id}"
            description = str(metadata_list[1]) if len(metadata_list) > 1 else "No description provided."
            
            # Truncate long descriptions to improve LLM context efficiency
            if len(description) > 100:
                description = description[:97] + "..."
                
            formatted_data[loc_id] = {
                "name": name,
                "description": description
            }

        logger.info(f"Formatted {len(formatted_data)} locations for LLM recommendation.")
        return formatted_data
    
    @staticmethod
    def format_llm_locations_for_db(llm_locations: LLM_LOCATION_FORMAT) -> DB_LOCATION_FORMAT:
        """
        Convert LLM-friendly format back to database format.
        Reverse operation of format_db_locations_for_llm.
        
        :param llm_locations: LLM-friendly format ({location_id: {"name": ..., "description": ...}}).
        :return: Database format ({location_id: [name, description]}).
        """
        db_format: DB_LOCATION_FORMAT = {}
        
        for loc_id, metadata_dict in llm_locations.items():
            if not isinstance(metadata_dict, dict):
                logger.warning(f"Metadata for location ID {loc_id} is not a dict. Skipping.")
                continue
            
            name = metadata_dict.get("name", f"Unknown Location {loc_id}")
            description = metadata_dict.get("description", "No description provided.")
            
            db_format[loc_id] = [name, description]
        
        logger.info(f"Converted {len(db_format)} locations back to DB format.")
        return db_format


class PipelineStorage:
    """
    Pipeline Storage Handler for AI Orchestration Service.
    
    Handles storage of all pipeline output results including:
    - Ingestion pipeline results (OCR text, vision analysis, recommendations, embeddings)
    - Search pipeline results
    - Document metadata and files
    - Error documents from failed pipeline executions
    - Location data for recommendations
    
    All data is saved and retrieved via remote API.
    """
    
    def __init__(self, storage_client: Optional[httpx.AsyncClient] = None):
        """
        Initialize pipeline storage handler.
        
        :param storage_client: HTTPX async client for API calls. If None, uses default client.
        """
        self.client = storage_client or _storage_client
        logger.info("PipelineStorage initialized (API-only mode)")
    
    async def save_document(self, document_data: Dict[str, Any]) -> str:
        """
        Save document data from ingestion pipeline via API.
        
        Saves complete document information including:
        - Document metadata (OCR text, recommendation info, etc.)
        - Document files (images/PDFs)
        - Embedding vectors (if available)
        
        NOTE: Local file storage is disabled. All data is saved via remote API.
        
        :param document_data: Dictionary containing all document-related data from pipeline
        :return: Document ID (UUID string, generated locally for API response)
        """
        logger.info("Saving document data via API...")
        
        # Generate UUID locally (needed for API response)
        # This will be used as document_id in the API response
        doc_id = str(uuid.uuid4())
        
        # Save to remote storage via API
        try:
            # Update document_data with document_id
            document_data_with_id = document_data.copy()
            document_data_with_id["document_id"] = doc_id
            
            # Asynchronously save to remote storage
            url = "/documents"
            response = await self.client.post(url, json=document_data_with_id, timeout=10.0)
            response.raise_for_status()
            
            remote_id = response.json().get("id") if response.status_code == 201 else None
            if remote_id:
                logger.info(f"Document saved to remote storage via API. Local ID: {doc_id}, Remote ID: {remote_id}")
            else:
                logger.info(f"Document saved to remote storage via API. Local ID: {doc_id}")
        except Exception as e:
            # API failure - log error but still return doc_id for response
            logger.error(f"Failed to save document to remote storage via API: {e}", exc_info=True)
            logger.warning(f"Returning generated document ID {doc_id} despite API failure")
        
        return doc_id
    
    async def save_error_document(self, document_data: Dict[str, Any], error_info: Dict[str, Any]) -> str:
        """
        Save error document from failed pipeline execution.
        
        NOTE: Local file storage is disabled. Error documents can be handled via API if needed.
        Currently just generates UUID for response.
        
        :param document_data: Dictionary containing document data from pipeline
        :param error_info: Dictionary containing error information (status, error, failed_step, etc.)
        :return: Error document ID (UUID string)
        """
        logger.warning("Handling error document (local file storage disabled)...")
        
        # Generate UUID for error document (needed for API response)
        error_id = str(uuid.uuid4())
        
        # TODO: Optionally save error documents via API if needed
        # For now, just log the error information
        logger.warning(f"Error document ID generated: {error_id}")
        logger.warning(f"    Error: {error_info.get('error', 'Unknown error')}")
        logger.warning(f"    Status: {error_info.get('status', 'failed')}")
        logger.warning(f"    Failed step: {error_info.get('failed_step', 'Unknown')}")
        
        return error_id
    
    async def save_file(self, file_path: str, doc_id: str, file_type: str = "image") -> Optional[str]:
        """
        Save document file (image or PDF) via API.
        
        NOTE: Local file storage is disabled. Files will be saved via API.
        
        :param file_path: Source file path (local path or URL)
        :param doc_id: Document ID
        :param file_type: File type ("image" or "pdf")
        :return: Original file path (files saved via API, not locally)
        """
        logger.info(f"File for document {doc_id} will be saved via API (local file save disabled)")
        
        # Return original path - file will be handled via API
        return file_path if file_path else None
    
    async def upload_document_file(
        self,
        file_path: str,
        owner_id: int,
        category: str = "UNKNOWN",
        event_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Upload document file to DataStorageService via HTTP API.
        
        This method communicates with the DataStorageService microservice through
        its public API endpoint. No direct code dependencies - pure HTTP communication.
        
        [API: POST /api/v1/documents/upload-and-process]
        Service: DataStorageService (separate microservice)
        
        :param file_path: Local file path or URL to the document file
        :param owner_id: Document owner user ID
        :param category: Document category (TAX, VISA, MED, INS, etc.)
        :param event_name: Optional associated event name
        :return: Response dictionary with id, filename, url, owner_id, created_at, or None if failed
        """
        url = "/api/v1/documents/upload-and-process"
        
        try:
            # Read file content
            file_content = await self._read_file_content(file_path)
            if not file_content:
                logger.error(f"Failed to read file content from {file_path}")
                return None
            
            # Determine filename from path
            filename = Path(file_path).name if file_path else "document"
            
            # Prepare multipart form data
            files = {
                "file": (filename, file_content, self._get_content_type(file_path))
            }
            data = {
                "owner_id": owner_id,
                "category": category
            }
            if event_name:
                data["event_name"] = event_name
            
            # Upload to DataStorageService via HTTP API (microservice communication)
            # Note: This endpoint is at /api/v1, not /internal
            # Extract base URL from STORAGE_SERVICE_URL (e.g., "http://localhost:8000" from "http://localhost:8000/internal")
            base_url = settings.STORAGE_SERVICE_URL.replace("/internal", "").rstrip("/")
            
            # Validate base URL
            if not base_url or not base_url.startswith(("http://", "https://")):
                logger.error(f"Invalid STORAGE_SERVICE_URL configuration: {settings.STORAGE_SERVICE_URL}")
                return None
            
            logger.debug(f"Uploading file to DataStorageService at: {base_url}{url}")
            
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
                response = await client.post(url, files=files, data=data)
                response.raise_for_status()
                
                result = response.json()
                logger.info(f"Document file uploaded successfully. ID: {result.get('id')}, URL: {result.get('url')}")
                return result
                
        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to DataStorageService at {base_url}. Service may be down or URL incorrect."
            logger.error(f"Connection error uploading document file: {error_msg}")
            logger.debug(f"Full error: {e}")
            return None
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error(f"Failed to upload document file via API: {error_msg}")
            return None
        except httpx.TimeoutException as e:
            error_msg = f"Timeout connecting to DataStorageService at {base_url}"
            logger.error(f"Timeout uploading document file: {error_msg}")
            return None
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"Error uploading document file via API: {error_msg}", exc_info=True)
            return None
    
    async def _read_file_content(self, file_path: str) -> Optional[bytes]:
        """
        Read file content from local path or URL.
        
        :param file_path: Local file path or URL
        :return: File content as bytes, or None if failed
        """
        try:
            # Check if it's a URL
            if file_path.startswith(("http://", "https://")):
                # Download from URL
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(file_path)
                    response.raise_for_status()
                    return response.content
            else:
                # Read from local file
                async with aiofiles.open(file_path, "rb") as f:
                    return await f.read()
        except Exception as e:
            logger.error(f"Failed to read file content from {file_path}: {e}")
            return None
    
    def _get_content_type(self, file_path: str) -> str:
        """
        Determine content type from file extension.
        
        :param file_path: File path
        :return: MIME type string
        """
        path = Path(file_path)
        ext = path.suffix.lower()
        
        content_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
            ".tiff": "image/tiff",
            ".pdf": "application/pdf"
        }
        
        return content_types.get(ext, "application/octet-stream")
    
    async def save_embedding(self, doc_id: str, embedding: List[float], embedding_dimension: int) -> bool:
        """
        Save document embedding vector via API.
        
        NOTE: Local file storage is disabled. Embeddings are saved as part of document data via API.
        
        :param doc_id: Document ID
        :param embedding: Embedding vector
        :param embedding_dimension: Embedding dimension
        :return: True if embedding is valid (saved via API as part of document)
        """
        if not embedding or embedding_dimension == 0:
            logger.warning(f"No embedding to save for document {doc_id}")
            return False
        
        logger.info(f"Embedding for document {doc_id} will be saved via API (local file save disabled)")
        return True
    
    async def update_document_metadata(
        self,
        document_id: int,
        metadata: Dict[str, Any],
        ocr_text: Optional[str] = None,
        embedding_vector: Optional[List[float]] = None
    ) -> bool:
        """
        Update document metadata, OCR text, and embedding vector via API.
        
        [API: PATCH /internal/documents/{document_id}]
        
        :param document_id: Document ID
        :param metadata: Metadata dictionary
        :param ocr_text: Optional OCR text
        :param embedding_vector: Optional embedding vector
        :return: True if updated successfully, False otherwise
        """
        url = f"/documents/{document_id}"
        
        payload = {"metadata": metadata}
        if ocr_text is not None:
            payload["ocr_text"] = ocr_text
        if embedding_vector is not None:
            payload["embedding"] = embedding_vector

        try:
            response = await self.client.patch(url, json=payload, timeout=5.0)
            response.raise_for_status()
            logger.info(f"Document metadata updated via API: {document_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update document metadata via API: {e}")
            return False
    
    async def get_document(self, doc_id: str, include_embedding: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get document data via API.
        
        [API: GET /internal/documents/{doc_id}]
        
        :param doc_id: Document ID
        :param include_embedding: Whether to include embedding vector
        :return: Document data dictionary, or None if not found
        """
        url = f"/documents/{doc_id}"
        try:
            response = await self.client.get(url, timeout=5.0)
            response.raise_for_status()
            document = response.json()
            
            # Filter embedding if not requested
            if not include_embedding and "embedding" in document:
                document.pop("embedding")
            
            return document
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Document not found: {doc_id}")
                return None
            logger.error(f"Failed to get document via API: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting document via API: {e}")
            return None
    
    async def get_embedding(self, doc_id: str) -> Optional[List[float]]:
        """
        Get document embedding vector via API.
        
        :param doc_id: Document ID
        :return: Embedding vector, or None if not found
        """
        document = await self.get_document(doc_id, include_embedding=True)
        if document:
            return document.get("embedding")
        return None
    
    async def list_documents(self, owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        List all documents via API.
        
        [API: GET /internal/documents?owner_id={owner_id}]
        
        :param owner_id: Optional owner ID for filtering
        :return: List of documents
        """
        url = "/documents"
        params = {}
        if owner_id is not None:
            params["owner_id"] = owner_id
        
        try:
            response = await self.client.get(url, params=params, timeout=5.0)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to list documents via API: {e}")
            return []
    
    async def delete_document(self, doc_id: str) -> bool:
        """
        Delete document via API.
        
        [API: DELETE /internal/documents/{doc_id}]
        
        :param doc_id: Document ID
        :return: True if deleted successfully, False otherwise
        """
        url = f"/documents/{doc_id}"
        try:
            response = await self.client.delete(url, timeout=5.0)
            response.raise_for_status()
            logger.info(f"Document deleted via API: {doc_id}")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Document not found for deletion: {doc_id}")
                return False
            logger.error(f"Failed to delete document via API: {e}")
            return False
        except Exception as e:
            logger.error(f"Error deleting document via API: {e}")
            return False
    
    async def get_location_info(self, location_id: int) -> Dict[str, Any]:
        """
        Get location information via API.
        
        [API: GET /internal/locations/{location_id}]
        
        :param location_id: Location ID
        :return: Location information dictionary
        """
        url = f"/locations/{location_id}"
        try:
            response = await self.client.get(url, timeout=5.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Location not found: {location_id}")
                raise RuntimeError(f"Location not found for ID: {location_id}")
            logger.error(f"Failed to get location info via API: {e}")
            raise RuntimeError(f"Failed to fetch location info for ID: {location_id}")
        except Exception as e:
            logger.error(f"Error getting location info via API: {e}")
            raise RuntimeError(f"Failed to fetch location info for ID: {location_id}")
    
    async def log_feedback(self, request) -> bool:
        """
        Log user feedback via API.
        
        [API: POST /internal/feedback]
        
        :param request: FeedbackRequest object
        :return: True if logged successfully, False otherwise
        """
        # Lazy import to avoid circular dependency
        from app.api.schemas import FeedbackRequest as _FeedbackRequest
        
        url = "/feedback"
        try:
            response = await self.client.post(url, json=request.model_dump(), timeout=5.0)
            response.raise_for_status()
            logger.info(f"Feedback logged via API for doc_id={request.document_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to log feedback via API: {e}")
            return False
    
    @staticmethod
    def get_pipeline_output(pipeline_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get complete pipeline output result.
        
        This method returns all pipeline processing results including:
        - Pipeline status and metadata
        - OCR results and extracted text
        - Vision analysis results
        - Cleaning information
        - Recommendation data
        - Embedding information
        - Error information (if any)
        
        :param pipeline_result: Dictionary from PipelineState.to_output_dict()
        :return: Complete pipeline output dictionary
        """
        return pipeline_result
    
    @staticmethod
    def get_all_pipeline_data(pipeline_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get all pipeline data in a structured format.
        
        This is an alias for get_pipeline_output() for backward compatibility.
        
        :param pipeline_result: Dictionary from PipelineState.to_output_dict()
        :return: Complete pipeline output dictionary
        """
        return PipelineStorage.get_pipeline_output(pipeline_result)


# Default instance for backward compatibility
_default_storage = PipelineStorage()

# Convenience functions for backward compatibility
async def save_document(document_data: Dict[str, Any]) -> str:
    """Convenience function: Save document using default storage instance."""
    return await _default_storage.save_document(document_data)


async def save_error_document(document_data: Dict[str, Any], error_info: Dict[str, Any]) -> str:
    """Convenience function: Save error document using default storage instance."""
    return await _default_storage.save_error_document(document_data, error_info)


async def get_document(doc_id: str, include_embedding: bool = False) -> Optional[Dict[str, Any]]:
    """Convenience function: Get document using default storage instance."""
    return await _default_storage.get_document(doc_id, include_embedding=include_embedding)


async def get_embedding(doc_id: str) -> Optional[List[float]]:
    """Convenience function: Get embedding using default storage instance."""
    return await _default_storage.get_embedding(doc_id)


async def get_all_embeddings(owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Get all documents with embeddings via API.
    
    :param owner_id: Optional owner ID for filtering
    :return: List of documents with embeddings
    """
    documents = await _default_storage.list_documents(owner_id=owner_id)
    # Filter to only include documents with embeddings
    return [doc for doc in documents if doc.get("embedding")]


async def get_location_info(location_id: int) -> Dict[str, Any]:
    """Convenience function: Get location info using default storage instance."""
    return await _default_storage.get_location_info(location_id)


async def update_document_metadata(
    document_id: int,
    metadata: Dict[str, Any],
    ocr_text: Optional[str] = None,
    embedding_vector: Optional[List[float]] = None
) -> bool:
    """Convenience function: Update document metadata using default storage instance."""
    return await _default_storage.update_document_metadata(
        document_id, metadata, ocr_text, embedding_vector
    )


async def log_feedback(request) -> bool:
    """Convenience function: Log feedback using default storage instance."""
    return await _default_storage.log_feedback(request)

