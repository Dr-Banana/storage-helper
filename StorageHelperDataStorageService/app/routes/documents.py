"""
Document management routes
"""
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from typing import List, Annotated, Optional, Dict, Any
from datetime import datetime, date
import json
import os
import urllib.parse
from pathlib import Path

from app.core.database import get_db
from app.core.auth import get_current_user_id
from app.core.auth_decorators import get_document_if_owner, get_location_if_owner
from app.models.document import Document
from app.models.document_page import DocumentPage
from app.models.document_embedding import DocumentEmbedding
from app.models.storage_location import StorageLocation
from app.models.document_category import DocumentCategory
from app.schemas.document import DocumentSearchRequest
from app.schemas.location import LocationResponse
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


# ============================================================
# Helper Functions
# ============================================================

def _convert_to_accessible_url(file_path: str) -> str:
    """
    Convert local file path to accessible HTTP URL.
    If already a URL (http/https), return as is.
    If local file path, convert to API endpoint URL.
    """
    # If already a URL, return as is
    if file_path.startswith(('http://', 'https://')):
        return file_path
    
    # If local file path, convert to API endpoint URL
    # Encode the file path to handle special characters
    encoded_path = urllib.parse.quote(file_path, safe='')
    return f"/api/documents/files?path={encoded_path}"


# ============================================================
# File Serving Endpoint
# ============================================================

def _hydrate_document_metadata(db: Session, document: Document) -> Dict[str, Any]:
    """
    Helper function to hydrate document metadata with live data from child items.
    Used for receipt documents to ensure items list reflects current inventory state.
    """
    doc_metadata = document.doc_metadata or {}
    
    # Check if this is a receipt with items
    if "items" in doc_metadata and isinstance(doc_metadata["items"], list):
        items = doc_metadata["items"]
        hydrated_items = []
        
        # Collect IDs to fetch
        doc_ids_to_fetch = []
        for item in items:
            if item.get("document_id"):
                doc_ids_to_fetch.append(item.get("document_id"))
        
        # Batch fetch child documents if we have IDs
        child_docs_map = {}
        if doc_ids_to_fetch:
            # Use joinedload to prevent N+1 queries and ensure data availability
            child_docs = db.query(Document).options(
                joinedload(Document.location),
                joinedload(Document.category)
            ).filter(Document.id.in_(doc_ids_to_fetch)).all()
            for child in child_docs:
                child_docs_map[child.id] = child
        
        # Also fetch by source_receipt_id for fallback/discovery
        related_docs = db.query(Document).options(
            joinedload(Document.location),
            joinedload(Document.category)
        ).filter(
            Document.owner_id == document.owner_id,
            text("metadata->>'source_receipt_id' = :receipt_id")
        ).params(receipt_id=str(document.id)).all()
        
        related_docs_map = {} # Map by original_text or title for fallback
        for doc in related_docs:
            key = (doc.doc_metadata or {}).get("original_text") or doc.title
            if key:
                related_docs_map[key] = doc
        
        for item in items:
            # 1. Try finding by ID
            child_doc = None
            if item.get("document_id"):
                child_doc = child_docs_map.get(item.get("document_id"))
            
            # 2. Fallback: Try finding by matching key (original_text or name)
            if not child_doc:
                key = item.get("original_text") or item.get("product_name")
                if key:
                    child_doc = related_docs_map.get(key)
            
            if child_doc:
                # Hydrate item with live data
                hydrated_item = item.copy()
                
                # 1. Sync Product Name
                if child_doc.title:
                    hydrated_item["product_name"] = child_doc.title
                
                # 2. Sync Metadata Fields (Quantity, Unit, Expiry, etc.)
                if child_doc.doc_metadata:
                    if "quantity" in child_doc.doc_metadata:
                        hydrated_item["quantity"] = child_doc.doc_metadata["quantity"]
                    if "unit" in child_doc.doc_metadata:
                        hydrated_item["unit"] = child_doc.doc_metadata["unit"]
                    if "expiry_date" in child_doc.doc_metadata:
                        expiry_str = child_doc.doc_metadata["expiry_date"]
                        hydrated_item["expiry_date"] = expiry_str
                        # Recalculate estimated_shelf_life_days so frontend "Life" column updates
                        try:
                            if expiry_str:
                                # Handle partial dates or simple strings if possible, but standard is YYYY-MM-DD
                                expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                                today = date.today()
                                delta = expiry - today
                                hydrated_item["estimated_shelf_life_days"] = delta.days
                        except (ValueError, TypeError):
                            # If parsing fails, leave existing estimate or None
                            pass
                
                # 3. Sync Location
                if child_doc.current_location_id:
                    hydrated_item["location_id"] = child_doc.current_location_id
                    if child_doc.location:
                        hydrated_item["location_name"] = child_doc.location.name
                        # Also update storage_suggestion to reflect actual location name for consistent display
                        hydrated_item["storage_suggestion"] = child_doc.location.name 
                else:
                    # If location removed on item, reflect that
                    hydrated_item["location_id"] = None
                    hydrated_item["location_name"] = None
                
                # 4. Sync Category
                if child_doc.category:
                    # Prefer Code for UI mapping, fallback to Name
                    hydrated_item["category"] = child_doc.category.code or child_doc.category.name
                
                # Ensure document_id is set
                hydrated_item["document_id"] = child_doc.id
                
                hydrated_items.append(hydrated_item)
            else:
                hydrated_items.append(item)
        
        # Return modified metadata (copy)
        doc_metadata = dict(doc_metadata)
        doc_metadata["items"] = hydrated_items
        return doc_metadata
    
    return doc_metadata


