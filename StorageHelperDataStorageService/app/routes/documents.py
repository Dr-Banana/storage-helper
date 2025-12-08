"""
Document management routes
"""
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.database import get_db
from app.models.document import Document
from app.models.document_page import DocumentPage

router = APIRouter(prefix="/documents", tags=["documents"])


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
