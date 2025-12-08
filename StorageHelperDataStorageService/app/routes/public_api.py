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
    "/documents/upload-and-process",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Upload document page with OCR result",
    description="""
    Upload a document page with OCR text.
    
    - If document_id is provided, adds page to existing document
    - If document_id is not provided, creates new document
    
    Returns document_id, page_id and status code
    """
)
def upload_and_process(
    file: UploadFile = File(..., description="Document page image file"),
    owner_id: int = Form(..., description="Document owner user ID"),
    page_number: int = Form(..., description="Page number within document (1-indexed)"),
    ocr_text: str = Form(..., description="OCR extracted text for this page"),
    document_id: Optional[int] = Form(None, description="Optional existing document ID. If not provided, creates new document"),
    db: Session = Depends(get_db)
):
    """
    Upload a document page with OCR result.
    
    - **file**: Document page image file
    - **owner_id**: Document owner user ID (required)
    - **page_number**: Page number within document (required, 1-indexed)
    - **ocr_text**: OCR extracted text for this page (required)
    - **document_id**: Optional existing document ID. If not provided, creates new document
    """
    try:
        # Read file content
        file_content = BytesIO(file.file.read())
        
        # Upload page and optionally create document
        doc_id, page_id = DocumentService.upload_document_page(
            db=db,
            file_content=file_content,
            filename=file.filename,
            owner_id=owner_id,
            page_number=page_number,
            ocr_text=ocr_text,
            document_id=document_id
        )
        
        # Return document_id, page_id and status
        return {
            "document_id": doc_id,
            "page_id": page_id,
            "status": "created" if not document_id else "updated",
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
            detail=f"Failed to upload document page: {str(e)}"
        )

