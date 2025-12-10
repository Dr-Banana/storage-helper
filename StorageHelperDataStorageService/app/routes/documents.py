"""
Document management routes
"""
from pydantic import BaseModel
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from app.core.database import get_db
from app.models.document import Document
from app.models.document_page import DocumentPage
from app.models.document_embedding import DocumentEmbedding

router = APIRouter(prefix="/documents", tags=["documents"])


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
    summary="Get all page IDs for a document",
    description="Retrieve all page IDs for a specific document"
)
def get_document_pages(document_id: int, db: Session = Depends(get_db)):
    """
    Get all page IDs for a specific document
    
    - **document_id**: The document's ID
    
    Returns a list of page IDs for the document
    """
    try:
        # Verify document exists
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID {document_id} not found"
            )
        
        # Get all pages for the document
        pages = db.query(DocumentPage.id).filter(
            DocumentPage.document_id == document_id
        ).order_by(DocumentPage.page_number).all()
        page_ids = [page.id for page in pages]
        
        return {
            "document_id": document_id,
            "total": len(page_ids),
            "page_ids": page_ids
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
        
        if existing_embedding:
            # Update existing embedding
            existing_embedding.embedding = embedding_request.embedding
            action = "updated"
        else:
            # Create new embedding
            new_embedding = DocumentEmbedding(
                document_id=document_id,
                embedding=embedding_request.embedding
            )
            db.add(new_embedding)
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