@router.get("/files", response_class=FileResponse, summary="Serve document files")
def serve_file(path: str):
    """
    Serve document files (images, PDFs) from local storage.
    
    - **path**: Encoded file path (URL encoded)
    
    Returns the file content with appropriate content type.
    """
    try:
        # Decode the file path
        file_path = urllib.parse.unquote(path)
        
        # Security check: ensure file exists and is within storage directory
        if not os.path.exists(file_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {file_path}"
            )
        
        # Determine media type based on file extension
        file_ext = Path(file_path).suffix.lower()
        media_type_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
            '.pdf': 'application/pdf',
        }
        
        media_type = media_type_map.get(file_ext, 'application/octet-stream')
        
        # For PDF files, ensure inline display (not download)
        headers = {}
        if file_ext == '.pdf':
            headers['Content-Disposition'] = 'inline'
        
        return FileResponse(
            file_path,
            media_type=media_type,
            filename=os.path.basename(file_path),
            headers=headers
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to serve file: {str(e)}"
        )


# ============================================================
# Data Models
# ============================================================

class DocumentLocationUpdate(BaseModel):
    """Request model for updating document storage location"""
    location_id: int


class DocumentUpdate(BaseModel):
    """Request model for updating document details"""
    title: Optional[str] = None
    category_id: Optional[int] = None
    current_location_id: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class EmbeddingRequest(BaseModel):
    """Request model for updating document embedding"""
    document_id: int
    embedding: List[float]  # 768-dimensional vector
    
    class Config:
        schema_extra = {
            "example": {
                "document_id": 123,
                "embedding": [0.123, -0.456, 0.789, "..."]  # 768 dimensions
            }
        }


@router.get(
    "/{document_id}/pages",
    response_model=dict,
    summary="Get all pages for a document",
    description="Retrieve all pages for a specific document. Returns full page details including image_url."
)
def get_document_pages(
    document: Annotated[Document, Depends(get_document_if_owner)],
    db: Session = Depends(get_db)
):
    """
    Get all pages for a specific document
    
    - **document_id**: The document's ID
    
    Returns a list of page details including image_url for the document
    
    Authorization: User must own the document (verified by dependency)
    """
    try:
        # Hydrate receipt items with live data from child documents
        hydrated_metadata = _hydrate_document_metadata(db, document)
        
        # Get all pages for the document with full details
        pages = db.query(DocumentPage).filter(
            DocumentPage.document_id == document.id
        ).order_by(DocumentPage.page_number).all()
        
        # Convert to response format
        page_details = [
            {
                "id": page.id,
                "document_id": page.document_id,
                "page_number": page.page_number,
                "image_url": _convert_to_accessible_url(page.image_url) if page.image_url else None,
                "ocr_text": page.ocr_text,
                "created_at": page.created_at.isoformat() if page.created_at else None,
                "updated_at": page.updated_at.isoformat() if page.updated_at else None,
            }
            for page in pages
        ]
        
        # Get unique files (deduplicate by image_url)
        # This ensures that if multiple pages point to the same file URL, 
        # we only return the file once
        unique_files = {}
        for page in pages:
            if page.image_url and page.image_url not in unique_files:
                # Determine file type based on file extension (more reliable)
                # Extract the actual filename/extension from the path
                file_path = page.image_url
                file_ext = Path(file_path).suffix.lower()
                
                # Check file extension to determine type
                # Only consider .pdf files as PDF type, everything else is image
                is_pdf = file_ext == '.pdf'
                
                # Convert local file path to accessible URL
                accessible_url = _convert_to_accessible_url(page.image_url)
                
                unique_files[page.image_url] = {
                    "url": accessible_url,
                    "file_type": "pdf" if is_pdf else "image",
                    "first_page_number": page.page_number,
                    # Include OCR text from the first page that references this file
                    "ocr_text": page.ocr_text,
                }
        
        # Convert to list
        file_list = list(unique_files.values())
        
        return {
            "document_id": document.id,
            "document": {
                "id": document.id,
                "title": document.title,
                "category_id": document.category_id,
                "owner_id": document.owner_id,
                "event_id": document.event_id,
                "current_location_id": document.current_location_id,
                "location_name": document.location.name if document.location else None,
                "metadata": hydrated_metadata,
                "image_url": _convert_to_accessible_url(document.image_url) if document.image_url else None,
                "created_at": document.created_at.isoformat() if document.created_at else None,
                "updated_at": document.updated_at.isoformat() if document.updated_at else None,
            },
            "total": len(page_details),
            "pages": page_details,
            "files": file_list,
            "total_files": len(file_list)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve document pages: {str(e)}"
        )


