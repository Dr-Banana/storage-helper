"""
Public API for external services (e.g., AI Orchestra Service)

These endpoints expose high-level business operations.
Internal schema details are completely hidden.
"""
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from io import BytesIO
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.services.document_service import DocumentService
from app.models.document import Document

router = APIRouter(prefix="/api/v1", tags=["public-api"])


# ============================================================
# Document Processing API for AI Service
# ============================================================

@router.post(
    "/documents/upload",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Upload document page image to storage",
    description="""
    Upload a document page image to storage backend.
    Returns image_url for later use in /documents/process endpoint.
    
    No database operations - purely file storage.
    
    Supports temporary upload mode for preview workflows:
    - If is_temporary=true, file is uploaded to tmp/ folder
    - These files can be automatically cleaned up if not confirmed within a week
    - When confirmed via /documents/process, they are moved to permanent storage
    """
)
def upload_document_image(
    file: UploadFile = File(..., description="Document page image file"),
    owner_id: int = Form(..., description="Document owner user ID"),
    is_temporary: bool = Form(False, description="If true, upload to tmp/ folder for preview (can be cleaned up later)"),
):
    """
    Upload document page image to storage.
    
    - **file**: Document page image file (required)
    - **owner_id**: Document owner user ID (required)
    - **is_temporary**: If true, upload to tmp/ folder for preview (default: false)
    
    Returns image_url for use in /documents/process endpoint
    """
    try:
        # Read file content
        file_content = BytesIO(file.file.read())
        
        # Upload to storage and get image_url
        image_url = DocumentService.upload_file_only(
            file_content=file_content,
            filename=file.filename,
            owner_id=owner_id,
            is_temporary=is_temporary
        )
        
        return {
            "image_url": image_url,
            "filename": file.filename,
            "is_temporary": is_temporary
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload document image: {str(e)}"
        )


class DocumentProcessRequest(BaseModel):
    """Schema for processing a document page (structured JSON request)"""

    image_url: str = Field(..., description="Image URL from /documents/upload endpoint")
    owner_id: int = Field(..., description="Document owner user ID")
    page_number: int = Field(..., description="Page number within document (1-indexed)")

    ocr_text: Optional[str] = Field(None, description="Optional: OCR extracted text for this page")
    document_id: Optional[int] = Field(
        None,
        description="Optional existing document ID. If not provided, creates new document",
    )
    category_id: Optional[int] = Field(None, description="Optional: Document category ID")
    location_id: Optional[int] = Field(None, description="Optional: Storage location ID (use -1 for no location)")

    # Optional extracted metadata JSON (will be persisted into Document.doc_metadata JSON)
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional: Extracted document metadata")


@router.post(
    "/documents/process",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Process and persist document page metadata",
    description="""
    Save document page metadata to database.
    Requires image_url from /documents/upload endpoint.
    
    - If document_id is provided, adds page to existing document
    - If document_id is not provided, creates new document
    
    Returns document_id, page_id and status code
    """
)
async def process_document_page(
    payload: DocumentProcessRequest,
    db: Session = Depends(get_db),
):
    """
    Process and persist document page metadata.

    Note:
    - This endpoint expects a JSON body for stability and clear typing.
    - `metadata` (if provided) will be merged into Document.doc_metadata (JSON) for future retrieval.
    """
    try:
        # Process page and save to database
        doc_id, page_id, returned_image_url, item_ids = DocumentService.process_document_page(
            db=db,
            image_url=payload.image_url,
            owner_id=payload.owner_id,
            page_number=payload.page_number,
            ocr_text=payload.ocr_text,
            document_id=payload.document_id,
            category_id=payload.category_id,
            location_id=payload.location_id,
            metadata=payload.metadata,
        )
        
        # Determine status
        if payload.document_id:
            status_value = "updated"
        else:
            status_value = "created"
        
        # item_ids is a list of created item document IDs
        items_created = len(item_ids) if item_ids else 0
        
        return {
            "document_id": doc_id,
            "page_id": page_id,
            "image_url": returned_image_url,
            "status": status_value,
            "page_number": payload.page_number,
            "items_created": items_created,
            "item_ids": item_ids  # Include item IDs for embedding generation
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process document page: {str(e)}"
        )

