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
    summary="Upload and register a new document",
    description="""
    Complete workflow: upload file to storage + create document record
    
    Called by AI Service to register new documents.
    Returns document metadata for further processing (OCR, embedding, etc.)
    """
)
def upload_and_process(
    file: UploadFile = File(..., description="Document image file"),
    owner_id: int = Form(..., description="Document owner user ID"),
    category: str = Form(..., description="Document category (TAX, VISA, MED, INS, etc.)"),
    event_name: Optional[str] = Form(None, description="Associated event name"),
    db: Session = Depends(get_db)
):
    """
    Upload document file and create database record.
    
    Returns only essential metadata (id, url, owner_id).
    Does not expose internal schema structure.
    """
    try:
        # Read file content
        file_content = BytesIO(file.file.read())
        
        # Process document (upload + save)
        document = DocumentService.process_new_document(
            db=db,
            file_content=file_content,
            filename=file.filename,
            owner_id=owner_id,
            category_code=category,
            event_name=event_name
        )
        
        # Return minimal response (hide internal schema)
        return {
            "id": document.id,
            "filename": document.title,
            "url": document.image_url,
            "owner_id": document.owner_id,
            "created_at": document.created_at.isoformat()
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process document: {str(e)}"
        )


@router.post(
    "/documents/{document_id}/save-ocr-and-embedding",
    response_model=dict,
    summary="Save OCR text and vector embedding",
    description="""
    Called by AI Service after OCR and embedding generation.
    
    Saves extracted text and vector embeddings for semantic search.
    """
)
def save_ocr_and_embedding(
    document_id: int,
    ocr_text: str = Form(..., description="Extracted text from OCR"),
    embedding: List[float] = Form(..., description="Vector embedding"),
    db: Session = Depends(get_db)
):
    """Save OCR text and embedding to document"""
    try:
        embedding_record = DocumentService.save_embedding_and_ocr(
            db=db,
            document_id=document_id,
            ocr_text=ocr_text,
            embedding=embedding
        )
        
        return {
            "document_id": document_id,
            "status": "saved",
            "ocr_length": len(ocr_text),
            "embedding_dimensions": len(embedding)
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get(
    "/documents/{document_id}",
    response_model=dict,
    summary="Get document details",
    description="Called by AI Service to retrieve document information"
)
def get_document_details(
    document_id: int,
    db: Session = Depends(get_db)
):
    """Get document details (minimal response, no schema details exposed)"""
    try:
        document = DocumentService.get_document_with_details(db, document_id)
        
        return {
            "id": document.id,
            "filename": document.title,
            "url": document.image_url,
            "owner_id": document.owner_id,
            "ocr_text": document.ocr_text,
            "created_at": document.created_at.isoformat()
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post(
    "/documents/search-similar",
    response_model=dict,
    summary="Search documents by vector similarity",
    description="Called by AI Service for semantic search"
)
def search_similar_documents(
    embedding: List[float] = Form(..., description="Query embedding vector"),
    limit: int = Form(10, description="Maximum results"),
    owner_id: Optional[int] = Form(None, description="Filter by owner"),
    db: Session = Depends(get_db)
):
    """Search documents by vector similarity"""
    try:
        documents = DocumentService.search_by_embedding(
            db=db,
            embedding=embedding,
            limit=limit,
            owner_id=owner_id
        )
        
        return {
            "count": len(documents),
            "documents": [
                {
                    "id": doc.id,
                    "filename": doc.title,
                    "owner_id": doc.owner_id
                }
                for doc in documents
            ]
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.patch(
    "/documents/{document_id}/status",
    response_model=dict,
    summary="Update document processing status",
    description="Called by AI Service to update document status"
)
def update_document_status(
    document_id: int,
    status_value: str = Form(..., description="Processing status"),
    metadata: Optional[dict] = Form(None, description="Additional metadata"),
    db: Session = Depends(get_db)
):
    """Update document status and metadata"""
    try:
        document = DocumentService.update_document_status(
            db=db,
            document_id=document_id,
            status=status_value,
            metadata_update=metadata
        )
        
        return {
            "id": document.id,
            "status": status_value,
            "updated_at": document.updated_at.isoformat()
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