@router.get(
    "/{document_id}",
    response_model=dict,
    summary="Get document by ID",
    description="Retrieve basic metadata for a specific document."
)
def get_document_by_id(
    document: Annotated[Document, Depends(get_document_if_owner)],
    db: Session = Depends(get_db)
):
    """
    Get a specific document's metadata.
    
    - **document_id**: The document's ID
    
    Authorization: User must own the document (verified by dependency)
    """
    # Hydrate receipt items with live data from child documents
    hydrated_metadata = _hydrate_document_metadata(db, document)

    return {
        "id": document.id,
        "title": document.title,
        "category_id": document.category_id,
        "owner_id": document.owner_id,
        "event_id": document.event_id,
        "current_location_id": document.current_location_id,
        "location_name": document.location.name if document.location else None,
        "metadata": hydrated_metadata,
        "image_url": _convert_to_accessible_url(document.image_url) if document.image_url else None,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }



# ============================================================
# Document Embedding API
# ============================================================

@router.post(
    "/{document_id}/embedding",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Update or create document embedding",
    description="""
    Update semantic vector embedding for a document.
    
    - Creates new embedding if document doesn't have one
    - Updates existing embedding if document already has one
    
    Returns document_id and confirmation status
    """
)
def update_document_embedding(
    document: Annotated[Document, Depends(get_document_if_owner)],
    embedding_request: EmbeddingRequest,
    db: Session = Depends(get_db)
):
    """
    Update or create embedding for a document.
    
    - **document_id**: Document ID (path parameter, must match request body)
    - **embedding**: 768-dimensional embedding vector
    
    Authorization: User must own the document (verified by dependency)
    """
    try:
        # Verify document_id matches request body
        if document.id != embedding_request.document_id:
            raise ValueError("document_id in path and body must match")
        
        # Verify embedding dimensions
        if len(embedding_request.embedding) != 768:
            raise ValueError(f"Embedding must be 768 dimensions, got {len(embedding_request.embedding)}")
        
        # Check if embedding already exists
        existing_embedding = db.query(DocumentEmbedding).filter(
            DocumentEmbedding.document_id == document.id
        ).first()
        
        if existing_embedding:
            # Update existing embedding
            existing_embedding.embedding = embedding_request.embedding
            action = "updated"
        else:
            # Insert new embedding
            new_embedding = DocumentEmbedding(
                document_id=document.id,
                embedding=embedding_request.embedding
            )
            db.add(new_embedding)
            action = "created"
        
        db.commit()
        
        return {
            "document_id": document.id,
            "status": action,
            "message": f"Embedding {action} successfully"
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update embedding: {str(e)}"
        )


# ============================================================
# Document Search API
# ============================================================

@router.post(
    "/search",
    response_model=List[int],
    summary="Search documents by embedding",
    description="""
    Search for top k documents matching the provided embedding for a specific user.
    If fewer than k documents are found, returns all matching documents.
    """
)
def search_documents(
    search_request: DocumentSearchRequest,
    db: Session = Depends(get_db)
):
    """
    Search documents by embedding.
    
    - **embedding**: 768-dimensional query vector
    - **user_id**: ID of the user whose documents to search
    - **top_k**: Number of results to return
    
    Returns a list of document IDs.
    """
    try:
        # Verify embedding dimensions
        if len(search_request.embedding) != 768:
            raise ValueError(f"Embedding must be 768 dimensions, got {len(search_request.embedding)}")
        
        documents = DocumentService.search_by_embedding(
            db=db,
            embedding=search_request.embedding,
            limit=search_request.top_k,
            owner_id=search_request.user_id,
            exclude_receipts=search_request.exclude_receipts
        )
        
        return [doc.id for doc in documents]
        
    except ValueError as e:
        # Log the detailed error before raising 400
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Search failed with ValueError: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search documents: {str(e)}"
        )


