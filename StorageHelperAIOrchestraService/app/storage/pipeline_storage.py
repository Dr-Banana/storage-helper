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
from typing import Dict, Any, Optional, List, TYPE_CHECKING, Union, Tuple
from pathlib import Path
import logging
import uuid
from datetime import datetime, timedelta, timezone
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


def _get_storage_base_url() -> Optional[str]:
    """
    Extract base URL from STORAGE_SERVICE_URL configuration.
    Handles various URL formats:
    - http://localhost:8000/internal -> http://localhost:8000
    - http://localhost:8000/api/v1 -> http://localhost:8000
    - https://xxx.onrender.com -> https://xxx.onrender.com
    - https://xxx.onrender.com/internal -> https://xxx.onrender.com
    
    Returns:
        Base URL string (without trailing slash) or None if invalid
    """
    storage_url = settings.STORAGE_SERVICE_URL.rstrip("/")
    
    # Remove /internal or /api/v1 suffix if present
    if storage_url.endswith("/internal"):
        base_url = storage_url[:-9]  # Remove "/internal"
    elif storage_url.endswith("/api/v1"):
        base_url = storage_url[:-7]  # Remove "/api/v1"
    else:
        base_url = storage_url
    
    # Remove any trailing slashes
    base_url = base_url.rstrip("/")
    
    # Validate base URL
    if not base_url or not base_url.startswith(("http://", "https://")):
        logger.error(f"Invalid STORAGE_SERVICE_URL configuration: {settings.STORAGE_SERVICE_URL} -> {base_url}")
        return None
    
    return base_url

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
            
            # Get owner_id for authorization
            owner_id = document_data_with_id.get("owner_id")
            
            # Prepare authorization header
            headers = {}
            if owner_id:
                auth_token = f"user_{owner_id}"
                headers["Authorization"] = f"Bearer {auth_token}"
            
            # Asynchronously save to remote storage
            url = "/documents"
            response = await self.client.post(url, json=document_data_with_id, timeout=10.0, headers=headers)
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
    
    async def upload_file_only(
        self,
        file_path: str,
        owner_id: int,
        is_temporary: bool = False
    ) -> Optional[str]:
        """
        Upload document file to DataStorageService and get image_url.
        
        Args:
            file_path: Local file path to upload
            owner_id: Document owner user ID
            is_temporary: If True, upload to tmp/ folder for preview (can be cleaned up later)
        
        Returns:
            Image URL of uploaded file, or None if upload failed
        """
        # Build the full endpoint URL safely
        endpoint_path = "/documents/upload"
        
        # Get base URL and ensure it doesn't end with /api/v1 if we're adding it
        base_url = _get_storage_base_url()
        if not base_url:
            return None
        
        # Ensure we don't double up on /api/v1
        if not base_url.endswith("/api/v1"):
            api_prefix = "/api/v1"
        else:
            api_prefix = ""
            
        full_url = f"{base_url}{api_prefix}{endpoint_path}"
        
        try:
            upload_mode = "temporary (preview)" if is_temporary else "permanent"
            logger.info(f"Uploading file to DataStorageService ({upload_mode}): {full_url}")
            
            # Read file content
            file_content = await self._read_file_content(file_path)
            if not file_content:
                logger.error(f"Failed to read file content from {file_path}")
                return None
            
            # Determine filename from path
            filename = Path(file_path).name if file_path else "document"
            logger.info(f"  Filename: {filename}, temporary: {is_temporary}")
            
            # Prepare multipart form data for upload
            files = {
                "file": (filename, file_content, self._get_content_type(file_path))
            }
            # FastAPI Form fields expect string values, which are then converted to the declared type
            upload_data = {
                "owner_id": str(owner_id),
                "is_temporary": "true" if is_temporary else "false"
            }
            
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
                upload_response = await client.post(
                    f"{api_prefix}{endpoint_path}",
                    files=files,
                    data=upload_data
                )
                
                # Log response details for debugging
                logger.debug(f"Upload response status: {upload_response.status_code}")
                logger.debug(f"Upload response headers: {dict(upload_response.headers)}")
                
                upload_response.raise_for_status()
                upload_result = upload_response.json()
                image_url = upload_result.get("image_url")
                
                if not image_url:
                    logger.error(f"Upload API did not return image_url. Response: {upload_result}")
                    return None
                
                logger.info(f"File uploaded successfully. Image URL: {image_url}")
                return image_url
                
        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to DataStorageService at {base_url}. Service may be down or URL incorrect."
            logger.error(f"Connection error uploading file: {error_msg}")
            logger.error(f"  Full URL attempted: {full_url}")
            logger.debug(f"Full error: {e}")
            return None
        except httpx.HTTPStatusError as e:
            # Enhanced error logging for 404 and other status errors
            error_detail = e.response.text[:500] if e.response.text else "No error details"
            error_msg = f"HTTP {e.response.status_code}: {error_detail}"
            logger.error(f"Failed to upload file via API: {error_msg}")
            logger.error(f"  Request URL: {full_url}")
            logger.error(f"  Response status: {e.response.status_code}")
            logger.error(f"  Response headers: {dict(e.response.headers)}")
            logger.error(f"  Response body: {error_detail}")
            return None
        except httpx.TimeoutException as e:
            error_msg = f"Timeout connecting to DataStorageService at {base_url}"
            logger.error(f"Timeout uploading file: {error_msg}")
            return None
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"Error uploading file via API: {error_msg}", exc_info=True)
            return None
    
    async def process_document_page(
        self,
        image_url: str,
        owner_id: int,
        page_number: int = 1,
        ocr_text: str = "",
        document_id: Optional[Union[int, str]] = None,
        category_id: Optional[int] = None,
        location_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Process document page metadata via DataStorageService API.
        """
        # Build the full endpoint URL safely
        endpoint_path = "/documents/process"
        
        base_url = _get_storage_base_url()
        if not base_url:
            return None
            
        # Ensure we don't double up on /api/v1
        if not base_url.endswith("/api/v1"):
            api_prefix = "/api/v1"
        else:
            api_prefix = ""
            
        full_url = f"{base_url}{api_prefix}{endpoint_path}"
        
        try:
            logger.info(f"Processing document page via DataStorageService: {full_url}")
            logger.info(f"  Owner ID: {owner_id}, Page: {page_number}, Doc ID: {document_id}")
            
            # Prepare JSON payload
            process_payload: Dict[str, Any] = {
                "image_url": image_url,
                "owner_id": owner_id,
                "page_number": page_number,
            }
            
            # Add Optional Fields only if they have values
            if ocr_text:
                process_payload["ocr_text"] = ocr_text
            
            if document_id is not None:
                # Can be str or int from pipeline state
                process_payload["document_id"] = int(document_id) if isinstance(document_id, str) else document_id
            
            if category_id is not None:
                process_payload["category_id"] = category_id
            
            if location_id is not None:
                process_payload["location_id"] = location_id
            
            if metadata is not None:
                process_payload["metadata"] = metadata
            
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
                process_response = await client.post(
                    f"{api_prefix}{endpoint_path}",
                    json=process_payload
                )
                
                # Log response details for debugging
                logger.debug(f"Process response status: {process_response.status_code}")
                logger.debug(f"Process response headers: {dict(process_response.headers)}")
                
                process_response.raise_for_status()
                process_result = process_response.json()
                
                result = {
                    "document_id": process_result.get("document_id"),
                    "page_id": process_result.get("page_id"),
                    "image_url": process_result.get("image_url") or image_url,
                    "status": process_result.get("status"),
                    "page_number": process_result.get("page_number", page_number),
                    "items_created": process_result.get("items_created", 0),
                    "item_ids": process_result.get("item_ids", [])  # List of created item document IDs
                }
                
                logger.info(
                    f"Document page processed successfully. "
                    f"Document ID: {result['document_id']}, "
                    f"Page ID: {result['page_id']}, "
                    f"Status: {result['status']}, "
                    f"Items created: {result['items_created']}"
                )
                return result
                
        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to DataStorageService at {base_url}. Service may be down or URL incorrect."
            logger.error(f"Connection error processing document page: {error_msg}")
            logger.error(f"  Full URL attempted: {full_url}")
            logger.debug(f"Full error: {e}")
            return None
        except httpx.HTTPStatusError as e:
            # Enhanced error logging for 404 and other status errors
            error_detail = e.response.text[:500] if e.response.text else "No error details"
            error_msg = f"HTTP {e.response.status_code}: {error_detail}"
            logger.error(f"Failed to process document page via API: {error_msg}")
            logger.error(f"  Request URL: {full_url}")
            logger.error(f"  Response status: {e.response.status_code}")
            logger.error(f"  Response headers: {dict(e.response.headers)}")
            logger.error(f"  Response body: {error_detail}")
            return None
        except httpx.TimeoutException as e:
            error_msg = f"Timeout connecting to DataStorageService at {base_url}"
            logger.error(f"Timeout processing document page: {error_msg}")
            logger.error(f"  Full URL attempted: {full_url}")
            return None
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"Error processing document page via API: {error_msg}", exc_info=True)
            return None
    
    async def upload_document_file(
        self,
        file_path: str,
        owner_id: int,
        page_number: int = 1,
        ocr_text: str = "",
        document_id: Optional[Union[int, str]] = None,
        category: str = "UNKNOWN",
        event_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Upload document file to DataStorageService via HTTP API (legacy method).
        
        This method is kept for backward compatibility but is deprecated.
        New code should use upload_file_only() + process_document_page() separately.
        
        This method communicates with the DataStorageService microservice through
        its public API endpoints. No direct code dependencies - pure HTTP communication.
        
        Process:
        1. [API: POST /api/v1/documents/upload] - Upload file and get image_url
        2. [API: POST /api/v1/documents/process] - Process document page metadata using image_url
        
        :param file_path: Local file path or URL to the document file
        :param owner_id: Document owner user ID
        :param page_number: Page number within document (1-indexed)
        :param ocr_text: OCR extracted text for this page
        :param document_id: Optional existing document ID. If not provided, creates new document
        :param category: Document category (TAX, VISA, MED, INS, etc.) - deprecated, kept for backward compatibility
        :param event_name: Optional associated event name - deprecated, kept for backward compatibility
        :return: Response dictionary with document_id, page_id, image_url, status, or None if failed
        """
        # Use the new split methods
        image_url = await self.upload_file_only(file_path, owner_id)
        if not image_url:
            return None
        
        return await self.process_document_page(
            image_url=image_url,
            owner_id=owner_id,
            page_number=page_number,
            ocr_text=ocr_text,
            document_id=document_id
        )
    
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
    
    async def save_document_embedding(
        self,
        document_id: Union[int, str],
        embedding: List[float],
        owner_id: int
    ) -> bool:
        """
        Save document embedding vector via DataStorageService API.
        
        [API: POST /api/documents/{document_id}/embedding]
        Service: DataStorageService (separate microservice)
        
        API format:
        - document_id (path parameter): Document ID
        - Body: {
            "document_id": int,
            "embedding": List[float]  # 768-dimensional vector
          }
        - Authorization header: Bearer user_{owner_id}
        
        :param document_id: Document ID (int or str that can be converted to int)
        :param embedding: Embedding vector (must be 768 dimensions)
        :param owner_id: Document owner user ID (for authorization)
        :return: True if saved successfully, False otherwise
        """
        # Convert document_id to int if needed
        try:
            doc_id_int = int(document_id) if isinstance(document_id, str) else document_id
        except (ValueError, TypeError):
            logger.error(f"Invalid document_id format: {document_id}")
            return False
        
        url = f"/api/documents/{doc_id_int}/embedding"
        
        # Ensure embedding is a list of floats (not string or other format)
        # Convert to list FIRST before validating dimensions
        # This handles JSON string inputs like '[0.1, 0.2, ...]'
        if isinstance(embedding, str):
            import json
            try:
                embedding = json.loads(embedding)
            except json.JSONDecodeError:
                logger.error(f"Invalid embedding format: cannot parse JSON string")
                return False
        elif not isinstance(embedding, list):
            logger.error(f"Invalid embedding format: expected list, got {type(embedding)}")
            return False
        
        # Ensure all elements are floats
        try:
            embedding_list = [float(x) for x in embedding]
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid embedding format: all elements must be floats. Error: {str(e)}")
            return False
        
        # Validate dimensions AFTER conversion to list
        # Now we can correctly check the list length, not string length
        if not embedding_list or len(embedding_list) != 768:
            logger.error(f"Embedding must be 768 dimensions, got {len(embedding_list) if embedding_list else 0}")
            return False
        
        # Prepare request body - ensure it's a proper list of floats
        payload = {
            "document_id": doc_id_int,
            "embedding": embedding_list  # Explicitly use the converted list
        }
        
        # Debug: Log the payload type
        logger.debug(f"Sending embedding: type={type(payload['embedding'])}, length={len(payload['embedding'])}, first 3 values={payload['embedding'][:3]}")
        
        try:
            # Extract base URL from STORAGE_SERVICE_URL
            base_url = _get_storage_base_url()
            if not base_url:
                return False
            
            full_url = f"{base_url}{url}"
            logger.debug(f"Saving embedding to DataStorageService at: {full_url}")
            
            # Generate authorization header for DataStorageService
            # Format: Bearer user_{owner_id}
            headers = {
                "Authorization": f"Bearer user_{owner_id}"
            }
            
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                
                result = response.json()
                status = result.get("status", "unknown")
                logger.info(f"Document embedding saved successfully. Document ID: {doc_id_int}, Status: {status}")
                return True
                
        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to DataStorageService at {base_url}. Service may be down or URL incorrect."
            logger.error(f"Connection error saving document embedding: {error_msg}")
            logger.debug(f"Full error: {e}")
            return False
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error(f"Failed to save document embedding via API: {error_msg}")
            return False
        except httpx.TimeoutException as e:
            error_msg = f"Timeout connecting to DataStorageService at {base_url}"
            logger.error(f"Timeout saving document embedding: {error_msg}")
            return False
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"Error saving document embedding via API: {error_msg}", exc_info=True)
            return False

    async def search_documents(
        self,
        query_embedding: List[float],
        owner_id: int,
        top_k: int = 5,
        exclude_receipts: bool = False
    ) -> List[int]:
        """
        Search for documents by embedding via DataStorageService API.
        
        [API: POST /api/documents/search]
        Service: DataStorageService
        
        :param query_embedding: 768-dimensional query vector
        :param owner_id: User ID to search documents for
        :param top_k: Number of results to return
        :param exclude_receipts: If True, exclude receipt parent documents
        :return: List of document IDs
        """
        url = "/api/documents/search"
        payload = {
            "embedding": query_embedding,
            "user_id": owner_id,
            "top_k": top_k,
            "exclude_receipts": exclude_receipts
        }
        
        # Extract base URL from STORAGE_SERVICE_URL
        base_url = _get_storage_base_url()
        if not base_url:
            logger.error("Invalid STORAGE_SERVICE_URL configuration")
            return []
        
        full_url = f"{base_url}{url}"
        
        try:
            # logger.info(f"Searching documents via DataStorageService")
            # logger.info(f"  Base URL: {base_url}")
            # logger.info(f"  Endpoint: {url}")
            # logger.info(f"  Full URL: {full_url}")
            # logger.info(f"  Owner ID: {owner_id}, Top K: {top_k}")
            
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
                # logger.info(f"Search completed. Found {len(result)} documents")
                return result
        except httpx.HTTPStatusError as e:
            error_detail = e.response.text[:500] if e.response.text else "No error details"
            logger.error(f"Failed to search documents via API: HTTP {e.response.status_code}: {error_detail}")
            logger.error(f"  Request URL: {full_url}")
            return []
        except Exception as e:
            logger.error(f"Failed to search documents via API: {e}", exc_info=True)
            return []

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
    
    async def get_document(self, doc_id: str, owner_id: Optional[int] = None, include_embedding: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get document data via API.
        
        [API: GET /api/documents/{doc_id}]
        
        :param doc_id: Document ID
        :param owner_id: Owner ID (required for authorization)
        :param include_embedding: Whether to include embedding vector
        :return: Document data dictionary, or None if not found
        """
        # Extract base URL
        base_url = _get_storage_base_url()
        if not base_url:
            return None
            
        url = f"/api/documents/{doc_id}"
        
        # Prepare headers
        headers = {}
        if owner_id:
            headers["Authorization"] = f"Bearer user_{owner_id}"
            
        try:
            # logger.info(f"Getting document via DataStorageService: {base_url}{url}")
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                response = await client.get(url, headers=headers)
                
                if response.status_code == 404:
                    logger.warning(f"Document not found: {doc_id}")
                    return None
                    
                response.raise_for_status()
                document = response.json()
                
                # Filter embedding if not requested
                if not include_embedding and "embedding" in document:
                    document.pop("embedding")
                
                return document
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get document via API: HTTP {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"Error getting document via API: {e}")
            return None
    
    async def get_embedding(self, doc_id: str, owner_id: Optional[int] = None) -> Optional[List[float]]:
        """
        Get document embedding vector via API.
        
        :param doc_id: Document ID
        :param owner_id: Owner ID (required for authorization)
        :return: Embedding vector, or None if not found
        """
        document = await self.get_document(doc_id, owner_id=owner_id, include_embedding=True)
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

    async def create_schedule(
        self,
        owner_id: int,
        title: str,
        scheduled_time: datetime,
        event_type: Optional[str] = None,
        description: Optional[str] = None,
        end_time: Optional[datetime] = None,
        location: Optional[str] = None,
        priority: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        Create a schedule entry via DataStorageService API.

        [API: POST /api/schedule]
        Authorization: Bearer user_{owner_id}

        :param owner_id: User ID (for authorization)
        :param title: Schedule title
        :param scheduled_time: Start time (required)
        :param event_type: Optional event type (e.g. "shopping_list", "meal_plan")
        :param description: Optional description
        :param end_time: Optional end time
        :param location: Optional location
        :param priority: Priority (default 0)
        :param metadata: Optional metadata (e.g. meal_plan + shopping_list for plan-ahead)
        :return: Created schedule ID, or None if failed
        """
        base_url = _get_storage_base_url()
        if not base_url:
            logger.error("Cannot create schedule: Invalid STORAGE_SERVICE_URL configuration")
            return None

        url = "/api/schedule"
        payload: Dict[str, Any] = {
            "title": title,
            "scheduled_time": scheduled_time.isoformat() if isinstance(scheduled_time, datetime) else scheduled_time,
        }
        if event_type is not None:
            payload["event_type"] = event_type
        if description is not None:
            payload["description"] = description
        if end_time is not None:
            payload["end_time"] = end_time.isoformat() if isinstance(end_time, datetime) else end_time
        if location is not None:
            payload["location"] = location
        if priority != 0:
            payload["priority"] = priority
        if metadata is not None:
            payload["metadata"] = metadata

        headers = {"Authorization": f"Bearer user_{owner_id}"}

        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
                schedule_id = result.get("id")
                logger.info(f"Schedule created via API: id={schedule_id}, title={title}")
                return int(schedule_id) if schedule_id is not None else None
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create schedule via API: HTTP {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"Error creating schedule via API: {e}", exc_info=True)
            return None

    async def get_user_schedules(self, owner_id: int) -> List[Dict[str, Any]]:
        """
        Get all schedules for a user via DataStorageService API.
        [API: GET /api/schedule]
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return []

        url = "/api/schedule"
        headers = {"Authorization": f"Bearer user_{owner_id}"}
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to get schedules via API: {e}")
            return []

    async def delete_schedule(
        self,
        schedule_id: int,
        owner_id: int,
    ) -> bool:
        """
        Delete a schedule via DataStorageService API.
        [API: DELETE /api/schedule/{schedule_id}]
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return False

        url = f"/api/schedule/{schedule_id}"
        
        try:
            # Use simple token format: "user_<owner_id>"
            token = f"user_{owner_id}"
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{base_url}{url}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10.0
                )
                if response.status_code in (200, 204):
                    logger.info(f"Schedule {schedule_id} deleted successfully")
                    return True
                else:
                    logger.warning(f"Failed to delete schedule {schedule_id}: HTTP {response.status_code}")
                    return False
        except Exception as e:
            logger.warning(f"Failed to delete schedule {schedule_id}: {e}")
            return False
    
    async def update_schedule(
        self,
        owner_id: int,
        schedule_id: int,
        title: Optional[str] = None,
        event_type: Optional[str] = None,
        description: Optional[str] = None,
        scheduled_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Update a schedule via DataStorageService API.
        [API: PUT /api/schedule/{schedule_id}]
        """
        base_url = _get_storage_base_url()
        if not base_url:
            return False

        url = f"/api/schedule/{schedule_id}"
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if event_type is not None:
            payload["event_type"] = event_type
        if description is not None:
            payload["description"] = description
        if scheduled_time is not None:
            payload["scheduled_time"] = scheduled_time.isoformat()
        if end_time is not None:
            payload["end_time"] = end_time.isoformat()
        if metadata is not None:
            payload["metadata"] = metadata

        headers = {"Authorization": f"Bearer user_{owner_id}"}
        try:
            _meta = payload.get("metadata") or {}
            _feat = _meta.get("features", [])
            _api_plans = []
            for f in _feat:
                if isinstance(f, dict) and f.get("type") == "meal_plan":
                    for p in f.get("plans", []):
                        _meals = [(m.get("mealTime"), [d.get("name") for d in m.get("dishes", [])]) for m in p.get("meals", [])]
                        _api_plans.append({"date": p.get("date"), "meals": _meals})
            logger.info(f"[API] PUT_request: schedule_id={schedule_id}, plans_summary={_api_plans}")
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                response = await client.put(url, json=payload, headers=headers)
                logger.info(f"[API] PUT_response: status={response.status_code}, ok={response.is_success}")
                response.raise_for_status()
                logger.info(f"Schedule updated via API: id={schedule_id}")
                return True
        except Exception as e:
            logger.warning(f"Failed to update schedule via API: {e}")
            return False

    def _convert_to_feature_format(
        self,
        meal_plan: Dict[str, str],
        shopping_list: List[str],
        dish_ingredients: Optional[Dict[str, List[str]]] = None,
        meal_plan_slots: Optional[Dict[str, Any]] = None,
        existing_cooking_steps: Optional[Dict[str, List[str]]] = None,
        existing_dish_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict:
        """Convert to Feature system format.

        meal_plan_slots values may be List[str] (Phase 2 format) or str (legacy " and "-joined).
        When meal_plan_slots is provided, builds one meal per slot; otherwise uses meal_plan (all dinner).

        existing_dish_data (preferred over existing_cooking_steps) is a mapping of
        dish_name -> {"steps": [...], "ingredients": [...]} extracted from the current DB
        state.  When provided, steps AND ingredient quantities are preserved across plan
        updates, even when the dish name is slightly modified (fuzzy-matched with
        SequenceMatcher so "炸鸡薯条" still matches "炸鸡芝士薯条").
        """
        import time
        import random
        import re
        from difflib import SequenceMatcher

        def generate_id(prefix: str) -> str:
            timestamp = int(time.time() * 1000)
            rand_str = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))
            return f"{prefix}_{timestamp}_{rand_str}"

        # Merge: prefer richer existing_dish_data; fall back to legacy existing_cooking_steps.
        _all_existing: Dict[str, Dict[str, Any]] = {}
        if existing_dish_data:
            _all_existing.update(existing_dish_data)
        if existing_cooking_steps:
            for _k, _v in existing_cooking_steps.items():
                if _k not in _all_existing:
                    _all_existing[_k] = {"steps": _v, "ingredients": []}

        def _fuzzy_find_dish_data(
            target: str,
            dish_data: Dict[str, Dict[str, Any]],
            fallback_steps: Optional[Dict[str, List[str]]],
        ) -> Optional[Dict[str, Any]]:
            """Return the best-matching dish snapshot for *target* dish name.

            Matching priority:
              1. Exact name match (case-insensitive)
              2. Substring containment
              3. SequenceMatcher ratio >= 0.55
            Returns None when no candidate clears the threshold.
            """
            if not dish_data:
                # Legacy fallback: steps-only dict
                if not fallback_steps:
                    return None
                steps = fallback_steps.get(target)
                if not steps:
                    tl = target.lower()
                    for k, v in fallback_steps.items():
                        if tl in k.lower() or k.lower() in tl:
                            steps = v
                            break
                return {"steps": steps, "ingredients": []} if steps else None

            tl = target.lower()
            best_score = 0.0
            best_val: Optional[Dict[str, Any]] = None

            for k, v in dish_data.items():
                kl = k.lower()
                if kl == tl:
                    return v  # exact match — return immediately
                if kl in tl or tl in kl:
                    # Overlap ratio: shorter / longer
                    score = len(min(kl, tl, key=len)) / max(len(kl), len(tl), 1)
                    # Boost substring matches so they beat SequenceMatcher
                    score = min(score + 0.2, 1.0)
                else:
                    score = SequenceMatcher(None, tl, kl).ratio()
                if score > best_score:
                    best_score = score
                    best_val = v

            return best_val if best_score >= 0.55 else None

        def build_dishes_for_names(
            dish_names: List[str],
            ingredients_pool: list,
            dish_ingredients: Optional[Dict[str, List[str]]],
            only_slot_today: bool = False,
        ) -> list:
            """Build feature-format dish list from a list of dish names."""
            dishes = []
            for dish_name in dish_names:
                cleaned = re.sub(r'\b(one|two|three|four|five|a|an)\s+', '', dish_name, flags=re.IGNORECASE).strip() or dish_name.strip()
                if not cleaned:
                    continue
                if dish_ingredients and cleaned in dish_ingredients:
                    raw = dish_ingredients[cleaned]
                    ing_list = [
                        item if isinstance(item, dict)
                        else {"name": item, "quantity": "", "category": "other"}
                        for item in raw
                        if item
                    ]
                elif dish_ingredients:
                    ing_list = []
                elif only_slot_today and len(dish_names) == 1:
                    ing_list = ingredients_pool.copy()
                else:
                    ing_list = []
                dish_obj: Dict[str, Any] = {
                    "id": generate_id("dish"),
                    "name": cleaned,
                    "ingredients": ing_list,
                    "servings": None,
                    "prepTime": None,
                    "cookTime": None,
                }
                # Preserve previously-saved cooking steps (and ingredient quantities) so a
                # plan update never wipes out data generated by CookingStepsAgent.
                #
                # cookingSteps  → EXACT match only.  Fuzzy-matched steps belong to a
                #                 *different* dish and will quickly become stale when the
                #                 background auto-gen runs for the renamed dish.
                # ingredient quantities → fuzzy match is safe: we're only restoring
                #                         numeric amounts, not regenerating the list.
                _preserved = _fuzzy_find_dish_data(cleaned, existing_dish_data, existing_cooking_steps)
                if _preserved:
                    _is_exact = bool(
                        existing_dish_data and
                        cleaned.lower() in {k.lower() for k in existing_dish_data}
                    )
                    if _is_exact and _preserved.get("steps"):
                        dish_obj["cookingSteps"] = _preserved["steps"]
                    # Restore ingredient quantities if the plan update left them blank
                    if _preserved.get("ingredients"):
                        qty_map = {
                            i.get("name", "").strip().lower(): i.get("quantity", "")
                            for i in _preserved["ingredients"]
                            if isinstance(i, dict)
                        }
                        for ing in dish_obj.get("ingredients", []):
                            key = ing.get("name", "").strip().lower()
                            if key in qty_map and qty_map[key] and not ing.get("quantity"):
                                ing["quantity"] = qty_map[key]
                dishes.append(dish_obj)
            if not dishes and dish_names:
                dishes = [{
                    "id": generate_id("dish"),
                    "name": dish_names[0].strip(),
                    "ingredients": [],
                    "servings": None,
                    "prepTime": None,
                    "cookTime": None,
                }]
            return dishes

        def slot_to_dish_names(slot_val: Any) -> List[str]:
            """Normalise slot value to a list of dish name strings."""
            if isinstance(slot_val, list):
                return [s.strip() for s in slot_val if s and s.strip()]
            if isinstance(slot_val, str) and slot_val.strip():
                return [p.strip() for p in re.split(r'\s+and\s+|\s+with\s+|,\s+', slot_val, flags=re.IGNORECASE) if p.strip()]
            return []

        now_iso = datetime.now(timezone.utc).isoformat()
        ingredients_pool = [
            item if isinstance(item, dict)
            else {"name": item, "quantity": "", "category": "other"}
            for item in (shopping_list or [])
            if item
        ]
        daily_plans = []
        slot_order = ["breakfast", "lunch", "dinner", "snack"]

        if meal_plan_slots:
            for date_str in sorted(meal_plan_slots.keys()):
                slots = meal_plan_slots.get(date_str) or {}
                num_slots_with_content = sum(1 for mt in slot_order if slot_to_dish_names(slots.get(mt)))
                only_slot_today = num_slots_with_content == 1
                meals = []
                for meal_time in slot_order:
                    dish_names = slot_to_dish_names(slots.get(meal_time))
                    if not dish_names:
                        continue
                    dishes = build_dishes_for_names(dish_names, ingredients_pool, dish_ingredients, only_slot_today=only_slot_today)
                    meals.append({"id": generate_id("meal"), "mealTime": meal_time, "dishes": dishes})
                if meals:
                    daily_plans.append({"date": date_str, "meals": meals})
        else:
            for date_str, meal_text in sorted(meal_plan.items()):
                dish_names = slot_to_dish_names(meal_text)
                if not dish_names:
                    dish_names = [meal_text.strip()]
                dishes = build_dishes_for_names(dish_names, ingredients_pool, dish_ingredients, only_slot_today=True)
                daily_plans.append({
                    "date": date_str,
                    "meals": [{"id": generate_id("meal"), "mealTime": "dinner", "dishes": dishes}],
                })

        _summary = [{"date": p.get("date"), "meals": [(m.get("mealTime"), [d.get("name") for d in m.get("dishes", [])]) for m in p.get("meals", [])]} for p in daily_plans]
        logger.info(f"[STORAGE] daily_plans_content: using_meal_plan_slots={bool(meal_plan_slots)}, summary={_summary}")
        meal_plan_feature = {
            "type": "meal_plan",
            "id": generate_id("mp"),
            "created_at": now_iso,
            "updated_at": now_iso,
            "plans": daily_plans
        }
        return {"features": [meal_plan_feature]}
    
    @staticmethod
    def _extract_meal_plan_from_schedule(
        schedule: Dict[str, Any],
    ) -> Tuple[Dict[str, str], List[str], Dict[str, List[str]], Dict[str, Dict[str, List[str]]]]:
        """Extract meal_plan (flat), shopping_list, dish_ingredients, and
        meal_plan_slots (date -> mealTime -> List[str]) from schedule metadata."""
        meta = schedule.get("metadata") or {}
        if not isinstance(meta, dict):
            return {}, [], {}, {}
        schedule_mp: Dict[str, str] = {}
        schedule_sl: List[str] = meta.get("shopping_list") or []
        dish_ingredients: Dict[str, List[str]] = {}
        meal_plan_slots: Dict[str, Dict[str, List[str]]] = {}

        if isinstance(meta.get("meal_plan"), dict):
            schedule_mp = meta.get("meal_plan")
        elif isinstance(meta.get("features"), list):
            for feat in meta.get("features", []):
                if isinstance(feat, dict) and feat.get("type") == "meal_plan":
                    for plan in feat.get("plans", []):
                        date_str = plan.get("date")
                        if not date_str or not plan.get("meals"):
                            continue
                        if date_str not in meal_plan_slots:
                            meal_plan_slots[date_str] = {}
                        all_dish_names: List[str] = []
                        for m in plan["meals"]:
                            meal_time = m.get("mealTime") or "dinner"
                            dish_names: List[str] = [d.get("name") for d in m.get("dishes", []) if d.get("name")]
                            if dish_names:
                                # Store as List[str] (Phase 2 format)
                                meal_plan_slots[date_str][meal_time] = dish_names
                                all_dish_names.extend(dish_names)
                            for d in m.get("dishes", []):
                                name = d.get("name")
                                if name:
                                    ing_names = [i.get("name", "").strip() for i in d.get("ingredients", []) if i.get("name", "").strip()]
                                    if ing_names:
                                        dish_ingredients[name] = ing_names
                        if all_dish_names:
                            schedule_mp[date_str] = " and ".join(all_dish_names)
                elif isinstance(feat, dict) and feat.get("type") == "shopping_list":
                    schedule_sl = feat.get("items", [])
        elif isinstance(meta.get("features"), dict):
            schedule_mp = meta.get("features").get("meal_plan") or {}
        return schedule_mp, schedule_sl, dish_ingredients, meal_plan_slots

    @staticmethod
    def _extract_existing_dish_data(
        schedule: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        """Walk a schedule and return a mapping of dish_name -> dish snapshot.

        The snapshot preserves everything the CookingStepsAgent wrote to the dish:
          {"steps": [...], "ingredients": [{"name": ..., "quantity": ..., ...}, ...]}

        Only dishes that have at least one of steps or ingredient-quantities are included.
        """
        result: Dict[str, Dict[str, Any]] = {}
        meta = schedule.get("metadata") or {}
        for feat in (meta.get("features") or []):
            if not isinstance(feat, dict) or feat.get("type") != "meal_plan":
                continue
            for plan in (feat.get("plans") or []):
                for meal in (plan.get("meals") or []):
                    for dish in (meal.get("dishes") or []):
                        name = dish.get("name")
                        if not name:
                            continue
                        steps = dish.get("cookingSteps") or []
                        ingredients = dish.get("ingredients") or []
                        # Only include if there is actual generated data worth preserving
                        has_steps = bool(steps)
                        has_quantities = any(
                            isinstance(i, dict) and i.get("quantity")
                            for i in ingredients
                        )
                        if has_steps or has_quantities:
                            result[name] = {
                                "steps": steps,
                                "ingredients": ingredients,
                            }
        return result

    @staticmethod
    def _extract_cooking_steps_from_schedule(
        schedule: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        """Backward-compat wrapper: returns dish_name -> cookingSteps list.

        Prefer _extract_existing_dish_data for new code.
        """
        data = PipelineStorage._extract_existing_dish_data(schedule)
        return {name: d["steps"] for name, d in data.items() if d.get("steps")}

    async def create_or_update_meal_plan_schedule(
        self,
        owner_id: int,
        meal_plan: Dict[str, str],
        shopping_list: List[str],
        existing_schedule_id: Optional[int] = None,
        event_type: str = "meal_plan_draft",
        user_timezone: Optional[str] = None,
        dish_ingredients: Optional[Dict[str, List[str]]] = None,
        meal_plan_slots: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Optional[int]:
        """
        Create or update a meal plan draft in schedule. Used during PLAN_AHEAD conversation
        for real-time persistence so the user can see the plan in Schedule page.
        Uses user_timezone for correct local date (e.g. next Monday in user's location).
        
        Converts simple meal_plan format (date -> meal_text) to Feature system format.
        """
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            tz = None
        now = datetime.now(tz) if tz else datetime.now(timezone.utc)
        today = now.date()
        days_ahead = (7 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_monday = today + timedelta(days=days_ahead)
        # Use noon (12:00) instead of midnight to avoid date boundary issues across timezones
        from datetime import time
        
        # If meal_plan has multiple dates, create separate schedules for each date
        # Each schedule's scheduled_time should match its own date, not the earliest date
        if meal_plan and len(meal_plan) > 1:
            # Multiple dates: create separate schedule for each date
            logger.info(f"[PIPELINE STORAGE] Creating separate schedules for {len(meal_plan)} dates: {list(meal_plan.keys())}")
            created_schedule_ids = []
            for date_str, meal_text in meal_plan.items():
                try:
                    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                    scheduled_time = datetime.combine(date_obj, time(12, 0, 0))
                    
                    # Create single-date meal_plan for this schedule (dish_ingredients filtered by date's dishes if needed)
                    single_date_meal_plan = {date_str: meal_text}
                    # Pass this date's slots so breakfast/lunch/dinner are stored correctly (not all as dinner)
                    single_date_slots = {date_str: (meal_plan_slots or {}).get(date_str) or {}}
                    sub_dish_ingredients = None
                    if dish_ingredients:
                        import re
                        # Prefer dish names from slots (List[str] format) over splitting meal_text
                        date_slot = single_date_slots.get(date_str) or {}
                        dish_names_from_slots: List[str] = []
                        for mt_val in date_slot.values():
                            if isinstance(mt_val, list):
                                dish_names_from_slots.extend(mt_val)
                            elif isinstance(mt_val, str):
                                dish_names_from_slots.extend([p.strip() for p in re.split(r'\s+and\s+', mt_val) if p.strip()])
                        if dish_names_from_slots:
                            dish_names = dish_names_from_slots
                        else:
                            dish_names = re.split(r'\s+and\s+|\s+with\s+|,\s+', meal_text, flags=re.IGNORECASE)
                            dish_names = [n.strip() for n in dish_names if n.strip()]
                        # Include keys that match split names OR the full meal_text
                        sub_dish_ingredients = {k: v for k, v in dish_ingredients.items() if k in dish_names or (meal_text and k.strip() == meal_text.strip())}
                    # Use only this date's ingredients as shopping_list so we don't attach other days' ingredients to this schedule (e.g. pancake getting burger/拉面 ingredients)
                    date_shopping_list = []
                    if sub_dish_ingredients:
                        for ing_list in sub_dish_ingredients.values():
                            if isinstance(ing_list, list):
                                date_shopping_list.extend(ing_list)
                    # metadata is built further below after existing_schedule is found
                    # (so we can pass existing_cooking_steps for preservation)
                    description = None
                    # Use actual dish name in title so Edit Schedule shows the meal (API requires title)
                    title = meal_text.strip() if meal_text else f"Meal Plan - {date_str}"
                    
                    # Check if schedule for this date already exists
                    schedules = await self.get_user_schedules(owner_id)
                    existing_schedule = None
                    for s in schedules:
                        s_mp, _, _, _ = self._extract_meal_plan_from_schedule(s)
                        if date_str in s_mp and len(s_mp) == 1:
                            existing_schedule = s
                            break

                    # Preserve any cooking steps / ingredient quantities already saved for this date.
                    existing_dd = self._extract_existing_dish_data(existing_schedule) if existing_schedule else {}
                    metadata = self._convert_to_feature_format(
                        single_date_meal_plan, date_shopping_list,
                        dish_ingredients=sub_dish_ingredients,
                        meal_plan_slots=single_date_slots if (meal_plan_slots or {}).get(date_str) else None,
                        existing_dish_data=existing_dd or None,
                    )

                    if existing_schedule:
                        # Update existing single-date schedule: only Kitchen & Dining (metadata) + event_type; do not overwrite title/scheduled_time/Notes
                        ok = await self.update_schedule(
                            owner_id=owner_id,
                            schedule_id=existing_schedule["id"],
                            event_type=event_type,
                            metadata=metadata,
                        )
                        if ok:
                            created_schedule_ids.append(existing_schedule["id"])
                    else:
                        # Create new single-date schedule: minimal title/scheduled_time (API required), event_type, metadata (Kitchen & Dining only; no Notes)
                        schedule_id = await self.create_schedule(
                            owner_id=owner_id,
                            title=title,
                            scheduled_time=scheduled_time,
                            event_type=event_type,
                            metadata=metadata,
                        )
                        if schedule_id:
                            created_schedule_ids.append(schedule_id)
                except Exception as e:
                    logger.warning(f"Failed to create/update schedule for date {date_str}: {e}", exc_info=True)
                    continue
            
            # Return the first created schedule ID (for backward compatibility)
            return created_schedule_ids[0] if created_schedule_ids else None
        
        # Single date or empty meal_plan: use original logic
        logger.info(f"[STORAGE] received_meal_plan_slots: existing_schedule_id={existing_schedule_id}, meal_plan_keys={list(meal_plan.keys()) if meal_plan else []}, meal_plan_slots={meal_plan_slots}")
        # Determine scheduled_time based on meal_plan dates
        # If meal_plan has one date, use that date; otherwise use next_monday
        scheduled_time = None
        if meal_plan and len(meal_plan) == 1:
            # Single date: use that date
            date_str = list(meal_plan.keys())[0]
            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                scheduled_time = datetime.combine(date_obj, time(12, 0, 0))
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse single date {date_str} for scheduled_time: {e}")
        
        # Fallback to next_monday if no valid dates found in meal_plan
        if scheduled_time is None:
            scheduled_time = datetime.combine(next_monday, time(12, 0, 0))
        
        # Keep scheduled_time naive (no timezone) - date is computed in user's local timezone
        
        # Merge dish_ingredients, meal_plan_slots, and dish data from existing schedule when updating by id
        merged_dish_ingredients = dish_ingredients
        merged_meal_plan_slots = meal_plan_slots
        merged_dish_data: Dict[str, Dict[str, Any]] = {}
        if existing_schedule_id:
            schedules = await self.get_user_schedules(owner_id)
            for s in schedules:
                if s.get("id") == existing_schedule_id:
                    _, _, existing_di, existing_slots = self._extract_meal_plan_from_schedule(s)
                    merged_dish_ingredients = {**existing_di, **(dish_ingredients or {})}
                    # Merge slots: per-date, per-mealTime; new overwrites
                    if existing_slots or meal_plan_slots:
                        merged_meal_plan_slots = {}
                        for d in set((existing_slots or {}).keys()) | set((meal_plan_slots or {}).keys()):
                            merged_meal_plan_slots[d] = {**((existing_slots or {}).get(d) or {}), **((meal_plan_slots or {}).get(d) or {})}
                    # Preserve cooking steps + ingredient quantities (richer than old steps-only dict)
                    merged_dish_data = self._extract_existing_dish_data(s)
                    break

        # Convert to Feature format (use meal_plan_slots when present so lunch/breakfast/dinner are stored)
        metadata = self._convert_to_feature_format(
            meal_plan, shopping_list,
            dish_ingredients=merged_dish_ingredients,
            meal_plan_slots=merged_meal_plan_slots,
            existing_dish_data=merged_dish_data or None,
        )
        # Do not save ingredients to Notes (description); they live only in metadata.features = Kitchen & Dining
        description = None
        # Minimal title for API (required); actual meal content is in metadata
        if event_type == "shopping_list":
            title = "Next Week Shopping List"
        elif meal_plan:
            meal_names = list(meal_plan.values())
            title = meal_names[0].strip() if meal_names and meal_names[0] else "Next Week Meal Plan (Draft)"
        else:
            title = "Next Week Meal Plan (Draft)"

        # Try to update existing schedule if ID is provided
        if existing_schedule_id:
            _feat = metadata.get("features", [])
            _plans_summary = []
            for f in _feat:
                if isinstance(f, dict) and f.get("type") == "meal_plan":
                    for p in f.get("plans", []):
                        _meals = [(m.get("mealTime"), [d.get("name") for d in m.get("dishes", [])]) for m in p.get("meals", [])]
                        _plans_summary.append({"date": p.get("date"), "meals": _meals})
            logger.info(f"[STORAGE] metadata_sent_to_api: existing_schedule_id={existing_schedule_id}, plans_summary={_plans_summary}")
            # Only update Kitchen & Dining (metadata) + event_type; do not overwrite title/scheduled_time/Notes
            ok = await self.update_schedule(
                owner_id=owner_id,
                schedule_id=existing_schedule_id,
                event_type=event_type,
                metadata=metadata,
            )
            if ok:
                return existing_schedule_id
            else:
                # Update failed (likely 404 - schedule was deleted), fallback to find/create
                logger.warning(f"Failed to update schedule id={existing_schedule_id}, will try to find or create new one")

        # Find existing draft schedule or create new one
        schedules = await self.get_user_schedules(owner_id)
        draft = None
        for s in schedules:
            if s.get("event_type") in ("meal_plan_draft", "shopping_list") and "Next Week" in (s.get("title") or ""):
                draft = s
                break

        if draft:
            # Merge existing meal_plan with new meal_plan instead of overwriting
            existing_meal_plan = {}
            if draft.get("metadata"):
                meta = draft.get("metadata", {})
                if isinstance(meta.get("meal_plan"), dict):
                    existing_meal_plan = meta.get("meal_plan", {}).copy()
                elif isinstance(meta.get("features"), list):
                    for feat in meta.get("features", []):
                        if isinstance(feat, dict) and feat.get("type") == "meal_plan":
                            for plan in feat.get("plans", []):
                                if plan.get("date") and plan.get("meals"):
                                    dish_names = [d.get("name") for m in plan["meals"] for d in m.get("dishes", []) if d.get("name")]
                                    if dish_names:
                                        existing_meal_plan[plan["date"]] = " and ".join(dish_names)
            
            # Merge: existing meal_plan + new meal_plan (new overwrites existing for same dates)
            merged_meal_plan = {**existing_meal_plan, **meal_plan}
            
            # Merge shopping lists
            existing_shopping_list = []
            if draft.get("metadata"):
                meta = draft.get("metadata", {})
                if isinstance(meta.get("shopping_list"), list):
                    existing_shopping_list = meta.get("shopping_list", []).copy()
                elif isinstance(meta.get("features"), list):
                    for feat in meta.get("features", []):
                        if isinstance(feat, dict) and feat.get("type") == "shopping_list":
                            existing_shopping_list = feat.get("items", [])

            merged_shopping_list = list(set(existing_shopping_list + shopping_list))
            # Merge dish_ingredients, meal_plan_slots, and full dish data from draft
            existing_dish_ingredients = {}
            existing_slots = {}
            draft_dish_data: Dict[str, Dict[str, Any]] = {}
            if draft.get("metadata") and isinstance(draft.get("metadata"), dict):
                _, _, existing_dish_ingredients, existing_slots = self._extract_meal_plan_from_schedule(draft)
                draft_dish_data = self._extract_existing_dish_data(draft)
            merged_dish_ingredients = {**existing_dish_ingredients, **(dish_ingredients or {})}
            merged_slots = {}
            for d in set((existing_slots or {}).keys()) | set((meal_plan_slots or {}).keys()):
                merged_slots[d] = {**((existing_slots or {}).get(d) or {}), **((meal_plan_slots or {}).get(d) or {})}
            merged_metadata = self._convert_to_feature_format(
                merged_meal_plan, merged_shopping_list,
                dish_ingredients=merged_dish_ingredients,
                meal_plan_slots=merged_slots or None,
                existing_dish_data=draft_dish_data or None,
            )

            # Only update Kitchen & Dining (metadata) + event_type
            ok = await self.update_schedule(
                owner_id=owner_id,
                schedule_id=draft["id"],
                event_type=event_type,
                metadata=merged_metadata,
            )
            return draft["id"] if ok else None

        # No existing draft found, create new schedule (minimal title/scheduled_time for API; no Notes)
        return await self.create_schedule(
            owner_id=owner_id,
            title=title,
            scheduled_time=scheduled_time,
            event_type=event_type,
            metadata=metadata,
        )

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


async def get_document(doc_id: str, owner_id: Optional[int] = None, include_embedding: bool = False) -> Optional[Dict[str, Any]]:
    """Convenience function: Get document using default storage instance."""
    return await _default_storage.get_document(doc_id, owner_id=owner_id, include_embedding=include_embedding)


async def get_embedding(doc_id: str, owner_id: Optional[int] = None) -> Optional[List[float]]:
    """Convenience function: Get embedding using default storage instance."""
    return await _default_storage.get_embedding(doc_id, owner_id=owner_id)


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

