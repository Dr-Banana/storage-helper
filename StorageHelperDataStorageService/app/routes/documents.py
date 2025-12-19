"""
Document management routes
"""
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from typing import List
import json
import os
import urllib.parse
from pathlib import Path

from app.core.database import get_db
from app.models.document import Document
from app.models.document_page import DocumentPage
from app.models.document_embedding import DocumentEmbedding

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
def get_document_pages(document_id: int, db: Session = Depends(get_db)):
    """
    Get all pages for a specific document
    
    - **document_id**: The document's ID
    
    Returns a list of page details including image_url for the document
    """
    try:
        # Verify document exists
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID {document_id} not found"
            )
        
        # Get all pages for the document with full details
        pages = db.query(DocumentPage).filter(
            DocumentPage.document_id == document_id
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
                # Determine file type based on URL
                image_url_lower = page.image_url.lower()
                is_pdf = (
                    image_url_lower.endswith('.pdf') or 
                    '.pdf' in image_url_lower or
                    'application/pdf' in image_url_lower
                )
                
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
            "document_id": document_id,
            "document": {
                "id": document.id,
                "title": document.title,
                "category_id": document.category_id,
                "owner_id": document.owner_id,
                "event_id": document.event_id,
                "current_location_id": document.current_location_id,
                "metadata": document.doc_metadata,
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
    document_id: int,
    embedding_request: EmbeddingRequest,
    db: Session = Depends(get_db)
):
    """
    Update or create embedding for a document.
    
    - **document_id**: Document ID (path parameter, must match request body)
    - **embedding**: 768-dimensional embedding vector
    """
    try:
        # Verify document_id matches
        if document_id != embedding_request.document_id:
            raise ValueError("document_id in path and body must match")
        
        # Verify embedding dimensions
        if len(embedding_request.embedding) != 768:
            raise ValueError(f"Embedding must be 768 dimensions, got {len(embedding_request.embedding)}")
        
        # Verify document exists
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            raise ValueError(f"Document with ID {document_id} not found")
        
        # Check if embedding already exists
        existing_embedding = db.query(DocumentEmbedding).filter(
            DocumentEmbedding.document_id == document_id
        ).first()
        
        # Use raw SQL with STRING_TO_VECTOR to properly insert VECTOR type
        # MySQL VECTOR type requires STRING_TO_VECTOR('[0.1, 0.2, ...]') format
        embedding_json_str = json.dumps(embedding_request.embedding)
        
        if existing_embedding:
            # Update existing embedding using raw SQL with STRING_TO_VECTOR
            sql = """
                UPDATE document_embedding 
                SET embedding = STRING_TO_VECTOR(:embedding_json)
                WHERE document_id = :document_id
            """
            db.execute(text(sql), {
                "document_id": document_id,
                "embedding_json": embedding_json_str
            })
            action = "updated"
        else:
            # Insert new embedding using raw SQL with STRING_TO_VECTOR
            sql = """
                INSERT INTO document_embedding (document_id, embedding)
                VALUES (:document_id, STRING_TO_VECTOR(:embedding_json))
            """
            db.execute(text(sql), {
                "document_id": document_id,
                "embedding_json": embedding_json_str
            })
            action = "created"
        
        db.commit()
        
        return {
            "document_id": document_id,
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