@router.patch(
    "/{document_id}",
    response_model=dict,
    summary="Update document details",
    description="Update document title, category, location, or metadata directly."
)
def update_document(
    document_id: int,
    update_data: DocumentUpdate,
    db: Session = Depends(get_db)
):
    """
    Update document details directly.
    """
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID {document_id} not found"
            )
        
        if update_data.title is not None:
            document.title = update_data.title
        
        if update_data.category_id is not None:
            document.category_id = update_data.category_id
            
        if update_data.current_location_id is not None:
            # Handle -1 as None (no location)
            loc_id = None if update_data.current_location_id == -1 else update_data.current_location_id
            document.current_location_id = loc_id
            
        if update_data.metadata is not None:
            # Merge metadata
            current_metadata = dict(document.doc_metadata or {})
            current_metadata.update(update_data.metadata)
            document.doc_metadata = current_metadata
            
            # Special logic: If this is a receipt and items are updated, sync items to child documents
            if "items" in update_data.metadata:
                # Check if it is a receipt (by category code or existing source_receipt_id in children)
                is_receipt = False
                if document.category_id:
                     category = db.query(DocumentCategory).filter(DocumentCategory.id == document.category_id).first()
                     if category and category.code.upper() in ["RECEIPT", "REC"]:
                         is_receipt = True
                
                if is_receipt:
                    # Sync items (update existing or create new)
                    # This preserves embeddings of existing items
                    DocumentService._create_item_documents(db, document, document.doc_metadata)

        db.commit()
        db.refresh(document)
        
        return {
            "id": document.id,
            "status": "updated",
            "message": "Document updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update document: {str(e)}"
        )


@router.get(
    "/{document_id}/location",
    response_model=LocationResponse,
    summary="Get document storage location",
    description="Retrieve the physical storage location details for a specific document."
)
def get_document_location(
    document: Annotated[Document, Depends(get_document_if_owner)],
    db: Session = Depends(get_db)
):
    """
    Get storage location for a specific document
    
    - **document_id**: The document's ID
    
    Returns the StorageLocation details
    
    Authorization: User must own the document (verified by dependency)
    """
    try:
        if not document.current_location_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID {document.id} has no storage location assigned"
            )
        
        # Get location details
        location = db.query(StorageLocation).filter(
            StorageLocation.id == document.current_location_id
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Storage location with ID {document.current_location_id} not found"
            )
        
        return location
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve document location: {str(e)}"
        )


@router.put(
    "/{document_id}/location",
    response_model=dict,
    summary="Update document storage location",
    description="Update the physical storage location for a specific document."
)
def update_document_location(
    document: Annotated[Document, Depends(get_document_if_owner)],
    location_update: DocumentLocationUpdate,
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: Session = Depends(get_db)
):
    """
    Set storage location for a specific document
    
    - **document_id**: The document's ID
    - **location_id**: The storage location ID to assign
    
    Returns status and confirmation message
    
    Authorization: User must own both the document and location
    """
    try:
        # Verify location ownership
        location = db.query(StorageLocation).filter(
            StorageLocation.id == location_update.location_id
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Location not found"
            )
        
        if location.user_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this location"
            )
        
        document.current_location_id = location.id
        db.commit()
        
        return {
            "document_id": document.id,
            "location_id": location.id,
            "status": "updated",
            "message": "Document storage location updated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update document location: {str(e)}"
        )


@router.get(
    "/locations/{location_id}/documents",
    response_model=List[int],
    summary="Get all documents in a location",
    description="Retrieve a list of document IDs stored in a specific physical location."
)
def get_location_documents(location_id: int, db: Session = Depends(get_db)):
    """
    Get all document IDs for a specific storage location
    
    - **location_id**: The storage location's ID
    
    Returns a list of document IDs.
    """
    try:
        # Verify location exists
        location = db.query(StorageLocation).filter(StorageLocation.id == location_id).first()
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Storage location with ID {location_id} not found"
            )
        
        # Get all documents assigned to this location
        documents = db.query(Document).filter(
            Document.current_location_id == location_id
        ).all()
        
        return [doc.id for doc in documents]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve documents for location: {str(e)}"
        )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete document",
    description="Delete a specific document and all its associated data (pages, embeddings, files)."
)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    """
    Delete a specific document
    
    - **document_id**: The document's ID
    """
    try:
        DocumentService.delete_document(db, document_id)
        return None
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete document: {str(e)}"
        )
