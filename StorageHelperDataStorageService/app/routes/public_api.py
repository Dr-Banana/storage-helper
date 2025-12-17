"""
Public API for external services (e.g., AI Orchestra Service)

These endpoints expose high-level business operations.
Internal schema details are completely hidden.
"""
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, List
from io import BytesIO

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
    """
)
def upload_document_image(
    file: UploadFile = File(..., description="Document page image file"),
    owner_id: int = Form(..., description="Document owner user ID"),
):
    """
    Upload document page image to storage.
    
    - **file**: Document page image file (required)
    - **owner_id**: Document owner user ID (required)
    
    Returns image_url for use in /documents/process endpoint
    """
    try:
        # Read file content
        file_content = BytesIO(file.file.read())
        
        # Upload to storage and get image_url
        image_url = DocumentService.upload_file_only(
            file_content=file_content,
            filename=file.filename,
            owner_id=owner_id
        )
        
        return {
            "image_url": image_url,
            "filename": file.filename
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
def process_document_page(
    image_url: str = Form(..., description="Image URL from /documents/upload endpoint"),
    owner_id: int = Form(..., description="Document owner user ID"),
    page_number: int = Form(..., description="Page number within document (1-indexed)"),
    ocr_text: Optional[str] = Form(None, description="Optional: OCR extracted text for this page"),
    document_id: Optional[int] = Form(None, description="Optional existing document ID. If not provided, creates new document"),
    db: Session = Depends(get_db)
):
    """
    Process and persist document page metadata.
    
    - **image_url**: Image URL (from /documents/upload)
    - **owner_id**: Document owner user ID (required)
    - **page_number**: Page number within document (required, 1-indexed)
    - **ocr_text**: OCR extracted text for this page (optional)
    - **document_id**: Optional existing document ID. If not provided, creates new document
    """
    try:
        # Process page and save to database
        doc_id, page_id, returned_image_url = DocumentService.process_document_page(
            db=db,
            image_url=image_url,
            owner_id=owner_id,
            page_number=page_number,
            ocr_text=ocr_text,
            document_id=document_id
        )
        
        # Determine status
        if document_id:
            status_value = "updated"
        else:
            status_value = "created"
        
        return {
            "document_id": doc_id,
            "page_id": page_id,
            "image_url": returned_image_url,
            "status": status_value,
            "page_number": page_number
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

