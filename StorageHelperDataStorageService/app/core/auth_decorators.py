"""
Authorization dependency functions for FastAPI routes.
Provides reusable authorization checks using FastAPI Depends system.
"""
from typing import Annotated
from fastapi import HTTPException, status, Depends, Path
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import get_current_user_id
from app.models.document import Document
from app.models.storage_location import StorageLocation


# ============================================================
# FastAPI Dependency Functions for Document Authorization
# ============================================================

def get_document_if_owner(
    document_id: Annotated[int, Path()],
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: Session = Depends(get_db)
) -> Document:
    """
    Dependency to fetch and verify document ownership.
    
    Usage in route:
        @router.get("/{document_id}/pages")
        def get_pages(
            doc: Annotated[Document, Depends(get_document_if_owner)],
            ...
        ):
    
    Raises:
        HTTPException: 404 if document not found, 403 if not owner
    """
    document = db.query(Document).filter(
        Document.id == document_id
    ).first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    if document.owner_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this document"
        )
    
    return document


# ============================================================
# FastAPI Dependency Functions for Location Authorization
# ============================================================

def get_location_if_owner(
    location_id: Annotated[int, Path()],
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: Session = Depends(get_db)
) -> StorageLocation:
    """
    Dependency to fetch and verify location ownership.
    
    Usage in route:
        @router.get("/{location_id}/image")
        def get_image(
            location: Annotated[StorageLocation, Depends(get_location_if_owner)],
            ...
        ):
    
    Raises:
        HTTPException: 404 if location not found, 403 if not owner
    """
    location = db.query(StorageLocation).filter(
        StorageLocation.id == location_id
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
    
    return location


